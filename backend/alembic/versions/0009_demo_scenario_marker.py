"""Mark demonstration organizations.

Adds one nullable column, `organizations.demo_scenario`. It is NULL for every
real organization (and for every organization that existed before this
migration) and holds a scenario key (for example `meridian-v1`) for an
organization created by a demo seeder. The console uses it to label the
organization as a fictional demonstration, and the seeder uses it to prove an
organization is its own before it writes anything.

Purely additive: no existing row or constraint changes. Downgrade drops the column.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("demo_scenario", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "demo_scenario")
