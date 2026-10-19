"""Administration: users, API keys and organization settings.

Everything here requires ADMIN (`users:manage`, `api_keys:manage`,
`org:manage`); the organization itself is readable by any signed-in caller.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, RequestId, require
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.permissions import ASSIGNABLE_KEY_ROLES, Permission, can_manage_role
from app.models.auth import ApiKey
from app.models.user import Organization, User
from app.schemas.auth import (
    ApiKeyCreate,
    ApiKeyCreatedOut,
    ApiKeyOut,
    OrganizationOut,
    OrganizationUpdate,
    PasswordReset,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit
from app.services import auth as auth_service
from app.services import users as user_service

router = APIRouter(tags=["admin"])


def _user_out(user: User) -> UserOut:
    now = datetime.now(UTC)
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        locked=bool(user.locked_until and user.locked_until > now),
    )


def _key_out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        label=key.label,
        role=key.role,
        key_prefix=key.key_prefix,
        created_at=key.created_at,
        created_by=key.created_by,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
        is_active=key.is_active and key.revoked_at is None,
    )


def _org_out(org: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        name=org.name,
        base_currency=org.base_currency,
        approval_threshold_minor=org.approval_threshold_minor,
        demo_scenario=org.demo_scenario,
    )


# --- users ----------------------------------------------------------------------------


@router.get(
    "/users",
    response_model=list[UserOut],
    dependencies=[Depends(require(Permission.USERS_MANAGE))],
)
def list_users(principal: CurrentPrincipal, db: DbSession) -> list[UserOut]:
    return [_user_out(u) for u in user_service.list_users(db, principal.organization_id)]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.USERS_MANAGE))],
)
def create_user(
    payload: UserCreate, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> UserOut:
    user = user_service.create_user(
        db,
        organization_id=principal.organization_id,
        actor_role=principal.role,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role=payload.role,
        actor=principal.label,
        request_id=request_id,
    )
    return _user_out(user)


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(require(Permission.USERS_MANAGE))],
)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> UserOut:
    user = user_service.update_user(
        db,
        principal.organization_id,
        user_id,
        actor_role=principal.role,
        role=payload.role,
        is_active=payload.is_active,
        full_name=payload.full_name,
        actor=principal.label,
        request_id=request_id,
    )
    return _user_out(user)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=UserOut,
    dependencies=[Depends(require(Permission.USERS_MANAGE))],
)
def reset_password(
    user_id: uuid.UUID,
    payload: PasswordReset,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> UserOut:
    user = user_service.reset_password(
        db,
        principal.organization_id,
        user_id,
        actor_role=principal.role,
        new_password=payload.new_password,
        actor=principal.label,
        request_id=request_id,
    )
    return _user_out(user)


# --- API keys ---------------------------------------------------------------------------


@router.get(
    "/api-keys",
    response_model=list[ApiKeyOut],
    dependencies=[Depends(require(Permission.API_KEYS_MANAGE))],
)
def list_api_keys(principal: CurrentPrincipal, db: DbSession) -> list[ApiKeyOut]:
    keys = (
        db.execute(
            select(ApiKey)
            .where(ApiKey.organization_id == principal.organization_id)
            .order_by(ApiKey.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_key_out(k) for k in keys]


@router.post(
    "/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.API_KEYS_MANAGE))],
)
def create_api_key(
    payload: ApiKeyCreate, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> ApiKeyCreatedOut:
    """The plaintext key is in this response and nowhere else, ever: only its
    hash is stored, so it cannot be shown again."""
    label = payload.label.strip()
    if payload.role not in ASSIGNABLE_KEY_ROLES:
        raise ValidationFailed("API keys cannot hold the OWNER role", details={"field": "role"})
    if not can_manage_role(principal.role, payload.role):
        raise PermissionDenied(
            f"a {principal.role.value} cannot create a {payload.role.value} API key"
        )
    if db.execute(
        select(ApiKey.id).where(
            ApiKey.label == label, ApiKey.organization_id == principal.organization_id
        )
    ).first():
        raise Conflict(f"an API key named {label!r} already exists")
    record, raw = auth_service.create_api_key(
        db,
        label=label,
        role=payload.role,
        organization_id=principal.organization_id,
        created_by=principal.label,
    )
    audit.record(
        db,
        event_type="api_key.created",
        resource_type="api_key",
        resource_id=str(record.id),
        actor=principal.label,
        request_id=request_id,
        payload={"label": label, "role": payload.role.value, "prefix": record.key_prefix},
    )
    db.commit()
    return ApiKeyCreatedOut(**_key_out(record).model_dump(), key=raw)


@router.post(
    "/api-keys/{key_id}/revoke",
    response_model=ApiKeyOut,
    dependencies=[Depends(require(Permission.API_KEYS_MANAGE))],
)
def revoke_api_key(
    key_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> ApiKeyOut:
    existing = db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.organization_id == principal.organization_id
        )
    ).scalar_one_or_none()
    if existing is None:  # another organization's key looks exactly like a missing one
        raise NotFound(f"API key {key_id} not found")
    already = existing.revoked_at is not None
    record = auth_service.revoke_api_key(db, key_id)
    if record is None:  # pragma: no cover - raced with a delete; keys are never deleted
        raise NotFound(f"API key {key_id} not found")
    if not already:
        audit.record(
            db,
            event_type="api_key.revoked",
            resource_type="api_key",
            resource_id=str(record.id),
            actor=principal.label,
            request_id=request_id,
            payload={"label": record.label},
        )
    db.commit()
    return _key_out(record)


# --- organization -------------------------------------------------------------------------


@router.get(
    "/organization",
    response_model=OrganizationOut,
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
)
def get_organization(principal: CurrentPrincipal, db: DbSession) -> OrganizationOut:
    org = user_service.get_organization(db, principal.organization_id)
    if org is None:
        raise NotFound("organization has not been set up")
    return _org_out(org)


@router.patch(
    "/organization",
    response_model=OrganizationOut,
    dependencies=[Depends(require(Permission.ORG_MANAGE))],
)
def update_organization(
    payload: OrganizationUpdate,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> OrganizationOut:
    org = user_service.update_organization(
        db,
        principal.organization_id,
        name=payload.name,
        approval_threshold_minor=payload.approval_threshold_minor,
        clear_threshold=payload.clear_approval_threshold,
        actor=principal.label,
        request_id=request_id,
    )
    return _org_out(org)
