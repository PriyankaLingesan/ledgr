"""External reconciliation endpoints.

Comparing outside evidence against the ledger. Every endpoint here is
either an import (writes only to `external_transactions`) or a run (writes
only to the reconciliation tables) - none of them can post a transaction,
reverse one, or touch a balance. The only way reconciliation output changes
financial state is through the existing, unmodified correction workflow.
Ambiguous matches stay ambiguous: nothing here resolves them by guessing.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.api.deps import Actor, CurrentPrincipal, DbSession, PaginationParams, RequestId, require
from app.core.permissions import Permission
from app.models.enums import InvestigationPriority, InvestigationSource, ReconciliationStatus
from app.models.investigation import Investigation
from app.models.reconciliation import ExternalTransaction
from app.schemas.casework import DecisionCreate, DecisionOut, SuggestionOut
from app.schemas.common import Money, Page
from app.schemas.investigation_case import InvestigationCaseOut
from app.schemas.reconciliation import (
    ExternalTransactionImportRequest,
    ImportResultOut,
    ReconciliationRunOut,
    ReconciliationRunSummaryOut,
    ReconciliationSourceOut,
)
from app.schemas.serialize import (
    investigation_case_out,
    reconciliation_run_out,
    reconciliation_run_summary_out,
)
from app.services import audit, casework, investigation_cases, onboarding, reconciliation

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


def _decision_out(d) -> DecisionOut:
    return DecisionOut(
        id=d.id,
        decision=d.decision,
        internal_transaction_id=d.internal_transaction_id,
        reason=d.reason,
        decided_by=d.decided_by,
        created_at=d.created_at,
    )


# Exceptions that deserve a high-priority case when opened for investigation.
_HIGH_PRIORITY = {
    ReconciliationStatus.AMOUNT_MISMATCH,
    ReconciliationStatus.AMBIGUOUS_INTERNAL_MATCH,
}


@router.post(
    "/import",
    response_model=ImportResultOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RECONCILIATION_RUN))],
)
def import_external_transactions(
    payload: ExternalTransactionImportRequest,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
) -> ImportResultOut:
    """Idempotent by (source_system, external_id): importing the same
    dataset twice never creates duplicate external records.
    """
    result = reconciliation.ingest_external_transactions(
        db,
        source_system=payload.source_system,
        records=[
            reconciliation.ExternalRecord(
                external_id=r.external_id,
                amount_minor=r.amount_minor,
                currency=r.currency,
                direction=r.direction,
                transaction_timestamp=r.transaction_timestamp,
                account_reference=r.account_reference,
                status=r.status,
                description=r.description,
            )
            for r in payload.records
        ],
    )
    audit.record(
        db,
        event_type="reconciliation.imported",
        resource_type="reconciliation",
        resource_id=payload.source_system[:64],
        actor=actor,
        request_id=request_id,
        payload={
            "source_system": payload.source_system,
            "imported": result.imported,
            "skipped_duplicates": result.skipped_duplicates,
        },
    )
    db.commit()
    return ImportResultOut(imported=result.imported, skipped_duplicates=result.skipped_duplicates)


@router.post(
    "/import-demo",
    response_model=ImportResultOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RECONCILIATION_RUN))],
)
def import_demo_data(db: DbSession, actor: Actor, request_id: RequestId) -> ImportResultOut:
    """Loads a small, deterministic demo scenario (4 matches, 1 amount
    mismatch, 3 missing-internal) so the feature can be demonstrated
    immediately - creating the two demo accounts first if they don't exist.
    Safe to call more than once - every write is idempotent.
    """
    result, _ = onboarding.load_demo_reconciliation(db, actor=actor, request_id=request_id)
    return ImportResultOut(imported=result.imported, skipped_duplicates=result.skipped_duplicates)


@router.post(
    "/run",
    response_model=ReconciliationRunOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RECONCILIATION_RUN))],
)
def run_reconciliation(db: DbSession, actor: Actor, request_id: RequestId) -> ReconciliationRunOut:
    """Compare every ingested external transaction against the ledger.

    Read-only with respect to `transactions`/`ledger_entries` - repeatable,
    and every call produces its own independent run. Recorded as a
    structured audit event (`reconciliation.run_completed`), attributed to
    the authenticated caller.
    """
    run = reconciliation.run_reconciliation(db, actor=actor, request_id=request_id)
    return _run_out(db, run, None)


@router.get(
    "/sources",
    response_model=list[ReconciliationSourceOut],
    dependencies=[Depends(require(Permission.RECONCILIATION_READ))],
)
def list_sources(db: DbSession) -> list[ReconciliationSourceOut]:
    """Every external system records have been imported from."""
    rows = db.execute(
        select(
            ExternalTransaction.source_system,
            func.count(ExternalTransaction.id),
            func.max(ExternalTransaction.transaction_timestamp),
            func.max(ExternalTransaction.created_at),
        )
        .group_by(ExternalTransaction.source_system)
        .order_by(ExternalTransaction.source_system)
    ).all()
    return [
        ReconciliationSourceOut(
            source_system=r[0],
            record_count=int(r[1]),
            latest_transaction_at=r[2],
            last_imported_at=r[3],
        )
        for r in rows
    ]


@router.get(
    "/runs",
    response_model=Page[ReconciliationRunSummaryOut],
    dependencies=[Depends(require(Permission.RECONCILIATION_READ))],
)
def list_reconciliation_runs(
    db: DbSession, page: PaginationParams
) -> Page[ReconciliationRunSummaryOut]:
    items, total = reconciliation.list_runs(db, limit=page.limit, offset=page.offset)
    return Page[ReconciliationRunSummaryOut](
        items=[reconciliation_run_summary_out(r) for r in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


def _run_out(db: DbSession, run, only: ReconciliationStatus | None) -> ReconciliationRunOut:
    out = reconciliation_run_out(run)
    if only is not None:
        out.results = [r for r in out.results if r.status is only]
    # Link each exception to the case already opened for it, if any.
    result_ids = [r.id for r in out.results]
    if result_ids:
        cases = db.execute(
            select(
                Investigation.reconciliation_result_id, Investigation.id, Investigation.case_number
            )
            .where(Investigation.reconciliation_result_id.in_(result_ids))
            .order_by(Investigation.created_at)
        ).all()
        by_result = {c[0]: c for c in cases}
        decisions = casework.latest_decisions(db, result_ids)
        for r in out.results:
            hit = by_result.get(r.id)
            if hit:
                r.investigation_id = hit[1]
                r.investigation_case_number = hit[2]
            d = decisions.get(r.id)
            if d:
                r.latest_decision = _decision_out(d)
    return out


@router.get(
    "/runs/{run_id}",
    response_model=ReconciliationRunOut,
    dependencies=[Depends(require(Permission.RECONCILIATION_READ))],
)
def get_reconciliation_run(
    run_id: uuid.UUID,
    db: DbSession,
    result_status: ReconciliationStatus | None = Query(default=None, alias="status"),
) -> ReconciliationRunOut:
    return _run_out(db, reconciliation.load_run(db, run_id), result_status)


@router.post(
    "/results/{result_id}/investigate",
    response_model=InvestigationCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def investigate_reconciliation_result(
    result_id: uuid.UUID, db: DbSession, actor: Actor, request_id: RequestId
) -> InvestigationCaseOut:
    """Open a case in the existing investigation system for a reconciliation
    exception - no separate investigation workflow. Whichever side of the
    comparison exists (external evidence, internal transaction, or both -
    MISSING_EXTERNAL has no external row) is folded into the case's issue
    summary; when an internal transaction is involved, the case is linked to
    it so lineage and balance-change evidence resolve automatically.

    Opening a case never changes the ledger or the reconciliation result.
    If a case is already open for this result it is returned instead of
    creating a duplicate.
    """
    existing = db.execute(
        select(Investigation.id).where(Investigation.reconciliation_result_id == result_id)
    ).first()
    if existing is not None:
        return investigation_case_out(investigation_cases.get_case(db, existing[0]))

    result = reconciliation.get_result(db, result_id)
    external = result.external_transaction

    label = external.external_id if external else result.internal_transaction.reference
    context = (
        f"Source: {external.source_system}. "
        f"External transaction timestamp: {external.transaction_timestamp.isoformat()}."
        if external
        else "No external record exists for this internal transaction."
    )

    case = investigation_cases.create_case(
        db,
        title=f"Reconciliation exception: {label} ({result.status.value})",
        issue_summary=f"{result.explanation} {context}",
        source=InvestigationSource.RECONCILIATION,
        source_reference=(
            str(result.internal_transaction_id) if result.internal_transaction_id else None
        ),
        priority=(
            InvestigationPriority.HIGH
            if result.status in _HIGH_PRIORITY
            else InvestigationPriority.MEDIUM
        ),
        reconciliation_result_id=result.id,
        created_by=actor,
        request_id=request_id,
    )
    return investigation_case_out(case)


@router.get(
    "/results/{result_id}/suggestions",
    response_model=list[SuggestionOut],
    dependencies=[Depends(require(Permission.RECONCILIATION_READ))],
)
def result_suggestions(result_id: uuid.UUID, db: DbSession) -> list[SuggestionOut]:
    """Deterministic candidate ledger transactions for one result, with the
    reasons for each. Suggestions only: nothing is matched or posted."""
    return [
        SuggestionOut(
            transaction_id=s.transaction.id,
            reference=s.transaction.reference,
            description=s.transaction.description,
            amount=Money.of(s.transaction.total_debits_minor, s.transaction.currency),
            effective_at=s.transaction.effective_at,
            score=s.score,
            reasons=s.reasons,
        )
        for s in casework.suggest_matches(db, result_id)
    ]


@router.get(
    "/results/{result_id}/decisions",
    response_model=list[DecisionOut],
    dependencies=[Depends(require(Permission.RECONCILIATION_READ))],
)
def result_decisions(result_id: uuid.UUID, db: DbSession) -> list[DecisionOut]:
    return [_decision_out(d) for d in casework.list_decisions(db, result_id)]


@router.post(
    "/results/{result_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.RECONCILIATION_RUN))],
)
def record_result_decision(
    result_id: uuid.UUID,
    payload: DecisionCreate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> DecisionOut:
    """Record that a person reviewed this result and reached a conclusion.

    Append-only and audited. It annotates the result; it does not change the
    result's deterministic status, post anything, or move a balance.
    """
    decision = casework.record_decision(
        db,
        result_id,
        principal=principal,
        decision=payload.decision,
        internal_transaction_id=payload.internal_transaction_id,
        reason=payload.reason,
        request_id=request_id,
    )
    return _decision_out(decision)
