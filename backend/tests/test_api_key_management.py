"""API key management: create, role, last-used, revoke - integrated with the
same authentication path every other request uses."""

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.main import app
from app.models.audit import AuditEvent
from app.models.auth import ApiKey


def keyed_client(raw: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {raw}"
    return c


def create_key(client, label="reporting-job", role="AUDITOR"):
    response = client.post("/api/v1/api-keys", json={"label": label, "role": role})
    assert response.status_code == 201, response.text
    return response.json()


def test_creating_a_key_returns_the_plaintext_exactly_once(client, db):
    created = create_key(client)
    assert created["key"].startswith("ledgr_")
    assert created["key_prefix"] == created["key"][:10]

    # Never retrievable again: neither the list nor the database holds it.
    listing = client.get("/api/v1/api-keys")
    assert created["key"] not in listing.text
    assert all("key" not in item or item.get("key") is None for item in listing.json())
    stored = db.execute(select(ApiKey.key_hash)).scalars().all()
    assert created["key"] not in stored
    assert (
        db.execute(
            text("SELECT count(*) FROM api_keys WHERE key_hash = :k"), {"k": created["key"]}
        ).scalar()
        == 0
    )


def test_a_created_key_authenticates_with_its_role(client):
    created = create_key(client, role="AUDITOR")
    key_client = keyed_client(created["key"])
    assert key_client.get("/api/v1/accounts").status_code == 200
    assert (
        key_client.post(
            "/api/v1/accounts",
            json={"code": "K.A", "name": "K", "type": "ASSET", "currency": "USD"},
        ).status_code
        == 403
    )
    me = key_client.get("/api/v1/auth/me").json()
    assert me["kind"] == "apikey"
    assert me["role"] == "AUDITOR"
    assert me["name"] == "reporting-job"


def test_last_used_is_updated_when_a_key_is_used(client):
    created = create_key(client)
    assert created["last_used_at"] is None
    keyed_client(created["key"]).get("/api/v1/accounts")
    listing = {k["id"]: k for k in client.get("/api/v1/api-keys").json()}
    assert listing[created["id"]]["last_used_at"] is not None


def test_a_revoked_key_stops_working_immediately(client):
    created = create_key(client)
    key_client = keyed_client(created["key"])
    assert key_client.get("/api/v1/accounts").status_code == 200

    revoked = client.post(f"/api/v1/api-keys/{created['id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert revoked.json()["is_active"] is False

    after = key_client.get("/api/v1/accounts")
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "invalid_credentials"


def test_revoking_twice_is_harmless_and_audited_once(client, db):
    created = create_key(client)
    assert client.post(f"/api/v1/api-keys/{created['id']}/revoke").status_code == 200
    assert client.post(f"/api/v1/api-keys/{created['id']}/revoke").status_code == 200
    db.expire_all()
    revoked = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "api_key.revoked"))
        .scalars()
        .all()
    )
    assert len(revoked) == 1


def test_key_creation_and_revocation_are_audited_without_the_secret(client, db):
    created = create_key(client)
    client.post(f"/api/v1/api-keys/{created['id']}/revoke")
    db.expire_all()
    events = (
        db.execute(
            select(AuditEvent).where(
                AuditEvent.event_type.in_(["api_key.created", "api_key.revoked"])
            )
        )
        .scalars()
        .all()
    )
    assert {e.event_type for e in events} == {"api_key.created", "api_key.revoked"}
    assert all(created["key"] not in str(e.payload) for e in events)


def test_key_labels_are_unique(client):
    create_key(client, label="duplicate-label")
    response = client.post("/api/v1/api-keys", json={"label": "duplicate-label", "role": "FINANCE"})
    assert response.status_code == 409


def test_actions_by_a_key_are_attributed_to_its_label(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    created = create_key(client, label="settlement-bot", role="FINANCE")
    response = keyed_client(created["key"]).post(
        "/api/v1/transactions",
        json={
            "description": "bot posting",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 500},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 500},
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["actor"] == "settlement-bot"


def test_legacy_keys_minted_without_a_role_stay_admin(db, api_key):
    record, _ = api_key  # minted via create_api_key(label=...) with no role, like the CLI
    stored = db.execute(select(ApiKey).where(ApiKey.id == record.id)).scalar_one()
    assert stored.role.value == "ADMIN"
