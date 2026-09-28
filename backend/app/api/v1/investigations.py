"""Investigation case endpoints.

A case is the operational record of "someone is looking into this" - see
`app.models.investigation`. It carries no money of its own; its evidence
endpoint assembles real, live-derived ledger data, the same functions the
Flight Recorder and Control Exception Engine already use.
"""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import Actor, DbSession, PaginationParams
from app.models.enums import InvestigationStatus
from app.schemas.common import Page
from app.schemas.investigation_case import (
    InvestigationCaseCreate,
    InvestigationCaseEvidenceOut,
    InvestigationCaseOut,
    InvestigationCaseResolve,
    InvestigationCaseStatusUpdate,
)
from app.schemas.serialize import investigation_case_evidence_out, investigation_case_out
from app.services import investigation_cases

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", response_model=InvestigationCaseOut, status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCaseCreate, db: DbSession, actor: Actor
) -> InvestigationCaseOut:
    case = investigation_cases.create_case(
        db,
        title=payload.title,
        issue_summary=payload.issue_summary,
        source=payload.source,
        source_reference=payload.source_reference,
        account_id=payload.account_id,
        window_from=payload.window_from,
        window_to=payload.window_to,
        created_by=actor,
    )
    return investigation_case_out(case)


@router.get("", response_model=Page[InvestigationCaseOut])
def list_investigations(
    db: DbSession,
    page: PaginationParams,
    case_status: InvestigationStatus | None = Query(default=None, alias="status"),
    account_id: uuid.UUID | None = Query(default=None),
) -> Page[InvestigationCaseOut]:
    items, total = investigation_cases.list_cases(
        db,
        status=case_status,
        account_id=account_id,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[InvestigationCaseOut](
        items=[investigation_case_out(c) for c in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{case_id}", response_model=InvestigationCaseOut)
def get_investigation(case_id: uuid.UUID, db: DbSession) -> InvestigationCaseOut:
    return investigation_case_out(investigation_cases.get_case(db, case_id))


@router.get("/{case_id}/evidence", response_model=InvestigationCaseEvidenceOut)
def get_investigation_evidence(case_id: uuid.UUID, db: DbSession) -> InvestigationCaseEvidenceOut:
    """Auto-assembled evidence: balance change over the case's window,
    account-scoped duplicate candidates, the ledger-wide integrity checklist,
    and - when the case names a triggering transaction - its lineage.
    """
    return investigation_case_evidence_out(investigation_cases.case_evidence(db, case_id))


@router.patch("/{case_id}/status", response_model=InvestigationCaseOut)
def update_investigation_status(
    case_id: uuid.UUID, payload: InvestigationCaseStatusUpdate, db: DbSession, actor: Actor
) -> InvestigationCaseOut:
    del actor  # authentication is required for this write; no per-status actor column exists yet
    return investigation_case_out(
        investigation_cases.update_status(db, case_id, status=payload.status)
    )


@router.post("/{case_id}/resolve", response_model=InvestigationCaseOut)
def resolve_investigation(
    case_id: uuid.UUID, payload: InvestigationCaseResolve, db: DbSession, actor: Actor
) -> InvestigationCaseOut:
    del actor  # authentication is required for this write; no per-resolution actor column exists yet
    return investigation_case_out(
        investigation_cases.resolve_case(
            db,
            case_id,
            resolution_note=payload.resolution_note,
            correction_transaction_id=payload.correction_transaction_id,
        )
    )
