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
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import AccountType  # noqa: E402
from app.services import accounts as account_service  # noqa: E402

TABLES = (
    "audit_events",
    "idempotency_keys",
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
def clean_tables():
    """TRUNCATE between tests.

    TRUNCATE fires no row-level triggers, so it clears the append-only tables
    without needing to disable the guards that protect them.
    """
    with engine.connect() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        conn.commit()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
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
