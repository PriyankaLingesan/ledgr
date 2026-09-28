"""System, statistics, integrity and audit endpoints.

Every figure served here is derived from the ledger. There are no counters
maintained on the side and nothing is estimated.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select, text

from app.api.deps import DbSession, PaginationParams
from app.core import money
from app.core.config import settings
from app.core.metrics import snapshot as metrics_snapshot
from app.models.account import Account, AccountBalance
from app.models.enums import AccountStatus, InvestigationStatus, TransactionStatus
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.schemas.common import Money, Page
from app.schemas.investigation import ControlExceptionOut, DuplicateGroupOut, IntegrityChecklistOut
from app.schemas.serialize import control_exception_out, duplicate_group_out, integrity_check_out
from app.schemas.system import (
    AccountTypeCount,
    AuditEventOut,
    CurrencyOut,
    DailyVolume,
    ErrorPathCount,
    HealthOut,
    IntegrityIssue,
    IntegrityReport,
    LatestReconciliationRunOut,
    OperationalSnapshotOut,
    SystemStatsOut,
    TrialBalanceRow,
)
from app.services import balances, investigation, investigation_cases, queries, reconciliation

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db: DbSession) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
        database = "up"
    except Exception:  # pragma: no cover - exercised only when the DB is down
        database = "down"
    return HealthOut(
        status="ok" if database == "up" else "degraded",
        service=settings.app_name,
        version="1.0.0",
        environment=settings.environment,
        database=database,
    )


@router.get("/metrics", response_model=OperationalSnapshotOut)
def operational_snapshot(db: DbSession) -> OperationalSnapshotOut:
    """One call for "does anything need attention right now" - composed
    entirely from existing, already-tested functions (integrity, open
    investigations, the latest reconciliation run) plus in-process request
    counters. Nothing here is estimated or cached separately from what
    those functions themselves would report.
    """
    metrics = metrics_snapshot()

    trial = balances.trial_balance(db)
    checks = investigation.integrity_checklist(db)

    _, open_count = investigation_cases.list_cases(
        db, status=InvestigationStatus.OPEN, limit=1
    )

    runs, _ = reconciliation.list_runs(db, limit=1)
    latest_run = (
        LatestReconciliationRunOut(
            run_number=runs[0].seq,
            created_at=runs[0].created_at,
            missing_internal_count=runs[0].missing_internal_count,
            missing_external_count=runs[0].missing_external_count,
            mismatch_count=runs[0].mismatch_count,
            ambiguous_count=runs[0].ambiguous_count,
        )
        if runs
        else None
    )

    return OperationalSnapshotOut(
        generated_at=datetime.now(UTC),
        uptime_seconds=metrics.uptime_seconds,
        total_requests=metrics.total_requests,
        requests_by_status_class=metrics.requests_by_status_class,
        top_error_paths=[
            ErrorPathCount(path=path, count=count) for path, count in metrics.top_error_paths
        ],
        ledger_balanced=all(row.balanced for row in trial),
        integrity_all_passed=all(c.passed for c in checks),
        open_investigation_count=open_count,
        latest_reconciliation_run=latest_run,
    )


@router.get("/currencies", response_model=list[CurrencyOut])
def currencies() -> list[CurrencyOut]:
    """Supported currencies and their minor-unit exponents.

    The UI formats every amount from this, so major/minor conversion is defined
    in exactly one place across the stack.
    """
    return [
        CurrencyOut(
            code=code,
            name=money.CURRENCY_NAMES.get(code, code),
            exponent=money.CURRENCY_EXPONENTS[code],
        )
        for code in money.SUPPORTED_CURRENCIES
    ]


def _trial_balance_rows(db: DbSession) -> list[TrialBalanceRow]:
    return [
        TrialBalanceRow(
            currency=row.currency,
            debits=Money.of(row.debits_minor, row.currency),
            credits=Money.of(row.credits_minor, row.currency),
            difference=Money.of(row.difference_minor, row.currency),
            balanced=row.balanced,
            entry_count=row.entry_count,
        )
        for row in balances.trial_balance(db)
    ]


@router.get("/stats", response_model=SystemStatsOut)
def stats(db: DbSession, days: int = Query(default=14, ge=1, le=90)) -> SystemStatsOut:
    account_count = db.execute(select(func.count(Account.id))).scalar_one()
    active_count = db.execute(
        select(func.count(Account.id)).where(Account.status == AccountStatus.ACTIVE)
    ).scalar_one()
    by_type = db.execute(
        select(Account.type, func.count(Account.id)).group_by(Account.type).order_by(Account.type)
    ).all()

    transaction_count = db.execute(select(func.count(Transaction.id))).scalar_one()
    reversed_count = db.execute(
        select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.REVERSED)
    ).scalar_one()
    entry_count = db.execute(select(func.count(LedgerEntry.id))).scalar_one()

    trial = _trial_balance_rows(db)

    return SystemStatsOut(
        generated_at=datetime.now(UTC),
        account_count=int(account_count),
        active_account_count=int(active_count),
        accounts_by_type=[
            AccountTypeCount(type=row[0].value, count=int(row[1])) for row in by_type
        ],
        transaction_count=int(transaction_count),
        reversed_transaction_count=int(reversed_count),
        entry_count=int(entry_count),
        trial_balance=trial,
        ledger_balanced=all(row.balanced for row in trial),
        daily_volume=[
            DailyVolume(
                day=row.day,
                currency=row.currency,
                transaction_count=row.transaction_count,
                posted=Money.of(row.posted_minor, row.currency),
            )
            for row in balances.daily_volume(db, days=days)
        ],
    )


@router.get("/integrity", response_model=IntegrityReport)
def integrity(db: DbSession) -> IntegrityReport:
    """Re-derive every balance from ledger entries and compare with the cache.

    This is the check that keeps the cache honest: if it ever disagrees with
    the entries, the entries win and this endpoint says so out loud.
    """
    derived = balances.derive_all(db)
    cached = {row.account_id: row for row in db.execute(select(AccountBalance)).scalars().all()}
    codes = dict(db.execute(select(Account.id, Account.code)).all())

    issues: list[IntegrityIssue] = []
    account_ids = set(derived) | set(cached)
    for account_id in account_ids:
        d = derived.get(account_id)
        c = cached.get(account_id)
        derived_debits = d.debits_minor if d else 0
        derived_credits = d.credits_minor if d else 0
        cached_debits = c.debits_minor if c else 0
        cached_credits = c.credits_minor if c else 0
        code = codes.get(account_id, "?")
        if cached_debits != derived_debits:
            issues.append(
                IntegrityIssue(
                    account_id=account_id,
                    account_code=code,
                    field="debits_minor",
                    cached=cached_debits,
                    derived=derived_debits,
                )
            )
        if cached_credits != derived_credits:
            issues.append(
                IntegrityIssue(
                    account_id=account_id,
                    account_code=code,
                    field="credits_minor",
                    cached=cached_credits,
                    derived=derived_credits,
                )
            )

    trial = balances.trial_balance(db)
    unbalanced = balances.unbalanced_transaction_ids(db)

    return IntegrityReport(
        checked_at=datetime.now(UTC),
        accounts_checked=len(account_ids),
        cache_consistent=not issues,
        trial_balance_balanced=all(row.balanced for row in trial),
        unbalanced_transaction_ids=unbalanced,
        issues=issues,
    )


@router.get("/duplicates", response_model=list[DuplicateGroupOut])
def duplicates(
    db: DbSession,
    window_minutes: int = Query(default=10, ge=1, le=1440),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[DuplicateGroupOut]:
    """Deterministic, structural duplicate-posting candidates.

    Same account, same signed amount, same direction, posted within
    `window_minutes` of each other on two different transactions. This is a
    prompt to look closer, not a verdict - no AI, no fraud scoring; the
    operator decides what a match actually means.
    """
    return [
        duplicate_group_out(g)
        for g in investigation.duplicate_candidates(db, window_minutes=window_minutes, limit=limit)
    ]


@router.get("/integrity-checklist", response_model=IntegrityChecklistOut)
def integrity_checklist(db: DbSession) -> IntegrityChecklistOut:
    """Plain-language checklist over the correctness properties LEDGR already
    enforces - every check here re-verifies real data, none are static claims.
    """
    checks = investigation.integrity_checklist(db)
    return IntegrityChecklistOut(
        checked_at=datetime.now(UTC),
        all_passed=all(c.passed for c in checks),
        checks=[integrity_check_out(c) for c in checks],
    )


@router.get("/control-exceptions", response_model=list[ControlExceptionOut])
def control_exceptions(
    db: DbSession,
    window_minutes: int = Query(default=10, ge=1, le=1440),
) -> list[ControlExceptionOut]:
    """The Control Exception Engine: every real signal LEDGR's own checks can
    currently support - potential duplicates and failed integrity checks -
    unified into one list. Each exception names what was detected and what
    data supports it; nothing here is a verdict, and nothing is ever labelled
    fraud. This is the list the Financial Control Center's "Control
    exceptions" section and "Investigate" actions read from.
    """
    return [
        control_exception_out(e)
        for e in investigation.list_control_exceptions(db, duplicate_window_minutes=window_minutes)
    ]


@router.get("/audit", response_model=Page[AuditEventOut])
def audit_events(
    db: DbSession,
    page: PaginationParams,
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> Page[AuditEventOut]:
    items, total = queries.list_audit_events(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[AuditEventOut](
        items=[
            AuditEventOut(
                id=e.id,
                seq=e.seq,
                event_type=e.event_type,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                actor=e.actor,
                request_id=e.request_id,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in items
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
