"""Initial ledger schema.

Creates the chart of accounts, the append-only ledger, transactions, the
balance cache, idempotency records and the audit log - together with the
database-level guards that make the financial invariants true regardless of
which client is talking to the database.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACCOUNT_TYPE = sa.Enum(
    "ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE", name="account_type"
)
NORMAL_BALANCE = sa.Enum("DEBIT", "CREDIT", name="normal_balance")
ACCOUNT_STATUS = sa.Enum("ACTIVE", "FROZEN", "CLOSED", name="account_status")
ENTRY_DIRECTION = sa.Enum("DEBIT", "CREDIT", name="entry_direction")
TRANSACTION_STATUS = sa.Enum("POSTED", "REVERSED", name="transaction_status")
TRANSACTION_KIND = sa.Enum("STANDARD", "REVERSAL", name="transaction_kind")
IDEMPOTENCY_STATUS = sa.Enum("IN_PROGRESS", "COMPLETED", name="idempotency_status")


# --------------------------------------------------------------------------
# Immutability guards.
#
# The application never issues UPDATE or DELETE against ledger_entries, but the
# ledger's credibility should not rest on that promise. These triggers make the
# guarantee structural: a psql session, a migration or a future bug all hit the
# same wall.
# --------------------------------------------------------------------------

IMMUTABILITY_SQL = """
CREATE OR REPLACE FUNCTION ledgr_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'LEDGR: % on % is forbidden - ledger history is append-only',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entries_immutable
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledgr_reject_mutation();

CREATE TRIGGER audit_events_immutable
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION ledgr_reject_mutation();

-- A posted transaction may only ever take one step: POSTED -> REVERSED.
-- Nothing financial about it can change, and it can never be deleted.
CREATE OR REPLACE FUNCTION ledgr_guard_transaction_update() RETURNS trigger AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
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

CREATE TRIGGER transactions_guarded
    BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION ledgr_guard_transaction_update();

CREATE TRIGGER transactions_no_delete
    BEFORE DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION ledgr_reject_mutation();
"""

DROP_IMMUTABILITY_SQL = """
DROP TRIGGER IF EXISTS transactions_no_delete ON transactions;
DROP TRIGGER IF EXISTS transactions_guarded ON transactions;
DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events;
DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries;
DROP FUNCTION IF EXISTS ledgr_guard_transaction_update();
DROP FUNCTION IF EXISTS ledgr_reject_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", ACCOUNT_TYPE, nullable=False),
        sa.Column("normal_balance", NORMAL_BALANCE, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", ACCOUNT_STATUS, nullable=False, server_default="ACTIVE"),
        sa.Column(
            "allows_negative_balance", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("code", name="uq_accounts_code"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_accounts_currency_is_iso4217"),
        sa.CheckConstraint("char_length(code) > 0", name="ck_accounts_code_not_empty"),
    )
    op.create_index("ix_accounts_type_status", "accounts", ["type", "status"])
    op.create_index("ix_accounts_currency", "accounts", ["currency"])

    op.create_table(
        "account_balances",
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debits_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credits_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_entry_seq", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_account_balances"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_balances_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "debits_minor >= 0 AND credits_minor >= 0",
            name="ck_account_balances_totals_non_negative",
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", TRANSACTION_STATUS, nullable=False, server_default="POSTED"),
        sa.Column("kind", TRANSACTION_KIND, nullable=False, server_default="STANDARD"),
        sa.Column("total_debits_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_credits_minor", sa.BigInteger(), nullable=False),
        sa.Column("entry_count", sa.BigInteger(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "posted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("reverses_transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("external_reference", sa.String(160), nullable=True),
        sa.Column(
            "extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint("seq", name="uq_transactions_seq"),
        sa.UniqueConstraint("reference", name="uq_transactions_reference"),
        # I11: at most one reversal per transaction, enforced by the database
        # rather than by hoping two racing requests do not overlap.
        sa.UniqueConstraint(
            "reverses_transaction_id", name="uq_transactions_reverses_transaction_id"
        ),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_id"],
            ["transactions.id"],
            name="fk_transactions_reverses_transaction_id_transactions",
        ),
        # I1, at the storage layer: a transaction row that does not balance
        # cannot physically exist.
        sa.CheckConstraint(
            "total_debits_minor = total_credits_minor",
            name="ck_transactions_transaction_is_balanced",
        ),
        sa.CheckConstraint("total_debits_minor > 0", name="ck_transactions_transaction_has_value"),
        sa.CheckConstraint("entry_count >= 2", name="ck_transactions_transaction_has_two_entries"),
        sa.CheckConstraint(
            "(kind = 'REVERSAL') = (reverses_transaction_id IS NOT NULL)",
            name="ck_transactions_reversal_links_original",
        ),
        sa.CheckConstraint(
            "(status = 'REVERSED') = (reversed_at IS NOT NULL)",
            name="ck_transactions_reversed_has_timestamp",
        ),
    )
    op.create_index("ix_transactions_posted_at", "transactions", ["posted_at"])
    op.create_index("ix_transactions_effective_at", "transactions", ["effective_at"])
    op.create_index("ix_transactions_status_kind", "transactions", ["status", "kind"])
    op.create_index("ix_transactions_currency", "transactions", ["currency"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", ENTRY_DIRECTION, nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("entry_index", sa.Integer(), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_entries"),
        sa.UniqueConstraint("seq", name="uq_ledger_entries_seq"),
        sa.UniqueConstraint("transaction_id", "entry_index", name="uq_entry_position"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_ledger_entries_transaction_id_transactions",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_ledger_entries_account_id_accounts"
        ),
        # I3: direction carries the sign, so the amount is always positive.
        sa.CheckConstraint("amount_minor > 0", name="ck_ledger_entries_amount_is_positive"),
        sa.CheckConstraint("entry_index >= 0", name="ck_ledger_entries_entry_index_non_negative"),
    )
    op.create_index("ix_ledger_entries_account_seq", "ledger_entries", ["account_id", "seq"])
    op.create_index("ix_ledger_entries_transaction", "ledger_entries", ["transaction_id"])
    op.create_index("ix_ledger_entries_created_at", "ledger_entries", ["created_at"])
    op.create_index(
        "ix_ledger_entries_currency_direction", "ledger_entries", ["currency", "direction"]
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", IDEMPOTENCY_STATUS, nullable=False, server_default="IN_PROGRESS"),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope", "key", name="pk_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_idempotency_keys_transaction_id_transactions",
        ),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])
    op.create_index("ix_idempotency_keys_transaction", "idempotency_keys", ["transaction_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False, server_default="system"),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint("seq", name="uq_audit_events_seq"),
    )
    op.create_index("ix_audit_events_resource", "audit_events", ["resource_type", "resource_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.execute(IMMUTABILITY_SQL)


def downgrade() -> None:
    op.execute(DROP_IMMUTABILITY_SQL)
    op.drop_table("audit_events")
    op.drop_table("idempotency_keys")
    op.drop_table("ledger_entries")
    op.drop_table("transactions")
    op.drop_table("account_balances")
    op.drop_table("accounts")
    for enum in (
        IDEMPOTENCY_STATUS,
        TRANSACTION_KIND,
        TRANSACTION_STATUS,
        ENTRY_DIRECTION,
        ACCOUNT_STATUS,
        NORMAL_BALANCE,
        ACCOUNT_TYPE,
    ):
        enum.drop(op.get_bind(), checkfirst=True)
