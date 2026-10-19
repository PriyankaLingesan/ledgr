"""Human authentication: login, sessions, lockout, first-run setup.

Everything goes through the real HTTP endpoints and a real database; sessions
are manipulated in SQL only to simulate the passage of time.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.core.config import settings
from app.main import app
from app.models.audit import AuditEvent
from app.models.enums import UserRole
from app.models.user import User, UserSession
from app.services import auth as auth_service
from tests.conftest import TEST_PASSWORD


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c


def login(client, email, password=TEST_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def events(db, event_type):
    db.expire_all()
    return list(
        db.execute(select(AuditEvent).where(AuditEvent.event_type == event_type)).scalars().all()
    )


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------


def test_passwords_are_stored_as_salted_scrypt_hashes_never_plaintext(db, make_user):
    user = make_user(UserRole.FINANCE)
    db.refresh(user)
    assert TEST_PASSWORD not in user.password_hash
    assert user.password_hash.startswith("scrypt$")
    assert auth_service.verify_password(TEST_PASSWORD, user.password_hash)
    assert not auth_service.verify_password("wrong-password", user.password_hash)


def test_the_same_password_hashes_differently_each_time():
    assert auth_service.hash_password("same-password-1") != auth_service.hash_password(
        "same-password-1"
    )


def test_a_weak_password_is_refused(client, org):
    response = client.post(
        "/api/v1/users",
        json={
            "email": "weak@example.com",
            "full_name": "Weak",
            "password": "short",
            "role": "FINANCE",
        },
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


def test_valid_login_returns_a_session_and_the_users_permissions(anon, make_user):
    user = make_user(UserRole.AUDITOR)
    response = login(anon, user.email)
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"].startswith("lgs_")
    assert body["user"]["role"] == "AUDITOR"
    assert "transactions:read" in body["user"]["permissions"]
    assert "transactions:post" not in body["user"]["permissions"]
    assert response.headers["cache-control"] == "no-store"


def test_login_is_case_insensitive_on_email(anon, make_user):
    make_user(UserRole.FINANCE, email="Mixed.Case@Example.com")
    assert login(anon, "mixed.case@example.com").status_code == 200


def test_invalid_password_is_rejected_with_a_generic_message(anon, make_user):
    user = make_user(UserRole.FINANCE)
    response = login(anon, user.email, "not-the-password")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert response.json()["error"]["message"] == "the email or password is incorrect"


def test_unknown_email_gets_the_same_response_as_a_wrong_password(anon, make_user):
    user = make_user(UserRole.FINANCE)
    wrong_password = login(anon, user.email, "not-the-password")
    unknown_user = login(anon, "nobody@example.com", "not-the-password")
    assert unknown_user.status_code == wrong_password.status_code == 401
    assert unknown_user.json()["error"]["message"] == wrong_password.json()["error"]["message"]


def test_login_success_and_failure_are_audited_without_the_password(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    login(anon, user.email, "not-the-password")
    login(anon, user.email)
    assert len(events(db, "auth.login_failed")) == 1
    assert len(events(db, "auth.login")) == 1
    for event in events(db, "auth.login_failed") + events(db, "auth.login"):
        assert TEST_PASSWORD not in str(event.payload)
        assert "not-the-password" not in str(event.payload)


def test_a_deactivated_user_cannot_log_in(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    db.execute(update(User).where(User.id == user.id).values(is_active=False))
    db.commit()
    assert login(anon, user.email).status_code == 401


# --------------------------------------------------------------------------
# lockout
# --------------------------------------------------------------------------


def test_repeated_failures_lock_the_account_even_for_the_right_password(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    for _ in range(settings.login_max_failures):
        assert login(anon, user.email, "wrong-password-x").status_code == 401

    # Locked: the correct password is now refused too, with the same generic error.
    response = login(anon, user.email)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    db.expire_all()
    assert db.get(User, user.id).locked_until is not None


def test_lockout_expires(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    for _ in range(settings.login_max_failures):
        login(anon, user.email, "wrong-password-x")
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()
    assert login(anon, user.email).status_code == 200


def test_a_successful_login_resets_the_failure_counter(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    for _ in range(settings.login_max_failures - 1):
        login(anon, user.email, "wrong-password-x")
    assert login(anon, user.email).status_code == 200
    db.expire_all()
    assert db.get(User, user.id).failed_login_count == 0


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def test_protected_endpoint_without_a_token_is_401(anon):
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_me_returns_the_signed_in_user(user_client):
    client = user_client(UserRole.OPERATIONS)
    body = client.get("/api/v1/auth/me").json()
    assert body["role"] == "OPERATIONS"
    assert body["email"] == "operations@example.com"
    assert body["kind"] == "user"


def test_a_garbage_session_token_is_rejected(anon):
    response = anon.get("/api/v1/auth/me", headers={"Authorization": "Bearer lgs_garbage"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_session_tokens_are_stored_only_as_hashes(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    token = login(anon, user.email).json()["access_token"]
    stored = db.execute(select(UserSession.token_hash)).scalars().all()
    assert token not in stored
    assert all(len(h) == 64 for h in stored)


def test_logout_ends_the_session_immediately(user_client, db):
    client = user_client(UserRole.FINANCE)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204

    after = client.get("/api/v1/auth/me")
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "session_expired"
    assert len(events(db, "auth.logout")) == 1


def test_an_expired_session_is_rejected_with_session_expired(db, user_client):
    client = user_client(UserRole.FINANCE)
    db.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_an_idle_session_is_rejected(db, user_client):
    client = user_client(UserRole.FINANCE)
    long_ago = datetime.now(UTC) - timedelta(minutes=settings.session_idle_minutes + 5)
    db.execute(update(UserSession).values(last_seen_at=long_ago))
    db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_deactivating_a_user_kills_their_live_sessions(client, user_client, db, org):
    finance = user_client(UserRole.FINANCE, email="to.disable@example.com")
    user_id = finance.get("/api/v1/auth/me").json()["id"]
    assert finance.get("/api/v1/auth/me").status_code == 200

    response = client.patch(f"/api/v1/users/{user_id}", json={"is_active": False})
    assert response.status_code == 200
    assert finance.get("/api/v1/auth/me").status_code == 401


def test_a_client_supplied_actor_header_is_ignored(user_client):
    client = user_client(UserRole.FINANCE, email="honest@example.com")
    me = client.get("/api/v1/auth/me", headers={"X-Actor": "someone-else"}).json()
    assert me["email"] == "honest@example.com"


def test_changing_password_requires_the_current_one_and_ends_sessions(db, user_client, anon):
    client = user_client(UserRole.FINANCE, email="changer@example.com")
    wrong = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "nope-nope-nope", "new_password": "a-brand-new-passphrase"},
    )
    assert wrong.status_code == 422

    ok = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "a-brand-new-passphrase"},
    )
    assert ok.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401  # old session ended
    assert login(anon, "changer@example.com", TEST_PASSWORD).status_code == 401
    assert login(anon, "changer@example.com", "a-brand-new-passphrase").status_code == 200


# --------------------------------------------------------------------------
# registration: a new organization and its OWNER, in one transaction
# --------------------------------------------------------------------------

REGISTER = {
    "organization_name": "Acme Payments",
    "base_currency": "USD",
    "email": "founder@acme.test",
    "full_name": "Founder",
    "password": "a-long-enough-passphrase",
}


def test_auth_config_is_public(anon):
    body = anon.get("/api/v1/auth/config").json()
    assert body == {"signup_enabled": True, "signup_code_required": False}


def test_register_creates_the_org_and_an_owner_and_signs_in(anon, db):
    response = anon.post("/api/v1/auth/register", json=REGISTER)
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["role"] == "OWNER"
    assert body["user"]["organization_name"] == "Acme Payments"
    assert body["access_token"].startswith("lgs_")
    assert body["csrf_token"]
    # The browser's credential is an httpOnly cookie.
    cookie = response.headers["set-cookie"].lower()
    assert "ledgr_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert len(events_for_org(body["user"]["organization_id"], "organization.registered")) == 1


def events_for_org(org_id, event_type):
    from app.db.session import SessionLocal
    from app.db.tenancy import mark_unscoped

    with SessionLocal() as session:
        mark_unscoped(session)
        return list(
            session.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.organization_id == uuid.UUID(org_id),
                )
            ).scalars()
        )


def test_many_organizations_can_register_independently(anon):
    first = anon.post("/api/v1/auth/register", json=REGISTER).json()
    second = anon.post(
        "/api/v1/auth/register",
        json=REGISTER | {"email": "other@rival.test", "organization_name": "Rival Co"},
    ).json()
    assert first["user"]["organization_id"] != second["user"]["organization_id"]
    assert second["user"]["role"] == "OWNER"


def test_a_duplicate_email_is_refused_and_creates_no_organization(anon):
    from app.db.session import SessionLocal
    from app.db.tenancy import mark_unscoped
    from app.models.user import Organization

    assert anon.post("/api/v1/auth/register", json=REGISTER).status_code == 201
    with SessionLocal() as session:
        mark_unscoped(session)
        before = session.execute(select(func.count(Organization.id))).scalar_one()

    dup = anon.post(
        "/api/v1/auth/register",
        json=REGISTER | {"organization_name": "Squatter Inc", "email": "FOUNDER@acme.test"},
    )
    assert dup.status_code == 409
    with SessionLocal() as session:
        mark_unscoped(session)
        after = session.execute(select(func.count(Organization.id))).scalar_one()
    assert after == before  # the organization insert rolled back with the user


def test_registration_validates_input(anon):
    assert (
        anon.post("/api/v1/auth/register", json=REGISTER | {"password": "short"}).status_code == 422
    )
    assert (
        anon.post("/api/v1/auth/register", json=REGISTER | {"email": "not-an-email"}).status_code
        == 422
    )
    assert (
        anon.post("/api/v1/auth/register", json=REGISTER | {"organization_name": "  "}).status_code
        == 422
    )
    assert (
        anon.post("/api/v1/auth/register", json=REGISTER | {"base_currency": "ZZZ"}).status_code
        == 422
    )


def test_signup_can_be_disabled(anon, monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    assert anon.get("/api/v1/auth/config").json()["signup_enabled"] is False
    response = anon.post("/api/v1/auth/register", json=REGISTER)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "signup_disabled"


def test_signup_code_is_required_when_configured(anon, monkeypatch):
    monkeypatch.setattr(settings, "signup_token", "invite-code-123")
    assert anon.get("/api/v1/auth/config").json()["signup_code_required"] is True
    assert anon.post("/api/v1/auth/register", json=REGISTER).status_code == 403
    ok = anon.post("/api/v1/auth/register", json=REGISTER | {"signup_code": "invite-code-123"})
    assert ok.status_code == 201


def test_registration_is_rate_limited_per_client(anon, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_register_per_hour", 2)
    for i in range(2):
        assert (
            anon.post(
                "/api/v1/auth/register", json=REGISTER | {"email": f"u{i}@x.test"}
            ).status_code
            == 201
        )
    limited = anon.post("/api/v1/auth/register", json=REGISTER | {"email": "u9@x.test"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1


def test_login_is_rate_limited_per_client(anon, make_user, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 3)
    user = make_user(UserRole.FINANCE)
    for _ in range(3):
        assert login(anon, user.email, "wrong-password-x").status_code == 401
    assert login(anon, user.email).status_code == 429


# --------------------------------------------------------------------------
# cookie sessions and CSRF
# --------------------------------------------------------------------------


def test_cookie_authenticated_reads_work_without_a_token_header(anon, make_user):
    user = make_user(UserRole.FINANCE)
    assert login(anon, user.email).status_code == 200  # TestClient keeps the cookie
    assert "Authorization" not in anon.headers
    assert anon.get("/api/v1/auth/me").json()["email"] == user.email


def test_cookie_authenticated_writes_require_the_csrf_token(anon, make_user, cash_and_revenue):
    cash, revenue = cash_and_revenue
    user = make_user(UserRole.FINANCE)
    csrf = login(anon, user.email).json()["csrf_token"]
    payload = {
        "description": "csrf check",
        "currency": "USD",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100},
        ],
    }

    missing = anon.post("/api/v1/transactions", json=payload)
    assert missing.status_code == 403
    assert missing.json()["error"]["details"] == {"reason": "csrf"}
    wrong = anon.post("/api/v1/transactions", json=payload, headers={"X-CSRF-Token": "forged"})
    assert wrong.status_code == 403

    ok = anon.post("/api/v1/transactions", json=payload, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201


def test_bearer_authentication_needs_no_csrf_token(user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client = user_client(UserRole.FINANCE)
    client.cookies.clear()  # prove it is the header, not the cookie, that authenticates
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "bearer",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100},
            ],
        },
    )
    assert response.status_code == 201


def test_logout_clears_the_cookie_and_ends_the_session(anon, make_user):
    user = make_user(UserRole.FINANCE)
    csrf = login(anon, user.email).json()["csrf_token"]
    response = anon.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert (
        'ledgr_session=""' in response.headers["set-cookie"]
        or "max-age=0" in response.headers["set-cookie"].lower()
    )
    anon.cookies.clear()
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_the_session_cookie_is_ignored_once_the_session_is_revoked(anon, make_user, db):
    user = make_user(UserRole.FINANCE)
    login(anon, user.email)
    db.execute(update(UserSession).values(revoked_at=datetime.now(UTC)))
    db.commit()
    response = anon.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


# --------------------------------------------------------------------------
# user administration
# --------------------------------------------------------------------------


def test_admin_can_create_users_and_email_is_unique_case_insensitively(client, org):
    payload = {
        "email": "new.hire@example.com",
        "full_name": "New Hire",
        "password": "a-long-enough-passphrase",
        "role": "OPERATIONS",
    }
    assert client.post("/api/v1/users", json=payload).status_code == 201
    dup = client.post("/api/v1/users", json=payload | {"email": "NEW.HIRE@example.com"})
    assert dup.status_code == 409


def test_an_organization_always_keeps_one_active_owner(user_client, make_user):
    owner = user_client(UserRole.OWNER, email="only.owner@example.com")
    me = owner.get("/api/v1/auth/me").json()["id"]
    assert owner.patch(f"/api/v1/users/{me}", json={"role": "AUDITOR"}).status_code == 409
    assert owner.patch(f"/api/v1/users/{me}", json={"is_active": False}).status_code == 409

    second = make_user(UserRole.OWNER, email="second.owner@example.com")
    assert owner.patch(f"/api/v1/users/{me}", json={"role": "AUDITOR"}).status_code == 200
    assert second.role is UserRole.OWNER


def test_an_admin_cannot_grant_or_touch_roles_at_or_above_admin(user_client, make_user):
    admin = user_client(UserRole.ADMIN, email="the.admin@example.com")
    peer = make_user(UserRole.ADMIN, email="peer.admin@example.com")
    owner = make_user(UserRole.OWNER, email="the.owner@example.com")
    worker = make_user(UserRole.FINANCE, email="worker@example.com")

    # cannot promote someone to ADMIN or OWNER...
    assert admin.patch(f"/api/v1/users/{worker.id}", json={"role": "ADMIN"}).status_code == 403
    assert admin.patch(f"/api/v1/users/{worker.id}", json={"role": "OWNER"}).status_code == 403
    # ...cannot change or deactivate a peer admin or an owner...
    assert admin.patch(f"/api/v1/users/{peer.id}", json={"role": "AUDITOR"}).status_code == 403
    assert admin.patch(f"/api/v1/users/{owner.id}", json={"is_active": False}).status_code == 403
    assert (
        admin.post(
            f"/api/v1/users/{owner.id}/reset-password", json={"new_password": "x" * 12}
        ).status_code
        == 403
    )
    # ...cannot create one...
    create = admin.post(
        "/api/v1/users",
        json={
            "email": "new.admin@example.com",
            "full_name": "N",
            "password": "x" * 12,
            "role": "ADMIN",
        },
    )
    assert create.status_code == 403
    # ...but can manage roles below ADMIN.
    assert admin.patch(f"/api/v1/users/{worker.id}", json={"role": "AUDITOR"}).status_code == 200


def test_an_owner_can_grant_any_role(user_client, make_user):
    owner = user_client(UserRole.OWNER, email="boss@example.com")
    worker = make_user(UserRole.FINANCE, email="promotee@example.com")
    assert owner.patch(f"/api/v1/users/{worker.id}", json={"role": "ADMIN"}).status_code == 200


def test_user_changes_are_audited(client, make_user, db):
    user = make_user(UserRole.FINANCE)
    client.patch(f"/api/v1/users/{user.id}", json={"role": "OPERATIONS"})
    created = events(db, "user.created")
    updated = events(db, "user.updated")
    assert created and updated
    assert updated[0].payload["changes"]["role"] == {"from": "FINANCE", "to": "OPERATIONS"}


def test_password_hash_never_appears_in_user_responses(client, make_user):
    make_user(UserRole.FINANCE)
    text_body = client.get("/api/v1/users").text
    assert "scrypt" not in text_body
    assert "password" not in text_body.lower().replace("last_login", "")


def test_session_rows_cascade_with_users(db, anon, make_user):
    user = make_user(UserRole.FINANCE)
    login(anon, user.email)
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar() == 1
    db.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
    db.commit()
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar() == 0
