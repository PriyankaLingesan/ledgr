# Financial invariants

These are the properties LEDGR must never violate. Each one is enforced in the service
layer, and — wherever PostgreSQL can express it — a second time in the database, so a
defect in application code still cannot corrupt the ledger.

The two enforcement points are not redundant. The service layer produces good error
messages for legitimate clients; the database layer is what makes the guarantee true for
*any* client, including a psql session, a data-fix script, or a future bug.

---

## I1 — Every posted transaction balances

Total debits equal total credits, and both are strictly greater than zero.

- **Service**: `posting._validate_shape` sums both sides before anything is inserted and
  raises `UnbalancedTransaction` with the exact difference in the error details.
- **Database**: `CHECK (total_debits_minor = total_credits_minor)` and
  `CHECK (total_debits_minor > 0)` on `transactions`. An unbalanced row cannot exist.
- **Tests**: `test_unbalanced_transaction_is_rejected`,
  `test_single_sided_transaction_is_rejected`,
  `test_unbalanced_row_cannot_be_inserted_directly`.

## I2 — A transaction has at least two entries

A single-sided posting is not a transaction, it is a mistake.

- **Service**: `ValidationFailed` below two entries; also capped at
  `max_entries_per_transaction` (default 64) so one request cannot lock an unbounded
  number of accounts.
- **Database**: `CHECK (entry_count >= 2)`.
- **Test**: `test_single_entry_transaction_is_rejected`.

## I3 — Amounts are positive integers of minor units

Direction carries the sign. A negative debit is a credit wearing a disguise, and allowing
both spellings makes every aggregate query ambiguous.

- **Service**: `InvalidAmount` for zero, negative or non-integer amounts.
- **Schema**: `amount_minor: int = Field(gt=0)` rejects it at the API boundary too.
- **Database**: `CHECK (amount_minor > 0)` on `ledger_entries`.
- **Test**: `test_non_positive_amounts_are_rejected` (parameterised over 0, −1, −5000).

## I4 — One currency per transaction, matching every account

Cross-currency movement is a pair of transactions plus an FX position, not a single
unbalanced posting. LEDGR refuses to blur that.

- **Service**: after locking, every account's `currency` is compared with the
  transaction's; mismatch raises `CurrencyMismatch` naming both. Unknown currency codes
  raise `UnsupportedCurrency` and return the supported list.
- **Tests**: `test_currency_must_match_every_account`,
  `test_unsupported_currency_is_rejected`.

## I5 — Ledger entries are never updated or deleted

- **Database**: `BEFORE UPDATE OR DELETE` trigger on `ledger_entries` raising
  `restrict_violation`. The same trigger protects `audit_events`.
- **Application**: no code path issues `UPDATE` or `DELETE` against these tables, and the
  ledger router exposes no `PUT`, `PATCH` or `DELETE` route.
- **Tests**: `test_ledger_entry_cannot_be_updated`, `test_ledger_entry_cannot_be_deleted`,
  `test_audit_events_are_append_only` — all issuing raw SQL, deliberately bypassing the
  service layer.

## I6 — Posted transactions are immutable except POSTED → REVERSED

- **Database**: `ledgr_guard_transaction_update()` compares every financial field on
  `UPDATE` and rejects the statement if any of them changed; a second trigger blocks
  `DELETE`; `REVERSED` is terminal.
- **Tests**: `test_transaction_financial_fields_are_frozen`,
  `test_transaction_description_is_frozen`, `test_transaction_cannot_be_deleted`,
  `test_status_transition_to_reversed_is_permitted`.

## I7 — A failed posting leaves no partial entries

All entries and the transaction row are written in one database transaction, committed
once at the end.

- **Test**: `test_failed_posting_leaves_no_partial_entries` triggers a failure that occurs
  *after* the rows have been inserted and flushed (a balance-floor breach), then asserts
  from an independent connection that nothing was committed — the real rollback path, not
  a pre-flight short-circuit.

## I8 — Entries may only be posted to ACTIVE accounts

- **Service**: status is checked while the account row is locked, so a freeze cannot slip
  in between the check and the write.
- **Test**: `test_inactive_accounts_cannot_receive_entries`, parameterised over `FROZEN`
  and `CLOSED`.

## I9 — The cached balance always equals the derived balance

`account_balances` is a performance artefact, never the source of truth.

- **Mechanism**: it is updated in the same database transaction as the entries that cause
  the change, while the account row is locked, so no update can be lost.
- **Verification**: `GET /system/integrity` re-derives every balance from
  `ledger_entries` and reports divergence; `GET /accounts/{id}/balance` returns the
  derived figure with a `cache_consistent` flag beside it.
- **Tests**: `test_cache_matches_derived_balance`,
  `test_concurrent_postings_keep_the_balance_cache_exact` (eight simultaneous writers on
  one account), `test_integrity_report_is_clean`.

## I10 — An idempotency key yields at most one transaction

- **Mechanism**: the claim is `INSERT … ON CONFLICT DO NOTHING` against the
  `(scope, key)` primary key, committed on a separate connection before any ledger work
  begins. The loser of the race reads the winner's record.
  - request hash matches, work finished → the stored response is replayed
  - request hash differs → `409 idempotency_key_reused`
  - work still running → `409 idempotent_request_in_flight`
- **On failure** the claim is released, so a legitimate retry is not locked out.
- **Tests**: `test_retry_with_same_key_replays_the_original_response`,
  `test_same_key_with_a_different_body_is_refused`,
  `test_failed_request_releases_the_key_for_retry`,
  `test_in_flight_duplicate_is_rejected_rather_than_queued`,
  `test_keys_are_scoped_per_operation`.

## I11 — A transaction is reversed at most once

- **Database**: `UNIQUE (reverses_transaction_id)` on `transactions`. This, not the row
  lock, is what makes the guarantee true under a race — the lock only makes conflict rare.
- **Service**: the original is locked `FOR UPDATE` and its status checked; an
  `IntegrityError` on the unique index is translated to `AlreadyReversed`.
- **Tests**: `test_a_transaction_cannot_be_reversed_twice`,
  `test_concurrent_reversals_produce_exactly_one` (four racing threads, exactly one wins).

## I12 — Corrections are compensating transactions

A reversal mirrors every entry — each debit becomes a credit of the same amount — and
links back via `reverses_transaction_id`. The original keeps every entry it was posted
with and is marked `REVERSED`. A reversal cannot itself be reversed; a further correction
is a new transaction.

Reversals deliberately **skip** balance-floor enforcement: refusing to correct because the
result dips below zero would trap the ledger in a state it is not allowed to leave.

- **Tests**: `test_reversal_mirrors_every_entry`,
  `test_reversal_returns_balances_to_zero`,
  `test_original_is_marked_reversed_but_keeps_its_entries`,
  `test_a_reversal_cannot_itself_be_reversed`,
  `test_reversal_bypasses_balance_floors`.

## I13 — The ledger balances globally, per currency

Σ debits = Σ credits across every entry in a currency. This is the cheapest end-to-end
proof that nothing has corrupted the ledger, and it is surfaced on the dashboard rather
than buried in a script.

- **Verification**: `balances.trial_balance` and `balances.unbalanced_transaction_ids`,
  exposed through `/system/stats` and `/system/integrity`.
- **Tests**: `test_trial_balance_nets_to_zero`, `test_stats_are_derived_from_real_activity`.

---

## Balance-floor rule (policy, not invariant)

Accounts created with `allows_negative_balance: false` reject any posting that would drive
their normal-orientation balance below zero. This is business policy rather than an
accounting invariant — a clearing account legitimately goes negative — so it is opt-in per
account.

It is the reason the posting engine takes row locks at all: the check and the write happen
while the account is locked, so two concurrent withdrawals cannot both observe sufficient
funds.

- **Tests**: `test_balance_floor_is_enforced`, `test_exact_drain_to_zero_is_allowed`,
  `test_unconstrained_accounts_may_go_negative`,
  `test_balance_floor_holds_under_concurrent_withdrawals`.
