"""Append-only casework records: investigation notes and reconciliation decisions.

Both are written once and never edited or deleted (a database trigger rejects
UPDATE/DELETE, like the audit log): the history of who concluded what, and
when, is itself evidence. Neither is a financial record - confirming a match
or writing a note changes no ledger entry and no balance.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.tenancy import TenantMixin
from app.models.enums import MatchDecision


class InvestigationNote(TenantMixin, Base):
    __tablename__ = "investigation_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False
    )
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    author_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_investigation_notes_investigation_id", "investigation_id"),)


class ReconciliationDecision(TenantMixin, Base):
    """A person's conclusion about one reconciliation result.

    The deterministic result itself is never rewritten - an ambiguous match
    stays ambiguous in the run that produced it. A decision is an annotation
    next to it: "a human reviewed this and concluded X, because Y".
    """

    __tablename__ = "reconciliation_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_results.id"), nullable=False
    )
    internal_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    decision: Mapped[MatchDecision] = mapped_column(
        Enum(MatchDecision, name="match_decision", native_enum=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_by_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_reconciliation_decisions_result_id", "result_id"),)
