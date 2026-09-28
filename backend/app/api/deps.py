"""Shared FastAPI dependencies."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationRequired
from app.db.session import get_db
from app.services import auth as auth_service

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
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    """Who is performing the write - server-verified, never a trusted header.

    Requires `Authorization: Bearer <api-key>`. The key is hashed and looked
    up against `api_keys`; the resolved label is what gets recorded as
    `actor` on every transaction and audit event this request produces. A
    caller cannot claim to be a different actor by sending a different
    header value - unlike the `X-Actor` header this replaces, which any
    client could set to anything.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationRequired(
            "this endpoint requires an 'Authorization: Bearer <api-key>' header"
        )
    raw_key = authorization[len("bearer "):].strip()
    if not raw_key:
        raise AuthenticationRequired("the bearer token was empty")
    return auth_service.authenticate(db, raw_key).label


Actor = Annotated[str, Depends(actor)]


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    return idempotency_key.strip()[:255] or None


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]
