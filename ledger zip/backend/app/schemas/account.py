"""Account request/response schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import AccountStatus, AccountType, NormalBalance
from app.schemas.common import CurrencyCode, Money


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$")
    name: str = Field(min_length=1, max_length=160)
    type: AccountType
    currency: CurrencyCode
    description: str | None = Field(default=None, max_length=2000)
    # Defaults to true: an ordinary account is a classification and may legally
    # carry any balance. Cash-like accounts opt in to the stricter rule.
    allows_negative_balance: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class AccountUpdate(BaseModel):
    """Only descriptive fields and status may change.

    Type, currency and code are structural: changing them would retroactively
    reinterpret every entry already posted against the account.
    """

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: AccountStatus | None = None
    allows_negative_balance: bool | None = None
    metadata: dict[str, Any] | None = None


class AccountSummary(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    type: AccountType
    normal_balance: NormalBalance
    currency: str
    status: AccountStatus


class AccountOut(AccountSummary):
    description: str | None
    allows_negative_balance: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    # Read from the transactionally-maintained cache. Authoritative figures
    # come from GET /accounts/{id}/balance, which re-derives from entries.
    balance: Money
    debits: Money
    credits: Money
    entry_count: int


class BalanceCacheOut(BaseModel):
    balance: Money
    debits: Money
    credits: Money
    entry_count: int
    last_entry_seq: int | None
    updated_at: datetime | None


class AccountBalanceOut(BaseModel):
    """Balance derived from ledger entries at read time.

    `signed_balance` is debit-positive and is what sums to zero across the
    whole ledger; `balance` is oriented to the account's normal balance, which
    is what an operator expects to read.
    """

    account_id: uuid.UUID
    account_code: str
    currency: str
    normal_balance: NormalBalance
    balance: Money
    signed_balance: Money
    debits: Money
    credits: Money
    entry_count: int
    last_entry_seq: int | None
    derived_at: datetime
    cache: BalanceCacheOut
    cache_consistent: bool
