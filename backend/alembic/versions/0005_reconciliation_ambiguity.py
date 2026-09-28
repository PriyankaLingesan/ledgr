"""Reconciliation matching integrity.

`Transaction.external_reference` was never unique - and cannot safely be
made unique, because the existing reversal and correction workflows
*deliberately* copy it onto the new transaction they produce (see
`posting.reverse_transaction`, `investigation.correct_transaction`): a
corrected transaction legitimately shares its external_reference with the
original it replaces. A naive reconciliation match (`LIMIT 1`) could
therefore silently pick an arbitrary one of several real candidates instead
of failing safely.

This migration only adjusts the reconciliation schema to let the matching
logic (in `app.services.reconciliation`) represent that honestly:

- two new outcomes on `reconciliation_status`: MISSING_EXTERNAL (a live
  internal transaction with no external evidence) and
  AMBIGUOUS_INTERNAL_MATCH (more than one live internal transaction shares
  a reference - surfaced explicitly rather than guessed at)
- `reconciliation_results.external_transaction_id` becomes nullable, since
  a MISSING_EXTERNAL result has no external row to point at
- a CHECK constraint so a result can never point at neither
- two new run-level counters

No existing table's uniqueness or the `Transaction` model itself is
touched - the correct identity for external evidence was already enforced
at the database level (`uq_external_transactions_source_external_id`, added
in 0003); this migration fixes the *matching logic's* honesty, not the
`Transaction` schema, which was never the wrong place to look.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE reconciliation_status ADD VALUE IF NOT EXISTS 'MISSING_EXTERNAL'")
    op.execute("ALTER TYPE reconciliation_status ADD VALUE IF NOT EXISTS 'AMBIGUOUS_INTERNAL_MATCH'")

    op.alter_column(
        "reconciliation_results", "external_transaction_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    op.create_check_constraint(
        "ck_reconciliation_results_has_a_subject",
        "reconciliation_results",
        "external_transaction_id IS NOT NULL OR internal_transaction_id IS NOT NULL",
    )

    op.add_column(
        "reconciliation_runs",
        sa.Column("missing_external_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "reconciliation_runs",
        sa.Column("ambiguous_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_runs", "ambiguous_count")
    op.drop_column("reconciliation_runs", "missing_external_count")

    op.drop_constraint(
        "ck_reconciliation_results_has_a_subject", "reconciliation_results", type_="check"
    )
    op.alter_column(
        "reconciliation_results", "external_transaction_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )

    # Postgres has no DROP VALUE for enums; the same pragmatic tradeoff as
    # 0003's investigation_source addition - left in place on downgrade.
