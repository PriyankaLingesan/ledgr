"""Operational views: dashboard, alerts and onboarding.

Everything here is assembled from real ledger / audit / reconciliation data at
request time. There are no stored counters and no decorative figures.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select, text

from app.api.deps import Actor, DbSession, RequestId, require
from app.core.config import settings
from app.core.permissions import Permission
from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.draft import TransactionDraft
from app.models.enums import AccountStatus, DraftStatus, InvestigationStatus
from app.models.investigation import Investigation
from app.models.transaction import Transaction
from app.schemas.common import Money
from app.schemas.serialize import audit_event_out
from app.schemas.workflow import (
    AlertOut,
    AlertsOut,
    DashboardInvestigation,
    DashboardOut,
    DashboardReconciliation,
    DashboardTransaction,
    OnboardingOut,
    OnboardingResult,
    OnboardingStep,
)
from app.services import alerts as alert_service
from app.services import balances, investigation, onboarding, queries, reconciliation

router = APIRouter(tags=["operations"])


def _alert_out(a: alert_service.Alert) -> AlertOut:
    return AlertOut(
        id=a.id,
        rule=a.rule,
        severity=a.severity,
        title=a.title,
        detail=a.detail,
        count=a.count,
        last_seen=a.last_seen,
        link=a.link,
        evidence=a.evidence,
    )


@router.get(
    "/alerts",
    response_model=AlertsOut,
    dependencies=[Depends(require(Permission.ALERTS_READ))],
)
def list_alerts(db: DbSession) -> AlertsOut:
    """Deterministic operational alerts, computed now from real data (see
    `app.services.alerts`). Informational only: an alert never changes the
    ledger, and nothing here is produced by AI."""
    alerts = alert_service.compute_alerts(db)
    return AlertsOut(
        generated_at=datetime.now(UTC),
        window_hours=settings.alert_window_hours,
        counts=alert_service.counts_by_severity(alerts),
        alerts=[_alert_out(a) for a in alerts],
    )


@router.get(
    "/dashboard",
    response_model=DashboardOut,
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
)
def dashboard(db: DbSession) -> DashboardOut:
    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)

    account_count = db.execute(select(func.count(Account.id))).scalar_one()
    active_count = db.execute(
        select(func.count(Account.id)).where(Account.status == AccountStatus.ACTIVE)
    ).scalar_one()
    transaction_count = db.execute(select(func.count(Transaction.id))).scalar_one()
    last_24h = db.execute(
        select(func.count(Transaction.id)).where(Transaction.posted_at >= day_ago)
    ).scalar_one()
    recent, _ = queries.list_transactions(db, limit=8)

    pending = db.execute(
        select(func.count(TransactionDraft.id)).where(
            TransactionDraft.status == DraftStatus.PENDING_APPROVAL
        )
    ).scalar_one()

    open_cases = db.execute(
        select(func.count(Investigation.id)).where(
            Investigation.status != InvestigationStatus.RESOLVED
        )
    ).scalar_one()
    recent_cases = (
        db.execute(
            select(Investigation)
            .where(Investigation.status != InvestigationStatus.RESOLVED)
            .order_by(Investigation.created_at.desc())
            .limit(5)
        )
        .scalars()
        .all()
    )

    runs, _ = reconciliation.list_runs(db, limit=1)
    latest = None
    if runs:
        r = runs[0]
        exceptions = (
            r.mismatch_count
            + r.missing_internal_count
            + r.missing_external_count
            + r.ambiguous_count
        )
        latest = DashboardReconciliation(
            run_id=r.id,
            run_number=r.seq,
            run_at=r.created_at,
            external_count=r.external_count,
            matched=r.matched_count,
            mismatched=r.mismatch_count,
            missing_internal=r.missing_internal_count,
            missing_external=r.missing_external_count,
            ambiguous=r.ambiguous_count,
            exceptions=exceptions,
        )

    trial = balances.trial_balance(db)
    checks = investigation.integrity_checklist(db)
    try:
        db.execute(text("SELECT 1"))
        database = "up"
    except Exception:  # pragma: no cover
        database = "down"

    volume_30d: dict[str, tuple[int, int]] = {}
    for row in balances.daily_volume(db, days=30):
        n, total = volume_30d.get(row.currency, (0, 0))
        volume_30d[row.currency] = (n + row.transaction_count, total + row.posted_minor)
    failed_ops = {
        label: int(
            db.execute(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.event_type == event_type, AuditEvent.created_at >= day_ago
                )
            ).scalar_one()
        )
        for label, event_type in (
            ("rejected_postings", "transaction.post_failed"),
            ("refused_approvals", "draft.approval_failed"),
            ("rejected_drafts", "draft.rejected"),
        )
    }
    alerts = alert_service.compute_alerts(db)
    audit_items, _ = queries.list_audit_events(db, limit=8)

    return DashboardOut(
        generated_at=now,
        account_count=int(account_count),
        active_account_count=int(active_count),
        transaction_count=int(transaction_count),
        transactions_last_24h=int(last_24h),
        recent_transactions=[
            DashboardTransaction(
                id=t.id,
                reference=t.reference,
                description=t.description,
                amount=Money.of(t.total_debits_minor, t.currency),
                status=t.status.value,
                kind=t.kind.value,
                actor=t.actor,
                posted_at=t.posted_at,
            )
            for t in recent
        ],
        volume_window_days=30,
        posted_volume=[
            {"currency": cur, "transaction_count": n, "posted_minor": total}
            for cur, (n, total) in sorted(volume_30d.items())
        ],
        failed_operations_24h=failed_ops,
        daily_volume=[
            {
                "day": row.day.isoformat(),
                "currency": row.currency,
                "transaction_count": row.transaction_count,
                "posted_minor": row.posted_minor,
            }
            for row in balances.daily_volume(db, days=14)
        ],
        pending_approvals=int(pending),
        open_investigations=int(open_cases),
        recent_investigations=[
            DashboardInvestigation(
                id=c.id,
                case_number=c.case_number,
                title=c.title,
                status=c.status.value,
                priority=c.priority.value,
                created_at=c.created_at,
            )
            for c in recent_cases
        ],
        latest_reconciliation=latest,
        ledger_balanced=all(row.balanced for row in trial),
        integrity_all_passed=all(c.passed for c in checks),
        integrity_checks_failed=[c.name for c in checks if not c.passed],
        database=database,
        alert_counts=alert_service.counts_by_severity(alerts),
        top_alerts=[_alert_out(a) for a in alerts[:5]],
        recent_audit=[audit_event_out(e) for e in audit_items],
    )


@router.get(
    "/onboarding",
    response_model=OnboardingOut,
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
)
def onboarding_checklist(db: DbSession) -> OnboardingOut:
    org_name, steps = onboarding.checklist(db)
    return OnboardingOut(
        organization_name=org_name,
        steps=[
            OnboardingStep(
                key=s.key,
                title=s.title,
                description=s.description,
                done=s.done,
                action_path=s.action_path,
            )
            for s in steps
        ],
        completed=sum(s.done for s in steps),
        total=len(steps),
    )


@router.post(
    "/onboarding/demo-data",
    response_model=OnboardingResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.ORG_MANAGE))],
)
def load_demo_data(db: DbSession, actor: Actor, request_id: RequestId) -> OnboardingResult:
    """Create the demo accounts if missing, post the demo transactions through
    the normal posting engine, and import the demo external records. Safe to
    repeat: every write is idempotent."""
    result, created = onboarding.load_demo_reconciliation(db, actor=actor, request_id=request_id)
    return OnboardingResult(
        message=(
            f"Imported {result.imported} external record(s); "
            f"{result.skipped_duplicates} already present. Run a reconciliation to compare them."
        ),
        created=created,
    )
