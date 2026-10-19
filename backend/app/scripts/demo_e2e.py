"""Repeatable end-to-end demonstration of LEDGR against a running API.

    python -m app.scripts.demo_e2e [--base-url http://localhost:8000/api/v1]

Creates two brand-new, clearly synthetic organizations (unique names/emails per
run, random one-off passwords that are never printed or stored), walks the whole
product through real HTTP calls, and prints PASS / FAIL / SKIP for each step.
Nothing existing is modified or deleted; re-running simply makes two more
organizations. Exit status is non-zero if any step fails.

Steps (matching docs/DEMO.md):
 1  register an organization and sign in (cookie session + CSRF)
 2  create ledger accounts
 3  post a valid balanced transaction
 4  repeat the identical request: idempotent replay, no duplicate posting
 5  import external records; reconcile
 6  see an exact match and an unmatched record; an ambiguous one stays ambiguous
 7  open an investigation, attach a note, download the evidence
 8  ask the AI copilot to summarise it (SKIP if no AI provider is configured)
 9  correct a transaction through the reversal workflow
10  verify ledger integrity and inspect the audit trail
11  prove a user of another organization cannot see or touch any of it
"""

from __future__ import annotations

import argparse
import secrets
import sys
import uuid

import httpx

results: list[tuple[str, str, str]] = []


def step(name: str, status: str, detail: str = "") -> bool:
    results.append((name, status, detail))
    print(f"{status:<5} {name}" + (f"  - {detail}" if detail else ""), flush=True)
    return status != "FAIL"


def check(name: str, condition: bool, detail: str = "") -> bool:
    return step(name, "PASS" if condition else "FAIL", "" if condition else detail)


class Session:
    """One signed-in browser-like session: cookie jar + CSRF header."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base, timeout=30)
        self.csrf: str | None = None

    def register(self, org: str, email: str, password: str) -> httpx.Response:
        response = self.http.post(
            "/auth/register",
            json={
                "organization_name": org,
                "base_currency": "USD",
                "email": email,
                "full_name": "Demo Owner",
                "password": password,
            },
        )
        if response.status_code == 201:
            self.csrf = response.json()["csrf_token"]
        return response

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        headers = kw.pop("headers", {})
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return self.http.request(method, path, headers=headers, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


def txn_body(cash, revenue, amount, **extra):
    return {
        "description": extra.pop("description", "Demo settlement"),
        "currency": "USD",
        "entries": [
            {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": amount},
            {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": amount},
        ],
        **extra,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    run = uuid.uuid4().hex[:8]
    try:
        health = httpx.get(f"{base}/system/ready", timeout=10)
    except httpx.HTTPError as exc:
        print(f"cannot reach {base}: {exc}")
        return 2
    if health.status_code != 200:
        print(f"API is not ready: {health.text}")
        return 2

    # 1 ---------------------------------------------------------------- register
    a = Session(base)
    password_a = secrets.token_urlsafe(18)
    reg = a.register(f"Demo Co {run}", f"owner-{run}@demo.ledgr.test", password_a)
    if not check("1  register an organization and sign in", reg.status_code == 201, reg.text):
        return 1
    me = a.get("/auth/me").json()
    check(
        "1  signed in as OWNER via httpOnly cookie",
        me["role"] == "OWNER" and "ledgr_session" in a.http.cookies,
    )
    no_csrf = a.http.post(
        "/accounts", json={"code": "X", "name": "X", "type": "ASSET", "currency": "USD"}
    )
    check("1  cookie writes without the CSRF token are refused", no_csrf.status_code == 403)

    # 2 ----------------------------------------------------------------- accounts
    cash = a.post(
        "/accounts",
        json={
            "code": "CASH.USD",
            "name": "Operating cash",
            "type": "ASSET",
            "currency": "USD",
            "allows_negative_balance": False,
        },
    ).json()
    revenue = a.post(
        "/accounts",
        json={"code": "REV.FEES", "name": "Fee revenue", "type": "REVENUE", "currency": "USD"},
    ).json()
    check("2  accounts created", "id" in cash and "id" in revenue)

    # 3 ---------------------------------------------------------------- posting
    key = str(uuid.uuid4())
    payload = txn_body(
        cash, revenue, 500_000, reference=f"DEMO-{run}-1", external_reference="PAY-1"
    )
    first = a.post("/transactions", json=payload, headers={"Idempotency-Key": key})
    check(
        "3  balanced transaction posted",
        first.status_code == 201 and first.json()["balanced"],
        first.text,
    )
    balance = a.get(f"/accounts/{cash['id']}/balance").json()["balance"]["amount_minor"]
    check("3  balance derived from entries", balance == 500_000)

    # 4 ------------------------------------------------------------- idempotency
    replay = a.post("/transactions", json=payload, headers={"Idempotency-Key": key})
    check(
        "4  identical retry replays the original (no second posting)",
        replay.status_code == 201
        and replay.headers.get("Idempotent-Replay") == "true"
        and replay.json()["id"] == first.json()["id"]
        and a.get("/transactions").json()["total"] == 1,
    )
    conflict = a.post(
        "/transactions", json=txn_body(cash, revenue, 1), headers={"Idempotency-Key": key}
    )
    check("4  same key, different body is refused", conflict.status_code == 409)

    # 5 / 6 ---------------------------------------------------------- reconciliation
    a.post(
        "/transactions",
        json=txn_body(
            cash, revenue, 450_000, reference=f"DEMO-{run}-2", external_reference="PAY-2"
        ),
    )
    a.post(
        "/transactions",
        json=txn_body(
            cash, revenue, 100_000, reference=f"DEMO-{run}-3a", external_reference="PAY-9"
        ),
    )
    a.post(
        "/transactions",
        json=txn_body(
            cash, revenue, 100_000, reference=f"DEMO-{run}-3b", external_reference="PAY-9"
        ),
    )
    records = [
        ("PAY-1", 500_000),  # exact match
        ("PAY-2", 500_000),  # amount mismatch (ledger says 4500.00)
        ("PAY-9", 100_000),  # two ledger transactions share the reference: ambiguous
        ("PAY-404", 12_345),  # unknown to the ledger
    ]
    imp = a.post(
        "/reconciliation/import",
        json={
            "source_system": "DEMO_BANK",
            "records": [
                {
                    "external_id": e,
                    "amount_minor": m,
                    "currency": "USD",
                    "direction": "DEBIT",
                    "transaction_timestamp": "2026-09-08T12:00:00Z",
                }
                for e, m in records
            ],
        },
    )
    check(
        "5  external records imported",
        imp.status_code == 201 and imp.json()["imported"] == 4,
        imp.text,
    )
    recon = a.post("/reconciliation/run").json()
    by_ext = {
        r["external_transaction"]["external_id"]: r
        for r in recon["results"]
        if r["external_transaction"]
    }
    check("6  exact match found", by_ext["PAY-1"]["status"] == "MATCHED")
    check(
        "6  mismatch explained with the difference",
        by_ext["PAY-2"]["status"] == "AMOUNT_MISMATCH"
        and by_ext["PAY-2"]["difference"]["amount_minor"] == 50_000,
    )
    check(
        "6  ambiguous match stays ambiguous (nothing chosen)",
        by_ext["PAY-9"]["status"] == "AMBIGUOUS_INTERNAL_MATCH"
        and by_ext["PAY-9"]["internal_transaction_id"] is None,
    )
    check("6  unknown record reported missing", by_ext["PAY-404"]["status"] == "MISSING_INTERNAL")

    # 7 -------------------------------------------------------------- investigation
    case = a.post(f"/reconciliation/results/{by_ext['PAY-2']['id']}/investigate").json()
    check(
        "7  investigation opened from the mismatch",
        case.get("source") == "RECONCILIATION" and case.get("priority") == "HIGH",
    )
    note = a.post(
        f"/investigations/{case['id']}/notes",
        json={"body": "Provider confirms the payout was 5000.00 gross."},
    )
    check("7  note attached (append-only)", note.status_code == 201)
    report = a.get(f"/investigations/{case['id']}/report")
    check(
        "7  evidence report downloaded",
        report.status_code == 200
        and "AMOUNT_MISMATCH" in report.text
        and "Provider confirms" in report.text,
    )

    # 8 ----------------------------------------------------------------------- AI
    ai = a.get("/ai/status").json()
    if not ai.get("available"):
        step(
            "8  AI copilot summary",
            "SKIP",
            "no AI provider configured (AI_PROVIDER/AI_MODEL/AI_API_KEY)",
        )
    else:
        answer = a.post(f"/ai/investigations/{case['id']}/assist", json={"task": "SUMMARIZE"})
        ok = (
            answer.status_code == 200
            and answer.json()["requires_review"]
            and "summary" in answer.json()
        )
        check("8  AI copilot summary (draft, cited, review required)", ok, answer.text[:200])

    # 9 ------------------------------------------------------------------ correction
    target = first.json()
    correction = a.post(
        f"/transactions/{target['id']}/correct",
        json={
            "reason": "Amount was keyed wrong",
            "entries": txn_body(cash, revenue, 480_000)["entries"],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    check(
        "9  correction = reversal + new transaction", correction.status_code == 201, correction.text
    )
    original = a.get(f"/transactions/{target['id']}").json()
    check(
        "9  original preserved, marked REVERSED",
        original["status"] == "REVERSED" and len(original["entries"]) == 2,
    )

    # 10 ------------------------------------------------------------ integrity + audit
    integrity = a.get("/system/integrity").json()
    check(
        "10 ledger integrity: balances re-derived, books balanced",
        integrity["cache_consistent"] and integrity["trial_balance_balanced"],
    )
    types = {e["event_type"] for e in a.get("/system/audit", params={"limit": 200}).json()["items"]}
    wanted = {
        "organization.registered",
        "account.created",
        "transaction.posted",
        "transaction.reversed",
        "reconciliation.run_completed",
        "investigation.created",
        "investigation.note_added",
    }
    check("10 audit trail records every step", wanted <= types, str(wanted - types))
    mutate = a.http.request("DELETE", "/system/audit", headers={"X-CSRF-Token": a.csrf or ""})
    check("10 audit history cannot be modified through the API", mutate.status_code in (404, 405))

    # 11 ------------------------------------------------------------ tenant isolation
    b = Session(base)
    reg_b = b.register(
        f"Rival Co {run}", f"owner-{run}@rival.ledgr.test", secrets.token_urlsafe(18)
    )
    check("11 second organization registered", reg_b.status_code == 201)
    leaks = []
    for label, path in (
        ("account", f"/accounts/{cash['id']}"),
        ("transaction", f"/transactions/{target['id']}"),
        ("investigation", f"/investigations/{case['id']}"),
        ("reconciliation run", f"/reconciliation/runs/{recon['id']}"),
        ("statement", f"/accounts/{cash['id']}/statement"),
    ):
        if b.get(path).status_code != 404:
            leaks.append(label)
    check(
        "11 other organization gets 404 for every record of the first",
        not leaks,
        f"visible: {leaks}",
    )
    empty = all(
        b.get(p).json()["total"] == 0 for p in ("/accounts", "/transactions", "/investigations")
    )
    check(
        "11 other organization's lists and totals are empty",
        empty and b.get("/dashboard").json()["transaction_count"] == 0,
    )
    forged = b.post("/transactions", json=txn_body(cash, revenue, 100))
    check(
        "11 other organization cannot post to the first one's accounts", forged.status_code == 404
    )

    failed = [r for r in results if r[1] == "FAIL"]
    skipped = [r for r in results if r[1] == "SKIP"]
    passed = len(results) - len(failed) - len(skipped)
    print(f"\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
