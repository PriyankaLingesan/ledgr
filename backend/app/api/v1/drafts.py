"""Maker-checker endpoints: propose, submit, approve, reject.

A draft carries no financial effect. `POST .../approve` is the only route by
which one reaches the ledger, and it does so through the existing posting
engine (see `app.services.drafts`) - there is no second implementation.
"""

import uuid

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentPrincipal, DbSession, PaginationParams, RequestId, require
from app.core.permissions import Permission
from app.models.draft import TransactionDraft
from app.models.enums import DraftStatus
from app.schemas.common import Money, Page
from app.schemas.transaction import TransactionCreate
from app.schemas.workflow import DraftDecision, DraftEntryOut, DraftOut, DraftRejection
from app.services import drafts as draft_service
from app.services.auth import Principal

router = APIRouter(prefix="/transaction-drafts", tags=["maker-checker"])


def _out(draft: TransactionDraft, codes: dict[str, str], principal: Principal) -> DraftOut:
    mine = draft.created_by_ref == principal.ref
    return DraftOut(
        id=draft.id,
        seq=draft.seq,
        status=draft.status,
        description=draft.description,
        currency=draft.currency,
        reference=draft.reference,
        external_reference=draft.external_reference,
        effective_at=draft.effective_at,
        entries=[
            DraftEntryOut(
                account_id=uuid.UUID(e["account_id"]),
                account_code=codes.get(e["account_id"]),
                direction=e["direction"],
                amount=Money.of(int(e["amount_minor"]), draft.currency),
                memo=e.get("memo"),
            )
            for e in draft.entries
        ],
        total=Money.of(draft.total_minor, draft.currency),
        created_by=draft.created_by,
        created_at=draft.created_at,
        submitted_at=draft.submitted_at,
        decided_at=draft.decided_at,
        decided_by=draft.decided_by,
        decision_note=draft.decision_note,
        last_error=draft.last_error,
        posted_transaction_id=draft.posted_transaction_id,
        is_mine=mine,
        can_approve=(
            draft.status is DraftStatus.PENDING_APPROVAL
            and not mine
            and principal.can(Permission.DRAFTS_APPROVE)
        ),
    )


def _single(db: DbSession, draft: TransactionDraft, principal: Principal) -> DraftOut:
    return _out(draft, draft_service.account_codes(db, [draft]), principal)


@router.post(
    "",
    response_model=DraftOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.DRAFTS_CREATE))],
)
def create_draft(
    payload: TransactionCreate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
    submit: bool = Query(default=False, description="Create already submitted for approval"),
) -> DraftOut:
    draft = draft_service.create_draft(
        db, payload, principal=principal, submit=submit, request_id=request_id
    )
    return _single(db, draft, principal)


@router.get(
    "",
    response_model=Page[DraftOut],
    dependencies=[Depends(require(Permission.DRAFTS_READ))],
)
def list_drafts(
    db: DbSession,
    page: PaginationParams,
    principal: CurrentPrincipal,
    draft_status: DraftStatus | None = Query(default=None, alias="status"),
    mine: bool = Query(default=False),
) -> Page[DraftOut]:
    items, total = draft_service.list_drafts(
        db,
        status=draft_status,
        mine_ref=principal.ref if mine else None,
        limit=page.limit,
        offset=page.offset,
    )
    codes = draft_service.account_codes(db, items)
    return Page[DraftOut](
        items=[_out(d, codes, principal) for d in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{draft_id}",
    response_model=DraftOut,
    dependencies=[Depends(require(Permission.DRAFTS_READ))],
)
def get_draft(draft_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal) -> DraftOut:
    return _single(db, draft_service.get_draft(db, draft_id), principal)


@router.put(
    "/{draft_id}",
    response_model=DraftOut,
    dependencies=[Depends(require(Permission.DRAFTS_CREATE))],
)
def update_draft(
    draft_id: uuid.UUID,
    payload: TransactionCreate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> DraftOut:
    draft = draft_service.update_draft(
        db, draft_id, payload, principal=principal, request_id=request_id
    )
    return _single(db, draft, principal)


@router.post(
    "/{draft_id}/submit",
    response_model=DraftOut,
    dependencies=[Depends(require(Permission.DRAFTS_CREATE))],
)
def submit_draft(
    draft_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> DraftOut:
    draft = draft_service.submit_draft(db, draft_id, principal=principal, request_id=request_id)
    return _single(db, draft, principal)


@router.post(
    "/{draft_id}/approve",
    response_model=DraftOut,
    dependencies=[Depends(require(Permission.DRAFTS_APPROVE))],
    responses={
        403: {"description": "Not permitted, or the caller created this draft"},
        409: {"description": "Draft is not pending approval"},
        422: {"description": "The ledger refused the posting; the draft stays pending"},
    },
)
def approve_draft(
    draft_id: uuid.UUID,
    payload: DraftDecision,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> DraftOut:
    draft = draft_service.approve_draft(
        db, draft_id, principal=principal, note=payload.note, request_id=request_id
    )
    return _single(db, draft, principal)


@router.post(
    "/{draft_id}/reject",
    response_model=DraftOut,
    dependencies=[Depends(require(Permission.DRAFTS_APPROVE))],
)
def reject_draft(
    draft_id: uuid.UUID,
    payload: DraftRejection,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> DraftOut:
    draft = draft_service.reject_draft(
        db, draft_id, principal=principal, note=payload.note, request_id=request_id
    )
    return _single(db, draft, principal)
