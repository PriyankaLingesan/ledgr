# Meridian Commerce Pvt. Ltd. — a fictional organization to learn LEDGR on

> **Everything here is invented.** The company, its customers and suppliers, the people, the
> "bank", every reference number and every rupee are synthetic. There is no real bank
> connection, no real customer activity, no tax filing and no payment processing. LEDGR is a
> reference implementation; this simulation is a learning aid, not a compliance tool.

Meridian lets you operate LEDGR the way a finance and operations team would: post and search
transactions, retry a payment safely, import a bank statement, work reconciliation exceptions,
open investigations, push a large payment through maker-checker approval, trace a reversal, and run
the integrity check — on a ledger with six months of history.

## What it looks like

| | |
| --- | --- |
| ![Dashboard](screenshots/meridian-dashboard.jpg) | ![Learning guide](screenshots/meridian-learning-guide.jpg) |
| *Dashboard of the demo organization: banner, Demo tag, real figures in rupees* | *The learning guide: progress, people, exercises* |
| ![Reconciliation](screenshots/meridian-reconciliation.jpg) | |
| *Run #1: 37 matched, 2 mismatches, 4 missing from the ledger, 3 missing from the bank, 1 ambiguous* | |

(Captured from the running app against a throw-away copy of the seed.)

## The company

Meridian Commerce Pvt. Ltd. is an invented Indian B2B trading and distribution company. It:

* **buys** industrial supplies (fasteners, bearings, resins, lubricants, packaging, electricals) from
  seven suppliers **on credit**, and
* **sells** them to twelve business customers **on credit**, then
* **collects** by bank transfer (NEFT/RTGS, cheque, UPI) and **pays** suppliers the same way,
* pays warehouse rent, 14 salaries, utilities, freight, loan interest and bank charges, and
* **reconciles its operating bank account** against a (synthetic) bank statement.

All amounts are in INR (₹) and shown with Indian digit grouping (`12,34,567.00`). Posting at or above
**₹10,00,000** needs a second person (maker-checker).

### What LEDGR does and does not model here

| Modelled with the ledger | Not modelled (and not pretended) |
| --- | --- |
| Sales invoices (debit receivables, credit sales and GST output) | No invoice documents, numbering rules or e-invoicing |
| Customer receipts, supplier bills, supplier payments | No customer/supplier sub-ledgers: names are in descriptions (one receivables and one payables control account) |
| Stock at cost, and a monthly cost-of-goods-sold journal | No stock quantities or item master |
| GST lines as **illustrative arithmetic** (18%) in the seed | **No tax computation.** TDS appears only as a ledger account and one explained short-payment |
| Refunds, reversals, corrections, internal transfers, bank fees | No bank connectivity; the statement is a CSV you import |

## Set it up

You need the usual LEDGR stack running (`docker compose up -d`, see the [README](../README.md)) and the
backend virtual environment. All commands run from `backend/`; nothing is deleted or reset.

```bash
# 1. Create (or complete) the organization and its history. Safe to run repeatedly.
DATABASE_URL=postgresql+psycopg://ledgr:ledgr@localhost:5433/ledgr \
  python -m app.scripts.seed_meridian seed

# 2. Give the five demo people logins (LOCAL USE ONLY). The password comes from the environment
#    or a hidden prompt - it is never printed, logged or stored in the clear.
MERIDIAN_DEMO_PASSWORD='choose-a-password-of-10+-chars' \
DATABASE_URL=postgresql+psycopg://ledgr:ledgr@localhost:5433/ledgr \
  python -m app.scripts.seed_meridian users

# 3. See what exists
DATABASE_URL=... python -m app.scripts.seed_meridian status
```

The first `seed` anchors the timeline to *today*; the anchor is recorded in the audit log and re-runs
reuse it, so the same command always yields the same logical scenario (`--as-of YYYY-MM-DD` pins a
date on the first run). The history ends the day before the anchor.

### Signing in (the real login flow)

1. Open the console (`http://localhost:8080`, or the `WEB_PORT` you chose) → **Sign in**.
2. Use any e-mail from the table below and the password you set in step 2.
3. A permanent **“Fictional demonstration”** banner and a **Demo** tag mark the organization, and a
   **Learning guide** entry appears in the sidebar.

There is no bypass: the demo people are ordinary users with ordinary roles. Until you run step 2
they are *inactive with no usable password*. `users` never overwrites an existing password, refuses to
run when `ENVIRONMENT=production`, and the same password applies to all five logins, so keep it
local. You can also create or reset users yourself as the owner (**Administration → Users & roles**).

| Person | Role | Why you would sign in as them |
| --- | --- | --- |
| **Meera Krishnan** `meera.krishnan@meridian-demo.test` | OWNER | Everything; set the approval threshold; manage users |
| **Kavya Menon** `kavya.menon@meridian-demo.test` | FINANCE | *Maker*: records transactions and proposes large ones. Cannot approve her own |
| **Rohan Iyer** `rohan.iyer@meridian-demo.test` | FINANCE | *Checker*: approves other people's drafts. Cannot approve his own |
| **Ishita Rao** `ishita.rao@meridian-demo.test` | OPERATIONS | Runs reconciliation, records decisions, works cases. Cannot post or approve |
| **Nikhil Shah** `nikhil.shah@meridian-demo.test` | AUDITOR | Read-only, including the audit log. Cannot change or approve anything |

### The approval flow

`direct posting ≥ ₹10,00,000 → refused (approval_required)` → **maker** creates a draft and submits it →
the draft waits in **Approvals** → a **different** finance person approves → the posting engine posts it
atomically. Self-approval, an Operations login and an Auditor login are all refused by the server. The
history already contains 15 approved drafts, one rejected draft (with the checker's reason) and one
pending payment of ₹11,80,000 waiting for a second person.

## The synthetic bank statements

The operating account's statement is split in two, deliberately:

| Statement | Window | Where it is |
| --- | --- | --- |
| **Statement 1** (44 records) | days −74 … −30 | **Already imported and reconciled** by the seed (run #1) |
| **Statement 2** (24 records) | days −29 … −1 | **A CSV you import yourself** (exercise 5) |

Get statement 2 from the guide's **Download** button, or:

```bash
python -m app.scripts.seed_meridian export-sample meridian_bank_statement_2.csv
```

`docs/demo/meridian_bank_statement_2.csv` is a copy generated for the seed made while this was written;
dates and bank ids depend on your anchor date, so use the guide's download for *your* organization.
Columns: `external_id, amount, currency, direction, timestamp, description` — the import wizard maps them
automatically (`credit` = money in, `debit` = money out, from the bank's point of view).

### What statement 1 contains (run #1) — and why

The reconciliation engine matches on the bank's reference and compares amounts. It never posts anything
to force agreement, and it never guesses between two candidates.

| Story | Engine result | Why it is realistic | Case |
| --- | --- | --- | --- |
| 37 ordinary receipts, payments, salaries… | `MATCHED` | The ledger carries the bank's reference | — |
| **Bank fee nobody recorded** (₹1,180) | `MISSING_INTERNAL` | The bank charges before you book it | INV-0001 resolved: fee posted |
| **Customer short-pay** (TDS: ₹5,80,000 vs ₹5,90,000) | `AMOUNT_MISMATCH` −₹10,000 | Customers withhold tax | INV-0002 resolved: reversed, re-recorded net, TDS moved |
| **Reference typo** (two digits swapped) | `MISSING_INTERNAL` + `MISSING_EXTERNAL` | A pair of orphans that are really one payment | INV-0003 resolved: `CONFIRMED_MATCH` decision |
| **Duplicate bank debit** (₹2,36,000 twice, different bank ids) | `MISSING_INTERNAL` (the twin) | The bank re-presented a payment | INV-0004 **open**, investigating |
| **Receipt recorded, bank never credited** (₹4,72,000) | `MISSING_EXTERNAL` | Booked before the money arrived | INV-0005 **open** |
| **Unidentified credit** (₹2,24,200) | `MISSING_INTERNAL` | No invoice reference on the transfer; a same-amount invoice is open | INV-0006 **open** |
| **Same bank reference on two ledger receipts** | `AMBIGUOUS_INTERNAL_MATCH` | A receipt keyed twice | INV-0007 **open** |
| **Supplier discount taken** (bank paid ₹5,000 less) | `AMOUNT_MISMATCH` −₹5,000 | Prompt-payment discount not yet booked | **No case** — you open it (exercise 8) |
| **Cheque not yet presented** | `MISSING_EXTERNAL` | A valid timing difference | none: it clears on statement 2 |
| Duplicated electricity bill, reversed next day | — (statement 2) | A mistake corrected the supported way | INV-0008 **open**: reversal needs review |

The explanations in the cases are a person's reasoning; the engine's own `explanation` field is
the authoritative result and the two are kept apart (and AI never writes either).

## The guided learning path

**Learning guide** (sidebar) walks through twelve exercises. Each has business context, the exact task, where
to go, what to inspect, what a correct result looks like, and the concept behind it. **Progress is verified
by the server against the real state of the books** — viewing a page never completes anything, and the
expected answers are computed from live data when you submit and are never sent to the browser. A passed
exercise is recorded in the audit log (`demo.exercise_completed`), per person.

| # | Exercise | How it is checked |
| --- | --- | --- |
| 1 | Chart of accounts and opening position | You enter the opening Inventory debit; compared with the ledger entry |
| 2 | Find a customer receipt, read its debit and credit | You enter its reference |
| 3 | Search and filter transactions | You enter the count for a given search, date range and status |
| 4 | Retry a posting safely (idempotency) | Needs an `idempotency.replay` event from you (use the in-page **Idempotency lab**) |
| 5 | Import the statement | All 24 statement-2 records must exist as external records |
| 6 | Investigate an unmatched bank fee | A posted ₹590 transaction, Dr Bank Charges / Cr bank, with the bank's reference |
| 7 | Resolve the ambiguous match using evidence | A `CONFIRMED_MATCH` decision on the **original** receipt, not the duplicate |
| 8 | Open an investigation, record a reasoned resolution | A case you opened on the supplier-discount mismatch, resolved with a 20+ character note |
| 9 | Submit a sensitive operation for approval | A posted draft whose approver differs from its creator |
| 10 | Inspect a reversal and trace its original | You enter the original's reference |
| 11 | Run the integrity check | You enter how many checks pass |
| 12 | Use the AI copilot to explain a transaction | An `ai.copilot_used` event from you — **only possible if live AI is configured** |

### Recommended first session (about 45 minutes)

1. As **Meera**: exercises 1–3 (read the books), then look at the dashboard, the audit log and System health.
2. As **Kavya**: exercise 4 (Idempotency lab), then import statement 2 as **Ishita** (exercise 5) and run reconciliation.
3. As **Kavya**: record the unmatched bank fee (6). As **Ishita**: ambiguous match (7), then the supplier-discount case (8).
4. As **Kavya** propose a ≥ ₹10 lakh purchase; try to approve it as Kavya, then as Ishita (both refused); approve as **Rohan** (9).
5. As **Meera**: reversal trace (10), integrity (11). As **Nikhil** (auditor): try to change anything and read the audit log.

## Is there a reset?

**No — deliberately.** Ledger history is append-only and enforced by database triggers; a scoped reset
would mean disabling those triggers and deleting rows, which this project does not do. Re-running `seed` is
safe and changes nothing. To start again you can seed a second copy under a different name
(`seed` currently supports one Meridian per database), or on a throw-away local database use
`docker compose down -v` (**this deletes the whole local database**, every organization included).

## AI copilot

The guide works fully without AI; exercise 12 is the only one that needs it and says so rather than
pretending. To enable it, set `AI_PROVIDER`, `AI_MODEL` and `AI_API_KEY` where the API runs (see
[AI.md](AI.md)). The copilot reads only the signed-in organization's records, can only draft text, and
cannot post, approve, match or resolve anything. Every use is audited (task only, never content).

## How the seed stays safe

* It only ever writes to an organization carrying the marker `demo_scenario = 'meridian-v1'` **and** the
  Meridian name. An unmarked organization that happens to share the name stops it; it is never adopted.
* It never deletes, resets or modifies any other organization, and refuses if a demo e-mail already belongs
  to an existing user.
* Everything goes through LEDGR's services: balanced double entry, the approval rules with two real
  people, reversals through `reverse_transaction`, bank records through the idempotent import, matching by
  the reconciliation engine, decisions and cases through their services.
* The history is back-dated through one narrow, service-level parameter (`posted_at`) that **no API
  path can set**; every simulated transaction is tagged `simulated` in its metadata.

## Known limitations

* One Meridian organization per database; no scoped reset (see above).
* Only the operating account is reconciled; the collections account is not.
* No customer/supplier sub-ledgers, stock quantities, invoice documents or tax engine.
* Audit events for the seeding carry the real time the seed ran, while the simulated transactions carry
  simulated posting times — so the audit log's dates differ from the transactions' dates.
* The history is anchored to the day you first seed it; recent-window widgets (for example "last 24
  hours") show quiet days if you come back much later.
* Live AI has not been exercised against this scenario in automated tests (a scripted provider is used).

## Verification results (what was actually run)

| Check | Command | Result |
| --- | --- | --- |
| Backend suite, real PostgreSQL | `pytest -q` in `backend/` | **499 passed** (26 are Meridian tests: determinism, balance, idempotent seed, isolation, maker-checker, the whole learning flow through the HTTP API) |
| Concurrency | `pytest -m concurrency -q` | 14 passed |
| Lint / format | `ruff check app tests`, `ruff format --check app tests` | clean |
| Frontend | `npm run typecheck`, `npm test`, `npm run build` | clean; **37 tests passed**; built |
| Existing end-to-end demo (Docker stack) | `python -m app.scripts.demo_e2e` | 25 passed, 0 failed, 1 skipped (AI) |
| Seed on the real dev database | `seed_meridian seed` twice | first run created 23 accounts, 273 postings, 3 reversals, 2 drafts, 1 reconciliation run, 1 decision, 8 cases; second run created nothing. The 20 pre-existing organizations were fingerprinted before and after (accounts, transactions, entries, audit events, users, a hash of their password hashes): **none changed** |
| Browser (manual, against a throw-away copy) | sign in as the owner through the login page | dashboard, banner, reconciliation run #1, learning guide; exercise 1 (wrong then right answer) and the Idempotency lab + exercise 4 completed by real state |

Not run: a live AI explanation of a Meridian record after the Gemini headroom fix (the provider answered 429); the
other exercises through the browser (they are exercised end to end through the HTTP API in `tests/test_meridian.py`).
