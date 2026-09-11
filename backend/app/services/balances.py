"""Balance derivation.

Balances are *computed from ledger entries*. Nothing in this module reads the
`account_balances` cache; that table exists to make list views and balance
constraints cheap, and these functions are what proves it honest.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from app.models.enums import EntryDirection, TransactionStatus
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction


@dataclass(frozen=True)
class DerivedBalance:
    debits_minor: int
    credits_minor: int
    entry_count: int
    last_entry_seq: int | None

    @property
    def signed_minor(self) -> int:
        """Debit-positive. Summed over every account this must be zero."""
        return self.debits_minor - self.credits_minor


_DEBIT_SUM = func.coalesce(
    func.sum(
        case((LedgerEntry.direction == EntryDirection.DEBIT, LedgerEntry.amount_minor), else_=0)
    ),
    0,
)
_CREDIT_SUM = func.coalesce(
    func.sum(
        case((LedgerEntry.direction == EntryDirection.CREDIT, LedgerEntry.amount_minor), else_=0)
    ),
    0,
)


def derive_for_account(db: Session, account_id: uuid.UUID) -> DerivedBalance:
    """Single-account aggregate, served by ix_ledger_entries_account_seq."""
    row = db.execute(
        select(
            _DEBIT_SUM,
            _CREDIT_SUM,
            func.count(LedgerEntry.id),
            func.max(LedgerEntry.seq),
        ).where(LedgerEntry.account_id == account_id)
    ).one()
    return DerivedBalance(int(row[0]), int(row[1]), int(row[2]), row[3])


def derive_all(db: Session) -> dict[uuid.UUID, DerivedBalance]:
    """Every account's balance in one pass, for integrity verification."""
    rows = db.execute(
        select(
            LedgerEntry.account_id,
            _DEBIT_SUM,
            _CREDIT_SUM,
            func.count(LedgerEntry.id),
            func.max(LedgerEntry.seq),
        ).group_by(LedgerEntry.account_id)
    ).all()
    return {row[0]: DerivedBalance(int(row[1]), int(row[2]), int(row[3]), row[4]) for row in rows}


@dataclass(frozen=True)
class TrialBalanceRow:
    currency: str
    debits_minor: int
    credits_minor: int
    entry_count: int

    @property
    def difference_minor(self) -> int:
        return self.debits_minor - self.credits_minor

    @property
    def balanced(self) -> bool:
        return self.difference_minor == 0


def trial_balance(db: Session) -> list[TrialBalanceRow]:
    """System-wide debits vs credits per currency (invariant I13)."""
    rows = db.execute(
        select(
            LedgerEntry.currency,
            _DEBIT_SUM,
            _CREDIT_SUM,
            func.count(LedgerEntry.id),
        )
        .group_by(LedgerEntry.currency)
        .order_by(LedgerEntry.currency)
    ).all()
    return [TrialBalanceRow(row[0], int(row[1]), int(row[2]), int(row[3])) for row in rows]


def unbalanced_transaction_ids(db: Session, limit: int = 50) -> list[uuid.UUID]:
    """Transactions whose entries do not net to zero.

    Should always return nothing: the posting service, a CHECK constraint on
    the transaction row and the immutability triggers each independently
    prevent it. Verifying anyway is the point of an integrity check.
    """
    signed = func.sum(
        case(
            (LedgerEntry.direction == EntryDirection.DEBIT, LedgerEntry.amount_minor),
            else_=-LedgerEntry.amount_minor,
        )
    )
    rows = db.execute(
        select(LedgerEntry.transaction_id)
        .group_by(LedgerEntry.transaction_id)
        .having(signed != 0)
        .limit(limit)
    ).all()
    return [row[0] for row in rows]


@dataclass(frozen=True)
class DailyVolumeRow:
    day: date
    currency: str
    transaction_count: int
    posted_minor: int


def daily_volume(db: Session, days: int = 14) -> list[DailyVolumeRow]:
    """Posted value per day, derived from transactions (not a vanity metric).

    `total_debits_minor` is the value of a transaction: in double entry the
    debit side is the whole amount that moved.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    day_col = func.date_trunc("day", Transaction.posted_at).label("day")
    rows = db.execute(
        select(
            day_col,
            Transaction.currency,
            func.count(Transaction.id).cast(Integer),
            func.coalesce(func.sum(Transaction.total_debits_minor), 0),
        )
        .where(Transaction.posted_at >= since)
        .where(Transaction.status != TransactionStatus.REVERSED)
        .group_by(day_col, Transaction.currency)
        .order_by(day_col)
    ).all()
    return [DailyVolumeRow(row[0].date(), row[1], int(row[2]), int(row[3])) for row in rows]
