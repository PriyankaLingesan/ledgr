"""What the Meridian seeder refuses to do, and what it must never weaken.

These run against a fresh database per test (no full seed), so they are quick.
"""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal, session_scope
from app.demo import meridian_scenario as scn
from app.demo import meridian_seed as ms
from app.main import app
from app.models.account import Account
from app.models.enums import UserRole
from app.models.transaction import Transaction
from app.models.user import Organization, User
from app.services import auth as auth_service
from tests.conftest import make_organization

AS_OF = date(2026, 10, 16)
API = "/api/v1"


def _orgs():
    with session_scope(unscoped=True) as db:
        return db.execute(select(func.count(Organization.id))).scalar_one()


def test_an_unmarked_organization_with_the_same_name_is_never_adopted():
    stranger = make_organization(scn.ORG_NAME)  # a real organization that happens to share the name
    before = _orgs()
    with pytest.raises(ms.SeedError, match="not marked"):
        ms.seed(AS_OF, log=lambda *_: None)
    assert _orgs() == before
    with session_scope(stranger.id) as db:  # nothing was written into it
        assert db.execute(select(func.count(Account.id))).scalar_one() == 0
    with session_scope(unscoped=True) as db:
        assert (
            db.execute(
                select(Organization.demo_scenario).where(Organization.id == stranger.id)
            ).scalar()
            is None
        )


def test_an_existing_users_email_blocks_the_seed_without_creating_anything():
    other = make_organization("Someone Else")
    with session_scope(unscoped=True) as db:
        db.add(
            User(
                organization_id=other.id,
                email=scn.PERSONAS["owner"].email.upper(),  # case-insensitive clash
                full_name="Real Person",
                password_hash=auth_service.hash_password("a-real-password-1"),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
    before = _orgs()
    with pytest.raises(ms.SeedError, match="already belong"):
        ms.ensure_organization(AS_OF)
    assert _orgs() == before
    with session_scope(unscoped=True) as db:  # the real person's login is untouched
        user = db.execute(
            select(User).where(func.lower(User.email) == scn.PERSONAS["owner"].email)
        ).scalar_one()
        assert auth_service.verify_password("a-real-password-1", user.password_hash)


def test_personas_start_locked_with_no_usable_password_and_the_anchor_is_recorded():
    before = _orgs()
    org_id, anchored = ms.ensure_organization(AS_OF)
    assert anchored == AS_OF
    # The audit event that anchors the timeline belongs to the organization (it was once written
    # from an unscoped session and was invisible to it).
    assert ms.recorded_as_of(org_id) == AS_OF
    with session_scope(unscoped=True) as db:
        org = db.get(Organization, org_id)
        users = db.execute(select(User).where(User.organization_id == org_id)).scalars().all()
    assert org.demo_scenario == scn.SCENARIO_KEY and org.base_currency == "INR"
    assert org.approval_threshold_minor == scn.APPROVAL_THRESHOLD_MINOR
    assert len(users) == 5 and not any(u.is_active for u in users)
    assert all(
        not auth_service.verify_password(p, u.password_hash)
        for u in users
        for p in ("", "!", ms.UNUSABLE_HASH)
    )
    # ...so the real login flow refuses them, with the same generic message as any bad login.
    client = TestClient(app)
    r = client.post(
        f"{API}/auth/login", json={"email": scn.PERSONAS["owner"].email, "password": "whatever-123"}
    )
    assert (
        r.status_code == 401
        and r.json()["error"]["message"] == "the email or password is incorrect"
    )
    # Re-running only completes missing pieces: still one organization, still five users.
    again, _ = ms.ensure_organization(AS_OF)
    assert again == org_id and _orgs() == before + 1


def test_configuring_users_is_local_only_never_overwrites_and_never_logs_the_password(
    capsys, monkeypatch
):
    ms.ensure_organization(AS_OF)
    secret = "this-is-a-test-only-password"

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(ms.SeedError, match="production"):
        ms.configure_users(secret)
    monkeypatch.setattr(settings, "environment", "test")

    with pytest.raises(Exception, match="at least"):
        ms.configure_users("short")
    with pytest.raises(ms.SeedError, match="unknown persona"):
        ms.configure_users(secret, ["nobody"])

    first = ms.configure_users(secret, ["owner", "checker"])
    assert first == {"owner": "activated", "checker": "activated"}
    second = ms.configure_users("a-different-test-password", ["owner", "maker"])
    assert second["owner"].startswith(
        "already configured"
    )  # an existing password is never replaced
    assert second["maker"] == "activated"
    out = capsys.readouterr()
    assert secret not in out.out + out.err and "a-different-test-password" not in out.out + out.err

    # The real login flow now works for the activated persona, and only with the right password.
    client = TestClient(app)
    ok = client.post(
        f"{API}/auth/login", json={"email": scn.PERSONAS["owner"].email, "password": secret}
    )
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "OWNER"
    assert ok.json()["user"]["organization_demo_scenario"] == scn.SCENARIO_KEY
    bad = client.post(
        f"{API}/auth/login",
        json={"email": scn.PERSONAS["owner"].email, "password": "a-different-test-password"},
    )
    assert bad.status_code == 401
    # The auditor was never activated, so it still cannot sign in.
    locked = client.post(
        f"{API}/auth/login", json={"email": scn.PERSONAS["auditor"].email, "password": secret}
    )
    assert locked.status_code == 401


def test_configuring_users_does_not_touch_other_organizations():
    other = make_organization("Another Co")
    with session_scope(unscoped=True) as db:
        db.add(
            User(
                organization_id=other.id,
                email="someone@another.test",
                full_name="Someone",
                password_hash=auth_service.hash_password("original-password-1"),
                role=UserRole.OWNER,
            )
        )
        db.commit()
    ms.ensure_organization(AS_OF)
    ms.configure_users("this-is-a-test-only-password")
    with session_scope(unscoped=True) as db:
        user = db.execute(select(User).where(User.email == "someone@another.test")).scalar_one()
    assert user.is_active and auth_service.verify_password(
        "original-password-1", user.password_hash
    )


def test_only_a_service_level_loader_can_set_the_system_time_of_a_posting(client, cash_and_revenue):
    """`posted_at` exists so a simulated history can be loaded. No API path may set it."""
    cash, revenue = cash_and_revenue
    body = {
        "description": "tries to backdate itself",
        "currency": "USD",
        "posted_at": "2020-01-01T00:00:00Z",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 1_000},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 1_000},
        ],
    }
    r = client.post(f"{API}/transactions", json=body)
    assert r.status_code == 201
    posted = datetime.fromisoformat(r.json()["posted_at"].replace("Z", "+00:00"))
    assert posted.year >= datetime.now(UTC).year  # the database clock decided, not the client
    with SessionLocal() as db:
        from app.db.tenancy import mark_unscoped

        mark_unscoped(db)
        assert db.execute(select(func.count(Transaction.id))).scalar_one() == 1
