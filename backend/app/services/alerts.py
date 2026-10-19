"""Operational alerts - deterministic rules over real data.

Every alert is computed on demand from the audit trail, the ledger and the
latest reconciliation run; nothing is stored, estimated or AI-generated, and
an alert can never change financial state. Each one names the rule that fired,
the evidence count behind it and where to look. Thresholds are configuration
(`ALERT_*` settings), not hidden constants.

Rules
-----
repeated_retries          the same idempotency key replayed >= N times
idempotency_conflicts     idempotency-key misuse (reused with a different body,
                          or retried while in flight) >= N times by one actor
failed_postings           >= N rejected posting attempts by one actor
large_transaction         a posted transaction at/above the configured size
repeated_reversals        >= N reversals posted by one actor
reconciliation_exceptions the latest run left mismatches / missing / ambiguous
balance_floor             a no-overdraft account is near (or at) its floor
ledger_integrity          debits != credits somewhere, or an unbalanced txn
repeated_login_failures   >= N failed logins for one identity
permission_denials        >= N refused actions by one actor
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.money import format_amount
from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.enums import AccountStatus, TransactionKind
from app.models.transaction import Transaction
from app.services import balances, reconciliation

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class Alert:
    id: str  # stable for the same underlying condition
    rule: str
    severity: str
    title: str
    detail: str
    count: int = 1
    last_seen: datetime | None = None
    link: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _grouped_audit(
    db: Session, *, event_type: str, since: datetime, group: Any, min_count: int, limit: int = 10
):
    return db.execute(
        select(group, func.count(AuditEvent.id), func.max(AuditEvent.created_at))
        .where(AuditEvent.event_type == event_type, AuditEvent.created_at >= since)
        .group_by(group)
        .having(func.count(AuditEvent.id) >= min_count)
        .order_by(func.count(AuditEvent.id).desc())
        .limit(limit)
    ).all()


def compute_alerts(db: Session) -> list[Alert]:
    now = datetime.now(UTC)
    since = now - timedelta(hours=settings.alert_window_hours)
    threshold = settings.alert_repeat_threshold
    window = f"{settings.alert_window_hours}h"
    alerts: list[Alert] = []

    # -- repeated retries: one idempotency key replayed over and over ---------
    for key, count, last in _grouped_audit(
        db,
        event_type="idempotency.replay",
        since=since,
        group=AuditEvent.payload["key"].astext,
        min_count=threshold,
    ):
        alerts.append(
            Alert(
                id=f"repeated_retries:{key}",
                rule="repeated_retries",
                severity="MEDIUM",
                title="Repeated transaction retries",
                detail=(
                    f"Idempotency key '{key}' was replayed {count} times in the last {window}. "
                    "The ledger posted it once; a client may be stuck in a retry loop."
                ),
                count=count,
                last_seen=last,
                link="/audit?event_type=idempotency.replay",
                evidence={"idempotency_key": key},
            )
        )

    # -- idempotency misuse ----------------------------------------------------
    for actor, count, last in _grouped_audit(
        db,
        event_type="idempotency.rejected",
        since=since,
        group=AuditEvent.actor,
        min_count=threshold,
    ):
        alerts.append(
            Alert(
                id=f"idempotency_conflicts:{actor}",
                rule="idempotency_conflicts",
                severity="MEDIUM",
                title="Repeated idempotency-key conflicts",
                detail=(
                    f"{actor} had {count} requests refused for idempotency-key misuse "
                    f"in the last {window}."
                ),
                count=count,
                last_seen=last,
                link="/audit?event_type=idempotency.rejected",
                evidence={"actor": actor},
            )
        )

    # -- failed posting attempts ------------------------------------------------
    for actor, count, last in _grouped_audit(
        db,
        event_type="transaction.post_failed",
        since=since,
        group=AuditEvent.actor,
        min_count=threshold,
    ):
        alerts.append(
            Alert(
                id=f"failed_postings:{actor}",
                rule="failed_postings",
                severity="HIGH",
                title="Multiple failed posting attempts",
                detail=(
                    f"{actor} had {count} posting attempts rejected by the ledger "
                    f"in the last {window}. Nothing was posted; check what is being attempted."
                ),
                count=count,
                last_seen=last,
                link="/audit?event_type=transaction.post_failed",
                evidence={"actor": actor},
            )
        )

    # -- large transactions -----------------------------------------------------
    large = (
        db.execute(
            select(Transaction)
            .where(
                Transaction.posted_at >= since,
                Transaction.kind == TransactionKind.STANDARD,
                Transaction.total_debits_minor >= settings.alert_large_transaction_minor,
            )
            .order_by(Transaction.posted_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    for txn in large:
        amount = format_amount(txn.total_debits_minor, txn.currency)
        alerts.append(
            Alert(
                id=f"large_transaction:{txn.id}",
                rule="large_transaction",
                severity="MEDIUM",
                title="Large transaction posted",
                detail=f"{txn.reference} moved {amount} {txn.currency} (posted by {txn.actor}).",
                last_seen=txn.posted_at,
                link=f"/transactions/{txn.id}",
                evidence={"transaction_id": str(txn.id), "total_minor": txn.total_debits_minor},
            )
        )

    # -- repeated reversals -----------------------------------------------------
    for actor, count, last in db.execute(
        select(Transaction.actor, func.count(Transaction.id), func.max(Transaction.posted_at))
        .where(Transaction.kind == TransactionKind.REVERSAL, Transaction.posted_at >= since)
        .group_by(Transaction.actor)
        .having(func.count(Transaction.id) >= threshold)
        .order_by(func.count(Transaction.id).desc())
        .limit(10)
    ).all():
        alerts.append(
            Alert(
                id=f"repeated_reversals:{actor}",
                rule="repeated_reversals",
                severity="HIGH",
                title="Repeated reversals",
                detail=f"{actor} reversed {count} transactions in the last {window}.",
                count=count,
                last_seen=last,
                link="/transactions?kind=REVERSAL",
                evidence={"actor": actor},
            )
        )

    # -- reconciliation exceptions (latest run) ---------------------------------
    runs, _ = reconciliation.list_runs(db, limit=1)
    if runs:
        run = runs[0]
        open_exceptions = (
            run.mismatch_count
            + run.missing_internal_count
            + run.missing_external_count
            + run.ambiguous_count
        )
        if open_exceptions:
            parts = [
                f"{n} {label}"
                for n, label in (
                    (run.mismatch_count, "amount mismatch(es)"),
                    (run.missing_internal_count, "missing internally"),
                    (run.missing_external_count, "missing externally"),
                    (run.ambiguous_count, "ambiguous"),
                )
                if n
            ]
            alerts.append(
                Alert(
                    id=f"reconciliation_exceptions:{run.id}",
                    rule="reconciliation_exceptions",
                    severity="HIGH" if run.mismatch_count or run.ambiguous_count else "MEDIUM",
                    title="Reconciliation exceptions need review",
                    detail=f"Run #{run.seq} left {', '.join(parts)}.",
                    count=open_exceptions,
                    last_seen=run.created_at,
                    link="/reconciliation",
                    evidence={"run_id": str(run.id)},
                )
            )

    # -- accounts near their balance floor ----------------------------------------
    floor_accounts = (
        db.execute(
            select(Account).where(
                Account.allows_negative_balance.is_(False), Account.status == AccountStatus.ACTIVE
            )
        )
        .scalars()
        .all()
    )
    if floor_accounts:
        derived = balances.derive_all(db)
        for account in floor_accounts:
            d = derived.get(account.id)
            if d is None:
                continue  # never used - nothing to warn about
            balance = (
                d.debits_minor - d.credits_minor
                if account.normal_balance.value == "DEBIT"
                else d.credits_minor - d.debits_minor
            )
            if balance <= settings.alert_balance_floor_warning_minor:
                at_floor = balance <= 0
                alerts.append(
                    Alert(
                        id=f"balance_floor:{account.id}",
                        rule="balance_floor",
                        severity="HIGH" if at_floor else "MEDIUM",
                        title="Account at its balance floor"
                        if at_floor
                        else "Account near its balance floor",
                        detail=(
                            f"{account.code} holds {format_amount(balance, account.currency)} "
                            f"{account.currency} and cannot go negative."
                        ),
                        link=f"/accounts/{account.id}",
                        evidence={"account_id": str(account.id), "balance_minor": balance},
                    )
                )

    # -- ledger integrity ---------------------------------------------------------
    unbalanced = balances.unbalanced_transaction_ids(db, limit=5)
    trial = balances.trial_balance(db)
    if unbalanced or not all(row.balanced for row in trial):
        alerts.append(
            Alert(
                id="ledger_integrity",
                rule="ledger_integrity",
                severity="CRITICAL",
                title="Ledger integrity check failed",
                detail=(
                    "Total debits no longer equal total credits, "
                    "or a transaction does not net to zero."
                ),
                link="/system",
                evidence={"unbalanced_transactions": [str(i) for i in unbalanced]},
            )
        )

    # -- security-flavoured signals -------------------------------------------------
    for actor, count, last in _grouped_audit(
        db,
        event_type="auth.login_failed",
        since=since,
        group=AuditEvent.actor,
        min_count=threshold,
    ):
        alerts.append(
            Alert(
                id=f"repeated_login_failures:{actor}",
                rule="repeated_login_failures",
                severity="MEDIUM",
                title="Repeated failed logins",
                detail=f"{count} failed sign-in attempts for {actor} in the last {window}.",
                count=count,
                last_seen=last,
                link="/audit?event_type=auth.login_failed",
                evidence={"identity": actor},
            )
        )
    for actor, count, last in _grouped_audit(
        db,
        event_type="auth.permission_denied",
        since=since,
        group=AuditEvent.actor,
        min_count=threshold,
    ):
        alerts.append(
            Alert(
                id=f"permission_denials:{actor}",
                rule="permission_denials",
                severity="MEDIUM",
                title="Repeated refused actions",
                detail=(
                    f"{actor} was refused {count} times for lacking permission "
                    f"in the last {window}."
                ),
                count=count,
                last_seen=last,
                link="/audit?event_type=auth.permission_denied",
                evidence={"actor": actor},
            )
        )

    order = {s: i for i, s in enumerate(SEVERITIES)}
    alerts.sort(key=lambda a: (order[a.severity], -(a.last_seen.timestamp() if a.last_seen else 0)))
    return alerts


def counts_by_severity(alerts: list[Alert]) -> dict[str, int]:
    out = dict.fromkeys(SEVERITIES, 0)
    for a in alerts:
        out[a.severity] += 1
    return out
