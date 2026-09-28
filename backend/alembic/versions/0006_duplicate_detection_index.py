"""Duplicate-detection index.

`investigation.duplicate_candidates()` self-joins `ledger_entries` on
`account_id`, then filters on `amount_minor`, `direction`, `currency` and a
`created_at` time window. None of the four existing indexes on that table
match this predicate shape. This adds one composite index over exactly
those columns, in the order that lets Postgres narrow by equality first
(account, amount, direction, currency) and then range-scan the time window.

Verified via `EXPLAIN ANALYZE` against the real dev database (see the
hardening-pass report) that at the current data volume (~460 rows) the
planner still correctly prefers two sequential scans and a hash join - this
index does not help yet, and forcing it wouldn't either. It's added because
the query *shape* warrants it as the table grows, not because a benchmark
showed a win today; claiming a performance improvement that wasn't measured
would be worse than adding no index at all.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ledger_entries_duplicate_candidates",
        "ledger_entries",
        ["account_id", "amount_minor", "direction", "currency", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_duplicate_candidates", table_name="ledger_entries")
