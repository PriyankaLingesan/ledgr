"""Account statements, the dashboard, system health and onboarding.

Every number asserted here is checked against figures the test itself posted
through the API - the dashboard has no fixtures of its own to be right about.
"""

import time

from app.models.enums import AccountType, UserRole


def post(client, debit, credit, amount, **extra):
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": extra.pop("description", "statement test"),
            "currency": "USD",
            "entries": [
                {"account_id": str(debit.id), "direction": "DEBIT", "amount_minor": amount},
                {"account_id": str(credit.id), "direction": "CREDIT", "amount_minor": amount},
            ],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def statement(client, account, **params):
    response = client.get(f"/api/v1/accounts/{account.id}/statement", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------


def test_running_balance_for_a_debit_normal_account(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 1_000)  # cash +1000
    post(client, revenue, cash, 300)  # cash -300
    post(client, cash, revenue, 500)  # cash +500

    body = statement(client, cash)
    assert [r["running_balance"]["amount_minor"] for r in body["items"]] == [1_200, 700, 1_000]
    assert body["current_balance"]["amount_minor"] == 1_200
    assert body["closing_balance"]["amount_minor"] == 1_200
    assert body["opening_balance"]["amount_minor"] == 0
    assert body["total_debits"]["amount_minor"] == 1_500
    assert body["total_credits"]["amount_minor"] == 300
    # Debit/credit columns are exclusive per row.
    first = body["items"][0]
    assert first["debit"]["amount_minor"] == 500 and first["credit"] is None


def test_running_balance_for_a_credit_normal_account(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 1_000)
    post(client, revenue, cash, 300)
    post(client, cash, revenue, 500)
    body = statement(client, revenue)
    assert body["account"]["normal_balance"] == "CREDIT"
    assert [r["running_balance"]["amount_minor"] for r in body["items"]] == [1_200, 700, 1_000]


def test_the_statement_agrees_with_the_ledgers_own_balance(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    for amount in (111, 222, 333):
        post(client, cash, revenue, amount)
    ledger = client.get(f"/api/v1/accounts/{cash.id}/balance").json()
    body = statement(client, cash)
    assert body["current_balance"]["amount_minor"] == ledger["balance"]["amount_minor"] == 666
    assert body["items"][0]["running_balance"]["amount_minor"] == 666


def test_filtering_and_paging_never_change_a_rows_running_balance(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 1_000)
    post(client, revenue, cash, 300)
    post(client, cash, revenue, 500)

    debits_only = statement(client, cash, direction="DEBIT")
    assert [r["running_balance"]["amount_minor"] for r in debits_only["items"]] == [1_200, 1_000]

    page = statement(client, cash, limit=1, offset=1)
    assert page["total"] == 3
    assert page["items"][0]["running_balance"]["amount_minor"] == 700


def test_statement_search_matches_reference_and_description(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 100, description="Invoice 4471 settlement", reference="INV-4471")
    post(client, cash, revenue, 200, description="Something else")
    assert statement(client, cash, q="4471")["total"] == 1
    assert statement(client, cash, q="inv-4471")["total"] == 1
    assert statement(client, cash, q="no-such-thing")["total"] == 0


def test_a_date_window_reports_opening_and_closing_balances(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 1_000)
    time.sleep(0.05)
    post(client, revenue, cash, 300)
    post(client, cash, revenue, 500)

    rows = statement(client, cash)["items"]
    since = rows[1]["posted_at"]  # the 300 credit - the oldest row inside the window
    windowed = statement(client, cash, since=since)

    assert windowed["opening_balance"]["amount_minor"] == 1_000
    assert windowed["total_debits"]["amount_minor"] == 500
    assert windowed["total_credits"]["amount_minor"] == 300
    assert windowed["closing_balance"]["amount_minor"] == 1_200
    assert (
        windowed["opening_balance"]["amount_minor"]
        + windowed["total_debits"]["amount_minor"]
        - windowed["total_credits"]["amount_minor"]
        == windowed["closing_balance"]["amount_minor"]
    )


def test_reversed_transactions_stay_visible_on_the_statement(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    posted = post(client, cash, revenue, 800)
    client.post(f"/api/v1/transactions/{posted['id']}/reverse", json={})
    body = statement(client, cash)
    assert body["total"] == 2  # the original and its reversal - history is append-only
    assert body["current_balance"]["amount_minor"] == 0
    assert {r["transaction_kind"] for r in body["items"]} == {"STANDARD", "REVERSAL"}


def test_statement_of_an_unknown_account_is_404(client):
    response = client.get("/api/v1/accounts/00000000-0000-0000-0000-000000000009/statement")
    assert response.status_code == 404


def test_auditors_can_read_statements(role_client, client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 100)
    auditor = role_client(UserRole.AUDITOR)
    assert statement(auditor, cash)["total"] == 1


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------


def test_the_dashboard_of_an_empty_system_is_honest_zeros(client):
    body = client.get("/api/v1/dashboard").json()
    assert body["account_count"] == 0
    assert body["transaction_count"] == 0
    assert body["recent_transactions"] == []
    assert body["open_investigations"] == 0
    assert body["latest_reconciliation"] is None
    assert body["pending_approvals"] == 0
    assert body["ledger_balanced"] is True
    assert body["database"] == "up"
    assert body["top_alerts"] == []


def test_the_dashboard_reflects_real_activity(client, user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 1_000)
    post(client, cash, revenue, 2_000)
    client.post("/api/v1/investigations", json={"title": "open case", "issue_summary": "x"})
    closed = client.post(
        "/api/v1/investigations", json={"title": "closed case", "issue_summary": "y"}
    ).json()
    client.post(f"/api/v1/investigations/{closed['id']}/resolve", json={"resolution_note": "done"})

    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    maker.post(
        "/api/v1/transaction-drafts?submit=true",
        json={
            "description": "pending one",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 10},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 10},
            ],
        },
    )

    body = client.get("/api/v1/dashboard").json()
    assert body["account_count"] == 2 and body["active_account_count"] == 2
    assert body["transaction_count"] == 2
    assert body["transactions_last_24h"] == 2
    assert [t["amount"]["amount_minor"] for t in body["recent_transactions"]] == [2_000, 1_000]
    assert body["open_investigations"] == 1
    assert [c["title"] for c in body["recent_investigations"]] == ["open case"]
    assert body["pending_approvals"] == 1
    assert body["ledger_balanced"] is True and body["integrity_all_passed"] is True
    assert body["recent_audit"], "recent audit activity comes from real audit rows"
    assert body["recent_audit"][0]["seq"] > body["recent_audit"][-1]["seq"]


def test_the_dashboard_reports_the_latest_reconciliation_run(client):
    client.post("/api/v1/reconciliation/import-demo")
    run = client.post("/api/v1/reconciliation/run").json()
    latest = client.get("/api/v1/dashboard").json()["latest_reconciliation"]
    assert latest["run_number"] == run["run_number"]
    assert latest["matched"] == run["matched_count"]
    assert latest["mismatched"] == run["mismatch_count"]
    assert latest["missing_internal"] == run["missing_internal_count"]
    assert latest["ambiguous"] == run["ambiguous_count"]
    assert latest["exceptions"] > 0


# --------------------------------------------------------------------------
# system health
# --------------------------------------------------------------------------


def test_health_detail_reports_real_component_status(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 100)
    body = client.get("/api/v1/system/health/detail").json()
    assert body["database"]["status"] == "ok"
    assert body["database_latency_ms"] >= 0
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from tests.conftest import BACKEND_DIR

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    assert body["schema_revision"] == ScriptDirectory.from_config(config).get_current_head()
    assert body["ledger_integrity"]["status"] == "ok"
    assert body["integrity_checks"] and all(c["passed"] for c in body["integrity_checks"])
    assert body["reconciliation"]["status"] == "none"
    assert body["api"]["status"] == "ok"


def test_health_detail_shows_unexpected_server_errors_that_really_happened(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import balances

    def boom(db):
        raise RuntimeError("synthetic failure for the test")

    monkeypatch.setattr(balances, "trial_balance", boom)
    token = client.headers["Authorization"]
    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = token
        assert c.get("/api/v1/system/stats").status_code == 500
    monkeypatch.undo()

    errors = client.get("/api/v1/system/health/detail").json()["recent_errors"]
    assert errors and errors[0]["status_code"] == 500
    assert errors[0]["path"] == "/api/v1/system/stats"
    assert "synthetic" not in str(errors)  # no internals leaked


def test_public_health_check_stays_unauthenticated():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        assert anon.get("/api/v1/system/health").json()["database"] == "up"


def test_security_headers_are_set(client):
    response = client.get("/api/v1/accounts")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------
# onboarding
# --------------------------------------------------------------------------


def steps(client):
    body = client.get("/api/v1/onboarding").json()
    return {s["key"]: s["done"] for s in body["steps"]}, body


def test_the_checklist_tracks_real_state(client, org, cash_and_revenue, make_user):
    cash, revenue = cash_and_revenue
    done, body = steps(client)
    assert done["organization"] is True
    assert done["account"] is True
    assert done["transaction"] is False
    assert done["teammate"] is False
    assert body["organization_name"] == "Test Org"

    post(client, cash, revenue, 100)
    make_user(UserRole.FINANCE)
    make_user(UserRole.AUDITOR)
    done, body = steps(client)
    assert done["transaction"] is True
    assert done["teammate"] is True
    assert body["completed"] == sum(done.values())


def test_demo_data_is_admin_only_and_idempotent(client, role_client):
    assert role_client(UserRole.FINANCE).post("/api/v1/onboarding/demo-data").status_code == 403

    first = client.post("/api/v1/onboarding/demo-data")
    assert first.status_code == 201
    assert sorted(first.json()["created"]) == ["CASH.OPERATING.USD", "REV.FEES.USD"]
    again = client.post("/api/v1/onboarding/demo-data")
    assert again.status_code == 201
    assert again.json()["created"] == []
    assert "0 external record" in again.json()["message"] or "Imported 0" in again.json()["message"]

    assert client.get("/api/v1/transactions").json()["total"] == 5  # not doubled
    done, _ = steps(client)
    assert done["external_data"] is True and done["reconciliation_run"] is False


def test_empty_state_lists_are_valid_json_not_errors(client):
    for path in (
        "/api/v1/accounts",
        "/api/v1/transactions",
        "/api/v1/investigations",
        "/api/v1/reconciliation/runs",
        "/api/v1/transaction-drafts",
    ):
        body = client.get(path).json()
        assert body["items"] == [] and body["total"] == 0, path
    assert client.get("/api/v1/reconciliation/sources").json() == []


def test_organization_settings_are_admin_only_and_audited(client, role_client, org):
    assert (
        client.patch("/api/v1/organization", json={"name": "Renamed Co"}).json()["name"]
        == "Renamed Co"
    )
    assert (
        role_client(UserRole.FINANCE).patch("/api/v1/organization", json={"name": "x"}).status_code
        == 403
    )
    events = client.get(
        "/api/v1/system/audit", params={"event_type": "organization.updated"}
    ).json()
    assert events["total"] == 1


def test_the_standard_chart_accounts_can_still_be_created_by_finance(role_client):
    finance = role_client(UserRole.FINANCE)
    response = finance.post(
        "/api/v1/accounts",
        json={
            "code": "CASH.OPERATING.USD",
            "name": "Operating",
            "type": AccountType.ASSET.value,
            "currency": "USD",
        },
    )
    assert response.status_code == 201
