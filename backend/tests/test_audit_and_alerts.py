"""The audit trail and deterministic alerts.

Audit rows are only ever written by real actions - these tests perform the
action through the API and then look for the row, never inserting rows by hand
to "test" the audit log. Alerts are computed from that same real data.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.models.audit import AuditEvent
from app.models.enums import AccountType, UserRole


def txn(cash, revenue, amount=1_000, **extra):
    return {
        "description": "audit/alert test",
        "currency": "USD",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": amount},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": amount},
        ],
        **extra,
    }


def audit_types(db):
    db.expire_all()
    return [e.event_type for e in db.execute(select(AuditEvent).order_by(AuditEvent.seq)).scalars()]


def alerts(client):
    body = client.get("/api/v1/alerts").json()
    return {a["rule"]: a for a in body["alerts"]}, body


# --------------------------------------------------------------------------
# important actions leave audit records
# --------------------------------------------------------------------------


def test_the_important_actions_each_write_an_audit_event(client, db, user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue

    posted = client.post("/api/v1/transactions", json=txn(cash, revenue)).json()
    client.post(f"/api/v1/transactions/{posted['id']}/reverse", json={"reason": "test"})
    case = client.post(
        "/api/v1/investigations", json={"title": "Look into it", "issue_summary": "because"}
    ).json()
    client.patch(f"/api/v1/investigations/{case['id']}/status", json={"status": "INVESTIGATING"})
    client.post(f"/api/v1/investigations/{case['id']}/resolve", json={"resolution_note": "fine"})
    client.post("/api/v1/reconciliation/run")
    key = client.post("/api/v1/api-keys", json={"label": "audited-key", "role": "AUDITOR"}).json()
    client.post(f"/api/v1/api-keys/{key['id']}/revoke")
    user_client(UserRole.FINANCE, email="audited@example.com")  # logs in

    seen = set(audit_types(db))
    assert {
        "account.created",
        "transaction.posted",
        "transaction.reversed",
        "investigation.created",
        "investigation.status_changed",
        "investigation.resolved",
        "reconciliation.run_completed",
        "api_key.created",
        "api_key.revoked",
        "user.created",
        "auth.login",
    } <= seen


def test_events_carry_the_verified_actor_and_the_request_id(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json=txn(cash, revenue),
        headers={"X-Request-ID": "req-audit-12345", "X-Actor": "forged-actor"},
    )
    db.expire_all()
    event = db.execute(
        select(AuditEvent).where(AuditEvent.event_type == "transaction.posted")
    ).scalar_one()
    assert event.actor == "test-client"  # from the verified key, not the header
    assert event.request_id == "req-audit-12345"


def test_no_audit_event_is_written_for_a_pure_read(client, db, cash_and_revenue):
    before = len(audit_types(db))
    client.get("/api/v1/accounts")
    client.get("/api/v1/transactions")
    client.get("/api/v1/dashboard")
    assert len(audit_types(db)) == before


# --------------------------------------------------------------------------
# audit history cannot be modified through the API (or the database)
# --------------------------------------------------------------------------


def test_the_api_exposes_no_way_to_change_audit_history(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post("/api/v1/transactions", json=txn(cash, revenue))
    event_id = client.get("/api/v1/system/audit").json()["items"][0]["id"]

    for method in ("put", "patch", "delete", "post"):
        for path in ("/api/v1/system/audit", f"/api/v1/system/audit/{event_id}"):
            response = getattr(client, method)(path)
            assert response.status_code in (404, 405), (method, path, response.status_code)


def test_the_database_rejects_updates_and_deletes_of_audit_rows(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post("/api/v1/transactions", json=txn(cash, revenue))
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_events SET actor = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()


# --------------------------------------------------------------------------
# reading and filtering
# --------------------------------------------------------------------------


def test_audit_log_filters_by_actor_type_and_search(client, user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post("/api/v1/transactions", json=txn(cash, revenue))
    finance = user_client(UserRole.FINANCE, email="finance@example.com")
    finance.post("/api/v1/transactions", json=txn(cash, revenue, amount=2_000))

    by_actor = client.get("/api/v1/system/audit", params={"actor": "finance@example.com"}).json()
    assert by_actor["total"] >= 1
    assert all(e["actor"] == "finance@example.com" for e in by_actor["items"])

    by_type = client.get("/api/v1/system/audit", params={"event_type": "transaction.posted"}).json()
    assert by_type["total"] == 2

    search = client.get("/api/v1/system/audit", params={"q": "finance@example.com"}).json()
    assert search["total"] >= 1

    types = client.get("/api/v1/system/audit/event-types").json()
    assert "transaction.posted" in types and "auth.login" in types


def test_an_auditor_can_read_the_audit_log(role_client, client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post("/api/v1/transactions", json=txn(cash, revenue))
    auditor = role_client(UserRole.AUDITOR)
    assert auditor.get("/api/v1/system/audit").json()["total"] >= 1


def test_failed_postings_are_audited_even_though_nothing_was_posted(client, db):
    bad = {
        "description": "to nowhere",
        "currency": "USD",
        "entries": [
            {
                "account_id": "00000000-0000-0000-0000-00000000000a",
                "direction": "DEBIT",
                "amount_minor": 5,
            },
            {
                "account_id": "00000000-0000-0000-0000-00000000000b",
                "direction": "CREDIT",
                "amount_minor": 5,
            },
        ],
    }
    assert client.post("/api/v1/transactions", json=bad).status_code == 404
    db.expire_all()
    failed = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "transaction.post_failed"))
        .scalars()
        .all()
    )
    assert len(failed) == 1
    assert failed[0].payload["code"] == "not_found"
    assert failed[0].actor == "test-client"


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------


def test_a_clean_system_has_no_alerts(client):
    by_rule, body = alerts(client)
    assert body["alerts"] == []
    assert body["counts"] == {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}


def test_repeated_failed_postings_raise_an_alert(client):
    bad = {
        "description": "to nowhere",
        "currency": "USD",
        "entries": [
            {
                "account_id": "00000000-0000-0000-0000-00000000000a",
                "direction": "DEBIT",
                "amount_minor": 5,
            },
            {
                "account_id": "00000000-0000-0000-0000-00000000000b",
                "direction": "CREDIT",
                "amount_minor": 5,
            },
        ],
    }
    for _ in range(settings.alert_repeat_threshold - 1):
        client.post("/api/v1/transactions", json=bad)
    assert "failed_postings" not in alerts(client)[0]  # below the threshold

    client.post("/api/v1/transactions", json=bad)
    alert = alerts(client)[0]["failed_postings"]
    assert alert["severity"] == "HIGH"
    assert alert["count"] == settings.alert_repeat_threshold
    assert alert["evidence"]["actor"] == "test-client"


def test_retrying_one_idempotency_key_repeatedly_raises_an_alert(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    body = txn(cash, revenue)
    headers = {"Idempotency-Key": "retry-storm-1"}
    first = client.post("/api/v1/transactions", json=body, headers=headers)
    assert first.status_code == 201
    for _ in range(settings.alert_repeat_threshold):
        replay = client.post("/api/v1/transactions", json=body, headers=headers)
        assert replay.headers["Idempotent-Replay"] == "true"
        assert replay.json()["id"] == first.json()["id"]

    alert = alerts(client)[0]["repeated_retries"]
    assert alert["evidence"]["idempotency_key"] == "retry-storm-1"
    # ...and the ledger still holds exactly one transaction.
    assert client.get("/api/v1/transactions").json()["total"] == 1


def test_reusing_a_key_with_a_different_body_is_audited_and_alerts(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    headers = {"Idempotency-Key": "conflict-key"}
    assert (
        client.post("/api/v1/transactions", json=txn(cash, revenue), headers=headers).status_code
        == 201
    )
    for amount in (2_000, 3_000, 4_000):
        response = client.post(
            "/api/v1/transactions", json=txn(cash, revenue, amount=amount), headers=headers
        )
        assert response.status_code == 409
    assert "idempotency_conflicts" in alerts(client)[0]


def test_a_large_transaction_raises_an_alert(client, cash_and_revenue, monkeypatch):
    cash, revenue = cash_and_revenue
    monkeypatch.setattr(settings, "alert_large_transaction_minor", 10_000)
    client.post("/api/v1/transactions", json=txn(cash, revenue, amount=9_999))
    assert "large_transaction" not in alerts(client)[0]
    posted = client.post("/api/v1/transactions", json=txn(cash, revenue, amount=10_000)).json()
    alert = alerts(client)[0]["large_transaction"]
    assert alert["evidence"]["transaction_id"] == posted["id"]
    assert alert["link"] == f"/transactions/{posted['id']}"


def test_repeated_reversals_raise_an_alert(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    for _ in range(settings.alert_repeat_threshold):
        posted = client.post("/api/v1/transactions", json=txn(cash, revenue)).json()
        assert (
            client.post(f"/api/v1/transactions/{posted['id']}/reverse", json={}).status_code == 201
        )
    assert "repeated_reversals" in alerts(client)[0]


def test_an_account_near_its_balance_floor_raises_an_alert(client, make_account, cash_and_revenue):
    _, revenue = cash_and_revenue
    vault = make_account("VAULT.USD", AccountType.ASSET, allows_negative_balance=False)
    client.post("/api/v1/transactions", json=txn(vault, revenue, amount=100))
    alert = alerts(client)[0]["balance_floor"]
    assert alert["evidence"]["balance_minor"] == 100
    assert alert["severity"] == "MEDIUM"

    # Draining it to zero escalates.
    client.post(
        "/api/v1/transactions",
        json={
            "description": "drain",
            "currency": "USD",
            "entries": [
                {"account_id": str(revenue.id), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(vault.id), "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )
    assert alerts(client)[0]["balance_floor"]["severity"] == "HIGH"


def test_reconciliation_exceptions_raise_an_alert(client):
    assert client.post("/api/v1/reconciliation/import-demo").status_code == 201
    run = client.post("/api/v1/reconciliation/run").json()
    alert = alerts(client)[0]["reconciliation_exceptions"]
    exceptions = (
        run["mismatch_count"]
        + run["missing_internal_count"]
        + run["missing_external_count"]
        + run["ambiguous_count"]
    )
    assert alert["count"] == exceptions > 0
    assert alert["severity"] == "HIGH"  # the demo contains an amount mismatch


def test_repeated_failed_logins_raise_an_alert(client, make_user):
    from fastapi.testclient import TestClient

    from app.main import app

    user = make_user(UserRole.FINANCE)
    with TestClient(app) as anon:
        for _ in range(settings.alert_repeat_threshold):
            anon.post(
                "/api/v1/auth/login", json={"email": user.email, "password": "nope-nope-nope"}
            )
    alert = alerts(client)[0]["repeated_login_failures"]
    assert alert["evidence"]["identity"] == user.email


def test_alerts_are_read_only_and_visible_to_auditors(role_client, client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    auditor = role_client(UserRole.AUDITOR)
    assert auditor.get("/api/v1/alerts").status_code == 200
    assert auditor.post("/api/v1/alerts").status_code == 405
    assert client.delete("/api/v1/alerts").status_code == 405


def test_computing_alerts_never_writes_to_the_ledger_or_audit_log(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post("/api/v1/transactions", json=txn(cash, revenue))
    before = len(audit_types(db))
    for _ in range(3):
        client.get("/api/v1/alerts")
    assert len(audit_types(db)) == before
