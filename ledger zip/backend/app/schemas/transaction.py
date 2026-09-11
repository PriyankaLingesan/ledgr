"""Transaction request/response schemas.

Request validation here is deliberately *structural* only (shapes, ranges,
cardinality). Financial invariants that need database state - account currency,
account status, balance constraints - are enforced in the posting service where
they can be checked under a lock.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import EntryDirection, TransactionKind, TransactionStatus
from app.schemas.account import AccountSummary
from app.schemas.common import CurrencyCode, Money


class EntryInput(BaseModel):
    account_id: uuid.UUID
    direction: EntryDirection
    amount_minor: int = Field(gt=0, le=10**15, description="Positive integer in minor units")
    memo: str | None = Field(default=None, max_length=500)


class TransactionCreate(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    currency: CurrencyCode
    entries: list[EntryInput] = Field(min_length=2, max_length=64)
    reference: str | None = Field(default=None, max_length=80)
    effective_at: datetime | None = None
    external_reference: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _check_shape(self) -> "TransactionCreate":
        debits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.DEBIT)
        credits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.CREDIT)
        if debits == 0 or credits == 0:
            raise ValueError("a transaction needs at least one debit and one credit entry")
        if debits != credits:
            raise ValueError(f"unbalanced: debits {debits} != credits {credits} (minor units)")
        return self


class TransactionReverse(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=80)
    effective_at: datetime | None = None


class TransactionSummary(BaseModel):
    id: uuid.UUID
    reference: str
    description: str
    status: TransactionStatus
    kind: TransactionKind
    currency: str
    posted_at: datetime
    effective_at: datetime


class LedgerEntryOut(BaseModel):
    id: uuid.UUID
    seq: int
    transaction_id: uuid.UUID
    account_id: uuid.UUID
    direction: EntryDirection
    amount: Money
    entry_index: int
    memo: str | None
    created_at: datetime
    account: AccountSummary | None = None
    transaction: TransactionSummary | None = None


class TransactionOut(BaseModel):
    id: uuid.UUID
    seq: int
    reference: str
    description: str
    currency: str
    status: TransactionStatus
    kind: TransactionKind

    total_debits: Money
    total_credits: Money
    # Recomputed from the persisted entries on every read, not trusted from the
    # denormalised totals - this is what the Transaction Detail page asserts.
    balanced: bool
    entry_count: int

    effective_at: datetime
    posted_at: datetime
    reversed_at: datetime | None
    reverses_transaction_id: uuid.UUID | None
    reversed_by_transaction_id: uuid.UUID | None

    actor: str
    request_id: str | None
    idempotency_key: str | None
    external_reference: str | None
    metadata: dict[str, Any]

    entries: list[LedgerEntryOut] = Field(default_factory=list)
