"""Proposed transactions awaiting maker-checker approval.

A `TransactionDraft` is *not* a financial record: it has no ledger entries, no
effect on any balance, and no immutability trigger (its status legitimately
moves DRAFT -> PENDING_APPROVAL -> POSTED/REJECTED). It only ever becomes
real through `app.services.drafts.approve`, which hands the proposed entries
to the existing `posting.post_transaction` - there is no second posting
implementation, and every ledger invariant is enforced exactly as for a
transaction posted directly.
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
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.tenancy import TenantMixin
from app.models.enums import DraftStatus


class TransactionDraft(TenantMixin, Base, TimestampMixin):
    __tablename__ = "transaction_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False, unique=True
    )

    description: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # [{"account_id": "...", "direction": "DEBIT", "amount_minor": 100, "memo": null}, ...]
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, name="draft_status", native_enum=True),
        nullable=False,
        default=DraftStatus.DRAFT,
        server_default="DRAFT",
    )

    # `created_by_ref` is "user:<uuid>" or "apikey:<uuid>" - the stable identity
    # the separation-of-duties check compares; `created_by` is the display label.
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when an approval attempt was refused by the ledger (e.g. insufficient
    # funds); the draft stays PENDING_APPROVAL so it can be fixed or rejected.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    posted_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "(status = 'POSTED') = (posted_transaction_id IS NOT NULL)",
            name="posted_iff_linked",
        ),
        CheckConstraint(
            "status NOT IN ('POSTED', 'REJECTED') OR decided_at IS NOT NULL",
            name="decided_has_timestamp",
        ),
        CheckConstraint("total_minor > 0", name="has_value"),
        Index("ix_transaction_drafts_status", "status"),
        Index("ix_transaction_drafts_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TransactionDraft {self.seq} {self.status.value}>"
