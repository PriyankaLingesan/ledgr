"""Idempotency.

Protocol
--------
1. **Claim** the key with `INSERT ... ON CONFLICT DO NOTHING`, committed on its
   own connection *before* any ledger work starts. Whoever wins the insert owns
   the request; the database arbitrates, not the application.
2. **Do the work** in a separate database transaction.
3. **Complete** the claim with the response snapshot, so a later retry replays
   byte-identical output instead of re-posting money.

Failure handling: if the work raises, the claim is released. The financial
write and the claim live in different transactions, but the ordering makes that
safe - a claim can outlive a failure (retry blocked until released) or be
released after a success only if the process dies between commit and complete,
which leaves a *posted* transaction and a free key. That residual risk is
handled by the client-supplied `reference` being unique: a blind retry then
fails with `duplicate_reference` rather than double-posting.

A concurrent retry that arrives while the first is still executing gets 409
rather than a queued wait: a caller retrying a request it has not yet had an
answer to should back off, not pile up connections.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.errors import IdempotencyKeyReused, IdempotentRequestInFlight
from app.db.session import session_scope
from app.models.enums import IdempotencyStatus
from app.models.idempotency import IdempotencyRecord

SCOPE_POST_TRANSACTION = "transactions.post"
SCOPE_REVERSE_TRANSACTION = "transactions.reverse"


def fingerprint(payload: Any) -> str:
    """Stable hash of a request body.

    Sorted keys and compact separators mean logically identical JSON hashes
    identically regardless of key order or whitespace.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: dict[str, Any]


def claim(
    *,
    scope: str,
    key: str,
    request_hash: str,
    request_id: str | None = None,
) -> Replay | None:
    """Claim a key. Returns a `Replay` if this request was already served."""
    with session_scope() as session:
        # `RETURNING`, not `rowcount`: whether `ON CONFLICT DO NOTHING` actually
        # inserted a row is unreliable to read off `CursorResult.rowcount` with
        # psycopg (it can report -1 for this statement shape even on a genuine
        # insert). `RETURNING` only ever yields the row that was inserted, so
        # "did I win the race" reduces to "is there a row here" - true
        # regardless of driver quirks.
        inserted = session.execute(
            pg_insert(IdempotencyRecord)
            .values(
                scope=scope,
                key=key,
                request_hash=request_hash,
                status=IdempotencyStatus.IN_PROGRESS,
                request_id=request_id,
            )
            .on_conflict_do_nothing(index_elements=["scope", "key"])
            .returning(IdempotencyRecord.key)
        ).first()
        if inserted is not None:
            return None  # claim acquired, caller proceeds

    with session_scope() as session:
        record = session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
            )
        ).scalar_one_or_none()

        if record is None:  # pragma: no cover - claim released between statements
            return None

        if record.request_hash != request_hash:
            raise IdempotencyKeyReused(
                "this idempotency key was already used with a different request body",
                details={"key": key, "scope": scope},
            )

        if record.status is IdempotencyStatus.COMPLETED and record.response_body is not None:
            return Replay(record.response_status or 200, record.response_body)

        raise IdempotentRequestInFlight(
            "a request with this idempotency key is still being processed",
            details={"key": key, "scope": scope},
        )


def complete(
    *,
    scope: str,
    key: str,
    status_code: int,
    body: dict[str, Any],
    transaction_id: uuid.UUID | None = None,
) -> None:
    with session_scope() as session:
        record = session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
            )
        ).scalar_one_or_none()
        if record is None:  # pragma: no cover
            return
        record.status = IdempotencyStatus.COMPLETED
        record.response_status = status_code
        record.response_body = body
        record.transaction_id = transaction_id
        record.completed_at = datetime.now(UTC)


def release(*, scope: str, key: str) -> None:
    """Free a claim whose work failed, so the caller may legitimately retry."""
    with session_scope() as session:
        session.execute(
            delete(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
                IdempotencyRecord.status == IdempotencyStatus.IN_PROGRESS,
            )
        )
