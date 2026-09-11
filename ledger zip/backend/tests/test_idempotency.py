"""Invariant I10: a retried write must not move money twice."""

import uuid

import pytest

from app.core.errors import IdempotencyKeyReused, IdempotentRequestInFlight
from app.models.enums import AccountType
from app.services import idempotency


@pytest.fixture
def accounts(client):
    def create(code, account_type):
        response = client.post(
            "/api/v1/accounts",
            json={"code": code, "name": code, "type": account_type.value, "currency": "USD"},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return create("IDEM.CASH.USD", AccountType.ASSET), create("IDEM.REV.USD", AccountType.REVENUE)


def payload(cash_id, revenue_id, amount=42_00, description="Idempotent posting"):
    return {
        "description": description,
        "currency": "USD",
        "entries": [
            {"account_id": cash_id, "direction": "DEBIT", "amount_minor": amount},
            {"account_id": revenue_id, "direction": "CREDIT", "amount_minor": amount},
        ],
    }


def test_retry_with_same_key_replays_the_original_response(client, accounts):
    cash_id, revenue_id = accounts
    body = payload(cash_id, revenue_id)
    key = str(uuid.uuid4())

    first = client.post("/api/v1/transactions", json=body, headers={"Idempotency-Key": key})
    second = client.post("/api/v1/transactions", json=body, headers={"Idempotency-Key": key})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["id"] == second.json()["id"]
    assert first.json() == second.json()

    listing = client.get("/api/v1/transactions").json()
    assert listing["total"] == 1


def test_same_key_with_a_different_body_is_refused(client, accounts):
    cash_id, revenue_id = accounts
    key = str(uuid.uuid4())

    client.post(
        "/api/v1/transactions",
        json=payload(cash_id, revenue_id, 10_00),
        headers={"Idempotency-Key": key},
    )
    conflicting = client.post(
        "/api/v1/transactions",
        json=payload(cash_id, revenue_id, 20_00),
        headers={"Idempotency-Key": key},
    )

    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_key_reused"
    assert client.get("/api/v1/transactions").json()["total"] == 1


def test_without_a_key_two_identical_requests_post_twice(client, accounts):
    """Idempotency is opt-in, and the API does not pretend otherwise."""
    cash_id, revenue_id = accounts
    body = payload(cash_id, revenue_id)

    client.post("/api/v1/transactions", json=body)
    client.post("/api/v1/transactions", json=body)

    assert client.get("/api/v1/transactions").json()["total"] == 2


def test_client_supplied_reference_still_blocks_duplicates(client, accounts):
    """The second line of defence when no idempotency key is used."""
    cash_id, revenue_id = accounts
    body = payload(cash_id, revenue_id) | {"reference": "TXN-INVOICE-4471"}

    assert client.post("/api/v1/transactions", json=body).status_code == 201
    duplicate = client.post("/api/v1/transactions", json=body)

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_reference"


def test_failed_request_releases_the_key_for_retry(client, accounts):
    """A key consumed by a failure would strand the caller forever."""
    cash_id, revenue_id = accounts
    key = str(uuid.uuid4())

    broken = payload(cash_id, revenue_id)
    broken["entries"][0]["account_id"] = str(uuid.uuid4())  # account does not exist
    failed = client.post("/api/v1/transactions", json=broken, headers={"Idempotency-Key": key})
    assert failed.status_code == 404

    retried = client.post(
        "/api/v1/transactions", json=payload(cash_id, revenue_id), headers={"Idempotency-Key": key}
    )
    assert retried.status_code == 201


def test_in_flight_duplicate_is_rejected_rather_than_queued(client, accounts):
    """A claim exists but has not completed: the retry is told to back off."""
    cash_id, revenue_id = accounts
    body = payload(cash_id, revenue_id)
    key = str(uuid.uuid4())

    # Simulate the first request still running.
    idempotency.claim(
        scope=idempotency.SCOPE_POST_TRANSACTION,
        key=key,
        request_hash=idempotency.fingerprint(body),
    )

    with pytest.raises(IdempotentRequestInFlight):
        idempotency.claim(
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
        )

    response = client.post("/api/v1/transactions", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotent_request_in_flight"


def test_keys_are_scoped_per_operation(client, accounts):
    """The same key means different things to different endpoints."""
    cash_id, revenue_id = accounts
    key = str(uuid.uuid4())
    body = payload(cash_id, revenue_id)

    created = client.post("/api/v1/transactions", json=body, headers={"Idempotency-Key": key})
    assert created.status_code == 201

    reversal = client.post(
        f"/api/v1/transactions/{created.json()['id']}/reverse",
        json={"reason": "same key, different operation"},
        headers={"Idempotency-Key": key},
    )
    assert reversal.status_code == 201


def test_fingerprint_ignores_key_order():
    assert idempotency.fingerprint({"a": 1, "b": 2}) == idempotency.fingerprint({"b": 2, "a": 1})
    assert idempotency.fingerprint({"a": 1}) != idempotency.fingerprint({"a": 2})


def test_reused_key_detection_is_hash_based(client, accounts):
    cash_id, revenue_id = accounts
    key = str(uuid.uuid4())
    body = payload(cash_id, revenue_id)

    idempotency.claim(
        scope=idempotency.SCOPE_POST_TRANSACTION,
        key=key,
        request_hash=idempotency.fingerprint(body),
    )
    with pytest.raises(IdempotencyKeyReused):
        idempotency.claim(
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint({"different": True}),
        )
