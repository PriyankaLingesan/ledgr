"""Shared FastAPI dependencies."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int


def pagination(
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


PaginationParams = Annotated[Pagination, Depends(pagination)]


def request_id(request: Request) -> str:
    """Correlation id, propagated from the edge or minted by the middleware."""
    return getattr(request.state, "request_id", "unknown")


RequestId = Annotated[str, Depends(request_id)]


def actor(
    x_actor: Annotated[str | None, Header(alias="X-Actor")] = None,
) -> str:
    """Who is performing the write.

    LEDGR is an internal service and deliberately has no auth of its own: it
    would sit behind an authenticating gateway that populates this header. The
    value is recorded on every transaction and audit event so history is
    attributable even without a user table here.
    """
    return (x_actor or "system").strip()[:120] or "system"


Actor = Annotated[str, Depends(actor)]


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    return idempotency_key.strip()[:255] or None


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]
