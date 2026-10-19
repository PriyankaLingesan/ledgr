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

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import UserRole


class ApiKey(Base):
    """One caller's credential. `label` becomes the `actor` recorded on
    every transaction and audit event this key's requests produce - the
    server-verified replacement for the old client-supplied `X-Actor` header.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # What this key may do - the same role vocabulary as a human user. Keys
    # minted before roles existed were full-access, so they migrate as ADMIN.
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True),
        nullable=False,
        default=UserRole.ADMIN,
        server_default="ADMIN",
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # First characters of the key, shown in the management UI so an operator
    # can tell keys apart. Not enough to authenticate with.
    key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Labels are unique within an organization, not globally.
        UniqueConstraint("organization_id", "label", name="uq_api_keys_label"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApiKey {self.label}>"
