"""Investigation cases.

Adds one table, `investigations`, for the operational workflow of looking
into a financial exception - status, ownership, and how it was resolved.
It carries no financial amounts and no immutability trigger: unlike
`transactions`/`ledger_entries`, a case's status is meant to change over
time, the same way `account_balances` already does elsewhere in this schema.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVESTIGATION_STATUS = sa.Enum(
    "OPEN", "INVESTIGATING", "RESOLVED", name="investigation_status"
)
INVESTIGATION_SOURCE = sa.Enum(
    "MANUAL", "DUPLICATE", "INTEGRITY", "FLIGHT_RECORDER", name="investigation_source"
)


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "seq", sa.BigInteger(), sa.Identity(always=False), nullable=False, unique=True
        ),
        sa.Column("case_number", sa.String(length=20), nullable=False, unique=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=False),
        sa.Column(
            "status",
            INVESTIGATION_STATUS,
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column(
            "source",
            INVESTIGATION_SOURCE,
            nullable=False,
            server_default="MANUAL",
        ),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", name="fk_investigations_account_id_accounts"),
            nullable=True,
        ),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "correction_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "transactions.id", name="fk_investigations_correction_transaction_id_transactions"
            ),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_investigations_status", "investigations", ["status"])
    op.create_index("ix_investigations_account_id", "investigations", ["account_id"])
    op.create_index("ix_investigations_created_at", "investigations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_investigations_created_at", table_name="investigations")
    op.drop_index("ix_investigations_account_id", table_name="investigations")
    op.drop_index("ix_investigations_status", table_name="investigations")
    op.drop_table("investigations")

    bind = op.get_bind()
    INVESTIGATION_SOURCE.drop(bind, checkfirst=True)
    INVESTIGATION_STATUS.drop(bind, checkfirst=True)
