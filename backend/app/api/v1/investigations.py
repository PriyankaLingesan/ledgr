"""Investigation case endpoints.

A case is the operational record of "someone is looking into this" - see
`app.models.investigation`. It carries no money of its own; its evidence
endpoint assembles real, live-derived ledger data, the same functions the
Flight Recorder and Control Exception Engine already use.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import Actor, CurrentPrincipal, DbSession, PaginationParams, RequestId, require
from app.core.permissions import Permission
from app.models.enums import InvestigationPriority, InvestigationStatus
from app.schemas.casework import NoteCreate, NoteOut
from app.schemas.common import Page
from app.schemas.investigation_case import (
    InvestigationCaseCreate,
    InvestigationCaseEvidenceOut,
    InvestigationCaseOut,
    InvestigationCaseResolve,
    InvestigationCaseStatusUpdate,
)
from app.schemas.serialize import investigation_case_evidence_out, investigation_case_out
from app.services import casework, investigation_cases

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post(
    "",
    response_model=InvestigationCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def create_investigation(
    payload: InvestigationCaseCreate, db: DbSession, actor: Actor, request_id: RequestId
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
        priority=payload.priority,
        created_by=actor,
        request_id=request_id,
    )
    return investigation_case_out(case)


@router.get(
    "",
    response_model=Page[InvestigationCaseOut],
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
)
def list_investigations(
    db: DbSession,
    page: PaginationParams,
    case_status: InvestigationStatus | None = Query(default=None, alias="status"),
    account_id: uuid.UUID | None = Query(default=None),
    priority: InvestigationPriority | None = Query(default=None),
    q: str | None = Query(default=None, description="Match case number or title"),
) -> Page[InvestigationCaseOut]:
    items, total = investigation_cases.list_cases(
        db,
        status=case_status,
        account_id=account_id,
        priority=priority,
        query=q,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[InvestigationCaseOut](
        items=[investigation_case_out(c) for c in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{case_id}",
    response_model=InvestigationCaseOut,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
)
def get_investigation(case_id: uuid.UUID, db: DbSession) -> InvestigationCaseOut:
    return investigation_case_out(investigation_cases.get_case(db, case_id))


@router.get(
    "/{case_id}/evidence",
    response_model=InvestigationCaseEvidenceOut,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
)
def get_investigation_evidence(case_id: uuid.UUID, db: DbSession) -> InvestigationCaseEvidenceOut:
    """Auto-assembled evidence: balance change over the case's window,
    account-scoped duplicate candidates, the ledger-wide integrity checklist,
    and - when the case names a triggering transaction - its lineage.
    """
    return investigation_case_evidence_out(investigation_cases.case_evidence(db, case_id))


@router.patch(
    "/{case_id}/status",
    response_model=InvestigationCaseOut,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def update_investigation_status(
    case_id: uuid.UUID,
    payload: InvestigationCaseStatusUpdate,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
) -> InvestigationCaseOut:
    return investigation_case_out(
        investigation_cases.update_status(
            db, case_id, status=payload.status, actor=actor, request_id=request_id
        )
    )


@router.post(
    "/{case_id}/resolve",
    response_model=InvestigationCaseOut,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def resolve_investigation(
    case_id: uuid.UUID,
    payload: InvestigationCaseResolve,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
) -> InvestigationCaseOut:
    return investigation_case_out(
        investigation_cases.resolve_case(
            db,
            case_id,
            resolution_note=payload.resolution_note,
            correction_transaction_id=payload.correction_transaction_id,
            actor=actor,
            request_id=request_id,
        )
    )


@router.get(
    "/{case_id}/notes",
    response_model=list[NoteOut],
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
)
def list_case_notes(case_id: uuid.UUID, db: DbSession) -> list[NoteOut]:
    return [
        NoteOut(id=n.id, author=n.author, body=n.body, created_at=n.created_at)
        for n in casework.list_notes(db, case_id)
    ]


@router.post(
    "/{case_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def add_case_note(
    case_id: uuid.UUID,
    payload: NoteCreate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> NoteOut:
    """Append an investigator note. Notes cannot be edited or deleted - the
    history of what was concluded, and by whom, is itself evidence."""
    note = casework.add_note(
        db, case_id, principal=principal, body=payload.body, request_id=request_id
    )
    return NoteOut(id=note.id, author=note.author, body=note.body, created_at=note.created_at)


@router.get(
    "/{case_id}/report",
    dependencies=[Depends(require(Permission.INVESTIGATIONS_READ))],
    responses={200: {"content": {"text/markdown": {}}}},
)
def download_case_report(case_id: uuid.UUID, db: DbSession) -> Response:
    """The case's evidence as a Markdown file - assembled from real records
    only (integrity checks, reconciliation evidence, notes, audit trail)."""
    report = casework.render_case_report_markdown(db, case_id)
    case = investigation_cases.get_case(db, case_id)
    return Response(
        content=report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{case.case_number}-evidence.md"'},
    )
