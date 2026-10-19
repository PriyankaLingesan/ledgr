# Repeatable demo

> Looking for something to *learn* the product on? See [`MERIDIAN.md`](MERIDIAN.md): a fictional trading company
> with six months of history and a guided, server-verified learning path.

All demo data is synthetic. Nothing here deletes or edits existing data: each run creates new
organizations with unique names and emails, and random one-off passwords that are never printed
or stored.

## 1. Start LEDGR

```bash
docker compose up -d --build       # db, migrations (+ demo seed), api, web
# another service already on port 8080? pick another:
#   WEB_PORT=8081 CORS_ORIGINS=http://localhost:8081 docker compose up -d
```

* Console: http://localhost:8080 (or your `WEB_PORT`) · API docs: http://localhost:8000/docs
* The migrate step creates a **"LEDGR Demo Company"** organization (22 accounts, ~190
  transactions) with owner `owner@demo.ledgr.test`. If you did not set `BOOTSTRAP_ADMIN_PASSWORD`,
  a random password is generated and printed **once** in the migrate logs:
  `docker logs ledgr-migrate`. Or skip it and create your own organization on the sign-up tab.

## 2. Automated end-to-end walkthrough

```bash
cd backend
python -m app.scripts.demo_e2e            # --base-url http://localhost:8000/api/v1 by default
```

It prints PASS / FAIL / SKIP per step and exits non-zero on any failure:

| Step | What it proves |
| --- | --- |
| 1 | Register → OWNER, httpOnly cookie session; cookie writes without CSRF token are refused |
| 2-3 | Create accounts; post a balanced transaction; balance is derived from entries |
| 4 | Identical retry replays the original (`Idempotent-Replay: true`, still one transaction); same key with a different body → `409` |
| 5-6 | Import external records and reconcile: exact match, amount mismatch with difference, **ambiguous stays ambiguous (nothing chosen)**, unknown record |
| 7 | Open an investigation from the mismatch, attach a note, download the evidence report |
| 8 | AI copilot summary - **SKIP** unless an AI provider is configured |
| 9 | Correct a transaction through the reversal workflow; original preserved, marked REVERSED |
| 10 | Integrity (balances re-derived, books balance), audit trail has every step, audit cannot be modified via the API |
| 11 | A second organization gets `404` for every record of the first, sees empty lists/totals, and cannot post to its accounts |

## 3. Manual tour (about 10 minutes)

1. **Sign up** (Create account tab) → you are the OWNER of an empty organization; the dashboard
   shows a *Getting started* checklist.
2. **Administration → Users & roles**: add a FINANCE user (maker-checker needs two people) and an
   AUDITOR. Sign in as the auditor in a private window: no "New transaction", no admin pages.
3. **Accounts → New account**; **New transaction** (or *Load demo reconciliation data* on the
   dashboard checklist as OWNER/ADMIN). Reload the page: you stay signed in (cookie); open dev tools →
   Application: no token in local/session storage.
4. **Approvals**: as the FINANCE user submit a draft; as the OWNER approve it. Try approving your
   own draft - refused.
5. **Reconciliation → Import records** (paste CSV, map columns, preview) → **Run**. Open the
   ambiguous exception: suggestions with reasons, record a decision. Open an investigation, add
   notes, **Download evidence**.
6. **Operations inbox**, **Alerts**, **Audit log** (filter by action/user/date), **System health**.
7. **Isolation**: sign up a second organization (private window) - it sees none of the above.

## 4. Optional: AI copilot

Set `AI_PROVIDER`, `AI_MODEL`, `AI_API_KEY` in your shell and restart the api container
(`docker compose up -d`). The case page's *AI copilot* panel and the reconciliation exception's
*Explain with AI* then work; output is a cited draft needing review. Without a key those panels
say AI is not configured and everything else is unaffected.

## 5. Reset

Ledger history is append-only, so there is no "reset" that edits it. To start from nothing locally:
`docker compose down -v` (this **deletes the local database volume** - only do it if you mean to).
To keep existing data and just add a fresh sandbox, run the walkthrough again or sign up a new
organization.
