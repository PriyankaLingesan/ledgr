"""Account endpoints."""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import Actor, DbSession, PaginationParams, RequestId
from app.models.enums import AccountStatus, AccountType, EntryDirection
from app.schemas.account import (
    AccountBalanceOut,
    AccountCreate,
    AccountOut,
    AccountUpdate,
)
from app.schemas.common import Page
from app.schemas.serialize import (
    account_balance_out,
    account_out,
    ledger_entry_out,
    transaction_out,
)
from app.schemas.transaction import LedgerEntryOut, TransactionOut
from app.services import accounts as account_service
from app.services import balances, queries

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
) -> AccountOut:
    account = account_service.create_account(
        db,
        code=payload.code,
        name=payload.name,
        account_type=payload.type,
        currency=payload.currency,
        description=payload.description,
        allows_negative_balance=payload.allows_negative_balance,
        metadata=payload.metadata,
        actor=actor,
        request_id=request_id,
    )
    return account_out(account)


@router.get("", response_model=Page[AccountOut])
def list_accounts(
    db: DbSession,
    page: PaginationParams,
    q: str | None = Query(default=None, description="Match against account code or name"),
    type: AccountType | None = Query(default=None),
    account_status: AccountStatus | None = Query(default=None, alias="status"),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
) -> Page[AccountOut]:
    items, total = account_service.list_accounts(
        db,
        query=q,
        account_type=type,
        status=account_status,
        currency=currency,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[AccountOut](
        items=[account_out(a) for a in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: uuid.UUID, db: DbSession) -> AccountOut:
    return account_out(account_service.get_account(db, account_id))


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: DbSession,
    actor: Actor,
    request_id: RequestId,
) -> AccountOut:
    account = account_service.update_account(
        db,
        account_id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        allows_negative_balance=payload.allows_negative_balance,
        metadata=payload.metadata,
        actor=actor,
        request_id=request_id,
    )
    return account_out(account)


@router.get("/{account_id}/balance", response_model=AccountBalanceOut)
def get_account_balance(account_id: uuid.UUID, db: DbSession) -> AccountBalanceOut:
    """Authoritative balance, aggregated from ledger entries at read time.

    The cached figure is returned alongside it with a `cache_consistent` flag,
    so a divergence is visible rather than silently served.
    """
    account = account_service.get_account(db, account_id)
    derived = balances.derive_for_account(db, account_id)
    return account_balance_out(
        account,
        derived_debits=derived.debits_minor,
        derived_credits=derived.credits_minor,
        derived_entry_count=derived.entry_count,
        derived_last_seq=derived.last_entry_seq,
    )


@router.get("/{account_id}/entries", response_model=Page[LedgerEntryOut])
def get_account_entries(
    account_id: uuid.UUID,
    db: DbSession,
    page: PaginationParams,
    direction: EntryDirection | None = Query(default=None),
) -> Page[LedgerEntryOut]:
    """The account's statement: every entry that shaped its balance."""
    account_service.get_account(db, account_id)  # 404 rather than an empty page
    items, total = queries.list_entries(
        db,
        account_id=account_id,
        direction=direction,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[LedgerEntryOut](
        items=[ledger_entry_out(e, with_account=False, with_transaction=True) for e in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{account_id}/transactions", response_model=Page[TransactionOut])
def get_account_transactions(
    account_id: uuid.UUID,
    db: DbSession,
    page: PaginationParams,
) -> Page[TransactionOut]:
    account_service.get_account(db, account_id)
    items, total = queries.list_transactions(
        db, account_id=account_id, limit=page.limit, offset=page.offset
    )
    return Page[TransactionOut](
        items=[transaction_out(t) for t in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
