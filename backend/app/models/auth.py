"""API key authentication.

A minimal, real authentication mechanism: a server-verified identity, never
a client-supplied header taken on faith. Keys are stored hashed (SHA-256
over a high-entropy, server-generated secret - the standard practice for
API keys, distinct from passwords which need a slow, salted hash because
humans pick low-entropy ones). A database read alone never discloses a
usable credential.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiKey(Base):
    """One caller's credential. `label` becomes the `actor` recorded on
    every transaction and audit event this key's requests produce - the
    server-verified replacement for the old client-supplied `X-Actor` header.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApiKey {self.label}>"
