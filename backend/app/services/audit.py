"""Audit event recording.

Events are added to the caller's session, so an audit record and the financial
change it describes commit or roll back together. There is no code path that
writes a ledger entry without the matching audit event.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


def record(
    db: Session,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: str = "system",
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=actor,
        request_id=request_id,
        payload=payload or {},
    )
    db.add(event)
    return event


def record_independent(
    *,
    organization_id: uuid.UUID | None,
    event_type: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: str = "system",
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record an event on its own connection and commit it immediately.

    For things that must be remembered *because* the request failed - a
    rejected posting, a refused retry - where the request's own session has
    rolled back (or never wrote anything). Never raises: losing an audit line
    must not turn a clean 4xx into a 500.
    """
    from app.db.session import session_scope

    try:
        with session_scope(organization_id) as db:
            record(
                db,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                actor=actor,
                request_id=request_id,
                payload=payload,
            )
    except Exception:  # pragma: no cover - best effort by design
        pass
