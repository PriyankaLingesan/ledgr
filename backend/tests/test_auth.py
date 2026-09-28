"""API key authentication.

`actor()` used to trust a client-supplied `X-Actor` header outright - any
caller could claim to be anyone. It now requires a real, DB-verified
`Authorization: Bearer <api-key>` credential, and the identity recorded on
every transaction/audit event comes from that verified key, never from a
header. These tests exercise the real HTTP path with a real, unauthenticated
`TestClient` (not the `client` fixture, which is pre-authenticated) so a
regression here would actually be caught.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.main import app
from app.models.auth import ApiKey
from app.services import auth as auth_service


@pytest.fixture
def anon_client():
    """A `TestClient` with no Authorization header - a genuine unauthenticated caller."""
    with TestClient(app) as test_client:
        yield test_client


def account_payload(code="AUTH.TEST.USD", account_type="ASSET"):
    return {"code": code, "name": code, "type": account_type, "currency": "USD"}


# --------------------------------------------------------------------------
# 1. unauthenticated write is rejected
# --------------------------------------------------------------------------


def test_posting_without_a_credential_is_rejected(anon_client):
    response = anon_client.post(
        "/api/v1/transactions",
        json={
            "description": "unauthenticated attempt",
            "currency": "USD",
            "entries": [
                {"account_id": str(uuid.uuid4()), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_creating_an_account_without_a_credential_is_rejected(anon_client):
    response = anon_client.post("/api/v1/accounts", json=account_payload())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_reconciliation_run_without_a_credential_is_rejected(anon_client):
    response = anon_client.post("/api/v1/reconciliation/run")
    assert response.status_code == 401


def test_malformed_authorization_header_is_rejected(anon_client):
    response = anon_client.post(
        "/api/v1/accounts",
        json=account_payload("AUTH.MALFORMED.USD"),
        headers={"Authorization": "not-a-bearer-token"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


# --------------------------------------------------------------------------
# 2. an authenticated caller can perform an authorized operation
# --------------------------------------------------------------------------


def test_authenticated_caller_can_create_an_account(client):
    response = client.post("/api/v1/accounts", json=account_payload("AUTH.OK.USD"))
    assert response.status_code == 201


def test_authenticated_caller_can_post_a_transaction(client):
    cash = client.post("/api/v1/accounts", json=account_payload("AUTH.CASH.USD", "ASSET")).json()
    revenue = client.post(
        "/api/v1/accounts", json=account_payload("AUTH.REV.USD", "REVENUE")
    ).json()

    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "authenticated posting",
            "currency": "USD",
            "entries": [
                {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": 500},
                {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": 500},
            ],
        },
    )
    assert response.status_code == 201


# --------------------------------------------------------------------------
# 3. audit identity comes from the authenticated key, not an arbitrary header
# --------------------------------------------------------------------------


def _post_transaction(client, description="actor check"):
    cash = client.post("/api/v1/accounts", json=account_payload(f"AUTH.{uuid.uuid4().hex[:8]}.C")).json()
    revenue = client.post(
        "/api/v1/accounts", json=account_payload(f"AUTH.{uuid.uuid4().hex[:8]}.R", "REVENUE")
    ).json()
    return client.post(
        "/api/v1/transactions",
        json={
            "description": description,
            "currency": "USD",
            "entries": [
                {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": 100},
                {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )


def test_recorded_actor_is_the_authenticated_identity_not_the_x_actor_header(client):
    """`client` is authenticated as the "test-client" key (see conftest). A
    spoofed `X-Actor` header must be silently ignored. `TransactionOut` (not
    `AccountOut`) is what carries the recorded `actor` field.
    """
    cash = client.post("/api/v1/accounts", json=account_payload("AUTH.SPOOF.C")).json()
    revenue = client.post(
        "/api/v1/accounts", json=account_payload("AUTH.SPOOF.R", "REVENUE")
    ).json()
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "spoofing attempt",
            "currency": "USD",
            "entries": [
                {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": 100},
                {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": 100},
            ],
        },
        headers={"X-Actor": "totally-not-test-client"},
    )
    assert response.status_code == 201
    assert response.json()["actor"] == "test-client"


def test_two_different_keys_produce_two_different_recorded_actors(db, api_key):
    # `api_key` (the fixture) is "test-client"; mint a second, distinct identity.
    _, other_raw_key = auth_service.create_api_key(db, label="ops-console")
    db.commit()

    with TestClient(app) as other_client:
        other_client.headers["Authorization"] = f"Bearer {other_raw_key}"
        response = _post_transaction(other_client, "second identity")
        assert response.status_code == 201
        assert response.json()["actor"] == "ops-console"


# --------------------------------------------------------------------------
# 4. invalid / revoked credentials are rejected
# --------------------------------------------------------------------------


def test_a_bogus_key_is_rejected(anon_client):
    response = anon_client.post(
        "/api/v1/accounts",
        json=account_payload("AUTH.BOGUS.USD"),
        headers={"Authorization": "Bearer ledgr_not_a_real_key"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_a_deactivated_key_is_rejected(db, api_key):
    record, raw_key = api_key
    db.execute(update(ApiKey).where(ApiKey.id == record.id).values(is_active=False))
    db.commit()

    with TestClient(app) as revoked_client:
        revoked_client.headers["Authorization"] = f"Bearer {raw_key}"
        response = revoked_client.post("/api/v1/accounts", json=account_payload("AUTH.REVOKED.USD"))
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"


def test_authenticate_service_rejects_unknown_key(db):
    from app.core.errors import InvalidCredentials

    with pytest.raises(InvalidCredentials):
        auth_service.authenticate(db, "ledgr_definitely_unknown")


def test_create_api_key_never_persists_the_plaintext(db):
    record, raw_key = auth_service.create_api_key(db, label="plaintext-check")
    assert record.key_hash != raw_key
    assert raw_key not in record.key_hash


# --------------------------------------------------------------------------
# reads remain open - only writes require authentication
# --------------------------------------------------------------------------


def test_reads_do_not_require_authentication(anon_client):
    response = anon_client.get("/api/v1/accounts")
    assert response.status_code == 200


def test_health_does_not_require_authentication(anon_client):
    response = anon_client.get("/api/v1/system/health")
    assert response.status_code == 200
