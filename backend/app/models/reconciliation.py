"""External financial reconciliation.

Three tables, all additive - nothing here touches `transactions` or
`ledger_entries`. An `ExternalTransaction` is evidence/input from outside
LEDGR, never a financial record of its own: it asserts nothing about the
ledger and cannot move money. A `ReconciliationRun` is a snapshot comparison
between that evidence and the real internal ledger (`Transaction.
external_reference`, the field the API already uses for exactly this kind
of external linkage), and a `ReconciliationResult` is one row of that
comparison, always re-derivable and never itself authoritative.
"""

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
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import EntryDirection, ReconciliationStatus


class ExternalTransaction(Base):
    """A record from an external system (a payment processor, a bank feed).

    Immutable evidence, not a source of financial truth: nothing here ever
    writes a ledger entry. `source_system` + `external_id` is the external
    system's own identity for the record, unique so re-importing the same
    dataset can never create a duplicate.
    """

    __tablename__ = "external_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False, unique=True)

    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    account_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    direction: Mapped[EntryDirection] = mapped_column(
        Enum(EntryDirection, name="entry_direction", native_enum=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, server_default="SETTLED")
    transaction_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_external_transactions_source_external_id"),
        Index("ix_external_transactions_external_id", "external_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ExternalTransaction {self.source_system}:{self.external_id}>"


class ReconciliationRun(Base):
    """One reconciliation pass: a repeatable, re-runnable comparison snapshot.

    Counts are denormalised onto the run for cheap listing, but are always
    derived by summing the `ReconciliationResult` rows written in the same
    transaction as this row - never a separate source of truth.
    """

    __tablename__ = "reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    external_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    missing_internal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    missing_external_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ambiguous_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    results: Mapped[list["ReconciliationResult"]] = relationship(
        back_populates="run", order_by="ReconciliationResult.created_at", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ReconciliationRun #{self.seq}>"


class ReconciliationResult(Base):
    """One outcome within one run - either an external record compared
    against the ledger, or (for MISSING_EXTERNAL) a live internal
    transaction with no external evidence.

    Both `external_transaction_id` and `internal_transaction_id` are
    nullable, but never both null at once (`ck_reconciliation_results_
    has_a_subject`, enforced in the database, not just in this service):
    every result must anchor to at least one real record. A MISSING_INTERNAL
    or AMBIGUOUS_INTERNAL_MATCH result has no single internal transaction to
    point at, by definition; a MISSING_EXTERNAL result has no external one.
    Never mutated after creation - a new run produces new result rows, so
    history of what a past run found is preserved rather than overwritten.
    """

    __tablename__ = "reconciliation_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_runs.id"), nullable=False
    )
    external_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_transactions.id"), nullable=True
    )
    internal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )

    status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus, name="reconciliation_status", native_enum=True), nullable=False
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    difference_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[ReconciliationRun] = relationship(back_populates="results")
    external_transaction: Mapped[ExternalTransaction | None] = relationship(lazy="joined")
    internal_transaction: Mapped["Transaction | None"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        Index("ix_reconciliation_results_run_id", "run_id"),
        Index("ix_reconciliation_results_status", "status"),
        CheckConstraint(
            "external_transaction_id IS NOT NULL OR internal_transaction_id IS NOT NULL",
            name="ck_reconciliation_results_has_a_subject",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ReconciliationResult {self.status.value}>"
