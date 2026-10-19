"""Investigation cases: the operational record of "someone looked into this".

An investigation is deliberately NOT a financial record. It carries no
amounts of its own and asserts nothing about the ledger - it only points at
real ledger data (an account, a time window, sometimes a transaction) and
tracks the human workflow of looking into it. Every number an investigation
view ever shows is still computed live from `ledger_entries` by
`app.services.investigation`; this table exists so that work has a status,
a history, and survives a page refresh.

Unlike `transactions`/`ledger_entries`, this table is ordinary and mutable:
case status legitimately changes over time, the same way `AccountBalance`
does. It carries no immutability trigger.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Identity, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.tenancy import TenantMixin
from app.models.enums import InvestigationPriority, InvestigationSource, InvestigationStatus


class Investigation(TenantMixin, Base, TimestampMixin):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Human-facing case number, e.g. "INV-1042" - monotonic, so cases sort
    # and read the way an operator expects a ticket number to.
    seq: Mapped[int] = mapped_column(Identity(always=False), nullable=False, unique=True)
    case_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_summary: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[InvestigationStatus] = mapped_column(
        Enum(InvestigationStatus, name="investigation_status", native_enum=True),
        nullable=False,
        default=InvestigationStatus.OPEN,
        server_default="OPEN",
    )
    source: Mapped[InvestigationSource] = mapped_column(
        Enum(InvestigationSource, name="investigation_source", native_enum=True),
        nullable=False,
        default=InvestigationSource.MANUAL,
        server_default="MANUAL",
    )
    # Free-text pointer to whatever triggered this case (a duplicate group's
    # description, a transaction reference) - not a foreign key, because the
    # trigger isn't always one single row.
    source_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)

    priority: Mapped[InvestigationPriority] = mapped_column(
        Enum(InvestigationPriority, name="investigation_priority", native_enum=True),
        nullable=False,
        default=InvestigationPriority.MEDIUM,
        server_default="MEDIUM",
    )
    # The reconciliation exception this case was opened from, if any - lets
    # the case view show the external record and the amounts side by side.
    reconciliation_result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # Explicit: the generated name would exceed PostgreSQL's 63-char limit.
        ForeignKey(
            "reconciliation_results.id",
            name="fk_investigations_recon_result_id_reconciliation_results",
        ),
        nullable=True,
    )

    # The account and window this case investigates. Both nullable: a
    # ledger-wide integrity case has no single account.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=True
    )
    window_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The transaction that resolved this case, if resolution took the form
    # of a correction. Not required - some cases resolve as "reviewed, no
    # action needed".
    correction_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )

    created_by: Mapped[str] = mapped_column(String(120), nullable=False, server_default="system")

    account: Mapped["Account | None"] = relationship()  # noqa: F821
    correction_transaction: Mapped["Transaction | None"] = relationship()  # noqa: F821

    __table_args__ = (
        Index("ix_investigations_status", "status"),
        Index("ix_investigations_account_id", "account_id"),
        Index("ix_investigations_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Investigation {self.case_number} {self.status.value}>"
