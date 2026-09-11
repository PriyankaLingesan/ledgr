# LEDGR

A double-entry financial ledger service and operations console.

Money is recorded as balanced sets of debit and credit entries. Ledger entries are
append-only, account balances are **derived** from those entries rather than stored as a
mutable running total, posting is atomic, writes are idempotent, and corrections are made
by posting reversals — never by editing history.

```
FastAPI + SQLAlchemy 2.0  ·  PostgreSQL 16  ·  React + TypeScript + Vite  ·  Docker  ·  GitHub Actions
```

---

## Why this exists

Most "transactions" tables do this:

```sql
UPDATE accounts SET balance = balance + 100 WHERE id = ...;
```

That is not a ledger. It loses history, it cannot be audited, it corrupts under
concurrency, and a retried HTTP request silently doubles someone's money. LEDGR is
built the way financial infrastructure actually is:

| Concern | How LEDGR handles it |
| --- | --- |
| Recording | Every transaction writes ≥2 entries whose debits equal credits |
| History | `ledger_entries` is append-only — enforced by a PostgreSQL trigger, not a convention |
| Balances | Aggregated from entries at read time; the cached figure is a separate, verifiable artefact |
| Atomicity | All entries commit together or none do, in one database transaction |
| Concurrency | Accounts locked `FOR UPDATE` in a global order; balance checks happen under the lock |
| Retries | `Idempotency-Key` claims are arbitrated by a primary-key insert, not application logic |
| Corrections | Reversals post a mirrored compensating transaction; the original is preserved |
| Audit | Entry → transaction → accounts → request id → actor, all traceable |

---

## Quick start

### With Docker (everything)

```bash
docker compose up --build
```

This starts PostgreSQL, applies migrations, seeds a realistic chart of accounts and ~150
transactions, then serves:

| Service | URL |
| --- | --- |
| Operations console | http://localhost:8080 |
| API | http://localhost:8000/api/v1 |
| OpenAPI docs | http://localhost:8000/docs |
| PostgreSQL | `localhost:5433` (user/password/db: `ledgr`) |

### Local development

Start only the database with Docker, then run the two services natively:

```bash
docker compose up -d db
```

**Backend** (Python 3.11+):

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
python -m app.scripts.seed
uvicorn app.main:app --reload
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

### Tests

```bash
cd backend
pytest -q                       # full suite
pytest -m concurrency -q        # only the concurrent-session tests
```

Tests run against real PostgreSQL in a `ledgr_test` database that the harness creates.
They deliberately do **not** use SQLite: row locks, native enums and the immutability
triggers do not exist there, so a green SQLite suite would prove nothing about the system
that actually ships.

---

## Financial invariants

These are the rules the system must never violate. Each is enforced in the service layer
and, wherever the database can express it, a second time in PostgreSQL — so a bug in
Python still cannot corrupt the ledger.

| # | Invariant | Enforced by |
| --- | --- | --- |
| I1 | Total debits = total credits on every posted transaction | `posting._validate_shape` + `CHECK (total_debits_minor = total_credits_minor)` |
| I2 | A transaction has at least two entries | service + `CHECK (entry_count >= 2)` |
| I3 | Every entry amount is a positive integer of minor units | service + `CHECK (amount_minor > 0)` |
| I4 | One currency per transaction, matching every account involved | service (needs account state) |
| I5 | Ledger entries are never updated or deleted | `BEFORE UPDATE OR DELETE` trigger |
| I6 | Posted transactions are immutable except `POSTED → REVERSED` | field-level trigger |
| I7 | A failed posting leaves no partial entries | single DB transaction |
| I8 | Entries may only be posted to `ACTIVE` accounts | service, under row lock |
| I9 | Cached balance always equals the balance derived from entries | written under the same lock; verified by `/system/integrity` |
| I10 | One idempotency key yields at most one transaction | `INSERT … ON CONFLICT DO NOTHING` on the PK |
| I11 | A transaction is reversed at most once | `UNIQUE (reverses_transaction_id)` |
| I12 | Corrections are new compensating transactions, never edits | reversal service + I5/I6 |
| I13 | Σ debits = Σ credits across the whole ledger, per currency | verified by `/system/stats` and `/system/integrity` |

Full detail, including which test covers each: [`docs/INVARIANTS.md`](docs/INVARIANTS.md).

---

## Architecture

```
                    ┌────────────────────────────────────────────┐
  Browser  ────────▶│  React console (Vite, TS, Tailwind)        │
                    └───────────────────┬────────────────────────┘
                                        │ REST + JSON
                    ┌───────────────────▼────────────────────────┐
                    │  FastAPI                                    │
                    │   api/v1     thin routers, validation       │
                    │   services   ALL invariants + unit of work  │
                    │   models     SQLAlchemy 2.0 ORM             │
                    └───────────────────┬────────────────────────┘
                                        │ psycopg 3
                    ┌───────────────────▼────────────────────────┐
                    │  PostgreSQL 16                              │
                    │   constraints · row locks · triggers        │
                    └─────────────────────────────────────────────┘
```

A modular monolith, deliberately. Nothing here is independently scalable, independently
deployable or written by a different team, so splitting it into services would add network
failure modes and distributed-transaction problems in exchange for nothing. The seams that
matter (routers / services / models) are enforced in code instead.

Deeper write-up: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Data model

```
accounts ──1:1──▶ account_balances        (derived cache, never the source of truth)
    ▲
    │ N
ledger_entries ──N:1──▶ transactions ──0:1──▶ transactions   (reversal link, unique)
                              ▲
                              │
                    idempotency_keys        audit_events
```

Money is stored as `BIGINT` **minor units** (cents, paise, yen) plus an ISO-4217 currency
code — never floats, never a decimal type whose rounding depends on driver settings.

---

## API

Base path `/api/v1`. Interactive docs at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/accounts` | Create an account (normal balance is derived from the type) |
| `GET` | `/accounts` | List, filter by type/status/currency, search by code or name |
| `GET` | `/accounts/{id}` | Account with its cached balance |
| `PATCH` | `/accounts/{id}` | Update descriptive fields and status only |
| `GET` | `/accounts/{id}/balance` | Balance **re-derived from entries**, plus cache agreement |
| `GET` | `/accounts/{id}/entries` | Account statement |
| `GET` | `/accounts/{id}/transactions` | Transactions touching this account |
| `POST` | `/transactions` | Post a balanced transaction (`Idempotency-Key` honoured) |
| `GET` | `/transactions` | List, filter by status/kind/currency/account/date, full-text |
| `GET` | `/transactions/{id}` | Transaction with every entry and reversal linkage |
| `GET` | `/transactions/by-reference/{ref}` | Lookup by client reference |
| `POST` | `/transactions/{id}/reverse` | Post a mirrored compensating transaction |
| `GET` | `/ledger/entries` | Ledger explorer: filter + keyset pagination |
| `GET` | `/ledger/entries/{id}` | One entry, traced to its transaction and account |
| `GET` | `/system/stats` | Real counts, trial balance, daily posting volume |
| `GET` | `/system/integrity` | Re-derives every balance and compares with the cache |
| `GET` | `/system/audit` | Append-only audit log |
| `GET` | `/system/currencies` | Supported currencies and minor-unit exponents |
| `GET` | `/system/health` | Liveness + database reachability |

### Posting a transaction

```bash
curl -X POST http://localhost:8000/api/v1/transactions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 7f1c2c9a-6c5e-4f4a-9a1e-5d2b0a2f9c31' \
  -H 'X-Actor: ops.priyanka' \
  -d '{
        "description": "Processor settlement",
        "currency": "USD",
        "entries": [
          {"account_id": "<bank>",     "direction": "DEBIT",  "amount_minor": 99600},
          {"account_id": "<fees>",     "direction": "DEBIT",  "amount_minor":   400},
          {"account_id": "<clearing>", "direction": "CREDIT", "amount_minor": 100000}
        ]
      }'
```

Replaying that exact request with the same key returns the original response with
`Idempotent-Replay: true` and posts nothing. Replaying it with a *different* body returns
`409 idempotency_key_reused` — the server refuses to guess which version was intended.

### Error shape

Every failure, from validation to lock timeout, returns the same envelope:

```json
{
  "error": {
    "code": "unbalanced_transaction",
    "message": "total debits must equal total credits",
    "details": { "total_debits_minor": 100000, "total_credits_minor": 99000,
                 "difference_minor": 1000, "currency": "USD" },
    "request_id": "0f9a1c7e5b2d4e77a1c3"
  }
}
```

Clients branch on `code`, never on prose.

---

## The console

Seven screens, all fed by real API data — there are no placeholder metrics anywhere.

- **Dashboard** — account/transaction/entry counts, per-currency trial balance, integrity
  check, 14-day posting volume, recent activity
- **Accounts** — filterable chart of accounts with derived balances
- **Account detail** — derived vs cached balance, full statement with per-entry balance
  effect, related transactions
- **Transactions** — the register, filterable by status, kind, currency and free text
- **Transaction detail** — the audit view: debits and credits side by side, independently
  totalled, with the balance equality asserted explicitly and the audit trail attached
- **New transaction** — multi-leg builder with live debit/credit totals, pre-flight checks
  mirroring the server's rules, and a visible idempotency key
- **Ledger explorer** — every entry, in classic debit/credit column form, with an auditor's
  filters
- **Audit log** — who did what, under which request id

Design system, information architecture and the debit/credit visual language:
[`docs/UI.md`](docs/UI.md).

---

## Project layout

```
backend/
  app/
    api/v1/       routers — request shape in, response models out, no business logic
    core/         config, money, structured errors, logging
    db/           engine, session factory, unit-of-work helpers
    models/       SQLAlchemy models + domain enums
    schemas/      Pydantic request/response models + explicit serializers
    services/     posting engine, balances, idempotency, accounts, queries, audit
    scripts/      seed data
  alembic/        migrations, including the immutability triggers
  tests/          invariant, immutability, idempotency, balance, reversal,
                  concurrency and API suites
frontend/
  src/
    components/   layout, UI primitives, ledger display atoms
    lib/          API client, types, money/date formatting, React Query hooks
    pages/        the seven screens
docs/             architecture, invariants, UI, deployment
```

---

## Notable engineering decisions

**Balances are derived, but there is still a cache.** `account_balances` is written inside
the same database transaction as the entries that change it, while the account row is
locked. It exists so listing 500 accounts does not become 500 aggregate scans, and so a
balance floor can be checked atomically. It is never authoritative: `/system/integrity`
re-derives every balance from entries and reports any divergence, and a test asserts the
two agree.

**Locks, not SERIALIZABLE.** Postings run at READ COMMITTED and lock each account they
touch with `SELECT … FOR UPDATE`, acquired in ascending account-id order. A global lock
ordering makes deadlock between overlapping transactions impossible, and contention stays
proportional to real contention — two transactions on disjoint accounts never block.
SERIALIZABLE would also be correct but pushes retry handling onto every caller and
serialises far more than the accounts actually involved.

**The database arbitrates idempotency.** The claim is `INSERT … ON CONFLICT DO NOTHING`
against a primary key, committed on its own connection before any ledger work starts.
Two simultaneous retries cannot both win, regardless of what the application believes.

**Immutability is structural.** Triggers reject `UPDATE`/`DELETE` on `ledger_entries` and
`audit_events`, and reject any change to a posted transaction other than the
`POSTED → REVERSED` transition. The guarantee holds for a psql session too, which is the
only version of the guarantee worth having.

**Reversals bypass balance floors, on purpose.** Refusing to reverse because the result
dips below zero would trap the ledger in a state it is not permitted to leave. A
correction must always be possible.

**No Redis, no Celery, no message queue.** Nothing in scope is asynchronous or needs a
cache tier. Adding them would be architecture as decoration.

---

## Deployment

Both images are production-shaped: multi-stage builds, non-root runtime users, health
checks. The intended AWS target is ECS Fargate behind an ALB with RDS PostgreSQL and
CloudWatch Logs — see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## CI

GitHub Actions runs, on every push and pull request: ruff lint and format checks, an
Alembic up/down round trip, the full pytest suite against a real PostgreSQL service
container, a TypeScript typecheck and production build of the console, and a build of both
container images.
