# Accounts, organizations and operations

LEDGR is a double-entry ledger with an operations product built *around* it. Nothing in
this layer posts money by its own means: the ledger (`posting.post_transaction`, balance
derivation, idempotency, append-only history, the PostgreSQL triggers) remains the single
financial source of truth, and every workflow below ends in that same engine.

This is a portfolio-grade reference implementation using synthetic data, **not** certified or
production-ready financial infrastructure. See [Known limitations](#known-limitations).

## Accounts, organizations and sessions

* **Signup** (`POST /auth/register`) creates an organization and its first member, an
  **OWNER**, in one transaction (a duplicate email rolls the organization back too). It
  grants access to no existing organization. `ALLOW_SIGNUP=false` turns it off;
  `SIGNUP_TOKEN` requires an invite code. Rate limited per client address.
* **Membership**: a user belongs to exactly one organization (their row *is* the
  membership). Owners and admins invite teammates under *Administration → Users & roles*.
* **Passwords**: salted **scrypt** (`hashlib.scrypt`, N=2¹⁴, r=8, p=1; parameters are stored
  in each hash so they can be raised later; stdlib/OpenSSL, so no extra dependency),
  minimum length 10. Plaintext is never stored or logged.
* **Login** (`POST /auth/login`) returns the same generic message for an unknown email, a
  wrong password, a locked or a deactivated account (no account enumeration; unknown emails
  cost the same time as wrong passwords). `LOGIN_MAX_FAILURES` consecutive failures lock
  the account for `LOGIN_LOCKOUT_MINUTES`; both endpoints are also throttled per client IP
  (`RATE_LIMIT_LOGIN_PER_MINUTE`, `RATE_LIMIT_REGISTER_PER_HOUR`, `429` + `Retry-After`).
* **Sessions are server-side**: an opaque token whose SHA-256 is stored in `user_sessions`,
  so logout, deactivating a user, a password change and expiry take effect on the very next
  request. Absolute lifetime `SESSION_TTL_MINUTES` (480), idle timeout
  `SESSION_IDLE_MINUTES` (60). `401 session_expired` is distinct from
  `401 invalid_credentials`.
* **The browser holds no credential that page scripts can read.** Login sets an
  **httpOnly** `ledgr_session` cookie (`SameSite` and `Secure` configurable). Because
  browsers attach cookies automatically, every state-changing cookie-authenticated request
  must carry the session's CSRF token in `X-CSRF-Token` (compared in constant time; `403`
  otherwise). The token is useless without the cookie and lives only in React memory, re-fetched from `GET /auth/me` after a reload.
  API clients may instead send `Authorization: Bearer <token or API key>`, which needs no
  CSRF token (it is not ambient). `X-Actor` is ignored: the recorded actor is the verified
  email or API-key label.
* `401` = no/invalid credential. `403` = authenticated but not permitted (the body names the
  missing permission). Requests for another organization's record answer `404`, never `403`.

## Roles and permissions

Enforced server-side on every request (`require(Permission.…)`; the matrix is
`app/core/permissions.py`). The console hides controls using the same list from
`GET /auth/me`, but hiding is only a convenience.

| Capability | OWNER | ADMIN | FINANCE | OPERATIONS | AUDITOR |
| --- | :-: | :-: | :-: | :-: | :-: |
| View accounts, statements, transactions, ledger | ✔ | ✔ | ✔ | ✔ | ✔ |
| View investigations, reconciliation, audit log, alerts, health, dashboard | ✔ | ✔ | ✔ | ✔ | ✔ |
| Create accounts | ✔ | ✔ | ✔ | | |
| Post / reverse / correct (subject to the approval threshold) | ✔ | ✔ | ✔ | | |
| Create & submit drafts | ✔ | ✔ | ✔ | | |
| Approve / reject drafts (never one's own) | ✔ | ✔ | ✔ | | |
| Work investigations, notes, import records, run reconciliation, record match decisions | ✔ | ✔ | ✔ | ✔ | |
| AI copilot | ✔ | ✔ | ✔ | ✔ | |
| Manage users, API keys, organization settings, demo data | ✔ | ✔ | | | |
| Grant/change ADMIN or OWNER; manage owners and admins | ✔ | | | | |

Mapping to the usual four-role model: **OWNER** and **ADMIN** administer; **OPERATOR** is
split into FINANCE (moves money) and OPERATIONS (investigates and reconciles); **READ_ONLY**
is AUDITOR, which holds no permission that creates, approves, posts or changes anything.

**No privilege escalation**: an ADMIN can only grant, change, reset or deactivate roles
*below* ADMIN, and can only mint API keys below ADMIN; only an OWNER manages owners and
admins. API keys can never hold OWNER. An organization always keeps at least one active OWNER.

## Tenant isolation

Every ledger and workflow table carries `organization_id`, derived from the authenticated
credential and never from anything the client sends. Three independent layers
(`app/db/tenancy.py`):

1. **Automatic read scoping, fail closed.** A SQLAlchemy `do_orm_execute` hook adds
   `organization_id = <caller's organization>` to every ORM SELECT/UPDATE/DELETE on a tenant
   table - joins, subqueries, aliases and relationship loads included. A session with no
   organization matches the nil UUID: it sees *nothing*.
2. **Write stamping and checking.** A `before_flush` hook stamps new rows with the session's
   organization, refuses a row that names another one, and refuses to re-parent a row.
3. **PostgreSQL constraints.** Composite foreign keys (e.g. `ledger_entries (account_id,
   organization_id) → accounts (id, organization_id)`) make a cross-organization reference
   impossible in the database whatever the application does; account codes, transaction
   references, idempotency keys, external record ids and API-key labels are unique **per
   organization**; a transaction's `organization_id` is frozen by the immutability trigger.

**What this is not**: PostgreSQL row-level security. The app connects as one database role, so
raw `text()` SQL or Core-table queries bypass layers 1-2 (one such bug - `duplicate_candidates`
- was found by the live demo and fixed; a static test now requires any module using
`Model.__table__` to mention `organization_id`). `tests/test_tenant_isolation.py` probes every
endpoint family from a second organization and checks the database constraints directly.

Users, organizations and sessions are *not* auto-scoped (login must find a user before it knows
the tenant); `app/services/users.py` filters by organization explicitly and answers `404` for
another organization's user/key.

## Maker-checker

```
DRAFT ──submit──▶ PENDING_APPROVAL ──approve──▶ POSTED
                         └────────reject────────▶ REJECTED
```

* A draft (`transaction_drafts`) is a **proposal**: no ledger entries, no balance effect.
* **Approve** locks the draft row (`FOR UPDATE`), then calls the existing
  `posting.post_transaction(…, auto_commit=False)` and updates the draft in the *same*
  database transaction, re-validating everything under the usual account locks. Concurrent
  approvals serialise: one posts, the rest get `409`. If the ledger refuses (e.g. insufficient
  funds) nothing is posted and the draft stays pending with the refusal recorded.
* **Separation of duties**: the identity that created a draft can never approve it
  (`403 self_approval_forbidden`) - including an OWNER.
* **Policy**: `organizations.approval_threshold_minor` - a direct `POST /transactions` or
  `/correct` at or above it is refused with `409 approval_required`. Reversals are not gated.
* Decisions are immutable in the audit trail (`draft.approved` / `draft.rejected` with
  requester, decider and reason); a CHECK ties status `POSTED` to a real transaction.

## API keys

Created and revoked in *Administration → API keys* (or
`python -m app.scripts.create_api_key <organization> <label> [ROLE]`). Each key belongs to one
organization and carries a role, creator, creation time, last-used time and optional
revocation. Only a hash is stored; the plaintext appears once, in the creation response. They
are for integrations and scripts - **not** the human login.

## Reconciliation workspace, investigations and notes

* Reconciliation is deterministic and read-only with respect to the ledger. **Ambiguous
  matches stay ambiguous.** Statuses: matched, amount mismatch, missing in ledger, missing
  externally, ambiguous.
* *Import*: CSV file or paste → column mapping → per-row validation preview → import
  (idempotent by source + external id). Nothing is sent until the preview is clean.
* *Suggestions*: `GET /reconciliation/results/{id}/suggestions` lists candidate ledger
  transactions with explicit reasons (amount equality, id mentioned, date proximity).
  Suggestions are never applied.
* *Decisions*: a person may record `CONFIRMED_MATCH` (validated: same currency, live
  transaction) or `NOT_A_MATCH` with a reason. Append-only, audited, and it does **not**
  change the deterministic result or the ledger.
* *Investigations*: OPEN → INVESTIGATING → RESOLVED, priority, linked reconciliation
  result, **append-only notes**, evidence (balance change, lineage, integrity), audit trail,
  and a downloadable Markdown evidence report built only from real records.
* *Related records*: `GET /transactions/{id}/related` links a transaction to its
  reconciliation results, cases and decisions.

## Audit trail

`audit_events` is append-only (database triggers reject UPDATE/DELETE) and the API exposes no
write route. Events are written by the real actions below, in the same transaction as the
change they describe - except refusals, recorded on an independent connection because their
own request rolled back. Each event carries the verified actor, resource, request id
(`X-Request-ID`) and a JSON payload that never contains passwords, tokens, keys or AI content.

| Area | Event types |
| --- | --- |
| Auth | `auth.login`, `auth.login_failed`, `auth.logout`, `auth.permission_denied`, `organization.registered` |
| Users & org | `user.created`, `user.updated`, `user.password_reset`, `user.password_changed`, `organization.updated` |
| API keys | `api_key.created`, `api_key.revoked` |
| Ledger | `account.created`, `account.updated`, `transaction.posted`, `transaction.reversed` |
| Rejections | `transaction.post_failed`, `idempotency.replay`, `idempotency.rejected` |
| Maker-checker | `draft.created/updated/submitted/approved/rejected`, `draft.approval_failed`, `draft.self_approval_blocked` |
| Casework | `investigation.created/status_changed/resolved/note_added`, `reconciliation.imported/run_completed/decision_recorded` |
| Other | `export.transactions`, `ai.copilot_used` (task name only), `demo.loaded` |

Failed sign-ins for an *unknown* email belong to no organization and are visible to none.

## Alerts, health and readiness

* **Alerts** (`app.services.alerts`) are deterministic rules over the caller's own data -
  repeated retries, idempotency misuse, failed postings, large transactions, repeated
  reversals, reconciliation exceptions, accounts near their balance floor, integrity failure,
  repeated login failures and permission denials. Computed on demand, read-only, no AI.
  Thresholds are `ALERT_*` settings.
* `GET /system/live` - the process is up. `GET /system/ready` - the database answers and
  the schema is at the revision this build expects (`503` otherwise). Both are public and
  tenant-agnostic. `GET /system/health/detail` (authenticated) adds the *organization's*
  ledger-integrity checks, latest reconciliation and recent server errors. A server that
  merely answers HTTP is not reported healthy.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local compose DB | SQLAlchemy URL (PostgreSQL only) |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:4173` | Console origins allowed to call the API **with the session cookie**. A `*` entry stops the API at startup |
| `ALLOW_SIGNUP` / `SIGNUP_TOKEN` | `true` / empty | Self-service signup / optional invite code |
| `SESSION_COOKIE_NAME` | `ledgr_session` | Cookie name |
| `SESSION_COOKIE_SAMESITE` / `SESSION_COOKIE_SECURE` | `lax` / `false` | Use `none` + `true` when console and API are on different sites (HTTPS only). `none` without `true`, or an unknown value, stops the API at startup |
| `SESSION_TTL_MINUTES` / `SESSION_IDLE_MINUTES` | 480 / 60 | Session lifetime / idle timeout |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_MINUTES` | 5 / 15 | Account lockout |
| `RATE_LIMIT_LOGIN_PER_MINUTE` / `RATE_LIMIT_REGISTER_PER_HOUR` | 10 / 10 | Per-IP throttles |
| `MIN_PASSWORD_LENGTH` | 10 | Password policy |
| `ALERT_WINDOW_HOURS`, `ALERT_REPEAT_THRESHOLD` | 24, 3 | Alert window / repeat threshold |
| `ALERT_LARGE_TRANSACTION_MINOR` | 10,000,000 | "Large transaction" size (minor units) |
| `ALERT_BALANCE_FLOOR_WARNING_MINOR` | 50,000 | Balance-floor warning distance |
| `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY`, `AI_BASE_URL` | empty (off) | Optional AI (`gemini`, `anthropic` or `openai`) |
| `AI_REQUEST_TIMEOUT_SECONDS`, `AI_RATE_LIMIT_PER_MINUTE` | 30, 20 | Provider timeout; per-user copilot throttle |
| `BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | empty | Seed script owner for the demo org (a random password is generated if unset) |

Secrets belong in the environment (never in source); `.env.example` has placeholders only.

## Known limitations

* **Application-layer tenancy, not row-level security** (see above). One database role; raw SQL
  is outside the automatic filter.
* **One organization per user**, no cross-organization membership or organization switcher.
* **In-process rate limiting**: per API process, resets on restart; a multi-instance deployment
  needs limiting at the edge as well. Per-account lockout lets an attacker lock a known account out
  for a while. Duplicate-email signup reveals that the email exists (no email verification).
* **No MFA, SSO, email verification or self-service password reset**; admins reset passwords.
* **Cookie sessions need a deployment where the console can reach the API with credentials**
  (same site, or SameSite=None + HTTPS); an httpOnly cookie is also unavailable to non-browser
  clients, which use bearer tokens/API keys.
* **Idempotency keys are scoped per organization**, not per caller; use unguessable (UUID) keys.
* **Approval threshold** compares minor units per transaction currency (no FX); reversals are not
  approval-gated.
* **Alerts** are computed per request, not persisted, acknowledged or routed.
* **In-process request counters / recent-error buffer** are per process and reset on restart. They
  are shared by every organization: they hold only totals and route *templates*
  (`/transactions/{transaction_id}`, at most 200 distinct), never raw URLs or ids, but the totals
  do include other tenants' traffic.
* **Behind a reverse proxy** the per-IP limiter sees the proxy's address unless uvicorn is told to
  trust it (`FORWARDED_ALLOW_IPS`); do that only when the proxy is the sole route to the API.
  Failed-login counting is read-modify-write, so a burst of parallel guesses can slightly exceed
  `LOGIN_MAX_FAILURES` before the lockout lands (the per-IP limiter bounds the burst).
* **An ADMIN-role API key outlives the person who created it** until someone revokes it.
* **Generated demo passwords** (seed script, when `BOOTSTRAP_ADMIN_PASSWORD` is unset) are printed
  once to the migrate container's log; treat that log as sensitive and rotate the password.
* **Case numbers** (`INV-0001`) come from a global sequence, so they reveal only how many cases
  exist across all organizations.
* **The AI copilot has only been exercised against a scripted provider in tests**; a live
  Gemini/Anthropic/OpenAI call was not run in the automated verification.
* **Not a regulated system**: no environment segregation, key-management service, tamper-evident
  external log shipping, penetration test or independent security review.

## Security review (v1.0)

A pre-release review of authentication, sessions/CSRF, role permissions, tenant scoping, the AI
endpoints and migration 0008 found and fixed the issues below; each has a regression test in
`backend/tests/test_security_audit.py` (or `test_migration_0008.py`).

| Finding | Fix |
| --- | --- |
| A client-supplied `X-Request-ID` longer than 64 characters made every audited write fail with a 500 (it is stored in 64-character columns) and was echoed unvalidated into headers and logs | Only `[A-Za-z0-9._-]{1,64}` is accepted; anything else is replaced |
| Error metrics were keyed by raw URL: process memory grew with every distinct 404 path (unauthenticated), and the health/metrics views could show another tenant's resource ids | Keyed by route template, bounded to 200 keys |
| Migration 0008 downgrade left `OWNER` users the previous release cannot read | Owners step down to ADMIN on downgrade; upgrading promotes the oldest ADMIN back |
| Four AI endpoints (`ask`, `propose`, `explain`, `ledger-brief`) were not rate limited; `days` was unbounded | Per-caller limit on all of them; `days` limited to 1-90 |
| `CORS_ORIGINS=*` or `SameSite=None` without `Secure` were accepted with credentialed cookies | Refused at startup |
| Database port was published on all interfaces with the demo credentials | Published on loopback only (IPv4 and IPv6; IPv4-only made `localhost` stall ~8 s per connection on Windows) |
| Frontend threw a raw `SyntaxError` on a non-JSON (gateway) response | Reported as an `ApiError` |

New guards rather than fixes: a sweep that every published route rejects an unauthenticated call
(except the six documented public ones); a sweep that every cookie-authenticated write without a
CSRF token is refused; a sweep that a second organization's ADMIN gets 404 for every
id-addressed route using real ids from the first and changes nothing; a test that the AI package
calls only a reviewed list of read-only service functions; and a populated-database test that
seeds a scratch database, walks migration 0008 down and up, and checks no row is lost, orphaned or
re-assigned.
