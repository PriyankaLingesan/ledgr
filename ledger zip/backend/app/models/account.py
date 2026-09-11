"""Accounts and the derived-balance cache."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import AccountStatus, AccountType, NormalBalance


class Account(Base, TimestampMixin):
    """A node in the chart of accounts.

    An account is a *classification*, not a wallet: it holds no money of its
    own. Its balance is a function of the ledger entries that reference it.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Stable human-facing handle used in operator tooling and imports.
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", native_enum=True), nullable=False
    )
    normal_balance: Mapped[NormalBalance] = mapped_column(
        Enum(NormalBalance, name="normal_balance", native_enum=True), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status", native_enum=True),
        nullable=False,
        default=AccountStatus.ACTIVE,
        server_default="ACTIVE",
    )

    # When false the posting engine locks this account and refuses any entry
    # that would drive its normal-orientation balance below zero.
    allows_negative_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    balance_cache: Mapped["AccountBalance"] = relationship(
        back_populates="account", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("char_length(currency) = 3", name="currency_is_iso4217"),
        CheckConstraint("char_length(code) > 0", name="code_not_empty"),
        Index("ix_accounts_type_status", "type", "status"),
        Index("ix_accounts_currency", "currency"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Account {self.code} {self.type.value} {self.currency}>"


class AccountBalance(Base):
    """Transactionally-maintained balance cache.

    NOT the source of truth. Ledger entries are authoritative; this row exists
    so listing 500 accounts does not fan out into 500 aggregate scans, and so
    balance constraints can be enforced under a row lock. Every write to it
    happens inside the same database transaction as the entries that caused it,
    and `/system/integrity` re-derives every balance from entries to prove the
    two agree.
    """

    __tablename__ = "account_balances"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    debits_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    credits_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_entry_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    account: Mapped[Account] = relationship(back_populates="balance_cache")

    __table_args__ = (
        CheckConstraint("debits_minor >= 0 AND credits_minor >= 0", name="totals_non_negative"),
    )
