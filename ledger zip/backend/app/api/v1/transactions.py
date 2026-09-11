"""Transaction endpoints.

Posting and reversal are the only money-moving operations in the system. Both
run through the same idempotency wrapper so a retried write is answered from
the stored response instead of being executed twice.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import Actor, DbSession, IdempotencyKey, PaginationParams, RequestId
from app.models.enums import TransactionKind, TransactionStatus
from app.schemas.common import Page
from app.schemas.serialize import transaction_out
from app.schemas.transaction import (
    TransactionCreate,
    TransactionOut,
    TransactionReverse,
)
from app.services import idempotency, posting, queries

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _replay(replay: idempotency.Replay) -> JSONResponse:
    return JSONResponse(
        content=replay.body,
        status_code=replay.status_code,
        headers={"Idempotent-Replay": "true"},
    )


@router.post(
    "",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Duplicate reference, or an identical request is still running"},
        422: {"description": "Unbalanced, wrong currency, inactive account or invalid amount"},
    },
)
def post_transaction(
    payload: TransactionCreate,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
    key: IdempotencyKey,
    response: Response,
):
    """Post a balanced double-entry transaction.

    Send an `Idempotency-Key` header: a retry after a timeout then returns the
    original result rather than moving money twice.
    """
    body = payload.model_dump(mode="json")

    if key:
        replay = idempotency.claim(
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
            request_id=request_id,
        )
        if replay is not None:
            return _replay(replay)

    cmd = posting.PostCommand(
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
        idempotency_key=key,
    )

    try:
        transaction = posting.post_transaction(db, cmd)
    except Exception:
        if key:
            # The write failed, so the key never produced a financial record:
            # let the caller retry with it.
            idempotency.release(scope=idempotency.SCOPE_POST_TRANSACTION, key=key)
        raise

    out = transaction_out(transaction)
    if key:
        idempotency.complete(
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            status_code=status.HTTP_201_CREATED,
            body=out.model_dump(mode="json"),
            transaction_id=transaction.id,
        )
    response.headers["Location"] = f"/api/v1/transactions/{transaction.id}"
    return out


@router.get("", response_model=Page[TransactionOut])
def list_transactions(
    db: DbSession,
    page: PaginationParams,
    q: str | None = Query(default=None, description="Match reference or description"),
    transaction_status: TransactionStatus | None = Query(default=None, alias="status"),
    kind: TransactionKind | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    account_id: uuid.UUID | None = Query(default=None),
    posted_from: datetime | None = Query(default=None),
    posted_to: datetime | None = Query(default=None),
) -> Page[TransactionOut]:
    items, total = queries.list_transactions(
        db,
        query=q,
        status=transaction_status,
        kind=kind,
        currency=currency,
        account_id=account_id,
        posted_from=posted_from,
        posted_to=posted_to,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[TransactionOut](
        items=[transaction_out(t) for t in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/by-reference/{reference}", response_model=TransactionOut)
def get_transaction_by_reference(reference: str, db: DbSession) -> TransactionOut:
    return transaction_out(queries.get_transaction_by_reference(db, reference))


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: uuid.UUID, db: DbSession) -> TransactionOut:
    return transaction_out(queries.get_transaction(db, transaction_id))


@router.post(
    "/{transaction_id}/reverse",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Already reversed"},
        422: {"description": "Reversals cannot themselves be reversed"},
    },
)
def reverse_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionReverse,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
    key: IdempotencyKey,
):
    """Reverse a posted transaction by posting its mirror image.

    Returns the *reversal* transaction. The original keeps every entry it was
    posted with and is marked REVERSED.
    """
    body = payload.model_dump(mode="json") | {"transaction_id": str(transaction_id)}

    if key:
        replay = idempotency.claim(
            scope=idempotency.SCOPE_REVERSE_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
            request_id=request_id,
        )
        if replay is not None:
            return _replay(replay)

    try:
        reversal = posting.reverse_transaction(
            db,
            transaction_id,
            reason=payload.reason,
            reference=payload.reference,
            effective_at=payload.effective_at,
            actor=actor,
            request_id=request_id,
            idempotency_key=key,
        )
    except Exception:
        if key:
            idempotency.release(scope=idempotency.SCOPE_REVERSE_TRANSACTION, key=key)
        raise

    out = transaction_out(reversal)
    if key:
        idempotency.complete(
            scope=idempotency.SCOPE_REVERSE_TRANSACTION,
            key=key,
            status_code=status.HTTP_201_CREATED,
            body=out.model_dump(mode="json"),
            transaction_id=reversal.id,
        )
    return out
