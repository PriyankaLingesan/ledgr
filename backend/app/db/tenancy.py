"""Tenant (organization) isolation.

Every ledger and workflow table carries an `organization_id`. Isolation is
enforced in three independent layers, so no single bug is enough to leak data:

1. **Automatic read scoping (fail closed).** A `do_orm_execute` hook adds
   `organization_id = <session's organization>` to every ORM SELECT, UPDATE and
   DELETE touching a tenant table - including joins, subqueries, aliases and
   lazy/eager relationship loads. A session with *no* organization matches the
   nil UUID, i.e. it sees nothing; it never sees everything.
2. **Write stamping and checking.** A `before_flush` hook stamps new rows with
   the session's organization and refuses a row that names a different one, and
   refuses to re-parent an existing row.
3. **Database constraints.** Composite foreign keys such as
   `ledger_entries (account_id, organization_id) -> accounts (id, organization_id)`
   make a cross-organization reference impossible in PostgreSQL itself, whatever
   the application does; uniqueness (account code, transaction reference,
   idempotency key, external record id) is per organization.

What this is not: PostgreSQL row-level security. The application connects as
one database role, so a developer who writes raw SQL (`text(...)`), or a
bulk Core statement, bypasses layers 1-2 and relies on layer 3 and on review.
The tests in `tests/test_tenant_isolation.py` probe every endpoint from a second
organization to catch exactly that.

The organization comes from the authenticated principal (`get_principal`), never
from anything the client sends.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, event, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    declared_attr,
    mapped_column,
    with_loader_criteria,
)

from app.core.errors import LedgrError

# Matches no row: what an unscoped session "sees".
NIL_ORG = uuid.UUID(int=0)

_ORG_KEY = "org_id"
_UNSCOPED_KEY = "unscoped"


class TenantContextError(LedgrError):
    """A tenant-scoped write was attempted with no (or the wrong) organization."""

    status_code = 500
    code = "tenant_context_error"


class TenantMixin:
    """Marks a model whose rows belong to exactly one organization."""

    # Rows that may legitimately exist without a tenant (e.g. a failed sign-in
    # for an unknown email in the audit trail). Such rows are invisible to every
    # scoped session.
    allow_null_tenant: bool = False

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("organizations.id"),
            nullable=cls.allow_null_tenant,
            index=True,
        )


def bind_organization(session: Session, organization_id: uuid.UUID) -> None:
    """Scope `session` to one organization. Called by the auth dependency."""
    session.info[_ORG_KEY] = organization_id


def session_organization(session: Session) -> uuid.UUID | None:
    return session.info.get(_ORG_KEY)


def require_organization(session: Session) -> uuid.UUID:
    org = session.info.get(_ORG_KEY)
    if org is None:
        raise TenantContextError("this operation requires an organization context")
    return org


def mark_unscoped(session: Session) -> None:
    """Explicit privileged mode for maintenance scripts and test harness code.

    Never used on a request path: HTTP sessions are scoped by the principal.
    """
    session.info[_UNSCOPED_KEY] = True


@event.listens_for(Session, "do_orm_execute")
def _scope_statement(state: ORMExecuteState) -> None:
    session = state.session
    if session.info.get(_UNSCOPED_KEY):
        return
    # Only top-level statements get the option; relationship/column loads
    # inherit it through `propagate_to_loaders`.
    if state.is_column_load or state.is_relationship_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return

    org = session.info.get(_ORG_KEY, NIL_ORG)
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantMixin,
            lambda cls: cls.organization_id == org,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_and_check(session: Session, flush_context: Any, instances: Any) -> None:
    if session.info.get(_UNSCOPED_KEY):
        return
    org = session.info.get(_ORG_KEY)

    for obj in session.new:
        if not isinstance(obj, TenantMixin):
            continue
        if obj.organization_id is None:
            if org is None:
                if obj.allow_null_tenant:
                    continue
                raise TenantContextError(
                    f"cannot create {type(obj).__name__} without an organization context"
                )
            obj.organization_id = org
        elif org is not None and obj.organization_id != org:
            raise TenantContextError(
                f"{type(obj).__name__} belongs to a different organization than this session"
            )

    for obj in session.dirty:
        if isinstance(obj, TenantMixin) and session.is_modified(obj):
            history = inspect(obj).attrs.organization_id.history
            if history.has_changes() and history.deleted:
                raise TenantContextError(
                    f"{type(obj).__name__}.organization_id cannot be changed once set"
                )
