"""Organizations, signup and user administration.

Every function here is explicit about the organization it acts on: users and
organizations are *not* covered by the automatic tenant scoping (login has to
find a user before it knows their organization), so isolation for this module
is enforced by always filtering on `organization_id` and by 404-ing - never
403-ing - on a user id from another organization, which would confirm it exists.

Privilege rules (`app.core.permissions.can_manage_role`): an OWNER manages every
role; an ADMIN manages only roles below ADMIN, so nobody can grant themselves
or a peer more power than they have. An organization always keeps at least one
active OWNER.
"""

from __future__ import annotations

import hmac
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import money
from app.core.config import settings
from app.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    SignupDisabled,
    UnsupportedCurrency,
    ValidationFailed,
)
from app.core.permissions import can_manage_role
from app.db.tenancy import bind_organization
from app.models.enums import UserRole
from app.models.user import Organization, User
from app.services import audit
from app.services import auth as auth_service

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    cleaned = email.strip()
    if not _EMAIL_RE.match(cleaned) or len(cleaned) > 254:
        raise ValidationFailed("enter a valid email address", details={"field": "email"})
    return cleaned


def get_organization(db: Session, organization_id: uuid.UUID) -> Organization | None:
    return db.get(Organization, organization_id)


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_organization(
    db: Session,
    *,
    organization_name: str,
    base_currency: str,
    email: str,
    full_name: str,
    password: str,
    signup_token: str | None,
    request_id: str | None = None,
) -> tuple[Organization, User]:
    """Create an organization and its first member, an OWNER, in one transaction.

    Either everything is created or nothing is: a duplicate email rolls back the
    organization too. Anyone can call this when `ALLOW_SIGNUP` is on - it
    creates a brand-new, empty tenant, and grants no access to any existing one.
    """
    if not settings.allow_signup:
        raise SignupDisabled("self-service signup is disabled on this deployment")
    if settings.signup_token and not hmac.compare_digest(signup_token or "", settings.signup_token):
        raise PermissionDenied("a valid signup code is required to create an organization")

    base_currency = base_currency.upper()
    if not money.is_supported(base_currency):
        raise UnsupportedCurrency(f"currency {base_currency} is not supported")
    email = normalize_email(email)
    auth_service.validate_password_strength(password)
    name = organization_name.strip()
    if not name:
        raise ValidationFailed(
            "organization name is required", details={"field": "organization_name"}
        )

    org = Organization(id=uuid.uuid4(), name=name, base_currency=base_currency)
    user = User(
        id=uuid.uuid4(),
        organization_id=org.id,
        email=email,
        full_name=full_name.strip(),
        password_hash=auth_service.hash_password(password),
        role=UserRole.OWNER,
    )
    db.add(org)
    db.flush()  # the organization must exist before the user references it
    db.add(user)
    bind_organization(db, org.id)
    audit.record(
        db,
        event_type="organization.registered",
        resource_type="organization",
        resource_id=str(org.id),
        actor=email,
        request_id=request_id,
        payload={"organization": name, "owner_email": email},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("an account with this email already exists") from exc
    return org, user


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


def list_users(db: Session, organization_id: uuid.UUID) -> list[User]:
    return list(
        db.execute(
            select(User).where(User.organization_id == organization_id).order_by(User.created_at)
        )
        .scalars()
        .all()
    )


def _get_member(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.execute(
        select(User).where(User.id == user_id, User.organization_id == organization_id)
    ).scalar_one_or_none()
    if user is None:
        raise NotFound(f"user {user_id} not found")
    return user


def _require_can_manage(actor_role: UserRole, target_role: UserRole) -> None:
    if not can_manage_role(actor_role, target_role):
        raise PermissionDenied(
            f"a {actor_role.value} cannot grant or change the {target_role.value} role",
            details={"actor_role": actor_role.value, "target_role": target_role.value},
        )


def create_user(
    db: Session,
    *,
    organization_id: uuid.UUID,
    actor_role: UserRole,
    email: str,
    full_name: str,
    password: str,
    role: UserRole,
    actor: str,
    request_id: str | None = None,
) -> User:
    _require_can_manage(actor_role, role)
    email = normalize_email(email)
    auth_service.validate_password_strength(password)
    user = User(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email=email,
        full_name=full_name.strip(),
        password_hash=auth_service.hash_password(password),
        role=role,
    )
    db.add(user)
    audit.record(
        db,
        event_type="user.created",
        resource_type="user",
        resource_id=str(user.id),
        actor=actor,
        request_id=request_id,
        payload={"email": email, "role": role.value},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"a user with email {email} already exists") from exc
    return user


def _active_owner_count(db: Session, organization_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count(User.id)).where(
            User.organization_id == organization_id,
            User.role == UserRole.OWNER,
            User.is_active.is_(True),
        )
    ).scalar_one()


def update_user(
    db: Session,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    actor_role: UserRole,
    role: UserRole | None,
    is_active: bool | None,
    full_name: str | None,
    actor: str,
    request_id: str | None = None,
) -> User:
    # Lock the row so two concurrent edits can't both pass the "last owner" check.
    user = db.execute(
        select(User)
        .where(User.id == user_id, User.organization_id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if user is None:
        db.rollback()
        raise NotFound(f"user {user_id} not found")

    try:
        _require_can_manage(actor_role, user.role)
        if role is not None and role != user.role:
            _require_can_manage(actor_role, role)
    except PermissionDenied:
        db.rollback()
        raise

    demoting = role is not None and role != user.role and user.role is UserRole.OWNER
    deactivating = is_active is False and user.is_active
    if (
        user.role is UserRole.OWNER
        and user.is_active
        and (demoting or deactivating)
        and _active_owner_count(db, organization_id) <= 1
    ):
        db.rollback()
        raise Conflict("an organization must keep at least one active owner")

    changes: dict[str, object] = {}
    if role is not None and role != user.role:
        changes["role"] = {"from": user.role.value, "to": role.value}
        user.role = role
    if is_active is not None and is_active != user.is_active:
        changes["is_active"] = is_active
        user.is_active = is_active
        if not is_active:
            auth_service.revoke_user_sessions(db, user.id)
    if full_name is not None and full_name.strip() != user.full_name:
        changes["full_name"] = full_name.strip()
        user.full_name = full_name.strip()

    if changes:
        audit.record(
            db,
            event_type="user.updated",
            resource_type="user",
            resource_id=str(user.id),
            actor=actor,
            request_id=request_id,
            payload={"email": user.email, "changes": changes},
        )
    db.commit()
    return user


def reset_password(
    db: Session,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    actor_role: UserRole,
    new_password: str,
    actor: str,
    request_id: str | None = None,
) -> User:
    auth_service.validate_password_strength(new_password)
    user = _get_member(db, organization_id, user_id)
    _require_can_manage(actor_role, user.role)
    user.password_hash = auth_service.hash_password(new_password)
    user.failed_login_count = 0
    user.locked_until = None
    auth_service.revoke_user_sessions(db, user.id)
    audit.record(
        db,
        event_type="user.password_reset",
        resource_type="user",
        resource_id=str(user.id),
        actor=actor,
        request_id=request_id,
        payload={"email": user.email},
    )
    db.commit()
    return user


def change_own_password(
    db: Session,
    user_id: uuid.UUID,
    *,
    current_password: str,
    new_password: str,
    request_id: str | None = None,
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("user not found")
    if not auth_service.verify_password(current_password, user.password_hash):
        raise ValidationFailed(
            "the current password is incorrect", details={"field": "current_password"}
        )
    auth_service.validate_password_strength(new_password)
    user.password_hash = auth_service.hash_password(new_password)
    # Every session - including this one - ends; the caller signs in again.
    auth_service.revoke_user_sessions(db, user.id)
    audit.record(
        db,
        event_type="user.password_changed",
        resource_type="user",
        resource_id=str(user.id),
        actor=user.email,
        request_id=request_id,
    )
    db.commit()


# --------------------------------------------------------------------------
# organization settings
# --------------------------------------------------------------------------


def update_organization(
    db: Session,
    organization_id: uuid.UUID,
    *,
    name: str | None,
    approval_threshold_minor: int | None,
    clear_threshold: bool,
    actor: str,
    request_id: str | None = None,
) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None:
        raise NotFound("organization not found")
    changes: dict[str, object] = {}
    if name is not None and name.strip() != org.name:
        changes["name"] = name.strip()
        org.name = name.strip()
    if clear_threshold and org.approval_threshold_minor is not None:
        changes["approval_threshold_minor"] = None
        org.approval_threshold_minor = None
    elif (
        approval_threshold_minor is not None
        and approval_threshold_minor != org.approval_threshold_minor
    ):
        changes["approval_threshold_minor"] = approval_threshold_minor
        org.approval_threshold_minor = approval_threshold_minor
    if changes:
        audit.record(
            db,
            event_type="organization.updated",
            resource_type="organization",
            resource_id=str(org.id),
            actor=actor,
            request_id=request_id,
            payload=changes,
        )
    db.commit()
    return org
