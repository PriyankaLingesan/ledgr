"""Maker-checker: proposed transactions and their approval.

    DRAFT --submit--> PENDING_APPROVAL --approve--> POSTED
                                        \\--reject---> REJECTED

A draft is a *proposal*. It writes no ledger entries and moves no balance.
`approve` is the only way a draft becomes real, and it does so by calling the
existing `posting.post_transaction` - the same engine, the same account
locking order, the same invariant checks - inside one database transaction
together with the draft's own status change. If the ledger refuses the
posting (insufficient funds, frozen account...) nothing is posted, the draft
stays PENDING_APPROVAL and the refusal is recorded on it.

Separation of duties: the identity that created a draft can never approve it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import (
    ApprovalRequired,
    Conflict,
    CurrencyMismatch,
    LedgrError,
    NotFound,
    PermissionDenied,
    SelfApprovalForbidden,
    ValidationFailed,
)
from app.db.tenancy import require_organization
from app.models.account import Account
from app.models.draft import TransactionDraft
from app.models.enums import DraftStatus, EntryDirection
from app.schemas.transaction import TransactionCreate
from app.services import audit, posting, users
from app.services.auth import Principal

_EDITABLE = {DraftStatus.DRAFT}


def _command_from_payload(payload: TransactionCreate, *, actor: str, request_id: str | None):
    return posting.PostCommand(
        description=payload.description,
        currency=payload.currency,
        entries=[
            posting.EntryCommand(
                account_id=e.account_id,
                direction=e.direction,
                amount_minor=e.amount_minor,
                memo=e.memo,
            )
            for e in payload.entries
        ],
        reference=payload.reference,
        effective_at=payload.effective_at,
        external_reference=payload.external_reference,
        metadata=payload.metadata,
        actor=actor,
        request_id=request_id,
    )


def _precheck(db: Session, payload: TransactionCreate) -> int:
    """Early, non-locking feedback so a maker finds out about a typo'd account
    or wrong currency now, not at approval. The authoritative checks still
    run under lock inside `posting.post_transaction` at approval time."""
    cmd = _command_from_payload(payload, actor="draft-precheck", request_id=None)
    debits, _ = posting._validate_shape(cmd)  # structure + balance; no DB
    ids = {e.account_id for e in payload.entries}
    accounts = {
        a.id: a for a in db.execute(select(Account).where(Account.id.in_(ids))).scalars().all()
    }
    for account_id in ids:
        account = accounts.get(account_id)
        if account is None:
            raise NotFound(
                f"account {account_id} does not exist", details={"account_id": str(account_id)}
            )
        if account.currency != payload.currency:
            raise CurrencyMismatch(
                f"account {account.code} is {account.currency}, transaction is {payload.currency}",
                details={"account_id": str(account_id)},
            )
    return debits


def _entries_json(payload: TransactionCreate) -> list[dict[str, Any]]:
    return [
        {
            "account_id": str(e.account_id),
            "direction": e.direction.value,
            "amount_minor": e.amount_minor,
            "memo": e.memo,
        }
        for e in payload.entries
    ]


def enforce_direct_posting_policy(db: Session, *, total_minor: int) -> None:
    """Organization policy: at/above the approval threshold a transaction may
    not be posted directly - it must be proposed and approved by someone else.
    The threshold is in minor units of the transaction's own currency."""
    org = users.get_organization(db, require_organization(db))
    threshold = org.approval_threshold_minor if org else None
    if threshold is not None and total_minor >= threshold:
        raise ApprovalRequired(
            "transactions at or above the approval threshold must be submitted as a draft "
            "and approved by a second person",
            details={"approval_threshold_minor": threshold, "total_minor": total_minor},
        )


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def get_draft(db: Session, draft_id: uuid.UUID) -> TransactionDraft:
    draft = db.get(TransactionDraft, draft_id)
    if draft is None:
        raise NotFound(f"draft {draft_id} not found", details={"draft_id": str(draft_id)})
    return draft


def list_drafts(
    db: Session,
    *,
    status: DraftStatus | None = None,
    mine_ref: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TransactionDraft], int]:
    filters = []
    if status is not None:
        filters.append(TransactionDraft.status == status)
    if mine_ref is not None:
        filters.append(TransactionDraft.created_by_ref == mine_ref)
    total = db.execute(select(func.count(TransactionDraft.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(TransactionDraft)
            .where(*filters)
            .order_by(TransactionDraft.seq.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def account_codes(db: Session, drafts: list[TransactionDraft]) -> dict[str, str]:
    ids = {uuid.UUID(e["account_id"]) for d in drafts for e in d.entries}
    if not ids:
        return {}
    rows = db.execute(select(Account.id, Account.code).where(Account.id.in_(ids))).all()
    return {str(i): c for i, c in rows}


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


def create_draft(
    db: Session,
    payload: TransactionCreate,
    *,
    principal: Principal,
    submit: bool,
    request_id: str | None,
) -> TransactionDraft:
    total = _precheck(db, payload)
    now = datetime.now(UTC)
    draft = TransactionDraft(
        id=uuid.uuid4(),
        description=payload.description,
        currency=payload.currency,
        reference=payload.reference,
        external_reference=payload.external_reference,
        effective_at=payload.effective_at,
        entries=_entries_json(payload),
        extra=payload.metadata,
        total_minor=total,
        status=DraftStatus.PENDING_APPROVAL if submit else DraftStatus.DRAFT,
        created_by=principal.label,
        created_by_ref=principal.ref,
        submitted_at=now if submit else None,
    )
    db.add(draft)
    db.flush()
    audit.record(
        db,
        event_type="draft.created",
        resource_type="transaction_draft",
        resource_id=str(draft.id),
        actor=principal.label,
        request_id=request_id,
        payload={"total_minor": total, "currency": payload.currency, "submitted": submit},
    )
    db.commit()
    return draft


def _lock(db: Session, draft_id: uuid.UUID) -> TransactionDraft:
    draft = db.execute(
        select(TransactionDraft)
        .where(TransactionDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if draft is None:
        db.rollback()
        raise NotFound(f"draft {draft_id} not found", details={"draft_id": str(draft_id)})
    return draft


def _expect(db: Session, draft: TransactionDraft, allowed: set[DraftStatus], verb: str) -> None:
    if draft.status not in allowed:
        status = draft.status.value
        db.rollback()
        raise Conflict(
            f"cannot {verb} a draft that is {status}",
            details={"status": status},
        )


def update_draft(
    db: Session,
    draft_id: uuid.UUID,
    payload: TransactionCreate,
    *,
    principal: Principal,
    request_id: str | None,
) -> TransactionDraft:
    draft = _lock(db, draft_id)
    _expect(db, draft, _EDITABLE, "edit")
    if draft.created_by_ref != principal.ref:
        db.rollback()
        raise PermissionDenied("only the person who created a draft can edit it")
    total = _precheck(db, payload)
    draft.description = payload.description
    draft.currency = payload.currency
    draft.reference = payload.reference
    draft.external_reference = payload.external_reference
    draft.effective_at = payload.effective_at
    draft.entries = _entries_json(payload)
    draft.extra = payload.metadata
    draft.total_minor = total
    audit.record(
        db,
        event_type="draft.updated",
        resource_type="transaction_draft",
        resource_id=str(draft.id),
        actor=principal.label,
        request_id=request_id,
        payload={"total_minor": total},
    )
    db.commit()
    return draft


def submit_draft(
    db: Session, draft_id: uuid.UUID, *, principal: Principal, request_id: str | None
) -> TransactionDraft:
    draft = _lock(db, draft_id)
    _expect(db, draft, {DraftStatus.DRAFT}, "submit")
    if draft.created_by_ref != principal.ref:
        db.rollback()
        raise PermissionDenied("only the person who created a draft can submit it")
    draft.status = DraftStatus.PENDING_APPROVAL
    draft.submitted_at = datetime.now(UTC)
    audit.record(
        db,
        event_type="draft.submitted",
        resource_type="transaction_draft",
        resource_id=str(draft.id),
        actor=principal.label,
        request_id=request_id,
        payload={"total_minor": draft.total_minor, "currency": draft.currency},
    )
    db.commit()
    return draft


def reject_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    principal: Principal,
    note: str,
    request_id: str | None,
) -> TransactionDraft:
    if not note.strip():
        raise ValidationFailed("a reason is required to reject a draft", details={"field": "note"})
    draft = _lock(db, draft_id)
    _expect(db, draft, {DraftStatus.PENDING_APPROVAL}, "reject")
    draft.status = DraftStatus.REJECTED
    draft.decided_at = datetime.now(UTC)
    draft.decided_by = principal.label
    draft.decision_note = note.strip()
    audit.record(
        db,
        event_type="draft.rejected",
        resource_type="transaction_draft",
        resource_id=str(draft.id),
        actor=principal.label,
        request_id=request_id,
        payload={"note": note.strip(), "maker": draft.created_by},
    )
    db.commit()
    return draft


def approve_draft(
    db: Session,
    draft_id: uuid.UUID,
    *,
    principal: Principal,
    note: str | None,
    request_id: str | None,
    posted_at: datetime | None = None,
) -> TransactionDraft:
    """Post the draft through the existing posting engine, atomically.

    The draft row is locked first (`FOR UPDATE`), so two approvers racing on
    the same draft serialise: the second sees POSTED and gets a 409, and the
    ledger can never receive the transaction twice. The posting itself then
    takes its usual account locks in the engine's deterministic order.
    """
    draft = _lock(db, draft_id)
    _expect(db, draft, {DraftStatus.PENDING_APPROVAL}, "approve")
    if draft.created_by_ref == principal.ref:
        db.rollback()
        audit.record_independent(
            organization_id=principal.organization_id,
            event_type="draft.self_approval_blocked",
            resource_type="transaction_draft",
            resource_id=str(draft_id),
            actor=principal.label,
            request_id=request_id,
        )
        raise SelfApprovalForbidden(
            "you created this draft, so someone else must approve it (maker-checker)"
        )

    cmd = posting.PostCommand(
        description=draft.description,
        currency=draft.currency,
        entries=[
            posting.EntryCommand(
                account_id=uuid.UUID(e["account_id"]),
                direction=EntryDirection(e["direction"]),
                amount_minor=int(e["amount_minor"]),
                memo=e.get("memo"),
            )
            for e in draft.entries
        ],
        reference=draft.reference,
        effective_at=draft.effective_at,
        external_reference=draft.external_reference,
        metadata={
            **(draft.extra or {}),
            "draft_id": str(draft.id),
            "maker": draft.created_by,
            "checker": principal.label,
        },
        # The transaction's actor is whoever released it to the ledger; the
        # maker is preserved in metadata and on the draft itself.
        actor=principal.label,
        request_id=request_id,
        idempotency_key=f"draft:{draft.id}",
        posted_at=posted_at,
    )

    try:
        transaction = posting.post_transaction(db, cmd, auto_commit=False)
        draft.status = DraftStatus.POSTED
        draft.posted_transaction_id = transaction.id
        draft.decided_at = datetime.now(UTC)
        draft.decided_by = principal.label
        draft.decision_note = (note or "").strip() or None
        draft.last_error = None
        audit.record(
            db,
            event_type="draft.approved",
            resource_type="transaction_draft",
            resource_id=str(draft.id),
            actor=principal.label,
            request_id=request_id,
            payload={
                "transaction_id": str(transaction.id),
                "reference": transaction.reference,
                "maker": draft.created_by,
                "total_minor": draft.total_minor,
            },
        )
        db.commit()
    except LedgrError as exc:
        # The ledger refused. Nothing was posted (rolled back with the failed
        # attempt); keep the draft pending and remember why.
        db.rollback()
        failed = _lock(db, draft_id)
        failed.last_error = f"{exc.code}: {exc.message}"
        audit.record(
            db,
            event_type="draft.approval_failed",
            resource_type="transaction_draft",
            resource_id=str(draft_id),
            actor=principal.label,
            request_id=request_id,
            payload={"error": exc.code, "message": exc.message},
        )
        db.commit()
        raise
    except Exception:
        db.rollback()
        raise

    return draft
