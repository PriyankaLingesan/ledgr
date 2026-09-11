"""Ledger entry endpoints.

Entries are read-only over HTTP by construction: there is no PUT, PATCH or
DELETE route on this router, and none can be added without also defeating the
database triggers that back invariant I5.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import DbSession, PaginationParams
from app.core.errors import NotFound
from app.models.enums import EntryDirection
from app.models.ledger_entry import LedgerEntry
from app.schemas.common import Page
from app.schemas.serialize import ledger_entry_out
from app.schemas.transaction import LedgerEntryOut
from app.services import queries

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get("/entries", response_model=Page[LedgerEntryOut])
def list_entries(
    db: DbSession,
    page: PaginationParams,
    account_id: uuid.UUID | None = Query(default=None),
    transaction_id: uuid.UUID | None = Query(default=None),
    direction: EntryDirection | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    min_amount_minor: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None, description="Match transaction reference or description"),
    before_seq: int | None = Query(
        default=None,
        description="Keyset cursor: return entries with seq strictly below this value",
    ),
) -> Page[LedgerEntryOut]:
    items, total = queries.list_entries(
        db,
        account_id=account_id,
        transaction_id=transaction_id,
        direction=direction,
        currency=currency,
        created_from=created_from,
        created_to=created_to,
        min_amount_minor=min_amount_minor,
        query=q,
        before_seq=before_seq,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[LedgerEntryOut](
        items=[ledger_entry_out(e, with_account=True, with_transaction=True) for e in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/entries/{entry_id}", response_model=LedgerEntryOut)
def get_entry(entry_id: uuid.UUID, db: DbSession) -> LedgerEntryOut:
    """Single entry with its account and originating transaction attached.

    This is the audit path: entry -> transaction -> accounts, in one hop.
    """
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        raise NotFound(f"ledger entry {entry_id} not found")
    return ledger_entry_out(entry, with_account=True, with_transaction=True)
