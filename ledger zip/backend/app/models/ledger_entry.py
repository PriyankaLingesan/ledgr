"""Ledger entries: the authoritative financial record.

This table is append-only. There is no update path in the application, and a
BEFORE UPDATE OR DELETE trigger rejects any attempt made outside it.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.account import Account
from app.models.enums import EntryDirection
from app.models.transaction import Transaction


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Global append order. Gives the ledger a total order for audit replay and
    # a natural keyset-pagination cursor that is immune to clock skew.
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False, unique=True
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )

    direction: Mapped[EntryDirection] = mapped_column(
        Enum(EntryDirection, name="entry_direction", native_enum=True), nullable=False
    )
    # Always positive. Direction carries the sign; a negative debit is just a
    # credit wearing a disguise, and allowing both spellings makes every
    # aggregate query ambiguous.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    entry_index: Mapped[int] = mapped_column(Integer, nullable=False)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transaction: Mapped[Transaction] = relationship(back_populates="entries")
    account: Mapped[Account] = relationship()

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_is_positive"),
        CheckConstraint("entry_index >= 0", name="entry_index_non_negative"),
        UniqueConstraint("transaction_id", "entry_index", name="uq_entry_position"),
        # Account statement / balance derivation path.
        Index("ix_ledger_entries_account_seq", "account_id", "seq"),
        Index("ix_ledger_entries_transaction", "transaction_id"),
        Index("ix_ledger_entries_created_at", "created_at"),
        Index("ix_ledger_entries_currency_direction", "currency", "direction"),
    )

    @property
    def signed_amount_minor(self) -> int:
        """Debit-positive signed amount, used when summing a whole ledger."""
        return self.amount_minor if self.direction is EntryDirection.DEBIT else -self.amount_minor

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LedgerEntry #{self.seq} {self.direction.value} {self.amount_minor}>"
