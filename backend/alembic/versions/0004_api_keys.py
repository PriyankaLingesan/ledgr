"""API key authentication.

Adds one table, `api_keys`. Replaces the old client-supplied `X-Actor`
header with a server-verified identity: a caller presents a bearer token,
its SHA-256 hash is looked up here, and the matching row's `label` becomes
the `actor` recorded on the transaction/audit trail. Plaintext keys are
never stored - only `key_hash` - and there is no immutability trigger here:
deactivating a key (`is_active = false`) is an ordinary, expected mutation,
the same precedent as `account_balances` and `investigations`.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("label", sa.String(length=120), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
