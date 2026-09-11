"""Posted transactions: the atomic unit of the ledger."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import TransactionKind, TransactionStatus


class Transaction(Base):
    """A balanced set of ledger entries recorded as one indivisible fact.

    There is no DRAFT or PENDING state: a transaction row exists only if it
    balanced and every one of its entries was written in the same database
    transaction. The only mutation a posted transaction ever undergoes is the
    POSTED -> REVERSED status transition, and a trigger enforces that nothing
    else about it can change.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Monotonic system-assigned ordering, independent of clock skew.
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False, unique=True
    )

    # Client-facing reference. Unique, so a caller that reuses its own
    # reference gets a conflict rather than a duplicate financial record.
    reference: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status", native_enum=True),
        nullable=False,
        default=TransactionStatus.POSTED,
        server_default="POSTED",
    )
    kind: Mapped[TransactionKind] = mapped_column(
        Enum(TransactionKind, name="transaction_kind", native_enum=True),
        nullable=False,
        default=TransactionKind.STANDARD,
        server_default="STANDARD",
    )

    # Denormalised totals, written once at post time and never updated. They
    # are a convenience for listing pages, and `total_debits == total_credits`
    # is enforced by a CHECK constraint so the row itself cannot lie.
    total_debits_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_credits_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_count: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Business time vs system time: back-dated postings are real, so the two
    # are recorded separately and never conflated.
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, unique=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Audit trail: who asked, under which request, with which idempotency key.
    actor: Mapped[str] = mapped_column(String(120), nullable=False, server_default="system")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    entries: Mapped[list["LedgerEntry"]] = relationship(  # noqa: F821
        back_populates="transaction",
        order_by="LedgerEntry.entry_index",
        lazy="selectin",
    )
    # Self-referential one-to-one. `reverses_transaction_id` is unique, so a
    # transaction has at most one reversal (invariant I11) and `reversed_by`
    # is a scalar rather than a collection.
    reverses: Mapped["Transaction | None"] = relationship(
        "Transaction",
        remote_side=[id],
        back_populates="reversed_by",
    )
    reversed_by: Mapped["Transaction | None"] = relationship(
        "Transaction",
        back_populates="reverses",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "total_debits_minor = total_credits_minor",
            name="transaction_is_balanced",
        ),
        CheckConstraint("total_debits_minor > 0", name="transaction_has_value"),
        CheckConstraint("entry_count >= 2", name="transaction_has_two_entries"),
        CheckConstraint(
            "(kind = 'REVERSAL') = (reverses_transaction_id IS NOT NULL)",
            name="reversal_links_original",
        ),
        CheckConstraint(
            "(status = 'REVERSED') = (reversed_at IS NOT NULL)",
            name="reversed_has_timestamp",
        ),
        Index("ix_transactions_posted_at", "posted_at"),
        Index("ix_transactions_effective_at", "effective_at"),
        Index("ix_transactions_status_kind", "status", "kind"),
        Index("ix_transactions_currency", "currency"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Transaction {self.reference} {self.status.value}>"
