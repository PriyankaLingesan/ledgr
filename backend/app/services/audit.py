"""Audit event recording.

Events are added to the caller's session, so an audit record and the financial
change it describes commit or roll back together. There is no code path that
writes a ledger entry without the matching audit event.
"""

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
