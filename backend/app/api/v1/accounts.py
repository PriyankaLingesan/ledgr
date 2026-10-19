"""Account endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Actor, DbSession, PaginationParams, RequestId, require
from app.core.permissions import Permission
from app.models.enums import AccountStatus, AccountType, EntryDirection
from app.schemas.account import (
    AccountBalanceOut,
    AccountCreate,
    AccountOut,
    AccountUpdate,
)
from app.schemas.common import Money, Page
from app.schemas.investigation import AccountBalanceAtOut, BalanceChangeOut
from app.schemas.serialize import (
    account_balance_at_out,
    account_balance_out,
    account_out,
    balance_change_out,
    ledger_entry_out,
    transaction_out,
)
from app.schemas.transaction import LedgerEntryOut, TransactionOut
from app.schemas.workflow import StatementAccountOut, StatementOut, StatementRowOut
from app.services import accounts as account_service
from app.services import balances, investigation, queries, statements

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.ACCOUNTS_WRITE))],
)
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


@router.get(
    "", response_model=Page[AccountOut], dependencies=[Depends(require(Permission.ACCOUNTS_READ))]
)
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


@router.get(
    "/{account_id}",
    response_model=AccountOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
def get_account(account_id: uuid.UUID, db: DbSession) -> AccountOut:
    return account_out(account_service.get_account(db, account_id))


@router.patch(
    "/{account_id}",
    response_model=AccountOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_WRITE))],
)
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


@router.get(
    "/{account_id}/balance",
    response_model=AccountBalanceOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
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


@router.get(
    "/{account_id}/entries",
    response_model=Page[LedgerEntryOut],
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
def get_account_entries(
    account_id: uuid.UUID,
    db: DbSession,
    page: PaginationParams,
    direction: EntryDirection | None = Query(default=None),
    since: datetime | None = Query(default=None, description="Only entries at/after this time"),
    until: datetime | None = Query(default=None, description="Only entries at/before this time"),
) -> Page[LedgerEntryOut]:
    """The account's statement: every entry that shaped its balance.

    `since`/`until` turn this into the account's financial timeline for a
    window - the same `queries.list_entries` filters the ledger explorer
    already uses, just exposed on this route too.
    """
    account_service.get_account(db, account_id)  # 404 rather than an empty page
    items, total = queries.list_entries(
        db,
        account_id=account_id,
        direction=direction,
        created_from=since,
        created_to=until,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[LedgerEntryOut](
        items=[ledger_entry_out(e, with_account=False, with_transaction=True) for e in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{account_id}/balance-at",
    response_model=AccountBalanceAtOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
def get_account_balance_at(
    account_id: uuid.UUID,
    db: DbSession,
    at: datetime = Query(..., description="Reconstruct the balance as of this instant"),
) -> AccountBalanceAtOut:
    """Historical balance reconstruction.

    Entries are append-only and immutable, so "the balance at `at`" is just
    `derive_for_account`'s own aggregate with one extra predicate
    (`created_at <= at`) - never a snapshot table, never a second opinion
    about what the balance was.
    """
    return account_balance_at_out(investigation.balance_at(db, account_id, at))


@router.get(
    "/{account_id}/balance-change",
    response_model=BalanceChangeOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
def get_account_balance_change(
    account_id: uuid.UUID,
    db: DbSession,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> BalanceChangeOut:
    """The Financial Flight Recorder: opening balance, every event in the
    window, and the closing balance those events produce.

    This is the one engine behind "Explain This Balance" (call it with no
    params - defaults to the last 30 days), "why did my balance change", and
    comparing two points in time (pass both `from` and `to` explicitly).
    `closing_balance - opening_balance` always equals the sum of the signed
    `effect` of every event returned - see `reconciled`.
    """
    return balance_change_out(investigation.balance_change(db, account_id, from_ts=from_, to_ts=to))


@router.get(
    "/{account_id}/transactions",
    response_model=Page[TransactionOut],
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
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


@router.get(
    "/{account_id}/statement",
    response_model=StatementOut,
    dependencies=[Depends(require(Permission.ACCOUNTS_READ))],
)
def get_account_statement(
    account_id: uuid.UUID,
    db: DbSession,
    page: PaginationParams,
    since: datetime | None = Query(default=None, description="Entries at/after this time"),
    until: datetime | None = Query(default=None, description="Entries at/before this time"),
    direction: EntryDirection | None = Query(default=None),
    q: str | None = Query(default=None, description="Match reference, description or memo"),
) -> StatementOut:
    """Entries with a running balance, newest first.

    The running balance is computed by the database over the account's full
    history and *then* filtered, so filtering/paging never changes a row's
    balance. Balances are in the account's normal-balance sense (debit-normal
    for assets/expenses, credit-normal otherwise). The ledger entries remain
    the only source of truth - nothing here is stored.
    """
    st = statements.account_statement(
        db,
        account_id,
        since=since,
        until=until,
        direction=direction,
        query=q,
        limit=page.limit,
        offset=page.offset,
    )
    cur = st.account.currency
    return StatementOut(
        account=StatementAccountOut(
            id=st.account.id,
            code=st.account.code,
            name=st.account.name,
            type=st.account.type.value,
            currency=cur,
            status=st.account.status.value,
            normal_balance=st.account.normal_balance.value,
        ),
        opening_balance=Money.of(st.opening_minor, cur),
        closing_balance=Money.of(st.closing_minor, cur),
        current_balance=Money.of(st.current_minor, cur),
        total_debits=Money.of(st.period_debits_minor, cur),
        total_credits=Money.of(st.period_credits_minor, cur),
        items=[
            StatementRowOut(
                entry_id=r.entry_id,
                seq=r.seq,
                transaction_id=r.transaction_id,
                reference=r.reference,
                description=r.description,
                transaction_status=r.transaction_status,
                transaction_kind=r.transaction_kind,
                actor=r.actor,
                posted_at=r.posted_at,
                direction=r.direction,
                debit=Money.of(r.amount_minor, cur)
                if r.direction is EntryDirection.DEBIT
                else None,
                credit=Money.of(r.amount_minor, cur)
                if r.direction is EntryDirection.CREDIT
                else None,
                running_balance=Money.of(r.running_balance_minor, cur),
                memo=r.memo,
            )
            for r in st.rows
        ],
        total=st.total,
        limit=st.limit,
        offset=st.offset,
    )
