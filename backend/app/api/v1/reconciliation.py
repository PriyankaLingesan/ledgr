"""External reconciliation endpoints.

Comparing outside evidence against the ledger. Every endpoint here is
either an import (writes only to `external_transactions`) or a run (writes
only to the reconciliation tables) - none of them can post a transaction,
reverse one, or touch a balance. The only way reconciliation output changes
financial state is through the existing, unmodified correction workflow.
"""

import uuid

from fastapi import APIRouter, status

from app.api.deps import Actor, DbSession, PaginationParams
from app.models.enums import InvestigationSource
from app.schemas.common import Page
from app.schemas.investigation_case import InvestigationCaseOut
from app.schemas.reconciliation import (
    ExternalTransactionImportRequest,
    ImportResultOut,
    ReconciliationRunOut,
    ReconciliationRunSummaryOut,
)
from app.schemas.serialize import (
    investigation_case_out,
    reconciliation_run_out,
    reconciliation_run_summary_out,
)
from app.services import accounts as account_service
from app.services import investigation_cases, reconciliation

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post("/import", response_model=ImportResultOut, status_code=status.HTTP_201_CREATED)
def import_external_transactions(
    payload: ExternalTransactionImportRequest, db: DbSession, actor: Actor
) -> ImportResultOut:
    """Idempotent by (source_system, external_id): importing the same
    dataset twice never creates duplicate external records.
    """
    del actor  # authentication required; ExternalTransaction has no per-import actor column
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
    return ImportResultOut(imported=result.imported, skipped_duplicates=result.skipped_duplicates)


@router.post("/import-demo", response_model=ImportResultOut, status_code=status.HTTP_201_CREATED)
def import_demo_data(db: DbSession, actor: Actor) -> ImportResultOut:
    """Loads a small, deterministic demo scenario (4 matches, 1 amount
    mismatch, 3 missing-internal) so the feature can be demonstrated
    immediately. Safe to call more than once - every write is idempotent.
    """
    del actor
    cash = account_service.get_account_by_code(db, "CASH.OPERATING.USD")
    revenue = account_service.get_account_by_code(db, "REV.FEES.USD")
    result = reconciliation.load_demo_data(db, cash_account_id=cash.id, revenue_account_id=revenue.id)
    return ImportResultOut(imported=result.imported, skipped_duplicates=result.skipped_duplicates)


@router.post("/run", response_model=ReconciliationRunOut, status_code=status.HTTP_201_CREATED)
def run_reconciliation(db: DbSession, actor: Actor) -> ReconciliationRunOut:
    """Compare every ingested external transaction against the ledger.

    Read-only with respect to `transactions`/`ledger_entries` - repeatable,
    and every call produces its own independent run. Recorded as a
    structured audit event (`reconciliation.run_completed`), attributed to
    the authenticated caller.
    """
    run = reconciliation.run_reconciliation(db, actor=actor)
    return reconciliation_run_out(run)


@router.get("/runs", response_model=Page[ReconciliationRunSummaryOut])
def list_reconciliation_runs(db: DbSession, page: PaginationParams) -> Page[ReconciliationRunSummaryOut]:
    items, total = reconciliation.list_runs(db, limit=page.limit, offset=page.offset)
    return Page[ReconciliationRunSummaryOut](
        items=[reconciliation_run_summary_out(r) for r in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runs/{run_id}", response_model=ReconciliationRunOut)
def get_reconciliation_run(run_id: uuid.UUID, db: DbSession) -> ReconciliationRunOut:
    return reconciliation_run_out(reconciliation.load_run(db, run_id))


@router.post(
    "/results/{result_id}/investigate",
    response_model=InvestigationCaseOut,
    status_code=status.HTTP_201_CREATED,
)
def investigate_reconciliation_result(
    result_id: uuid.UUID, db: DbSession, actor: Actor
) -> InvestigationCaseOut:
    """Open a case in the existing investigation system for a reconciliation
    exception - no separate investigation workflow. Whichever side of the
    comparison exists (external evidence, internal transaction, or both -
    MISSING_EXTERNAL has no external row) is folded into the case's issue
    summary; when an internal transaction is involved, the case is linked to
    it so lineage and balance-change evidence resolve automatically.
    """
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
        source_reference=str(result.internal_transaction_id) if result.internal_transaction_id else None,
        created_by=actor,
    )
    return investigation_case_out(case)
