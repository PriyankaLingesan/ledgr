"""Migration 0008 against a populated database.

The rest of the suite builds an empty schema through Alembic. That proves the
migration *runs*; it says nothing about what happens to rows that already
exist. This test populates a throw-away database with the real seed script
(accounts, ~190 transactions with entries, reversals, reconciliation data,
audit events), then walks 0008 down and up and checks that nothing is lost,
nothing is orphaned and every row lands in the right organization.

It runs against its own scratch database (`ledgr_migtest`, created and dropped
here) and never touches `ledgr` or `ledgr_test`.
"""

import os
import subprocess
import sys

import pytest
import sqlalchemy as sa

from tests.conftest import _ROOT, BACKEND_DIR

SCRATCH_DB = "ledgr_migtest"
SCRATCH_URL = f"{_ROOT}/{SCRATCH_DB}"

COUNTED_TABLES = (
    "organizations",
    "users",
    "accounts",
    "account_balances",
    "transactions",
    "ledger_entries",
    "external_transactions",
    "reconciliation_runs",
    "reconciliation_results",
    "investigations",
    "audit_events",
    "idempotency_keys",
    "transaction_drafts",
    "api_keys",
)


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": SCRATCH_URL,
        "ENVIRONMENT": "test",
        "BOOTSTRAP_ADMIN_PASSWORD": "migration-test-only-password",
    }
    result = subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{args} failed:\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    return result


def _alembic(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return _run(["-m", "alembic", *args], check=check)


@pytest.fixture
def scratch():
    admin = sa.create_engine(f"{_ROOT}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    engine = sa.create_engine(SCRATCH_URL)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        admin.dispose()


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar() for t in COUNTED_TABLES
        }


def _ledger_fingerprint(engine) -> list[tuple]:
    """Everything that matters about the money, independent of tenancy columns."""
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                sa.text(
                    "SELECT t.reference, a.code, e.direction::text, e.amount_minor, e.currency "
                    "FROM ledger_entries e JOIN transactions t ON t.id = e.transaction_id "
                    "JOIN accounts a ON a.id = e.account_id "
                    "ORDER BY t.reference, a.code, e.direction::text, e.amount_minor, e.id"
                )
            )
        ]


def test_0008_preserves_a_populated_database_through_downgrade_and_upgrade(scratch):
    _alembic("upgrade", "head")
    _run(["-m", "app.scripts.seed"])

    before = _counts(scratch)
    fingerprint = _ledger_fingerprint(scratch)
    assert before["transactions"] > 100 and before["ledger_entries"] > 200
    assert before["organizations"] == 1

    with scratch.connect() as conn:
        org_id = conn.execute(sa.text("SELECT id FROM organizations")).scalar()
        assert (
            conn.execute(sa.text("SELECT count(*) FROM users WHERE role = 'OWNER'")).scalar() == 1
        )

    # --- down: the previous release's schema, with every row intact -----------
    _alembic("downgrade", "0007")
    with scratch.connect() as conn:
        columns = conn.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'ledger_entries' AND column_name = 'organization_id'"
            )
        ).scalar()
        assert columns == 0
        # The old release cannot read an OWNER; none may be left behind.
        assert (
            conn.execute(sa.text("SELECT count(*) FROM users WHERE role = 'OWNER'")).scalar() == 0
        )
        assert (
            conn.execute(sa.text("SELECT count(*) FROM users WHERE role = 'ADMIN'")).scalar() >= 1
        )
    downgraded = _counts(scratch)
    for table in set(before) - {"investigation_notes", "reconciliation_decisions"}:
        assert downgraded[table] == before[table], table

    # --- up again: every legacy row is assigned to the existing organization ----
    _alembic("upgrade", "head")
    after = _counts(scratch)
    assert after == before

    with scratch.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM organizations")).scalar() == 1
        for table in (
            "accounts",
            "account_balances",
            "transactions",
            "ledger_entries",
            "external_transactions",
            "reconciliation_runs",
            "reconciliation_results",
            "investigations",
            "audit_events",
            "idempotency_keys",
            "transaction_drafts",
            "api_keys",
        ):
            foreign = conn.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE organization_id IS DISTINCT FROM :o"),
                {"o": org_id},
            ).scalar()
            assert foreign == 0, f"{table}: rows outside the original organization"
        assert (
            conn.execute(sa.text("SELECT count(*) FROM users WHERE role = 'OWNER'")).scalar() == 1
        )

    assert _ledger_fingerprint(scratch) == fingerprint, (
        "ledger entries changed across the round trip"
    )

    # The database itself now forbids a cross-organization reference.
    with scratch.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO organizations (id, name, base_currency) "
                "VALUES (gen_random_uuid(), 'Second', 'USD')"
            )
        )
    with pytest.raises(sa.exc.IntegrityError), scratch.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE ledger_entries SET organization_id = (SELECT id FROM organizations "
                "WHERE name = 'Second') WHERE id = (SELECT id FROM ledger_entries LIMIT 1)"
            )
        )


def test_0008_downgrade_refuses_to_merge_two_organizations_that_share_a_code(scratch):
    _alembic("upgrade", "head")
    _run(["-m", "app.scripts.seed"])
    # A second organization that reuses an existing account code: legal now,
    # impossible under the old global uniqueness rule.
    _run(
        [
            "-c",
            (
                "from app.db.session import session_scope\n"
                "from app.models.account import Account\n"
                "from app.models.user import Organization\n"
                "from sqlalchemy import select\n"
                "import uuid\n"
                "with session_scope(unscoped=True) as db:\n"
                "    first = db.execute(select(Account).limit(1)).scalar_one()\n"
                "    org = Organization(id=uuid.uuid4(), name='Twin', base_currency='USD')\n"
                "    db.add(org)\n"
                "    db.flush()\n"
                "    db.add(Account(id=uuid.uuid4(), organization_id=org.id, code=first.code,\n"
                "        name='Twin', type=first.type,\n"
                "        normal_balance=first.normal_balance, currency=first.currency))\n"
                "    db.commit()\n"
            ),
        ]
    )
    before = _counts(scratch)

    result = _alembic("downgrade", "0007", check=False)
    assert result.returncode != 0, "downgrade must fail rather than merge tenants"

    # Transactional DDL: the failed downgrade left the database exactly as it was.
    assert _counts(scratch) == before
    with scratch.connect() as conn:
        # the failed downgrade rolled back as one unit: still at the head revision (0009 or later)
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() >= "0008"
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'accounts' AND column_name = 'organization_id'"
                )
            ).scalar()
            == 1
        )
