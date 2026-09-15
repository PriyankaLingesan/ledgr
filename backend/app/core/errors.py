"""Domain errors and their HTTP representation.

Every failure the API can produce is a `LedgrError` carrying a stable machine
readable `code`, so clients branch on codes rather than parsing prose.
"""

from typing import Any


class LedgrError(Exception):
    status_code: int = 400
    code: str = "ledgr_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- invariant violations -------------------------------------------------


class ValidationFailed(LedgrError):
    status_code = 422
    code = "validation_failed"


class UnbalancedTransaction(LedgrError):
    """I1: total debits must equal total credits."""

    status_code = 422
    code = "unbalanced_transaction"


class CurrencyMismatch(LedgrError):
    """I4: one currency per transaction, matching every account involved."""

    status_code = 422
    code = "currency_mismatch"


class UnsupportedCurrency(LedgrError):
    status_code = 422
    code = "unsupported_currency"


class InvalidAmount(LedgrError):
    """I3: every entry amount must be a positive integer of minor units."""

    status_code = 422
    code = "invalid_amount"


class AccountNotPostable(LedgrError):
    """I8: entries may only be posted to ACTIVE accounts."""

    status_code = 422
    code = "account_not_postable"


class InsufficientFunds(LedgrError):
    """Balance-constrained account would go negative."""

    status_code = 422
    code = "insufficient_funds"


class ImmutableRecord(LedgrError):
    """I5/I6: posted ledger history cannot be edited or deleted."""

    status_code = 409
    code = "immutable_record"


# --- lifecycle ------------------------------------------------------------


class NotFound(LedgrError):
    status_code = 404
    code = "not_found"


class Conflict(LedgrError):
    status_code = 409
    code = "conflict"


class DuplicateReference(Conflict):
    code = "duplicate_reference"


class AlreadyReversed(Conflict):
    """I11: a transaction may be reversed at most once."""

    code = "already_reversed"


class NotReversible(LedgrError):
    status_code = 422
    code = "not_reversible"


# --- idempotency ----------------------------------------------------------


class IdempotencyKeyRequired(LedgrError):
    status_code = 400
    code = "idempotency_key_required"


class IdempotencyKeyReused(Conflict):
    """Same key, different request body: refusing to guess which one is real."""

    code = "idempotency_key_reused"


class IdempotentRequestInFlight(Conflict):
    """An identical request is still executing; the retry must wait."""

    code = "idempotent_request_in_flight"


# --- AI (LEDGR Intelligence) -----------------------------------------------
#
# The AI layer never writes to the ledger, so its failures are never
# financial-integrity failures - just a feature that couldn't run. These are
# kept separate from the invariant errors above for exactly that reason.


class AIUnavailable(LedgrError):
    """No AI provider is configured, or the provider call itself failed."""

    status_code = 503
    code = "ai_unavailable"


class AIResponseInvalid(LedgrError):
    """The model's output didn't parse, or the proposal it described is invalid.

    Distinct from `UnbalancedTransaction` et al.: this describes a *proposal*
    that was never posted and never will be from this response - not a rule
    the deterministic posting engine enforced.
    """

    status_code = 422
    code = "ai_response_invalid"
