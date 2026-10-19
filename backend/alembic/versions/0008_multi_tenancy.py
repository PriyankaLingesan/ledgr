"""Multi-tenancy: organization-scoped ledger, OWNER role, casework tables.

Adds `organization_id` to every ledger and workflow table and moves the
uniqueness rules (account code, transaction reference, idempotency key,
external record id, API key label) from global to per-organization. Composite
foreign keys then make a cross-organization reference impossible *inside
PostgreSQL*: a ledger entry cannot name an account or a transaction from a
different organization, whatever the application does.

Existing data is preserved, never deleted: every pre-existing row is assigned
to the organization that already exists (the oldest one), or to a new
"Legacy organization" if the database held data but no organization. The
oldest ADMIN of each organization without an OWNER becomes its OWNER.

Backfilling touches append-only tables, so their immutability triggers are
disabled for exactly the backfill statements and re-enabled immediately. The
transaction-guard trigger is also extended so `organization_id` can never be
changed afterwards.

Also adds: `investigation_notes` and `reconciliation_decisions` (append-only),
and a CSRF token on login sessions.

Downgrade removes the tenant columns/constraints and restores the global
uniqueness rules; it fails (rather than merges data) if two organizations now
hold the same account code or transaction reference. Users holding `OWNER` are
set to `ADMIN` (the previous release cannot read the new role); the `OWNER` value
itself cannot be removed from a PostgreSQL enum and is left in place.

Revision ID: 0008
Revises: 0007
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables whose rows belong to one organization. audit_events stays nullable
# (an event about an unknown sign-in belongs to no tenant).
TENANT_TABLES = [
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
]
NULLABLE_TENANT = {"audit_events"}

# Append-only tables whose triggers must be paused for the backfill.
PROTECTED = {
    "ledger_entries": "ledger_entries_immutable",
    "audit_events": "audit_events_immutable",
    "transactions": "transactions_guarded",
}

# (referenced table) -> needs a UNIQUE (id, organization_id) for composite FKs.
COMPOSITE_TARGETS = [
    "accounts",
    "transactions",
    "reconciliation_runs",
    "external_transactions",
    "reconciliation_results",
    "investigations",
]

# (name, table, local column, referenced table)
COMPOSITE_FKS = [
    ("fk_ledger_entries_account_org", "ledger_entries", "account_id", "accounts"),
    ("fk_ledger_entries_transaction_org", "ledger_entries", "transaction_id", "transactions"),
    ("fk_account_balances_account_org", "account_balances", "account_id", "accounts"),
    ("fk_transactions_reverses_org", "transactions", "reverses_transaction_id", "transactions"),
    ("fk_recon_results_run_org", "reconciliation_results", "run_id", "reconciliation_runs"),
    (
        "fk_recon_results_external_org",
        "reconciliation_results",
        "external_transaction_id",
        "external_transactions",
    ),
    (
        "fk_recon_results_internal_org",
        "reconciliation_results",
        "internal_transaction_id",
        "transactions",
    ),
    ("fk_investigations_account_org", "investigations", "account_id", "accounts"),
    (
        "fk_investigations_correction_org",
        "investigations",
        "correction_transaction_id",
        "transactions",
    ),
    (
        "fk_investigations_recon_result_org",
        "investigations",
        "reconciliation_result_id",
        "reconciliation_results",
    ),
    ("fk_drafts_posted_txn_org", "transaction_drafts", "posted_transaction_id", "transactions"),
    ("fk_idempotency_txn_org", "idempotency_keys", "transaction_id", "transactions"),
]

MATCH_DECISION = postgresql.ENUM(
    "CONFIRMED_MATCH", "NOT_A_MATCH", name="match_decision", create_type=False
)

GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION ledgr_guard_transaction_update() RETURNS trigger AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.seq IS DISTINCT FROM OLD.seq
       OR NEW.reference IS DISTINCT FROM OLD.reference
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.total_debits_minor IS DISTINCT FROM OLD.total_debits_minor
       OR NEW.total_credits_minor IS DISTINCT FROM OLD.total_credits_minor
       OR NEW.entry_count IS DISTINCT FROM OLD.entry_count
       OR NEW.effective_at IS DISTINCT FROM OLD.effective_at
       OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
       OR NEW.reverses_transaction_id IS DISTINCT FROM OLD.reverses_transaction_id
    THEN
        RAISE EXCEPTION
            'LEDGR: posted transaction % is immutable; post a reversal instead',
            OLD.reference
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.status = 'REVERSED' AND NEW.status IS DISTINCT FROM 'REVERSED' THEN
        RAISE EXCEPTION 'LEDGR: a reversal cannot be undone'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# The pre-tenancy version, restored on downgrade.
OLD_GUARD_FUNCTION = GUARD_FUNCTION.replace(
    "       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id\n", ""
)


def _has_rows(bind, table: str) -> bool:
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar())


def upgrade() -> None:
    bind = op.get_bind()

    # --- enum values / types --------------------------------------------------
    # ALTER TYPE ... ADD VALUE cannot be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'OWNER' BEFORE 'ADMIN'")
    postgresql.ENUM("CONFIRMED_MATCH", "NOT_A_MATCH", name="match_decision").create(
        bind, checkfirst=True
    )

    op.add_column("user_sessions", sa.Column("csrf_token", sa.String(length=64), nullable=True))

    # --- which organization owns the pre-existing data ----------------------------
    default_org = bind.execute(
        sa.text("SELECT id FROM organizations ORDER BY created_at, id LIMIT 1")
    ).scalar()
    legacy_data = any(_has_rows(bind, t) for t in TENANT_TABLES + ["api_keys"])
    if default_org is None and legacy_data:
        default_org = uuid.uuid4()
        bind.execute(
            sa.text("INSERT INTO organizations (id, name, base_currency) VALUES (:i, :n, 'USD')"),
            {"i": default_org, "n": "Legacy organization"},
        )

    # --- add the tenant column (nullable first), backfill, then tighten -----------
    for table in TENANT_TABLES:
        op.add_column(table, sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))

    for table, trigger in PROTECTED.items():
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    if default_org is not None:
        for table in TENANT_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET organization_id = :o WHERE organization_id IS NULL"),
                {"o": default_org},
            )
        bind.execute(
            sa.text("UPDATE api_keys SET organization_id = :o WHERE organization_id IS NULL"),
            {"o": default_org},
        )
    for table, trigger in PROTECTED.items():
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")

    for table in TENANT_TABLES:
        if table not in NULLABLE_TENANT:
            op.alter_column(table, "organization_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_organization_id_organizations",
            table,
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    op.alter_column("api_keys", "organization_id", nullable=False)

    # An organization that already has users gets an OWNER (its oldest ADMIN).
    bind.execute(
        sa.text(
            """
            UPDATE users SET role = 'OWNER'
            WHERE id IN (
                SELECT DISTINCT ON (u.organization_id) u.id
                FROM users u
                WHERE u.role = 'ADMIN'
                  AND NOT EXISTS (
                      SELECT 1 FROM users o
                      WHERE o.organization_id = u.organization_id AND o.role = 'OWNER'
                  )
                ORDER BY u.organization_id, u.created_at, u.id
            )
            """
        )
    )

    # --- per-organization uniqueness ------------------------------------------------
    op.drop_constraint("uq_accounts_code", "accounts", type_="unique")
    op.create_unique_constraint("uq_accounts_code", "accounts", ["organization_id", "code"])

    op.drop_constraint("uq_transactions_reference", "transactions", type_="unique")
    op.create_unique_constraint(
        "uq_transactions_reference", "transactions", ["organization_id", "reference"]
    )

    op.drop_constraint(
        "uq_external_transactions_source_external_id", "external_transactions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_external_transactions_source_external_id",
        "external_transactions",
        ["organization_id", "source_system", "external_id"],
    )

    op.drop_constraint("pk_idempotency_keys", "idempotency_keys", type_="primary")
    op.create_primary_key(
        "pk_idempotency_keys", "idempotency_keys", ["organization_id", "scope", "key"]
    )

    op.drop_constraint("uq_api_keys_label", "api_keys", type_="unique")
    op.create_unique_constraint("uq_api_keys_label", "api_keys", ["organization_id", "label"])

    # --- composite foreign keys: cross-tenant references are impossible -----------
    for table in COMPOSITE_TARGETS:
        op.create_unique_constraint(f"uq_{table}_id_organization_id", table, ["id", "organization_id"])
    for name, table, column, target in COMPOSITE_FKS:
        op.create_foreign_key(
            name, table, target, [column, "organization_id"], ["id", "organization_id"]
        )

    # --- transactions: organization is as immutable as everything else -----------
    op.execute(GUARD_FUNCTION)

    # --- casework tables (append-only) -----------------------------------------------
    op.create_table(
        "investigation_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("investigation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=False),
        sa.Column("author_ref", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_investigation_notes_organization_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["investigation_id", "organization_id"],
            ["investigations.id", "investigations.organization_id"],
            name="fk_notes_investigation_org",
        ),
        sa.CheckConstraint("char_length(body) > 0", name="ck_investigation_notes_body_not_empty"),
    )
    op.create_index("ix_investigation_notes_organization_id", "investigation_notes", ["organization_id"])
    op.create_index("ix_investigation_notes_investigation_id", "investigation_notes", ["investigation_id"])

    op.create_table(
        "reconciliation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("internal_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", MATCH_DECISION, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=False),
        sa.Column("decided_by_ref", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_reconciliation_decisions_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["result_id", "organization_id"],
            ["reconciliation_results.id", "reconciliation_results.organization_id"],
            name="fk_decisions_result_org",
        ),
        sa.ForeignKeyConstraint(
            ["internal_transaction_id", "organization_id"],
            ["transactions.id", "transactions.organization_id"],
            name="fk_decisions_txn_org",
        ),
        sa.CheckConstraint("char_length(reason) > 0", name="ck_reconciliation_decisions_reason_not_empty"),
    )
    op.create_index(
        "ix_reconciliation_decisions_organization_id", "reconciliation_decisions", ["organization_id"]
    )
    op.create_index("ix_reconciliation_decisions_result_id", "reconciliation_decisions", ["result_id"])

    for table in ("investigation_notes", "reconciliation_decisions"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ledgr_reject_mutation()"
        )


def downgrade() -> None:
    # The previous release has no OWNER role and cannot read one (and the enum
    # value itself cannot be dropped), so owners step down to ADMIN. Upgrading
    # again promotes each organization's oldest ADMIN back to OWNER.
    op.execute("UPDATE users SET role = 'ADMIN' WHERE role = 'OWNER'")
    for table in ("reconciliation_decisions", "investigation_notes"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
        op.drop_table(table)

    op.execute(OLD_GUARD_FUNCTION)

    for name, table, _column, _target in reversed(COMPOSITE_FKS):
        op.drop_constraint(name, table, type_="foreignkey")
    for table in reversed(COMPOSITE_TARGETS):
        op.drop_constraint(f"uq_{table}_id_organization_id", table, type_="unique")

    op.drop_constraint("uq_api_keys_label", "api_keys", type_="unique")
    op.create_unique_constraint("uq_api_keys_label", "api_keys", ["label"])
    op.drop_constraint("pk_idempotency_keys", "idempotency_keys", type_="primary")
    op.create_primary_key("pk_idempotency_keys", "idempotency_keys", ["scope", "key"])
    op.drop_constraint(
        "uq_external_transactions_source_external_id", "external_transactions", type_="unique"
    )
    op.create_unique_constraint(
        "uq_external_transactions_source_external_id",
        "external_transactions",
        ["source_system", "external_id"],
    )
    op.drop_constraint("uq_transactions_reference", "transactions", type_="unique")
    op.create_unique_constraint("uq_transactions_reference", "transactions", ["reference"])
    op.drop_constraint("uq_accounts_code", "accounts", type_="unique")
    op.create_unique_constraint("uq_accounts_code", "accounts", ["code"])

    op.alter_column("api_keys", "organization_id", nullable=True)
    for table in reversed(TENANT_TABLES):
        op.drop_index(f"ix_{table}_organization_id", table_name=table)
        op.drop_constraint(f"fk_{table}_organization_id_organizations", table, type_="foreignkey")
        op.drop_column(table, "organization_id")

    op.drop_column("user_sessions", "csrf_token")
    postgresql.ENUM(name="match_decision").drop(op.get_bind(), checkfirst=True)
