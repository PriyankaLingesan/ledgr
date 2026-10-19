"""Organizations, users, roles, sessions, maker-checker drafts.

Turns LEDGR from a service with API keys into a multi-user operations product
without touching the ledger itself:

* `organizations`, `users`, `user_sessions` - who is asking (scrypt password
  hashes; session tokens stored only as SHA-256 hashes).
* `transaction_drafts` - proposed transactions awaiting approval. Not
  financial records: no entries, no balances, no immutability trigger. An
  approval hands the entries to the existing posting engine.
* `api_keys` gains a role (existing keys migrate as ADMIN - they were always
  full-access), an optional organization, creator, display prefix, revoked_at.
* `investigations` gains a priority and a link to the reconciliation result
  the case was opened from.
* an index on `audit_events.actor` for the audit-log filter.

Nothing here alters `transactions`, `ledger_entries` or their triggers.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("ADMIN", "FINANCE", "OPERATIONS", "AUDITOR")
DRAFT_STATUSES = ("DRAFT", "PENDING_APPROVAL", "POSTED", "REJECTED")
PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# `create_type=False` everywhere: each type is created exactly once, explicitly,
# below (user_role is shared by `users` and `api_keys`).
USER_ROLE = postgresql.ENUM(*ROLES, name="user_role", create_type=False)
DRAFT_STATUS = postgresql.ENUM(*DRAFT_STATUSES, name="draft_status", create_type=False)
PRIORITY = postgresql.ENUM(*PRIORITIES, name="investigation_priority", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*ROLES, name="user_role").create(bind, checkfirst=True)
    postgresql.ENUM(*DRAFT_STATUSES, name="draft_status").create(bind, checkfirst=True)
    postgresql.ENUM(*PRIORITIES, name="investigation_priority").create(bind, checkfirst=True)

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("approval_threshold_minor", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "approval_threshold_minor IS NULL OR approval_threshold_minor > 0",
            name="ck_organizations_threshold_positive",
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", name="fk_users_organization_id_organizations"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", USER_ROLE, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_user_sessions_user_id_users"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    op.create_table(
        "transaction_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reference", sa.String(length=80), nullable=True),
        sa.Column("external_reference", sa.String(length=160), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entries", postgresql.JSONB(), nullable=False),
        sa.Column(
            "extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", DRAFT_STATUS, nullable=False, server_default="DRAFT"),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("created_by_ref", sa.String(length=64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "posted_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "transactions.id", name="fk_transaction_drafts_posted_transaction_id_transactions"
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(status = 'POSTED') = (posted_transaction_id IS NOT NULL)",
            name="ck_transaction_drafts_posted_iff_linked",
        ),
        sa.CheckConstraint(
            "status NOT IN ('POSTED', 'REJECTED') OR decided_at IS NOT NULL",
            name="ck_transaction_drafts_decided_has_timestamp",
        ),
        sa.CheckConstraint("total_minor > 0", name="ck_transaction_drafts_has_value"),
    )
    op.create_index("ix_transaction_drafts_status", "transaction_drafts", ["status"])
    op.create_index("ix_transaction_drafts_created_at", "transaction_drafts", ["created_at"])

    # --- api_keys: roles, ownership, revocation ----------------------------
    op.add_column("api_keys", sa.Column("role", USER_ROLE, nullable=False, server_default="ADMIN"))
    op.add_column(
        "api_keys",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", name="fk_api_keys_organization_id_organizations"),
            nullable=True,
        ),
    )
    op.add_column("api_keys", sa.Column("created_by", sa.String(length=120), nullable=True))
    op.add_column("api_keys", sa.Column("key_prefix", sa.String(length=16), nullable=True))
    op.add_column("api_keys", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))

    # --- investigations: priority + the reconciliation result it came from --
    op.add_column(
        "investigations",
        sa.Column("priority", PRIORITY, nullable=False, server_default="MEDIUM"),
    )
    op.add_column(
        "investigations",
        sa.Column(
            "reconciliation_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "reconciliation_results.id",
                name="fk_investigations_recon_result_id_reconciliation_results",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_investigations_reconciliation_result_id",
        "investigations",
        ["reconciliation_result_id"],
    )

    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor", table_name="audit_events")

    op.drop_index("ix_investigations_reconciliation_result_id", table_name="investigations")
    op.drop_column("investigations", "reconciliation_result_id")
    op.drop_column("investigations", "priority")

    op.drop_column("api_keys", "revoked_at")
    op.drop_column("api_keys", "key_prefix")
    op.drop_column("api_keys", "created_by")
    op.drop_column("api_keys", "organization_id")
    op.drop_column("api_keys", "role")

    op.drop_table("transaction_drafts")
    op.drop_table("user_sessions")
    op.drop_table("users")
    op.drop_table("organizations")

    bind = op.get_bind()
    postgresql.ENUM(name="investigation_priority").drop(bind, checkfirst=True)
    postgresql.ENUM(name="draft_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="user_role").drop(bind, checkfirst=True)
