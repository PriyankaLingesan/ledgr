"""Read-side queries for transactions, ledger entries and audit events.

Kept apart from the posting engine: reads have completely different concerns
(filters, pagination, eager loading) and no business rules to enforce.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFound
from app.models.audit import AuditEvent
from app.models.enums import EntryDirection, TransactionKind, TransactionStatus
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction

# --------------------------------------------------------------------------
# transactions
# --------------------------------------------------------------------------


def list_transactions(
    db: Session,
    *,
    query: str | None = None,
    status: TransactionStatus | None = None,
    kind: TransactionKind | None = None,
    currency: str | None = None,
    account_id: uuid.UUID | None = None,
    posted_from: datetime | None = None,
    posted_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Transaction], int]:
    filters = []
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(
            or_(
                Transaction.reference.ilike(pattern),
                Transaction.description.ilike(pattern),
                Transaction.external_reference.ilike(pattern),
            )
        )
    if status is not None:
        filters.append(Transaction.status == status)
    if kind is not None:
        filters.append(Transaction.kind == kind)
    if currency:
        filters.append(Transaction.currency == currency.upper())
    if posted_from is not None:
        filters.append(Transaction.posted_at >= posted_from)
    if posted_to is not None:
        filters.append(Transaction.posted_at <= posted_to)
    if account_id is not None:
        # EXISTS rather than a join: a transaction with several entries against
        # the same account must not appear more than once.
        filters.append(
            select(LedgerEntry.id)
            .where(
                LedgerEntry.transaction_id == Transaction.id,
                LedgerEntry.account_id == account_id,
            )
            .exists()
        )

    total = db.execute(select(func.count(Transaction.id)).where(*filters)).scalar_one()

    rows = (
        db.execute(
            select(Transaction)
            .options(
                selectinload(Transaction.entries).selectinload(LedgerEntry.account),
                selectinload(Transaction.reversed_by),
            )
            .where(*filters)
            .order_by(Transaction.seq.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def get_transaction(db: Session, transaction_id: uuid.UUID) -> Transaction:
    transaction = db.execute(
        select(Transaction)
        .options(
            selectinload(Transaction.entries).selectinload(LedgerEntry.account),
            selectinload(Transaction.reverses),
            selectinload(Transaction.reversed_by),
        )
        .where(Transaction.id == transaction_id)
    ).scalar_one_or_none()
    if transaction is None:
        raise NotFound(f"transaction {transaction_id} not found")
    return transaction


def get_transaction_by_reference(db: Session, reference: str) -> Transaction:
    transaction = db.execute(
        select(Transaction)
        .options(
            selectinload(Transaction.entries).selectinload(LedgerEntry.account),
            selectinload(Transaction.reverses),
            selectinload(Transaction.reversed_by),
        )
        .where(Transaction.reference == reference)
    ).scalar_one_or_none()
    if transaction is None:
        raise NotFound(f"transaction {reference!r} not found")
    return transaction


# --------------------------------------------------------------------------
# ledger entries
# --------------------------------------------------------------------------


def list_entries(
    db: Session,
    *,
    account_id: uuid.UUID | None = None,
    transaction_id: uuid.UUID | None = None,
    direction: EntryDirection | None = None,
    currency: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    min_amount_minor: int | None = None,
    query: str | None = None,
    before_seq: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[LedgerEntry], int]:
    filters = []
    if account_id is not None:
        filters.append(LedgerEntry.account_id == account_id)
    if transaction_id is not None:
        filters.append(LedgerEntry.transaction_id == transaction_id)
    if direction is not None:
        filters.append(LedgerEntry.direction == direction)
    if currency:
        filters.append(LedgerEntry.currency == currency.upper())
    if created_from is not None:
        filters.append(LedgerEntry.created_at >= created_from)
    if created_to is not None:
        filters.append(LedgerEntry.created_at <= created_to)
    if min_amount_minor is not None:
        filters.append(LedgerEntry.amount_minor >= min_amount_minor)
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(
            select(Transaction.id)
            .where(
                Transaction.id == LedgerEntry.transaction_id,
                or_(
                    Transaction.reference.ilike(pattern),
                    Transaction.description.ilike(pattern),
                ),
            )
            .exists()
        )

    total = db.execute(select(func.count(LedgerEntry.id)).where(*filters)).scalar_one()

    stmt = (
        select(LedgerEntry)
        .options(
            selectinload(LedgerEntry.account),
            selectinload(LedgerEntry.transaction),
        )
        .where(*filters)
        .order_by(LedgerEntry.seq.desc())
        .limit(limit)
    )
    if before_seq is not None:
        # Keyset pagination: stable under concurrent inserts, unlike OFFSET,
        # which shifts rows as new entries land at the head of the ledger.
        stmt = stmt.where(LedgerEntry.seq < before_seq)
    else:
        stmt = stmt.offset(offset)

    return list(db.execute(stmt).scalars().all()), int(total)


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


def list_audit_events(
    db: Session,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    filters = []
    if resource_type:
        filters.append(AuditEvent.resource_type == resource_type)
    if resource_id:
        filters.append(AuditEvent.resource_id == resource_id)
    if event_type:
        filters.append(AuditEvent.event_type == event_type)

    total = db.execute(select(func.count(AuditEvent.id)).where(*filters)).scalar_one()
    rows = (
        db.execute(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.seq.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)
