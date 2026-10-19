"""Authentication: passwords, login sessions and API keys.

Two kinds of credential resolve to one `Principal`:

* **Login sessions** (humans). A password is verified against a salted scrypt
  hash (stdlib `hashlib.scrypt`; memory-hard, no extra dependency); success
  mints an opaque random token (`lgs_...`). Only the token's SHA-256 hash is
  stored, in `user_sessions`, so reading the database never yields a usable
  session. Sessions are server-side rather than stateless JWTs because logout,
  deactivating a user and expiry then take effect on the very next request.
  A session ends at an absolute deadline *or* after an idle period.
* **API keys** (services). `ledgr_...` high-entropy secrets stored as SHA-256
  hashes, each with a role, last-used timestamp and revocation.

Repeated failed logins lock the account briefly. Every failure answers with
the same generic message so the API does not reveal which emails exist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import InvalidCredentials, SessionExpired, ValidationFailed
from app.core.permissions import Permission, permissions_for
from app.db.tenancy import bind_organization
from app.models.auth import ApiKey
from app.models.enums import UserRole
from app.models.user import User, UserSession
from app.services import audit

_KEY_PREFIX = "ledgr_"
SESSION_PREFIX = "lgs_"

# scrypt cost: ~16 MiB / ~50 ms per hash. Parameters are stored in each hash,
# so they can be raised later without invalidating existing passwords.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

# How often a session's `last_seen_at` is written - not on every request.
_TOUCH_INTERVAL = timedelta(seconds=30)

_GENERIC_LOGIN_FAILURE = "the email or password is incorrect"


# --------------------------------------------------------------------------
# principals
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, however it authenticated."""

    kind: str  # "user" | "apikey"
    id: uuid.UUID
    label: str  # recorded as `actor` on transactions and audit events
    role: UserRole
    organization_id: uuid.UUID
    session_id: uuid.UUID | None = None
    # Present for login sessions: what a cookie-authenticated browser must echo
    # in `X-CSRF-Token` on state-changing requests.
    csrf_token: str | None = None

    @property
    def ref(self) -> str:
        """Stable identity used for separation-of-duties checks."""
        return f"{self.kind}:{self.id}"

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """`scrypt$N$r$p$<salt>$<hash>` - self-describing so costs can evolve."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


# Verified against when the email is unknown, so "no such user" costs the same
# as "wrong password" and response time does not reveal which emails exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def validate_password_strength(password: str) -> None:
    if len(password) < settings.min_password_length:
        raise ValidationFailed(
            f"password must be at least {settings.min_password_length} characters",
            details={"field": "password"},
        )
    if len(password) > 256:
        raise ValidationFailed("password is too long", details={"field": "password"})
    if not password.strip():
        raise ValidationFailed("password cannot be blank", details={"field": "password"})


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# login sessions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    token: str
    user: User
    expires_at: datetime
    csrf_token: str


def _find_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalar_one_or_none()


def login(
    db: Session,
    *,
    email: str,
    password: str,
    request_id: str | None = None,
    user_agent: str | None = None,
) -> LoginResult:
    """Verify credentials and open a session, or raise `InvalidCredentials`.

    The failure bookkeeping (counter, lockout, audit event) is committed
    *before* raising, so a rejected attempt is never rolled back and lost.
    """
    user = _find_user_by_email(db, email)
    now = _now()

    if user is None:
        verify_password(password, _DUMMY_HASH)
        audit.record(
            db,
            event_type="auth.login_failed",
            resource_type="user",
            actor=email.strip()[:120] or "anonymous",
            request_id=request_id,
            payload={"reason": "unknown_user"},
        )
        db.commit()
        raise InvalidCredentials(_GENERIC_LOGIN_FAILURE)

    # The user's organization is known from here on, so the audit trail below
    # lands in the right tenant.
    bind_organization(db, user.organization_id)
    locked = user.locked_until is not None and user.locked_until > now
    ok = verify_password(password, user.password_hash)

    if locked or not ok or not user.is_active:
        reason = "locked" if locked else "inactive" if not user.is_active else "bad_password"
        if not locked and user.is_active and not ok:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
                reason = "lockout_triggered"
        audit.record(
            db,
            event_type="auth.login_failed",
            resource_type="user",
            resource_id=str(user.id),
            actor=user.email,
            request_id=request_id,
            payload={"reason": reason},
        )
        db.commit()
        raise InvalidCredentials(_GENERIC_LOGIN_FAILURE)

    raw = SESSION_PREFIX + secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires_at = now + timedelta(minutes=settings.session_ttl_minutes)
    db.add(
        UserSession(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=_hash(raw),
            csrf_token=csrf,
            expires_at=expires_at,
            last_seen_at=now,
            user_agent=(user_agent or "")[:255] or None,
        )
    )
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit.record(
        db,
        event_type="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        actor=user.email,
        request_id=request_id,
        payload={"role": user.role.value},
    )
    db.commit()
    return LoginResult(token=raw, user=user, expires_at=expires_at, csrf_token=csrf)


def authenticate_session(db: Session, raw_token: str) -> Principal:
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == _hash(raw_token))
    ).one_or_none()
    if row is None:
        raise InvalidCredentials("the session token is invalid")
    session, user = row

    now = _now()
    idle_limit = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if session.revoked_at is not None:
        raise SessionExpired("this session has ended; sign in again")
    if session.expires_at <= now or idle_limit <= now:
        raise SessionExpired("your session has expired; sign in again")
    if not user.is_active:
        raise InvalidCredentials("this account is deactivated")

    if now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()

    return Principal(
        kind="user",
        id=user.id,
        label=user.email,
        role=user.role,
        organization_id=user.organization_id,
        session_id=session.id,
        csrf_token=session.csrf_token,
    )


def logout(db: Session, principal: Principal, *, request_id: str | None = None) -> None:
    if principal.session_id is not None:
        db.execute(
            update(UserSession)
            .where(UserSession.id == principal.session_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
    audit.record(
        db,
        event_type="auth.logout",
        resource_type="user",
        resource_id=str(principal.id),
        actor=principal.label,
        request_id=request_id,
    )
    db.commit()


def revoke_user_sessions(db: Session, user_id: uuid.UUID) -> int:
    """End every live session for a user (deactivation, password reset).
    Does not commit - the caller's unit of work does."""
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    return result.rowcount or 0


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------


def generate_key() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def create_api_key(
    db: Session,
    *,
    label: str,
    organization_id: uuid.UUID,
    role: UserRole = UserRole.ADMIN,
    created_by: str | None = None,
) -> tuple[ApiKey, str]:
    """Mint a new key. The plaintext is returned once and never stored -
    only its hash is. Lose it and a new key must be minted.

    A key belongs to exactly one organization and can never carry OWNER.
    """
    if role is UserRole.OWNER:
        raise ValidationFailed("API keys cannot hold the OWNER role")
    raw_key = generate_key()
    record = ApiKey(
        id=uuid.uuid4(),
        label=label,
        key_hash=_hash(raw_key),
        role=role,
        organization_id=organization_id,
        created_by=created_by,
        key_prefix=raw_key[: len(_KEY_PREFIX) + 4],
    )
    db.add(record)
    db.commit()
    return record, raw_key


def authenticate(db: Session, raw_key: str) -> ApiKey:
    """Resolve a presented key to its server-side identity, or reject it.

    Looked up by the hash of what was presented, never the plaintext - an
    attacker who reads the `api_keys` table gets nothing usable.
    """
    record = db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == _hash(raw_key),
            ApiKey.is_active.is_(True),
            ApiKey.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if record is None:
        raise InvalidCredentials("the API key is invalid or has been revoked")
    record.last_used_at = _now()
    db.commit()
    return record


def revoke_api_key(db: Session, key_id: uuid.UUID) -> ApiKey | None:
    record = db.get(ApiKey, key_id)
    if record is None:
        return None
    if record.revoked_at is None:
        record.revoked_at = _now()
        record.is_active = False
    db.flush()
    return record


def authenticate_bearer(db: Session, raw_token: str) -> Principal:
    """Resolve whatever bearer credential was presented."""
    if raw_token.startswith(SESSION_PREFIX):
        return authenticate_session(db, raw_token)
    key = authenticate(db, raw_token)
    return Principal(
        kind="apikey",
        id=key.id,
        label=key.label,
        role=key.role,
        organization_id=key.organization_id,
    )
