"""Investigation case lifecycle.

An investigation case is the operational record of "someone is looking into
this" - status, ownership, when it was opened and closed. It is never a
financial record and carries no amounts of its own: every figure a case's
evidence view shows is computed live, at read time, by
`app.services.investigation` from `ledger_entries`. This module only manages
the `investigations` table's own lifecycle - the workflow, not the numbers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFound, ValidationFailed
from app.models.enums import InvestigationSource, InvestigationStatus
from app.models.investigation import Investigation
from app.services import investigation as investigation_service

# What status an open case may move to next. RESOLVED -> INVESTIGATING covers
# reopening a case that turns out not to be settled after all; nothing may
# jump directly back to OPEN once work has started, since that would erase
# the fact that someone looked at it.
_VALID_TRANSITIONS: dict[InvestigationStatus, set[InvestigationStatus]] = {
    InvestigationStatus.OPEN: {InvestigationStatus.INVESTIGATING, InvestigationStatus.RESOLVED},
    InvestigationStatus.INVESTIGATING: {InvestigationStatus.RESOLVED},
    InvestigationStatus.RESOLVED: {InvestigationStatus.INVESTIGATING},
}


def _load(db: Session, case_id: uuid.UUID) -> Investigation:
    case = db.execute(
        select(Investigation)
        .options(
            selectinload(Investigation.account),
            selectinload(Investigation.correction_transaction),
        )
        .where(Investigation.id == case_id)
    ).scalar_one_or_none()
    if case is None:
        raise NotFound(
            f"investigation case {case_id} not found", details={"case_id": str(case_id)}
        )
    return case


def create_case(
    db: Session,
    *,
    title: str,
    issue_summary: str,
    source: InvestigationSource = InvestigationSource.MANUAL,
    source_reference: str | None = None,
    account_id: uuid.UUID | None = None,
    window_from: datetime | None = None,
    window_to: datetime | None = None,
    created_by: str = "system",
) -> Investigation:
    case = Investigation(
        id=uuid.uuid4(),
        case_number="",  # filled in below once `seq` is assigned
        title=title,
        issue_summary=issue_summary,
        source=source,
        source_reference=source_reference,
        account_id=account_id,
        window_from=window_from,
        window_to=window_to,
        created_by=created_by,
    )
    db.add(case)
    db.flush()  # assigns case.seq
    case.case_number = f"INV-{case.seq:04d}"
    db.commit()
    return _load(db, case.id)


def list_cases(
    db: Session,
    *,
    status: InvestigationStatus | None = None,
    account_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Investigation], int]:
    stmt = select(Investigation)
    count_stmt = select(func.count(Investigation.id))
    if status is not None:
        stmt = stmt.where(Investigation.status == status)
        count_stmt = count_stmt.where(Investigation.status == status)
    if account_id is not None:
        stmt = stmt.where(Investigation.account_id == account_id)
        count_stmt = count_stmt.where(Investigation.account_id == account_id)

    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(
            stmt.options(selectinload(Investigation.account))
            .order_by(Investigation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(items), int(total)


def get_case(db: Session, case_id: uuid.UUID) -> Investigation:
    return _load(db, case_id)


def update_status(
    db: Session,
    case_id: uuid.UUID,
    *,
    status: InvestigationStatus,
) -> Investigation:
    case = _load(db, case_id)
    if status != case.status and status not in _VALID_TRANSITIONS.get(case.status, set()):
        raise ValidationFailed(
            f"cannot move a case from {case.status.value} to {status.value}",
            details={"from": case.status.value, "to": status.value},
        )
    case.status = status
    if status is InvestigationStatus.RESOLVED:
        case.resolved_at = datetime.now(UTC)
    else:
        case.resolved_at = None
    db.commit()
    return _load(db, case.id)


def resolve_case(
    db: Session,
    case_id: uuid.UUID,
    *,
    resolution_note: str,
    correction_transaction_id: uuid.UUID | None = None,
) -> Investigation:
    case = _load(db, case_id)
    case.status = InvestigationStatus.RESOLVED
    case.resolved_at = datetime.now(UTC)
    case.resolution_note = resolution_note
    if correction_transaction_id is not None:
        case.correction_transaction_id = correction_transaction_id
    db.commit()
    return _load(db, case.id)


@dataclass(frozen=True)
class CaseEvidence:
    case: Investigation
    balance_change: investigation_service.BalanceChange | None
    duplicate_candidates: list[investigation_service.DuplicateGroup]
    integrity: list[investigation_service.IntegrityCheck]
    lineage: investigation_service.TransactionLineage | None


def case_evidence(db: Session, case_id: uuid.UUID) -> CaseEvidence:
    """Auto-assemble everything an investigator needs, from real data only.

    Nothing here is a new query: every figure comes from the same functions
    the Flight Recorder and Control Exception Engine already use. This
    function's only job is picking which of them apply to *this* case - an
    account-scoped case gets its balance change and account-scoped
    duplicates; a case that names a triggering transaction gets that
    transaction's lineage; the integrity checklist, being ledger-wide, is
    always included.
    """
    case = _load(db, case_id)

    change = None
    if case.account_id is not None:
        change = investigation_service.balance_change(
            db, case.account_id, from_ts=case.window_from, to_ts=case.window_to
        )

    duplicates = investigation_service.duplicate_candidates(db, limit=20)
    if case.account_id is not None:
        duplicates = [
            g for g in duplicates if any(e.account_id == case.account_id for e in g.entries)
        ]

    integrity = investigation_service.integrity_checklist(db)

    lineage = None
    if case.source_reference:
        try:
            txn_id = uuid.UUID(case.source_reference)
        except ValueError:
            txn_id = None
        if txn_id is not None:
            try:
                lineage = investigation_service.transaction_lineage(db, txn_id)
            except NotFound:
                lineage = None

    return CaseEvidence(
        case=case,
        balance_change=change,
        duplicate_candidates=duplicates,
        integrity=integrity,
        lineage=lineage,
    )
