# LEDGR — Financial Ledger & Reconciliation Platform

A multi-tenant, double-entry financial ledger and the operations product around it:
accounts and organizations, role-based access, maker-checker approvals, account statements,
reconciliation and investigation workspaces, an append-only audit log, deterministic alerts,
system health, and an optional, read-only AI copilot.

Money is recorded as balanced sets of debit and credit entries. Ledger entries are
append-only, account balances are **derived** from those entries rather than stored as a
mutable running total, posting is atomic, writes are idempotent, and corrections are made
by posting reversals — never by editing history. Each organization's data is isolated from
every other's.

> A portfolio-grade reference implementation using **synthetic data**. It is not certified,
> audited or production-ready financial infrastructure — see
> [known limitations](docs/OPERATIONS.md#known-limitations).

```
FastAPI + SQLAlchemy 2.0  ·  PostgreSQL 16  ·  React + TypeScript + Vite  ·  Docker  ·  GitHub Actions
```

---

## Screenshots

Captured from the running application with synthetic data (a second organization, signed in as its owner):

| | |
| --- | --- |
| ![Dashboard](docs/screenshots/dashboard.jpg) | ![Reconciliation workspace](docs/screenshots/reconciliation.jpg) |
| *Dashboard — real figures, stated windows, no fake zeros* | *Reconciliation — matched / mismatched / missing / ambiguous* |
| ![Exception review](docs/screenshots/reconciliation-exception.jpg) | ![Investigation](docs/screenshots/investigation.jpg) |
| *Ambiguous exception: nothing chosen; suggestions are for review* | *Investigation: append-only notes, evidence, AI panel* |
| ![Approvals](docs/screenshots/approvals.jpg) | ![Audit log](docs/screenshots/audit-log.jpg) |
| *Maker-checker approval* | *Audit log (filterable, append-only)* |

## What is implemented

| Area | Status |
| --- | --- |
| Double-entry ledger, atomic posting, idempotency, locking, reversals/corrections, immutability triggers | Implemented, tested against real PostgreSQL |
| Signup / login / logout, httpOnly cookie sessions + CSRF, server-side revocable sessions, lockout, rate limiting | Implemented, tested |
| Organizations, OWNER / ADMIN / FINANCE / OPERATIONS / AUDITOR roles, backend-enforced permissions, no privilege escalation | Implemented, tested |
| Tenant isolation (automatic scoping + composite foreign keys + per-organization uniqueness) | Implemented; probed from a second organization in `tests/test_tenant_isolation.py` |
| API keys (scoped, hashed, revocable) for integrations | Implemented |
| Maker-checker approvals, organization approval threshold | Implemented, incl. concurrency and failure-injection tests |
| Dashboard, account statements, transaction search/export (CSV), lineage and related records | Implemented |
| Reconciliation workspace: CSV import with mapping + preview, deterministic matching, ambiguity preserved, suggestions, recorded decisions | Implemented |
| Investigation centre: workflow, priority, append-only notes, evidence, downloadable report | Implemented |
| Audit log UI, deterministic alerts, operations inbox, liveness/readiness/health | Implemented |
| AI copilot (Gemini/Anthropic/OpenAI): cited drafts, hypotheses separated, no actions | Implemented and tested with a scripted provider; **no live model call is part of the automated tests** |
| **Meridian Commerce**: a fictional Indian trading company with six months of history, bank statements, exceptions, cases, approvals and a 12-exercise learning guide | Implemented; see [`docs/MERIDIAN.md`](docs/MERIDIAN.md) |
| Row-level security, MFA/SSO, email verification, webhooks, period-end close, multi-org membership | **Not implemented** |

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
| Tenancy | Every row belongs to one organization; cross-organization references are rejected by PostgreSQL |

---

## Quick start

### With Docker (everything)

```bash
docker compose up -d --build
```

This starts PostgreSQL, applies migrations (see [Migrations](#migrations)), seeds a synthetic
**"LEDGR Demo Company"** organization (22 accounts, ~190 transactions), then serves:

| Service | URL |
| --- | --- |
| Operations console | http://localhost:8080 (set `WEB_PORT` and `CORS_ORIGINS` to use another port) |
| API | http://localhost:8000/api/v1 |
| OpenAPI docs | http://localhost:8000/docs |
| Liveness / readiness | `/api/v1/system/live` · `/api/v1/system/ready` |
| PostgreSQL | `localhost:5433` - published on the loopback interface only, IPv4 and IPv6 (user/password/db: `ledgr`; change them for anything beyond a local demo) |

**Sign in:** either **create your own organization** on the console's *Create account* tab
(you become its OWNER), or use the demo owner `owner@demo.ledgr.test` — its password is
`BOOTSTRAP_ADMIN_PASSWORD` if you set one, otherwise a random one printed once in
`docker logs ledgr-migrate`. Then follow [`docs/DEMO.md`](docs/DEMO.md), or run the automated
walkthrough:

```bash
cd backend && python -m app.scripts.demo_e2e
```

### Learn LEDGR on a fictional company

LEDGR ships a complete, **entirely synthetic** organization to operate as a finance and operations team
would: *Meridian Commerce Pvt. Ltd.*, an invented Indian B2B trading company (INR, ~270 postings over six
months, 68 bank-statement records, eight cases, maker-checker approvals, a twelve-exercise learning guide
whose progress is verified against the real books). Nothing real is involved: no real company, bank,
customer or money.

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://ledgr:ledgr@localhost:5433/ledgr
python -m app.scripts.seed_meridian seed      # create / complete (idempotent; never touches other organizations)
MERIDIAN_DEMO_PASSWORD='choose-10+-chars' python -m app.scripts.seed_meridian users   # local demo logins
python -m app.scripts.seed_meridian status
```

Sign in through the normal login page as `meera.krishnan@meridian-demo.test` (owner) - the other roles,
the approval flow, the bank-statement import and the exercises are in
[`docs/MERIDIAN.md`](docs/MERIDIAN.md). The seed refuses to run against an unmarked organization of the
same name, never deletes anything, and `users` refuses to run when `ENVIRONMENT=production`.

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
pytest -q                       # full backend suite, against real PostgreSQL
pytest -m concurrency -q        # only the concurrent-session tests
ruff check app tests && ruff format --check app tests
cd ../frontend
npm run typecheck && npm test && npm run build
```

Backend tests run against real PostgreSQL in a `ledgr_test` database that the harness creates
and rebuilds through Alembic. They deliberately do **not** use SQLite: row locks, native enums,
composite foreign keys and the immutability triggers do not exist there, so a green SQLite suite
would prove nothing about the system that ships. Test counts are whatever the commands above
report on your machine; this README does not hard-code them.

### Migrations

```bash
cd backend
alembic upgrade head      # apply
alembic downgrade -1      # step back one
```

`0008_multi_tenancy` adds `organization_id` to the ledger tables **without deleting data**: every
existing row is assigned to the oldest existing organization (or a "Legacy organization" if there
was data but none), and the oldest ADMIN of each organization becomes its OWNER. Downgrade removes the tenant columns but fails — rather than merge
data — if two organizations now share an account code or transaction reference. See the
migration's docstring.

### Environment variables

Full table in [`docs/OPERATIONS.md`](docs/OPERATIONS.md#configuration); placeholders in
[`.env.example`](.env.example). Never commit real secrets.

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
| I13 | Σ debits = Σ credits across each organization's ledger, per currency | verified by `/system/stats` and `/system/integrity` |
| I14 | A ledger entry, balance row, reconciliation result or case can only reference records of its own organization | composite foreign keys in PostgreSQL (application-layer scoping is a second line) |
| I15 | Account code, transaction reference, idempotency key and external record id are unique per organization | per-organization unique constraints |

Full detail, including which test covers each: [`docs/INVARIANTS.md`](docs/INVARIANTS.md).

---

## Architecture

```
  Browser ── httpOnly session cookie + CSRF header ──┐
  Scripts ── Authorization: Bearer <API key> ────────┤
                                                     ▼
                    ┌────────────────────────────────────────────┐
                    │  FastAPI                                    │
                    │   api/v1     thin routers, require(PERM)    │
                    │   deps       principal → organization       │
                    │   services   ALL invariants + unit of work  │
                    │   ai         read-only copilot (no tools)   │
                    │   db/tenancy automatic per-org scoping      │
                    └───────────────────┬────────────────────────┘
                                        │ psycopg 3
                    ┌───────────────────▼────────────────────────┐
                    │  PostgreSQL 16                              │
                    │   constraints · composite FKs · row locks   │
                    │   immutability triggers · per-org uniques   │
                    └─────────────────────────────────────────────┘
        optional: Gemini / Anthropic / OpenAI  ◀── JSON in, JSON out, never writes
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

Every table in the diagram above also carries an `organization_id` (see
[`docs/OPERATIONS.md`](docs/OPERATIONS.md#tenant-isolation)); entries, balances, results and cases
reference only rows of their own organization via composite foreign keys.

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
| `GET` | `/system/health` | Liveness + database reachability (public) |
| `GET` | `/system/health/detail` | Checked-now component status: DB latency, schema revision, integrity, reconciliation, recent errors |
| `GET` | `/accounts/{id}/statement` | Entries with a DB-computed running balance; date/side/text filters |
| `POST` | `/auth/register`, `/auth/login`, `/auth/logout` · `GET` `/auth/me`, `/auth/config` | Signup (new organization), sessions (opaque, server-side, revocable) |
| `POST` | `/transaction-drafts` · `…/{id}/submit` · `…/approve` · `…/reject` | Maker-checker; approval uses the normal posting engine |
| `GET` | `/dashboard`, `/alerts`, `/onboarding` | Real-data dashboard, deterministic alerts, setup checklist |
| `*` | `/users`, `/api-keys`, `/organization` | Administration (ADMIN only) |

Every endpoint except `/system/health`, `/system/live`, `/system/ready`, `GET /auth/config`,
`POST /auth/register` and `POST /auth/login` requires credentials: either
`Authorization: Bearer <session token or API key>` (scripts and integrations) or the httpOnly
session cookie plus an `X-CSRF-Token` header on writes (the browser console). Each endpoint
declares the permission it needs; the full role matrix is in
[`docs/OPERATIONS.md`](docs/OPERATIONS.md). A test enumerates every published route and fails
if one is reachable without credentials.

### Posting a transaction

```bash
curl -X POST http://localhost:8000/api/v1/transactions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 7f1c2c9a-6c5e-4f4a-9a1e-5d2b0a2f9c31' \
  -H 'Authorization: Bearer ledgr_...' \   # an API key with a FINANCE/ADMIN role, or a session token
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

Every screen is fed by real API data — there are no placeholder metrics anywhere — and the
navigation shows only what the signed-in role may use (the server enforces it regardless).

- **Dashboard** — accounts, 24h activity, approvals waiting, open investigations, the latest
  reconciliation counts, ledger-integrity status, active alerts, recent audit activity
- **Accounts / statements** — chart of accounts; per-account statement with running balance,
  filters and CSV export of the visible page
- **Transactions & New transaction** — register and builder; finance users post directly,
  or *Submit for approval* (required above the organization's threshold)
- **Approvals** — the maker-checker queue: approve (posts via the normal engine) or reject
  with a reason; you can never approve your own draft
- **Investigations** — priority, evidence (balance change, lineage, integrity), the
  reconciliation exception it came from, and a real audit trail
- **Reconciliation workspace** — sources, runs, matched / mismatched / missing / ambiguous
  counts, exception drill-down; never modifies the ledger, ambiguous stays ambiguous
- **Alerts · Audit log · System health** — deterministic rules, filterable append-only
  events, live component checks
- **Administration** — users & roles, API keys (shown once, revocable), organization policy
- **Flight Recorder · Ledger explorer · LEDGR Intelligence** — as before (AI is read-only and
  proposal-only; it makes no financial decisions)

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
    services/     posting engine, balances, idempotency, accounts, auth, users, drafts,
                  reconciliation, casework, alerts, statements, ai/ (copilot, tools, provider)
    scripts/      demo seed, API-key CLI, end-to-end demo
  alembic/        migrations, including the immutability triggers
  tests/          invariant, immutability, idempotency, balance, reversal,
                  concurrency and API suites
frontend/
  src/
    components/   layout, UI primitives, ledger display atoms
    lib/          API client, types, money/date formatting, React Query hooks
    pages/        console screens (dashboard, approvals, reconciliation, investigations, admin...)
docs/             architecture, invariants, operations, AI, demo, UI, deployment
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

### Deployment prerequisites

* PostgreSQL 16 and `DATABASE_URL`; migrations run before each release (`alembic upgrade head`).
* HTTPS, with `CORS_ORIGINS` listing the console's exact origin(s). If the console and API are on
  different sites (e.g. two `onrender.com` subdomains) set `SESSION_COOKIE_SAMESITE=none` and
  `SESSION_COOKIE_SECURE=true`; same-site deployments keep the `lax` default. `render.yaml` does this.
* Decide on signup: `ALLOW_SIGNUP=false`, or `SIGNUP_TOKEN` as an invite code. Never expose an open
  signup on a public URL you did not mean to.
* Edge rate limiting: the built-in limiter is per process (see limitations).
* Optional AI: `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY` as secrets.

## CI

GitHub Actions runs, on every push and pull request: ruff lint and format checks, an
Alembic up/down round trip, the full pytest suite against a real PostgreSQL service
container, a TypeScript typecheck and production build of the console, and a build of both
container images.
