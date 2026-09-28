"""External reconciliation.

Adds three tables - `external_transactions` (evidence from outside LEDGR),
`reconciliation_runs` and `reconciliation_results` (deterministic comparison
snapshots against `transactions.external_reference`, a field the schema
already had). None of this touches `transactions` or `ledger_entries`, and
none of it carries an immutability trigger: unlike ledger history, a
reconciliation run is a snapshot an operator may want to re-run, the same
precedent as `investigations`.

Also extends `investigation_source` with one new value, `RECONCILIATION`, so
a case opened from a reconciliation exception is labelled honestly rather
than as `MANUAL`.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECONCILIATION_STATUS = sa.Enum(
    "MATCHED", "AMOUNT_MISMATCH", "MISSING_INTERNAL", name="reconciliation_status"
)
# `entry_direction` was created by 0001 and already exists - referenced here
# with create_type=False so this migration never tries to create it again
# (see 0002's original bug: a second creation attempt raises DuplicateObject).
ENTRY_DIRECTION_EXISTING = postgresql.ENUM(
    "DEBIT", "CREDIT", name="entry_direction", create_type=False
)


def upgrade() -> None:
    op.execute("ALTER TYPE investigation_source ADD VALUE IF NOT EXISTS 'RECONCILIATION'")

    op.create_table(
        "external_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False, unique=True),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("account_reference", sa.String(length=160), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("direction", ENTRY_DIRECTION_EXISTING, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="SETTLED"),
        sa.Column("transaction_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "source_system", "external_id", name="uq_external_transactions_source_external_id"
        ),
    )
    op.create_index(
        "ix_external_transactions_external_id", "external_transactions", ["external_id"]
    )

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("external_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mismatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_internal_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "reconciliation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reconciliation_runs.id", name="fk_reconciliation_results_run_id_runs"),
            nullable=False,
        ),
        sa.Column(
            "external_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "external_transactions.id",
                name="fk_reconciliation_results_external_transaction_id",
            ),
            nullable=False,
        ),
        sa.Column(
            "internal_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "transactions.id",
                name="fk_reconciliation_results_internal_transaction_id_transactions",
            ),
            nullable=True,
        ),
        sa.Column("status", RECONCILIATION_STATUS, nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("difference_minor", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_reconciliation_results_run_id", "reconciliation_results", ["run_id"]
    )
    op.create_index(
        "ix_reconciliation_results_status", "reconciliation_results", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_reconciliation_results_status", table_name="reconciliation_results")
    op.drop_index("ix_reconciliation_results_run_id", table_name="reconciliation_results")
    op.drop_table("reconciliation_results")

    op.drop_table("reconciliation_runs")

    op.drop_index("ix_external_transactions_external_id", table_name="external_transactions")
    op.drop_table("external_transactions")

    bind = op.get_bind()
    RECONCILIATION_STATUS.drop(bind, checkfirst=True)

    # Postgres has no DROP VALUE for enums; removing 'RECONCILIATION' from
    # investigation_source would require rebuilding the type. Left in place
    # on downgrade, the same pragmatic tradeoff Postgres itself forces on
    # every ADD VALUE - this is intentionally not reversed.
