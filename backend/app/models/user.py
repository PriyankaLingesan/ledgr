"""Organizations, users and login sessions.

None of these tables carries financial data. They answer "who is asking" and
"what are they allowed to do"; the ledger itself stays the single source of
financial truth and is not partitioned by organization (one deployment, one
ledger - see the README's limitations).

Passwords are stored only as salted scrypt hashes (`app.services.auth`).
Session tokens, like API keys, are stored only as SHA-256 hashes: reading
these tables never discloses a usable credential.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import UserRole


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    # Maker-checker policy: a direct `POST /transactions` at or above this
    # many minor units is refused (409 approval_required) and must go through
    # the draft -> approval workflow instead. NULL disables the policy.
    approval_threshold_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Set only by a demo seeder (e.g. `meridian-v1`): marks the organization as a
    # fictional demonstration. NULL for every real organization.
    demo_scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # Brute-force protection: consecutive failures lock the account briefly.
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")

    __table_args__ = (
        # Case-insensitive uniqueness: "A@x.com" and "a@x.com" are one person.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("ix_users_organization_id", "organization_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} {self.role.value}>"


class UserSession(Base):
    """A server-side login session. The token itself is never stored.

    Server-side (rather than a stateless JWT) so that logout, deactivating a
    user, and expiry are all *enforced* by the server on the next request
    instead of being merely hoped for until a token's own `exp` passes.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Random value the browser must echo in `X-CSRF-Token` on state-changing
    # requests when it authenticates with the session cookie. Useless without
    # the cookie, so (unlike the session token) it is stored in the clear.
    csrf_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship()

    __table_args__ = (Index("ix_user_sessions_user_id", "user_id"),)
