"""LEDGR Intelligence request/response schemas.

`TransactionProposal` is the important one: it is built from the model's
output but validated the same way a real posting would be, and it is never,
itself, something the backend can turn into ledger entries. Posting still
requires the caller to submit it to `POST /transactions` - the existing
endpoint, with the existing `TransactionCreate` validation and idempotency
handling - which is why this schema's `entries` already carry resolved
`account_id` values in exactly the shape that endpoint expects.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import EntryDirection
from app.schemas.common import Money


class AIStatusOut(BaseModel):
    available: bool
    provider: str | None
    reason: str | None = None


# --- transaction assistant -------------------------------------------------


class TransactionAssistRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class ProposedEntryOut(BaseModel):
    account_id: uuid.UUID | None
    account_code: str
    account_name: str | None
    direction: EntryDirection
    amount: Money


class TransactionProposalOut(BaseModel):
    """An AI-generated draft. `valid=False` means: show the issues, don't offer Post."""

    valid: bool
    issues: list[str] = Field(default_factory=list)
    description: str
    currency: str
    entries: list[ProposedEntryOut]
    total_debits: Money | None = None
    total_credits: Money | None = None
    # Ready to hand straight to the existing POST /transactions body once the
    # user reviews it - never submitted by this endpoint itself.
    post_body: dict[str, Any] | None = None


# --- ask ledger -------------------------------------------------------------


class AskLedgerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskLedgerOut(BaseModel):
    answer: str
    tools_used: list[str] = Field(default_factory=list)


# --- explain transaction ----------------------------------------------------


class ExplainTransactionOut(BaseModel):
    transaction_id: uuid.UUID
    reference: str
    explanation: str


# --- ledger brief ------------------------------------------------------------


class CurrencyActivityOut(BaseModel):
    currency: str
    transaction_count: int
    total_value: Money
    largest_transaction_reference: str | None
    largest_amount: Money


class LedgerBriefStatsOut(BaseModel):
    """The facts, computed without an LLM. `summary` below is just prose over these."""

    window_days: int
    total_transactions: int
    all_transactions_balanced: bool
    by_currency: list[CurrencyActivityOut]


class LedgerBriefOut(BaseModel):
    generated_at: datetime
    is_ai_generated: bool
    summary: str
    stats: LedgerBriefStatsOut
