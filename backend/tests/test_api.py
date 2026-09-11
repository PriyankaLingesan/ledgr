"""HTTP surface: contracts, error shapes, filtering, auditability."""

import uuid

import pytest


@pytest.fixture
def chart(client):
    def create(code, account_type, currency="USD", allows_negative=True):
        response = client.post(
            "/api/v1/accounts",
            json={
                "code": code,
                "name": code.replace(".", " ").title(),
                "type": account_type,
                "currency": currency,
                "allows_negative_balance": allows_negative,
            },
            headers={"X-Actor": "ops.tester"},
        )
        assert response.status_code == 201, response.text
        return response.json()

    return {
        "cash": create("API.CASH.USD", "ASSET"),
        "wallet": create("API.WALLET.USD", "LIABILITY"),
        "revenue": create("API.REV.USD", "REVENUE"),
    }


def post_transaction(client, debit, credit, amount=250_00, **extra):
    body = {
        "description": extra.pop("description", "API posting"),
        "currency": "USD",
        "entries": [
            {"account_id": debit["id"], "direction": "DEBIT", "amount_minor": amount},
            {"account_id": credit["id"], "direction": "CREDIT", "amount_minor": amount},
        ],
        **extra,
    }
    return client.post("/api/v1/transactions", json=body, headers={"X-Actor": "ops.tester"})


# --- accounts -------------------------------------------------------------


def test_account_normal_balance_is_derived_not_supplied(client, chart):
    assert chart["cash"]["normal_balance"] == "DEBIT"
    assert chart["wallet"]["normal_balance"] == "CREDIT"
    assert chart["revenue"]["normal_balance"] == "CREDIT"


def test_duplicate_account_code_conflicts(client, chart):
    response = client.post(
        "/api/v1/accounts",
        json={"code": "API.CASH.USD", "name": "dup", "type": "ASSET", "currency": "USD"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_unsupported_currency_is_reported_with_the_supported_list(client):
    response = client.post(
        "/api/v1/accounts",
        json={"code": "API.XYZ", "name": "x", "type": "ASSET", "currency": "XYZ"},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "unsupported_currency"
    assert "USD" in body["details"]["supported"]


def test_account_listing_filters_and_paginates(client, chart):
    all_accounts = client.get("/api/v1/accounts").json()
    assert all_accounts["total"] == 3

    filtered = client.get("/api/v1/accounts", params={"type": "LIABILITY"}).json()
    assert [a["code"] for a in filtered["items"]] == ["API.WALLET.USD"]

    searched = client.get("/api/v1/accounts", params={"q": "wallet"}).json()
    assert searched["total"] == 1

    page = client.get("/api/v1/accounts", params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2 and page["total"] == 3


def test_account_balance_is_derived_and_flags_cache_agreement(client, chart):
    post_transaction(client, chart["cash"], chart["wallet"], 900_00)
    post_transaction(client, chart["wallet"], chart["cash"], 100_00)

    balance = client.get(f"/api/v1/accounts/{chart['cash']['id']}/balance").json()

    assert balance["debits"]["amount_minor"] == 900_00
    assert balance["credits"]["amount_minor"] == 100_00
    assert balance["balance"]["amount_minor"] == 800_00
    assert balance["balance"]["amount"] == "800.00"
    assert balance["cache_consistent"] is True
    assert balance["cache"]["balance"]["amount_minor"] == 800_00


def test_account_statement_lists_entries_newest_first(client, chart):
    post_transaction(client, chart["cash"], chart["revenue"], 10_00)
    post_transaction(client, chart["cash"], chart["revenue"], 20_00)

    entries = client.get(f"/api/v1/accounts/{chart['cash']['id']}/entries").json()
    assert entries["total"] == 2
    assert entries["items"][0]["amount"]["amount_minor"] == 20_00
    assert entries["items"][0]["transaction"]["reference"]
    assert entries["items"][0]["direction"] == "DEBIT"


def test_missing_account_returns_a_structured_404(client):
    response = client.get(f"/api/v1/accounts/{uuid.uuid4()}")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


# --- transactions ---------------------------------------------------------


def test_posting_returns_the_full_entry_set(client, chart):
    response = post_transaction(client, chart["cash"], chart["wallet"], 1_234_56)
    assert response.status_code == 201
    body = response.json()

    assert body["balanced"] is True
    assert body["total_debits"] == body["total_credits"]
    assert body["total_debits"]["amount"] == "1234.56"
    assert len(body["entries"]) == 2
    assert {e["direction"] for e in body["entries"]} == {"DEBIT", "CREDIT"}
    assert body["entries"][0]["account"]["code"]
    assert body["actor"] == "ops.tester"
    assert body["request_id"]


def test_unbalanced_posting_is_rejected_with_the_difference(client, chart):
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "bad",
            "currency": "USD",
            "entries": [
                {"account_id": chart["cash"]["id"], "direction": "DEBIT", "amount_minor": 100},
                {"account_id": chart["wallet"]["id"], "direction": "CREDIT", "amount_minor": 90},
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_negative_amount_is_rejected_by_schema(client, chart):
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "bad",
            "currency": "USD",
            "entries": [
                {"account_id": chart["cash"]["id"], "direction": "DEBIT", "amount_minor": -100},
                {"account_id": chart["wallet"]["id"], "direction": "CREDIT", "amount_minor": -100},
            ],
        },
    )
    assert response.status_code == 422


def test_transaction_filtering(client, chart):
    post_transaction(client, chart["cash"], chart["revenue"], 10_00, description="Fee income")
    post_transaction(client, chart["cash"], chart["wallet"], 20_00, description="Deposit")

    by_text = client.get("/api/v1/transactions", params={"q": "deposit"}).json()
    assert by_text["total"] == 1

    by_account = client.get(
        "/api/v1/transactions", params={"account_id": chart["revenue"]["id"]}
    ).json()
    assert by_account["total"] == 1

    by_currency = client.get("/api/v1/transactions", params={"currency": "EUR"}).json()
    assert by_currency["total"] == 0


def test_lookup_by_reference(client, chart):
    post_transaction(client, chart["cash"], chart["revenue"], 5_00, reference="TXN-LOOKUP-1")
    found = client.get("/api/v1/transactions/by-reference/TXN-LOOKUP-1")
    assert found.status_code == 200
    assert found.json()["reference"] == "TXN-LOOKUP-1"


def test_reversal_endpoint_links_both_directions(client, chart):
    original = post_transaction(client, chart["cash"], chart["wallet"], 400_00).json()

    reversal = client.post(
        f"/api/v1/transactions/{original['id']}/reverse",
        json={"reason": "duplicate deposit"},
        headers={"X-Actor": "ops.tester"},
    )
    assert reversal.status_code == 201
    reversal_body = reversal.json()
    assert reversal_body["kind"] == "REVERSAL"
    assert reversal_body["reverses_transaction_id"] == original["id"]

    refreshed = client.get(f"/api/v1/transactions/{original['id']}").json()
    assert refreshed["status"] == "REVERSED"
    assert refreshed["reversed_by_transaction_id"] == reversal_body["id"]

    second_attempt = client.post(
        f"/api/v1/transactions/{original['id']}/reverse", json={"reason": "again"}
    )
    assert second_attempt.status_code == 409
    assert second_attempt.json()["error"]["code"] == "already_reversed"


# --- ledger ---------------------------------------------------------------


def test_ledger_explorer_filters(client, chart):
    posted = post_transaction(client, chart["cash"], chart["revenue"], 42_00).json()

    everything = client.get("/api/v1/ledger/entries").json()
    assert everything["total"] == 2
    assert everything["items"][0]["account"]["code"]
    assert everything["items"][0]["transaction"]["reference"] == posted["reference"]

    debits = client.get("/api/v1/ledger/entries", params={"direction": "DEBIT"}).json()
    assert debits["total"] == 1

    by_transaction = client.get(
        "/api/v1/ledger/entries", params={"transaction_id": posted["id"]}
    ).json()
    assert by_transaction["total"] == 2

    by_amount = client.get("/api/v1/ledger/entries", params={"min_amount_minor": 100_00}).json()
    assert by_amount["total"] == 0


def test_keyset_pagination_walks_the_ledger(client, chart):
    for i in range(5):
        post_transaction(client, chart["cash"], chart["revenue"], (i + 1) * 100)

    first = client.get("/api/v1/ledger/entries", params={"limit": 4}).json()
    assert len(first["items"]) == 4

    cursor = first["items"][-1]["seq"]
    second = client.get("/api/v1/ledger/entries", params={"limit": 4, "before_seq": cursor}).json()
    assert all(item["seq"] < cursor for item in second["items"])
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}


def test_entry_traces_back_to_transaction_and_account(client, chart):
    posted = post_transaction(client, chart["cash"], chart["revenue"], 77_00).json()
    entry_id = posted["entries"][0]["id"]

    entry = client.get(f"/api/v1/ledger/entries/{entry_id}").json()
    assert entry["transaction"]["id"] == posted["id"]
    assert entry["account"]["id"] == chart["cash"]["id"]


# --- system ---------------------------------------------------------------


def test_health(client):
    body = client.get("/api/v1/system/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "up"


def test_currencies_expose_exponents(client):
    codes = {c["code"]: c for c in client.get("/api/v1/system/currencies").json()}
    assert codes["USD"]["exponent"] == 2
    assert codes["JPY"]["exponent"] == 0


def test_stats_are_derived_from_real_activity(client, chart):
    post_transaction(client, chart["cash"], chart["revenue"], 100_00)
    post_transaction(client, chart["cash"], chart["wallet"], 300_00)

    stats = client.get("/api/v1/system/stats").json()
    assert stats["account_count"] == 3
    assert stats["transaction_count"] == 2
    assert stats["entry_count"] == 4
    assert stats["ledger_balanced"] is True
    usd = next(row for row in stats["trial_balance"] if row["currency"] == "USD")
    assert usd["debits"]["amount_minor"] == usd["credits"]["amount_minor"] == 400_00
    assert usd["difference"]["amount_minor"] == 0
    assert sum(d["transaction_count"] for d in stats["daily_volume"]) == 2


def test_integrity_report_is_clean(client, chart):
    post_transaction(client, chart["cash"], chart["revenue"], 12_34)

    report = client.get("/api/v1/system/integrity").json()
    assert report["cache_consistent"] is True
    assert report["trial_balance_balanced"] is True
    assert report["issues"] == []
    assert report["unbalanced_transaction_ids"] == []


def test_audit_trail_records_every_write(client, chart):
    posted = post_transaction(client, chart["cash"], chart["revenue"], 60_00).json()

    events = client.get("/api/v1/system/audit", params={"resource_id": posted["id"]}).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["event_type"] == "transaction.posted"
    assert event["actor"] == "ops.tester"
    assert event["payload"]["reference"] == posted["reference"]
    assert event["request_id"] == posted["request_id"]


def test_request_id_is_echoed(client):
    response = client.get("/api/v1/system/health", headers={"X-Request-ID": "trace-abc"})
    assert response.headers["X-Request-ID"] == "trace-abc"


def test_openapi_document_builds(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "LEDGR API"
    assert "/api/v1/transactions" in schema["paths"]
