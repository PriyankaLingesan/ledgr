"""Lightweight observability: in-process request counters, one operational
snapshot endpoint, and a structured audit event for reconciliation runs.
Not a metrics exporter - a single-process counter and a composition of
already-tested functions, verified end to end rather than assumed correct.
"""

from app.core import metrics


def test_metrics_snapshot_reflects_real_requests(client):
    before = metrics.snapshot().total_requests

    client.get("/api/v1/accounts")
    client.get("/api/v1/does-not-exist")  # a genuine 404

    after = metrics.snapshot()
    assert after.total_requests >= before + 2
    assert after.requests_by_status_class.get("2xx", 0) >= 1
    assert after.requests_by_status_class.get("4xx", 0) >= 1


def test_metrics_tracks_error_paths(client):
    # Counted under the route template, not the raw URL (see app.core.metrics).
    template = "/api/v1/transactions/{transaction_id}"
    raw = "/api/v1/transactions/not-a-real-uuid"
    before = metrics.error_count_for(template)

    client.get(raw)  # 422 validation error

    assert metrics.error_count_for(template) == before + 1
    assert metrics.error_count_for(raw) == 0


def test_operational_snapshot_endpoint_composes_real_data(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "observability check",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )

    response = client.get("/api/v1/system/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["ledger_balanced"] is True
    assert body["integrity_all_passed"] is True
    assert body["total_requests"] > 0
    assert "open_investigation_count" in body


def test_operational_snapshot_has_no_reconciliation_run_when_none_exist(client):
    response = client.get("/api/v1/system/metrics")
    assert response.json()["latest_reconciliation_run"] is None


def test_operational_snapshot_reflects_the_latest_reconciliation_run(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100},
            ],
            "external_reference": "OBS-001",
        },
    )
    client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "PAYMENT_PROVIDER_A",
            "records": [
                {
                    "external_id": "OBS-002",
                    "amount_minor": 5_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T00:00:00Z",
                }
            ],
        },
    )
    client.post("/api/v1/reconciliation/run")

    body = client.get("/api/v1/system/metrics").json()
    assert body["latest_reconciliation_run"] is not None
    assert body["latest_reconciliation_run"]["missing_internal_count"] == 1


def test_metrics_endpoint_requires_authentication(client):
    # Operational data is no longer public: it needs a credential with
    # `system:read`. (Only /system/health stays open, for load balancers.)
    assert client.get("/api/v1/system/metrics").status_code == 200
    client.headers.pop("Authorization")
    assert client.get("/api/v1/system/metrics").status_code == 401


# --------------------------------------------------------------------------
# reconciliation runs produce a structured audit event
# --------------------------------------------------------------------------


def test_reconciliation_run_is_recorded_as_an_audit_event(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "audit check",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )

    run = client.post("/api/v1/reconciliation/run")
    assert run.status_code == 201
    run_id = run.json()["id"]

    events = client.get(
        "/api/v1/system/audit",
        params={"resource_id": run_id, "event_type": "reconciliation.run_completed"},
    ).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["actor"] == "test-client"  # the authenticated caller, not a header
    assert "external_count" in event["payload"]
