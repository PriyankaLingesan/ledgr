"""Test harness.

These tests run against a real PostgreSQL database, not SQLite. Half of what
LEDGR guarantees - row locks, native enums, partial unique indexes, the
immutability triggers - simply does not exist in SQLite, so testing against it
would verify a different system than the one that ships.
"""

import os
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND_DIR = Path(__file__).resolve().parents[1]

_DEFAULT_URL = "postgresql+psycopg://ledgr:ledgr@localhost:5433/ledgr"
_BASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_URL)
_ROOT, _, _NAME = _BASE_URL.rpartition("/")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "ledgr_test")
TEST_DATABASE_URL = f"{_ROOT}/{TEST_DB_NAME}"

# Must happen before app modules import their (cached) settings.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("ENVIRONMENT", "test")


def _ensure_test_database() -> None:
    admin = sa.create_engine(f"{_ROOT}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            ).scalar()
            if not exists:
                conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin.dispose()


_ensure_test_database()

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from alembic import command  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.db.tenancy import bind_organization, mark_unscoped  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import AccountType, UserRole  # noqa: E402
from app.models.user import Organization  # noqa: E402
from app.services import accounts as account_service  # noqa: E402
from app.services import auth as auth_service  # noqa: E402
from app.services import users as user_service  # noqa: E402

TEST_PASSWORD = "correct-horse-battery"

TABLES = (
    "investigation_notes",
    "reconciliation_decisions",
    "transaction_drafts",
    "user_sessions",
    "users",
    "organizations",
    "audit_events",
    "idempotency_keys",
    "investigations",
    "reconciliation_results",
    "reconciliation_runs",
    "external_transactions",
    "api_keys",
    "ledger_entries",
    "transactions",
    "account_balances",
    "accounts",
)


@pytest.fixture(scope="session", autouse=True)
def migrated_schema():
    """Build the schema exactly the way production does: through Alembic.

    Using `metadata.create_all` here would skip the migration's triggers, and
    the immutability guarantees would go untested.
    """
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
        conn.commit()

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables(request):
    """TRUNCATE between tests.

    TRUNCATE fires no row-level triggers, so it clears the append-only tables
    without needing to disable the guards that protect them.

    A module marked `shared_world` builds one expensive world (for example the
    Meridian demonstration, ~20 s to seed) once and runs several tests against
    it, so it is exempt; its own module-scoped fixture truncates before seeding.
    """
    if request.node.get_closest_marker("shared_world"):
        yield
        return
    with engine.connect() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        conn.commit()
    yield


_current = {"org_id": None}


def new_session(organization_id=None):
    """A session scoped to the current test's organization (or another one).

    Every ledger table is tenant-scoped, so a session without an organization
    sees nothing - tests that open their own sessions use this helper.
    """
    session = SessionLocal()
    bind_organization(session, organization_id or _current["org_id"])
    return session


def current_org_id():
    return _current["org_id"]


def truncate_everything():
    """Empty every table (module-scoped fixtures of `shared_world` modules)."""
    with engine.connect() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        conn.commit()


def make_organization(name: str = "Test Org"):
    with SessionLocal() as session:
        mark_unscoped(session)
        organization = Organization(name=name, base_currency="USD")
        session.add(organization)
        session.commit()
        return organization


@pytest.fixture(autouse=True)
def _no_live_ai(monkeypatch):
    """The suite must never reach a real model, whatever the developer's `.env` holds.

    A key in `backend/.env` makes `get_provider()` return a live provider, so any test that
    touches an AI route without patching it would send a real (billable) request. Blank the
    key for every test; tests that exercise AI behaviour patch `get_provider` themselves.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_api_key", "")


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The limiter is in-process state: one test's logins must not throttle the next."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def org(clean_tables):
    """The primary organization for this test (what the default fixtures act as)."""
    organization = make_organization("Test Org")
    _current["org_id"] = organization.id
    return organization


@pytest.fixture
def other_org(org):
    """A second, independent organization - for isolation tests."""
    return make_organization("Other Org")


@pytest.fixture
def db(org):
    session = new_session(org.id)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def api_key(org):
    """Mints a real, DB-backed API key so HTTP-level tests authenticate the
    same way a real caller would - not a bypass, the actual auth path.
    """
    session = SessionLocal()
    try:
        record, raw_key = auth_service.create_api_key(
            session, label="test-client", organization_id=org.id
        )
        yield record, raw_key
    finally:
        session.close()


@pytest.fixture
def client(api_key):
    """Pre-authenticated by default: every existing test that posts through
    the API exercises the real, server-verified auth path (see `api_key`
    above) via a default header, rather than every test file needing its own
    `Authorization` boilerplate. Tests that specifically exercise auth
    failure build their own unauthenticated `TestClient` instead.
    """
    _, raw_key = api_key
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {raw_key}"
        yield test_client


@pytest.fixture
def make_account(db):
    """Factory for accounts, created through the service under test."""

    def _make(
        code: str,
        account_type: AccountType = AccountType.ASSET,
        currency: str = "USD",
        allows_negative_balance: bool = True,
        name: str | None = None,
    ):
        return account_service.create_account(
            db,
            code=code,
            name=name or code.replace(".", " ").title(),
            account_type=account_type,
            currency=currency,
            allows_negative_balance=allows_negative_balance,
            actor="pytest",
        )

    return _make


@pytest.fixture
def cash_and_revenue(make_account):
    """The smallest useful chart of accounts: one asset, one revenue."""
    cash = make_account("CASH.USD", AccountType.ASSET)
    revenue = make_account("REV.USD", AccountType.REVENUE)
    return cash, revenue


@pytest.fixture
def make_user(db, org):
    """Create a real user through the service layer (hashed password and all)."""

    def _make(role: UserRole, email: str | None = None, password: str = TEST_PASSWORD):
        return user_service.create_user(
            db,
            organization_id=org.id,
            actor_role=UserRole.OWNER,
            email=email or f"{role.value.lower()}@example.com",
            full_name=f"{role.value.title()} Tester",
            password=password,
            role=role,
            actor="pytest",
        )

    return _make


@pytest.fixture
def user_client(make_user):
    """A TestClient signed in as a real user of the given role - through the
    actual login endpoint, so the session path is what gets exercised."""
    opened: list[TestClient] = []

    def _make(role: UserRole, email: str | None = None) -> TestClient:
        user = make_user(role, email=email)
        test_client = TestClient(app)
        response = test_client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text
        test_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        opened.append(test_client)
        return test_client

    yield _make
    for c in opened:
        c.close()


@pytest.fixture
def role_client(db, org):
    """A TestClient authenticating with an API key of the given role."""
    opened: list[TestClient] = []

    def _make(role: UserRole, label: str | None = None) -> TestClient:
        _, raw = auth_service.create_api_key(
            db, label=label or f"{role.value.lower()}-key", role=role, organization_id=org.id
        )
        test_client = TestClient(app)
        test_client.headers["Authorization"] = f"Bearer {raw}"
        opened.append(test_client)
        return test_client

    yield _make
    for c in opened:
        c.close()


@pytest.fixture
def org_client():
    """Factory: a TestClient authenticating as an API key of `role` in `organization`."""
    opened: list[TestClient] = []

    def _make(organization, role: UserRole = UserRole.ADMIN, label: str | None = None):
        with SessionLocal() as session:
            _, raw = auth_service.create_api_key(
                session,
                label=label or f"{organization.name}-{role.value}".lower().replace(" ", "-"),
                role=role,
                organization_id=organization.id,
            )
        test_client = TestClient(app)
        test_client.headers["Authorization"] = f"Bearer {raw}"
        opened.append(test_client)
        return test_client

    yield _make
    for c in opened:
        c.close()


@pytest.fixture
def org_session():
    """Factory: a database session scoped to the given organization."""
    opened = []

    def _make(organization):
        session = new_session(organization.id)
        opened.append(session)
        return session

    yield _make
    for session in opened:
        session.rollback()
        session.close()
