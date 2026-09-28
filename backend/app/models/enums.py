"""Ledger domain enumerations.

These are stored as native PostgreSQL enum types: the database rejects a value
the application never intended to write, even from a psql prompt.
"""

from enum import StrEnum


class AccountType(StrEnum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class NormalBalance(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


# Accounting identity: assets and expenses increase on the debit side, every
# other account family increases on the credit side.
NORMAL_BALANCE_BY_TYPE: dict[AccountType, NormalBalance] = {
    AccountType.ASSET: NormalBalance.DEBIT,
    AccountType.EXPENSE: NormalBalance.DEBIT,
    AccountType.LIABILITY: NormalBalance.CREDIT,
    AccountType.EQUITY: NormalBalance.CREDIT,
    AccountType.REVENUE: NormalBalance.CREDIT,
}


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class EntryDirection(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class TransactionStatus(StrEnum):
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class TransactionKind(StrEnum):
    STANDARD = "STANDARD"
    REVERSAL = "REVERSAL"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class InvestigationStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"


class InvestigationSource(StrEnum):
    """Where a case came from - never a source of financial truth on its
    own, just where an operator's attention was first drawn."""

    MANUAL = "MANUAL"
    DUPLICATE = "DUPLICATE"
    INTEGRITY = "INTEGRITY"
    FLIGHT_RECORDER = "FLIGHT_RECORDER"
    RECONCILIATION = "RECONCILIATION"


class ReconciliationStatus(StrEnum):
    """A deterministic, structural comparison outcome - never a fraud
    verdict, never AI-judged.

    `STATUS_MISMATCH` and `DUPLICATE_EXTERNAL` are deliberately absent:
    `ExternalTransaction.status` (a settlement state like "SETTLED") and
    `Transaction.status` (POSTED/REVERSED, a reversal state) describe
    different things, and comparing them would invent a semantic
    relationship the schema doesn't actually support. Detecting duplicate
    *external* records structurally would require a second self-join
    detector mirroring `duplicate_candidates()` - real, but out of scope for
    this pass.
    """

    MATCHED = "MATCHED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    MISSING_INTERNAL = "MISSING_INTERNAL"
    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    AMBIGUOUS_INTERNAL_MATCH = "AMBIGUOUS_INTERNAL_MATCH"
