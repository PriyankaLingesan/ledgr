"""Idempotency records.

The primary key is (scope, key). Claiming a key is an `INSERT ... ON CONFLICT
DO NOTHING`, so the database - not application logic - decides which of two
racing retries gets to do the work.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import IdempotencyStatus


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"

    # Scope prevents a key minted for "post transaction" from colliding with
    # the same string used for "reverse transaction".
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)

    # SHA-256 of the canonical request body. A retry that changes the payload
    # is a client bug, not a retry, and is rejected rather than replayed.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[IdempotencyStatus] = mapped_column(
        Enum(IdempotencyStatus, name="idempotency_status", native_enum=True),
        nullable=False,
        default=IdempotencyStatus.IN_PROGRESS,
        server_default="IN_PROGRESS",
    )

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )

    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_idempotency_keys_created_at", "created_at"),
        Index("ix_idempotency_keys_transaction", "transaction_id"),
    )
