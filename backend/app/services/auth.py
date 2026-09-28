"""API key authentication.

Two operations: mint a key (returns the plaintext exactly once - only the
hash is ever persisted) and authenticate one (hash the presented value,
look it up, reject if absent or deactivated). No sessions, no JWTs, no
password flow - a caller either presents a valid key on every request or it
doesn't get in, which is the right amount of mechanism for a service-to-
service API with no end-user login of its own.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InvalidCredentials
from app.models.auth import ApiKey

_KEY_PREFIX = "ledgr_"


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def create_api_key(db: Session, *, label: str) -> tuple[ApiKey, str]:
    """Mint a new key. The plaintext is returned once and never stored -
    only its hash is. Lose it and a new key must be minted.
    """
    raw_key = generate_key()
    record = ApiKey(id=uuid.uuid4(), label=label, key_hash=_hash(raw_key))
    db.add(record)
    db.commit()
    return record, raw_key


def authenticate(db: Session, raw_key: str) -> ApiKey:
    """Resolve a presented key to its server-side identity, or reject it.

    Looked up by the hash of what was presented, never the plaintext - an
    attacker who reads the `api_keys` table gets nothing usable.
    """
    record = db.execute(
        select(ApiKey).where(ApiKey.key_hash == _hash(raw_key), ApiKey.is_active.is_(True))
    ).scalar_one_or_none()
    if record is None:
        raise InvalidCredentials("the API key is invalid or has been deactivated")
    record.last_used_at = datetime.now(UTC)
    db.commit()
    return record
