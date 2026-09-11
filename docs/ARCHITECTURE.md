# Architecture

## Shape of the system

A modular monolith with a strict inward dependency rule:

```
api/v1  ──▶  services  ──▶  models  ──▶  PostgreSQL
   │             │
   └── schemas ──┘
```

- **`api/v1`** — routers. They parse and validate request shape, call one service, and
  serialise the result. No router computes a balance, decides whether something is valid,
  or opens a transaction.
- **`services`** — where every financial rule lives, and where the unit of work is owned.
  A service decides when to commit; a router never does.
- **`models`** — SQLAlchemy 2.0 mapped classes plus the constraints and indexes that back
  them.
- **`schemas`** — Pydantic models for the wire, and hand-written serializers. Serialization
  is explicit rather than `from_attributes` because every monetary field must be rendered
  against its own currency exponent, and because the API shape should be free to diverge
  from the storage shape (`extra` in the database, `metadata` on the wire).

### Why not microservices

Nothing in scope is independently scalable, independently deployable, or owned by a
different team. Splitting a ledger across services would replace a database transaction
with a distributed one — the single hardest problem in the domain — and buy nothing. The
seams that matter are enforced by module boundaries and tests instead of by network hops.

---

## Money

Every amount is a `BIGINT` count of **minor units** (cents, paise, yen) alongside an
ISO-4217 currency code. No floats — `0.1 + 0.2` is a bug in a ledger. No `NUMERIC` either:
integers make every sum, comparison and constraint exact and index-friendly, and the
minor-unit convention matches what payment infrastructure already speaks.

The currency exponent lives in one place (`app/core/money.py`) and is published to the
frontend through `GET /system/currencies`, so major/minor conversion has exactly one
definition across the stack.

---

## Database schema

### `accounts`

The chart of accounts. An account is a *classification*, not a wallet — it holds no money
of its own.

| Column | Notes |
| --- | --- |
| `id` | UUID PK |
| `code` | unique human handle, e.g. `CASH.OPERATING.USD` |
| `type` | `ASSET · LIABILITY · EQUITY · REVENUE · EXPENSE` (native enum) |
| `normal_balance` | **derived** from type, never client-supplied |
| `currency` | ISO-4217, immutable once entries exist |
| `status` | `ACTIVE · FROZEN · CLOSED`; only `ACTIVE` accepts entries |
| `allows_negative_balance` | opt-in balance floor |
| `extra` | JSONB metadata |

Indexes: `(type, status)`, `(currency)`, unique `(code)`.

### `account_balances`

One row per account: `debits_minor`, `credits_minor`, `entry_count`, `last_entry_seq`.

**This is a cache, not the truth.** It is written in the same database transaction as the
entries that change it, while the account row is locked. It exists so that listing 500
accounts is not 500 aggregate scans, and so a balance floor can be checked atomically.
`/system/integrity` re-derives every balance from entries and reports divergence.

### `transactions`

| Column | Notes |
| --- | --- |
| `id`, `seq` | UUID PK plus a monotonic `IDENTITY` ordering independent of clock skew |
| `reference` | unique; a caller reusing its own reference gets `409`, not a duplicate |
| `status` | `POSTED · REVERSED` — there is no DRAFT or PENDING |
| `kind` | `STANDARD · REVERSAL` |
| `total_debits_minor`, `total_credits_minor`, `entry_count` | written once, never updated |
| `effective_at` vs `posted_at` | business time vs system time, never conflated |
| `reverses_transaction_id` | unique, self-referencing → at most one reversal |
| `actor`, `request_id`, `idempotency_key`, `external_reference` | the audit trail |

Constraints: balanced totals, positive value, ≥2 entries, `kind = REVERSAL` iff
`reverses_transaction_id` is set, `status = REVERSED` iff `reversed_at` is set.

There is no DRAFT state because a transaction that has not balanced is not a transaction —
it is a request that failed validation, and requests belong in logs, not in the ledger.

### `ledger_entries`

The authoritative financial record. Append-only.

| Column | Notes |
| --- | --- |
| `seq` | global `IDENTITY` — a total order for audit replay and a keyset cursor |
| `direction` | `DEBIT · CREDIT` |
| `amount_minor` | always positive; direction carries the sign |
| `entry_index` | position within the transaction, unique per transaction |

Indexes: `(account_id, seq)` serves both the account statement and balance derivation;
`(transaction_id)` serves the audit path; `(currency, direction)` serves the trial balance.

### `idempotency_keys`

Primary key `(scope, key)`. Stores the request hash, the response snapshot and the
resulting transaction id. Scope prevents a key minted for "post transaction" colliding
with the same string used for "reverse transaction".

### `audit_events`

Append-only, with a global `seq`. Written in the same transaction as the change it
describes, so an audit row and its financial effect commit or roll back together.

---

## Concurrency

### The problem

Two requests post against the same account at the same time. Naively:

1. both read the balance as 100
2. both check "is 60 ≤ 100?" — both say yes
3. both write

The account ends at −20, and one of those withdrawals was money the system did not have.

### The approach

Postings run at READ COMMITTED and take explicit row locks:

```python
for account_id in sorted(account_ids, key=str):          # global lock ordering
    SELECT * FROM accounts WHERE id = :id FOR UPDATE
```

Locks are acquired **one account at a time, in ascending id order**. A single
`WHERE id IN (…) ORDER BY id FOR UPDATE` would be terser, but the order in which rows are
actually locked is then the planner's decision, not ours. Taking them individually makes
the ordering explicit, and a global lock ordering is what makes deadlock between two
transactions with overlapping account sets impossible.

The balance check and the balance write both happen inside that lock, so step 2 above
cannot interleave.

### Why not SERIALIZABLE

It would also be correct. It was rejected because:

- it pushes serialization-failure retry handling onto every caller,
- it serialises far more than the accounts actually involved,
- and the failure mode (random 40001 errors under load) is much harder to reason about
  than a lock that is held for the duration of one small transaction.

Explicit per-account locks keep contention proportional to real contention: two
transactions touching disjoint accounts never block each other — asserted by
`test_disjoint_accounts_do_not_block_each_other`.

### Why not distributed locking

There is one database and one writer per account. Redis or a lock service would add a
second source of truth about who holds a lock, and a new failure mode where the two
disagree. PostgreSQL already provides exactly the primitive needed.

### Timeouts

Every connection sets `lock_timeout` (5s), `statement_timeout` (15s) and
`idle_in_transaction_session_timeout` (30s). A ledger write blocked forever on a row lock
is an incident; the database gives up first and the API returns `503 lock_timeout` with
advice to retry using the same idempotency key.

---

## Idempotency protocol

```
client                     API                      idempotency table       ledger
  │   POST + Idempotency-Key │                              │                  │
  ├─────────────────────────▶│  INSERT … ON CONFLICT DO NOTHING ──────────────▶│
  │                          │◀── inserted? ────────────────┤                  │
  │                          │                              │                  │
  │              yes ────────┤  post transaction (separate txn) ──────────────▶│
  │                          │  UPDATE claim → COMPLETED + response snapshot   │
  │◀── 201 + body ───────────┤                              │                  │
  │                          │                              │                  │
  │              no  ────────┤  read existing claim                            │
  │◀── 201 + Idempotent-Replay: true (same body)                               │
  │◀── 409 idempotency_key_reused        (different request hash)              │
  │◀── 409 idempotent_request_in_flight  (still running)                       │
```

The claim is committed on its own connection *before* ledger work starts — that ordering
is what makes a concurrent retry visible to the second caller. The database, not
application logic, decides which request wins.

**Failure handling.** If the ledger write raises, the claim is released so a legitimate
retry is not locked out forever. The residual risk is a process dying between the ledger
commit and the claim update: that leaves a posted transaction and a free key. It is
covered by the second line of defence — a client-supplied `reference` is unique, so a
blind retry fails with `duplicate_reference` rather than double-posting.

An in-flight duplicate returns `409` rather than blocking. A caller retrying a request it
has not yet had an answer to should back off, not pile up connections.

---

## Balance derivation

```sql
SELECT
  COALESCE(SUM(CASE WHEN direction = 'DEBIT'  THEN amount_minor ELSE 0 END), 0) AS debits,
  COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount_minor ELSE 0 END), 0) AS credits
FROM ledger_entries
WHERE account_id = :id;
```

Two orientations are exposed, because both are needed and conflating them causes bugs:

- **`signed_balance`** — debit-positive (`debits − credits`). Summed over every account
  this is always zero. This is the orientation arithmetic uses.
- **`balance`** — oriented to the account's normal balance. A liability with 500 credited
  and 100 debited reads as **400**, not −400. This is the orientation humans use.

---

## Error handling

Every failure produces one envelope with a stable `code`, so clients branch on codes
rather than parsing prose. Domain errors are typed (`UnbalancedTransaction`,
`InsufficientFunds`, `AlreadyReversed`, …), each carrying its HTTP status. Unanticipated
`IntegrityError`s are translated to `409 constraint_violation` — the database kept the
ledger correct, and the API does not pretend the write succeeded.

Every request carries an `X-Request-ID` (propagated from the edge or minted by middleware).
It appears in the response, in every structured log line, on the transaction row and in the
audit event — so one identifier joins an API call to its financial consequence.

---

## Testing strategy

Tests run against real PostgreSQL. SQLite was rejected outright: it has no row locks, no
native enums, no `FOR UPDATE`, and none of the triggers that carry half of the guarantees.
A green suite there would be evidence about a different system.

| Suite | Covers |
| --- | --- |
| `test_posting_invariants` | balanced/unbalanced, amounts, currency, account status, atomic rollback |
| `test_immutability` | triggers, via raw SQL that bypasses the service layer entirely |
| `test_balances` | derivation, orientation, cache agreement, trial balance, balance floors |
| `test_idempotency` | replay, hash mismatch, in-flight, release-on-failure, scoping |
| `test_reversal` | mirroring, status transition, double-reversal, floor bypass, audit |
| `test_concurrency` | real threads on real connections: lost updates, double-spend, deadlock-freedom, racing reversals |
| `test_api` | HTTP contracts, filters, pagination, error shapes, audit traceability |

The concurrency suite uses `threading.Barrier` to release all workers simultaneously,
maximising real contention rather than hoping for an accidental interleaving.
