"""Invariant I10: a retried write must not move money twice."""

import uuid

import pytest

from app.core.errors import IdempotencyKeyReused, IdempotentRequestInFlight
from app.models.enums import AccountType
from app.schemas.transaction import TransactionCreate
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


def canonical_hash(body: dict) -> str:
    """The exact transformation `POST /transactions` applies before hashing.

    The route hashes `payload.model_dump(mode="json")` of the *parsed*
    `TransactionCreate` - which fills in every optional field's default
    (`reference: null`, `metadata: {}`, each entry's `memo: null`, ...) - not
    the bare dict a test builds by hand. A test that pre-seeds a claim must
    hash the payload the same way, or its hash will never match what a real
    retry of the same logical request produces.
    """
    return idempotency.fingerprint(TransactionCreate(**body).model_dump(mode="json"))


def test_claim_of_a_brand_new_key_succeeds_immediately():
    """Regression: a never-before-seen key must never read back as in-flight.

    `claim()` decides whether it won the `INSERT ... ON CONFLICT DO NOTHING`
    race by checking the statement result. Reading that off
    `CursorResult.rowcount` is unreliable with psycopg for this statement
    shape - it can report -1 even though the row was genuinely inserted -
    which made every first-ever claim misread itself as a pre-existing
    in-flight request. `claim()` must use `RETURNING` instead, so it returns
    `None` (claim acquired) rather than raising.
    """
    key = str(uuid.uuid4())
    result = idempotency.claim(
        scope=idempotency.SCOPE_POST_TRANSACTION,
        key=key,
        request_hash=idempotency.fingerprint({"probe": True}),
    )
    assert result is None


def test_fresh_idempotency_key_never_reports_in_flight_over_http(client, accounts):
    """The same regression, exercised through the real POST endpoint.

    Posting five transactions in a row, each with its own new key, must
    succeed every time - not fail on the first attempt the way the rowcount
    bug did.
    """
    cash_id, revenue_id = accounts
    for _ in range(5):
        key = str(uuid.uuid4())
        response = client.post(
            "/api/v1/transactions",
            json=payload(cash_id, revenue_id),
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 201, response.text
        assert response.headers.get("Idempotent-Replay") is None


def test_different_keys_each_create_their_own_transaction(client, accounts):
    cash_id, revenue_id = accounts
    ids = set()
    for _ in range(3):
        key = str(uuid.uuid4())
        response = client.post(
            "/api/v1/transactions",
            json=payload(cash_id, revenue_id),
            headers={"Idempotency-Key": key},
        )
        assert response.status_code == 201, response.text
        ids.add(response.json()["id"])

    assert len(ids) == 3
    assert client.get("/api/v1/transactions").json()["total"] == 3


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
        request_hash=canonical_hash(body),
    )

    with pytest.raises(IdempotentRequestInFlight):
        idempotency.claim(
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            request_hash=canonical_hash(body),
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
