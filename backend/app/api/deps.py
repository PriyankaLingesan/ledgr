"""Shared FastAPI dependencies."""

import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationRequired, PermissionDenied, TooManyRequests
from app.core.permissions import Permission
from app.core.ratelimit import limiter
from app.db.session import get_db, session_scope
from app.db.tenancy import bind_organization
from app.services import audit
from app.services import auth as auth_service
from app.services.auth import Principal

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


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_principal(
    request: Request,
    db: DbSession,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal:
    """Who is calling - server-verified, never a trusted header.

    Two ways to authenticate, both resolving to a `Principal` with a role and
    an organization:

    * `Authorization: Bearer <token>` - an API key (`ledgr_...`) or a session
      token (`lgs_...`). Used by services, scripts and tests.
    * The `ledgr_session` cookie - an httpOnly cookie set at login, so the
      browser console never holds the credential in JavaScript-readable
      storage. Because browsers attach cookies automatically, every
      state-changing request on this path must also carry the session's CSRF
      token in `X-CSRF-Token` (checked here, constant-time).

    The principal's organization is then bound to the database session, which is
    what scopes every query in the request to that tenant (`app.db.tenancy`).
    """
    via = "bearer"
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[len("bearer ") :].strip()
        if not raw:
            raise AuthenticationRequired("the bearer token was empty")
        principal = auth_service.authenticate_bearer(db, raw)
    elif authorization:
        raise AuthenticationRequired(
            "this endpoint requires an 'Authorization: Bearer <token>' header"
        )
    else:
        cookie = request.cookies.get(settings.session_cookie_name)
        if not cookie:
            raise AuthenticationRequired(
                "this endpoint requires an 'Authorization: Bearer <token>' header "
                "or a signed-in session"
            )
        via = "cookie"
        principal = auth_service.authenticate_session(db, cookie)
        if request.method not in _SAFE_METHODS:
            presented = request.headers.get("X-CSRF-Token", "")
            expected = principal.csrf_token or ""
            if not expected or not hmac.compare_digest(presented, expected):
                raise PermissionDenied(
                    "missing or invalid CSRF token for a cookie-authenticated request",
                    details={"reason": "csrf"},
                )
    bind_organization(db, principal.organization_id)
    request.state.principal = principal
    request.state.auth_via = via
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require(permission: Permission):
    """Dependency factory: 401 if unauthenticated, 403 if the role lacks
    `permission`. Enforced server-side on every request.

    A denial is itself audited (`auth.permission_denied`) on an independent
    connection, so it is recorded even though the request fails.
    """

    def _check(request: Request, principal: CurrentPrincipal) -> Principal:
        if not principal.can(permission):
            try:
                with session_scope(principal.organization_id) as audit_db:
                    audit.record(
                        audit_db,
                        event_type="auth.permission_denied",
                        resource_type="endpoint",
                        resource_id=f"{request.method} {request.url.path}"[:64],
                        actor=principal.label,
                        request_id=getattr(request.state, "request_id", None),
                        payload={"permission": permission.value, "role": principal.role.value},
                    )
            except Exception:  # pragma: no cover - never mask the 403
                pass
            raise PermissionDenied(
                f"your role ({principal.role.value}) is not permitted to do this",
                details={"required_permission": permission.value},
            )
        return principal

    return _check


def current_org(principal: CurrentPrincipal) -> uuid.UUID:
    """The caller's organization - from the verified principal, never the client."""
    return principal.organization_id


OrgId = Annotated[uuid.UUID, Depends(current_org)]


def rate_limit(bucket: str, *, limit: Callable[[], int], window_seconds: int):
    """Dependency factory: at most `limit()` calls per client IP per window.

    `limit` is a callable so the configured value is read at request time.
    The client address comes from the TCP peer (uvicorn's `--proxy-headers`
    substitutes the proxy-supplied one when the proxy is trusted).
    """

    def _check(request: Request) -> None:
        host = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.check(
            f"{bucket}:{host}", limit=limit(), window_seconds=window_seconds
        )
        if not allowed:
            raise TooManyRequests(
                "too many attempts; wait a moment and try again",
                details={"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

    return _check


def rate_limit_per_principal(bucket: str, *, limit: Callable[[], int], window_seconds: int = 60):
    """Like `rate_limit`, but keyed by the authenticated caller (for costly,
    metered endpoints such as the AI copilot) rather than by IP."""

    def _check(principal: CurrentPrincipal) -> None:
        allowed, retry_after = limiter.check(
            f"{bucket}:{principal.ref}", limit=limit(), window_seconds=window_seconds
        )
        if not allowed:
            raise TooManyRequests(
                "you are sending requests too quickly; wait a moment and try again",
                details={"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

    return _check


login_rate_limit = rate_limit(
    "login", limit=lambda: settings.rate_limit_login_per_minute, window_seconds=60
)
register_rate_limit = rate_limit(
    "register", limit=lambda: settings.rate_limit_register_per_hour, window_seconds=3600
)


def actor(principal: CurrentPrincipal) -> str:
    """The label recorded as `actor` on every transaction and audit event
    this request produces (user email, or API key label)."""
    return principal.label


Actor = Annotated[str, Depends(actor)]


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    return idempotency_key.strip()[:255] or None


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]
