"""Account statements: entries with a running balance.

The running balance is computed by the database with a window function over
the account's *entire* entry history (ordered by the append-only `seq`), then
filtered - so filtering or paging a statement never changes a row's balance.
Nothing is stored; the ledger entries remain the only source of truth.

Balances are expressed in the account's normal-balance sense: an ASSET or
EXPENSE account grows with debits, every other family grows with credits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.enums import EntryDirection, NormalBalance
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.services import accounts as account_service
from app.services import balances


@dataclass(frozen=True)
class StatementRow:
    entry_id: uuid.UUID
    seq: int
    transaction_id: uuid.UUID
    reference: str
    description: str
    transaction_status: str
    transaction_kind: str
    actor: str
    posted_at: datetime
    direction: EntryDirection
    amount_minor: int
    running_balance_minor: int
    memo: str | None


@dataclass(frozen=True)
class Statement:
    account: Account
    opening_minor: int
    closing_minor: int
    current_minor: int
    period_debits_minor: int
    period_credits_minor: int
    rows: list[StatementRow]
    total: int
    limit: int
    offset: int


def _sign(account: Account) -> int:
    return 1 if account.normal_balance is NormalBalance.DEBIT else -1


def account_statement(
    db: Session,
    account_id: uuid.UUID,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    direction: EntryDirection | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Statement:
    account = account_service.get_account(db, account_id)
    sign = _sign(account)

    signed = case(
        (LedgerEntry.direction == EntryDirection.DEBIT, LedgerEntry.amount_minor),
        else_=-LedgerEntry.amount_minor,
    )

    inner = (
        select(
            LedgerEntry.id.label("id"),
            LedgerEntry.seq.label("seq"),
            LedgerEntry.transaction_id.label("transaction_id"),
            LedgerEntry.direction.label("direction"),
            LedgerEntry.amount_minor.label("amount_minor"),
            LedgerEntry.memo.label("memo"),
            LedgerEntry.created_at.label("created_at"),
            (func.sum(signed).over(order_by=LedgerEntry.seq) * literal(sign)).label("running"),
        )
        .where(LedgerEntry.account_id == account_id)
        .subquery("e")
    )

    filters = []
    if since is not None:
        filters.append(inner.c.created_at >= since)
    if until is not None:
        filters.append(inner.c.created_at <= until)
    if direction is not None:
        filters.append(inner.c.direction == direction)
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                Transaction.reference.ilike(pattern),
                Transaction.description.ilike(pattern),
                inner.c.memo.ilike(pattern),
            )
        )

    base = select(inner, Transaction).join(Transaction, Transaction.id == inner.c.transaction_id)
    count_stmt = (
        select(func.count())
        .select_from(inner)
        .join(Transaction, Transaction.id == inner.c.transaction_id)
        .where(*filters)
    )
    total = int(db.execute(count_stmt).scalar_one())

    rows = db.execute(
        base.where(*filters).order_by(inner.c.seq.desc()).limit(limit).offset(offset)
    ).all()

    out: list[StatementRow] = []
    for row in rows:
        txn = row[-1]  # the Transaction entity follows the subquery columns
        out.append(
            StatementRow(
                entry_id=row.id,
                seq=row.seq,
                transaction_id=row.transaction_id,
                reference=txn.reference,
                description=txn.description,
                transaction_status=txn.status.value,
                transaction_kind=txn.kind.value,
                actor=txn.actor,
                posted_at=row.created_at,
                direction=row.direction,
                amount_minor=row.amount_minor,
                running_balance_minor=int(row.running),
                memo=row.memo,
            )
        )

    # Window summary (date range only - not the direction/search filters, so
    # opening + debits - credits = closing always holds for the period shown).
    def _net(before: datetime | None = None, after: datetime | None = None) -> int:
        stmt = select(func.coalesce(func.sum(signed), 0)).where(
            LedgerEntry.account_id == account_id
        )
        if before is not None:
            stmt = stmt.where(LedgerEntry.created_at < before)
        if after is not None:
            stmt = stmt.where(LedgerEntry.created_at <= after)
        return int(db.execute(stmt).scalar_one())

    opening_signed = _net(before=since) if since is not None else 0
    period = select(
        func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.direction == EntryDirection.DEBIT, LedgerEntry.amount_minor),
                    else_=0,
                )
            ),
            0,
        ),
        func.coalesce(
            func.sum(
                case(
                    (LedgerEntry.direction == EntryDirection.CREDIT, LedgerEntry.amount_minor),
                    else_=0,
                )
            ),
            0,
        ),
    ).where(LedgerEntry.account_id == account_id)
    if since is not None:
        period = period.where(LedgerEntry.created_at >= since)
    if until is not None:
        period = period.where(LedgerEntry.created_at <= until)
    debits, credits = db.execute(period).one()
    debits, credits = int(debits), int(credits)

    current = balances.derive_for_account(db, account_id)
    return Statement(
        account=account,
        opening_minor=opening_signed * sign,
        closing_minor=(opening_signed + debits - credits) * sign,
        current_minor=current.signed_minor * sign,
        period_debits_minor=debits,
        period_credits_minor=credits,
        rows=out,
        total=total,
        limit=limit,
        offset=offset,
    )
