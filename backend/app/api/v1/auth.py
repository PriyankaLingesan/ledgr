"""Authentication endpoints: signup, login, logout, current user."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    RequestId,
    login_rate_limit,
    register_rate_limit,
)
from app.core.config import settings
from app.core.errors import PermissionDenied
from app.schemas.auth import (
    AuthConfigOut,
    CurrentUserOut,
    LoginRequest,
    LoginResponse,
    PasswordChange,
    RegisterRequest,
)
from app.services import auth as auth_service
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])


def current_user_out(db: DbSession, principal: auth_service.Principal) -> CurrentUserOut:
    org = users.get_organization(db, principal.organization_id)
    return CurrentUserOut(
        id=principal.id,
        kind=principal.kind,
        email=principal.label if principal.kind == "user" else None,
        name=principal.label,
        role=principal.role,
        permissions=sorted(p.value for p in principal.permissions),
        organization_id=principal.organization_id,
        organization_name=org.name if org else None,
        organization_demo_scenario=org.demo_scenario if org else None,
        csrf_token=principal.csrf_token,
    )


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    """The browser's credential: httpOnly (page scripts cannot read it), SameSite
    as configured, Secure when served over HTTPS."""
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )


def _login_response(
    db: DbSession, response: Response, result: auth_service.LoginResult
) -> LoginResponse:
    # Credentials in a response must never be cached by a browser or proxy.
    response.headers["Cache-Control"] = "no-store"
    _set_session_cookie(response, result.token, result.expires_at)
    principal = auth_service.authenticate_session(db, result.token)
    return LoginResponse(
        access_token=result.token,
        expires_at=result.expires_at,
        csrf_token=result.csrf_token,
        user=current_user_out(db, principal),
    )


@router.get("/config", response_model=AuthConfigOut)
def auth_config() -> AuthConfigOut:
    """Public: lets the sign-in screen know whether to offer signup."""
    return AuthConfigOut(
        signup_enabled=settings.allow_signup,
        signup_code_required=bool(settings.signup_token),
    )


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
def register(
    payload: RegisterRequest,
    db: DbSession,
    request_id: RequestId,
    response: Response,
    user_agent: str | None = Header(default=None),
) -> LoginResponse:
    """Create a new organization and its first member (the OWNER), then sign in.

    Creates an empty tenant only - it grants no access to any existing
    organization's data. Rate limited per client address.
    """
    _, user = users.register_organization(
        db,
        organization_name=payload.organization_name,
        base_currency=payload.base_currency,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        signup_token=payload.signup_code,
        request_id=request_id,
    )
    result = auth_service.login(
        db,
        email=user.email,
        password=payload.password,
        request_id=request_id,
        user_agent=user_agent,
    )
    return _login_response(db, response, result)


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(login_rate_limit)])
def login(
    payload: LoginRequest,
    db: DbSession,
    request_id: RequestId,
    response: Response,
    user_agent: str | None = Header(default=None),
) -> LoginResponse:
    result = auth_service.login(
        db,
        email=payload.email,
        password=payload.password,
        request_id=request_id,
        user_agent=user_agent,
    )
    return _login_response(db, response, result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: DbSession, principal: CurrentPrincipal, request_id: RequestId, request: Request
) -> Response:
    """Ends the current session server-side; the token and cookie stop working at once."""
    auth_service.logout(db, principal, request_id=request_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response)
    return response


@router.get("/me", response_model=CurrentUserOut)
def me(db: DbSession, principal: CurrentPrincipal) -> CurrentUserOut:
    return current_user_out(db, principal)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> Response:
    if principal.kind != "user":
        raise PermissionDenied("API keys have no password")
    users.change_own_password(
        db,
        principal.id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request_id=request_id,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response)
    return response
