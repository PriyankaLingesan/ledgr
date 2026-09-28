"""Financial investigation: request/response shapes.

Every monetary figure here is a `Money`, derived the same way every other
monetary figure in the API is - nothing is a raw float and nothing is
estimated.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import EntryDirection, NormalBalance, TransactionKind, TransactionStatus
from app.schemas.account import AccountSummary
from app.schemas.common import Money
from app.schemas.transaction import EntryInput


class AccountBalanceAtOut(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    currency: str
    normal_balance: NormalBalance
    as_of: datetime
    balance: Money
    debits: Money
    credits: Money
    entry_count: int


class InvestigationEventOut(BaseModel):
    """One ledger entry, framed as a cause of a balance change.

    Deliberately thin: the transaction it belongs to - its full debit/credit
    breakdown, reversal relationship, actor, audit trail - is one click away
    at the existing `GET /transactions/{id}`, not duplicated here.
    """

    entry_id: uuid.UUID
    seq: int
    transaction_id: uuid.UUID
    reference: str
    description: str
    direction: EntryDirection
    amount: Money
    effect: Money  # signed, in the account's own normal orientation
    running_balance: Money
    created_at: datetime
    transaction_status: TransactionStatus
    transaction_kind: TransactionKind
    reverses_transaction_id: uuid.UUID | None
    reversed_by_transaction_id: uuid.UUID | None


class BalanceChangeOut(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    currency: str
    normal_balance: NormalBalance
    from_: datetime = Field(serialization_alias="from")
    to: datetime
    opening_balance: Money
    closing_balance: Money
    net_change: Money
    event_count: int
    events: list[InvestigationEventOut]
    # Computed independently from two different queries and compared - see
    # `investigation.BalanceChange.reconciled`. Always true in practice; the
    # field exists so a future regression is visible in the API response
    # rather than silently wrong.
    reconciled: bool

    model_config = {"populate_by_name": True}


class DuplicateEntryOut(BaseModel):
    transaction_id: uuid.UUID
    reference: str
    description: str
    account_code: str
    direction: EntryDirection
    amount: Money
    created_at: datetime


class DuplicateGroupOut(BaseModel):
    label: str = "POSSIBLE_DUPLICATE"
    reasons: list[str]
    entries: list[DuplicateEntryOut]


class IntegrityCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class IntegrityChecklistOut(BaseModel):
    checked_at: datetime
    all_passed: bool
    checks: list[IntegrityCheckOut]


class TransactionCorrectionRequest(BaseModel):
    """A correction is a new, independently-valid transaction: the same
    shape `POST /transactions` already accepts, plus a mandatory reason.

    The balance check is the same rule `TransactionCreate` enforces - checked
    here too so a malformed correction fails fast with a clear message,
    exactly like a hand-typed transaction would.
    """

    entries: list[EntryInput] = Field(min_length=2, max_length=64)
    reason: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    reference: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _check_shape(self) -> "TransactionCorrectionRequest":
        debits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.DEBIT)
        credits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.CREDIT)
        if debits == 0 or credits == 0:
            raise ValueError("a transaction needs at least one debit and one credit entry")
        if debits != credits:
            raise ValueError(f"unbalanced: debits {debits} != credits {credits} (minor units)")
        return self


class TransactionCorrectionOut(BaseModel):
    original_id: uuid.UUID
    original_reference: str
    reversal_id: uuid.UUID
    reversal_reference: str
    corrected_id: uuid.UUID
    corrected_reference: str


# --------------------------------------------------------------------------
# transaction lineage
# --------------------------------------------------------------------------


class TransactionNodeOut(BaseModel):
    """One node in a lineage diagram - deliberately as thin as
    `InvestigationEventOut`: the full transaction, with every entry, is one
    click away at `GET /transactions/{id}`.
    """

    id: uuid.UUID
    reference: str
    description: str
    status: TransactionStatus
    kind: TransactionKind
    currency: str
    total_debits: Money
    total_credits: Money
    posted_at: datetime


class TransactionLineageOut(BaseModel):
    root: TransactionNodeOut
    accounts: list[AccountSummary]
    reverses: TransactionNodeOut | None
    reversed_by: TransactionNodeOut | None
    corrects: TransactionNodeOut | None
    corrected_by: TransactionNodeOut | None


# --------------------------------------------------------------------------
# what-if correction simulator
# --------------------------------------------------------------------------


class SimulateCorrectionRequest(BaseModel):
    """Same shape as a real correction's entries, minus the reason: a
    simulation never writes an audit trail, so it needs none.
    """

    entries: list[EntryInput] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def _check_shape(self) -> "SimulateCorrectionRequest":
        debits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.DEBIT)
        credits = sum(e.amount_minor for e in self.entries if e.direction is EntryDirection.CREDIT)
        if debits == 0 or credits == 0:
            raise ValueError("a transaction needs at least one debit and one credit entry")
        if debits != credits:
            raise ValueError(f"unbalanced: debits {debits} != credits {credits} (minor units)")
        return self


class SimulatedAccountImpactOut(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    currency: str
    current_balance: Money
    simulated_balance: Money
    delta: Money


class CorrectionSimulationOut(BaseModel):
    original_id: uuid.UUID
    original_reference: str
    impacts: list[SimulatedAccountImpactOut]


# --------------------------------------------------------------------------
# control exceptions
# --------------------------------------------------------------------------


class ControlExceptionOut(BaseModel):
    """A real, already-detected signal that warrants a look - never a
    verdict. `id` is a stable fingerprint of the exception's own content
    (not a stored row), so the same exception gets the same id across calls
    for as long as the underlying condition persists.
    """

    id: str
    kind: str
    title: str
    detail: str
    account_id: uuid.UUID | None
    transaction_ids: list[uuid.UUID]
    detected_at: datetime
