"""External reconciliation: comparing outside evidence against the ledger.

Deterministic and read-only with respect to the ledger. Nothing here posts a
transaction, edits a balance, or touches `ledger_entries` - the only writes
are to the three reconciliation tables themselves. Matching uses the field
the schema already had for exactly this purpose: `Transaction.
external_reference`. No fuzzy matching, no similarity scores, no AI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.core.errors import DuplicateReference, NotFound
from app.models.enums import (
    EntryDirection,
    ReconciliationStatus,
    TransactionKind,
    TransactionStatus,
)
from app.models.reconciliation import ExternalTransaction, ReconciliationResult, ReconciliationRun
from app.models.transaction import Transaction
from app.services import audit
from app.services import posting as posting_service
from app.services.posting import EntryCommand, PostCommand


@dataclass(frozen=True)
class ExternalRecord:
    external_id: str
    amount_minor: int
    currency: str
    direction: EntryDirection
    transaction_timestamp: datetime
    account_reference: str | None = None
    status: str = "SETTLED"
    description: str = ""


@dataclass(frozen=True)
class IngestResult:
    imported: int
    skipped_duplicates: int


def ingest_external_transactions(
    db: Session, *, source_system: str, records: list[ExternalRecord]
) -> IngestResult:
    """Idempotent by (source_system, external_id): importing the same
    dataset twice never creates duplicate external records - the same
    `INSERT ... ON CONFLICT DO NOTHING ... RETURNING` shape `idempotency.py`
    already uses, applied here to evidence rows instead of a claim row.
    """
    imported = 0
    for record in records:
        result = db.execute(
            pg_insert(ExternalTransaction)
            .values(
                id=uuid.uuid4(),
                source_system=source_system,
                external_id=record.external_id,
                account_reference=record.account_reference,
                amount_minor=record.amount_minor,
                currency=record.currency.upper(),
                direction=record.direction,
                status=record.status,
                transaction_timestamp=record.transaction_timestamp,
                description=record.description,
            )
            .on_conflict_do_nothing(
                index_elements=["source_system", "external_id"]
            )
            .returning(ExternalTransaction.id)
        ).first()
        if result is not None:
            imported += 1
    db.commit()
    return IngestResult(imported=imported, skipped_duplicates=len(records) - imported)


@dataclass(frozen=True)
class _Outcome:
    status: ReconciliationStatus
    internal: Transaction | None
    explanation: str
    difference_minor: int | None


def _amount_outcome(external: ExternalTransaction, internal: Transaction) -> _Outcome:
    # A transaction's "value" is its total_debits_minor - the same convention
    # `daily_volume` already uses: in double-entry the debit side is the
    # whole amount that moved.
    if internal.total_debits_minor == external.amount_minor and internal.currency == external.currency:
        explanation = (
            f"External id {external.external_id} matched internal transaction "
            f"{internal.reference}. Amount: {external.amount_minor / 100:.2f} "
            f"{external.currency} = {internal.total_debits_minor / 100:.2f} {internal.currency}."
        )
        return _Outcome(ReconciliationStatus.MATCHED, internal, explanation, 0)

    difference = external.amount_minor - internal.total_debits_minor
    explanation = (
        f"External amount: {external.amount_minor / 100:.2f} {external.currency}. "
        f"Internal amount: {internal.total_debits_minor / 100:.2f} {internal.currency}. "
        f"Difference: {abs(difference) / 100:.2f} {external.currency}."
    )
    return _Outcome(ReconciliationStatus.AMOUNT_MISMATCH, internal, explanation, difference)


def _classify(db: Session, external: ExternalTransaction) -> _Outcome:
    """One external record's outcome. Matching key: `external_reference`
    equals the external system's own id for the record - the primary
    identity `Transaction` already exposes for exactly this linkage.

    `external_reference` is not unique on `Transaction` - and cannot safely
    be made unique, because reversal and correction *legitimately* copy it
    onto the new transaction they produce (the original and its replacement
    share a reference by design). So every transaction sharing this
    reference is fetched, then narrowed to the "live" one: POSTED and
    STANDARD-kind, the ledger's own definition of "the current, standing
    representation" (a REVERSED original or a REVERSAL leg is superseded,
    not a second candidate). Only if more than one transaction is *still*
    live after that narrowing - a genuine data anomaly, not the normal
    correction chain - is the match reported as ambiguous rather than
    silently guessed at.
    """
    candidates = (
        db.execute(
            select(Transaction).where(Transaction.external_reference == external.external_id)
        )
        .scalars()
        .all()
    )
    live = [
        t
        for t in candidates
        if t.status is TransactionStatus.POSTED and t.kind is TransactionKind.STANDARD
    ]

    if len(live) == 1:
        return _amount_outcome(external, live[0])

    if len(live) > 1:
        refs = ", ".join(sorted(t.reference for t in live))
        explanation = (
            f"{len(live)} internal transactions are all currently POSTED with "
            f"external_reference={external.external_id!r}: {refs}. Cannot determine a single "
            f"match without more information - this requires investigation, not a guess."
        )
        return _Outcome(ReconciliationStatus.AMBIGUOUS_INTERNAL_MATCH, None, explanation, None)

    # len(live) == 0: either no transaction ever referenced this external id,
    # or every one that did has since been reversed with nothing posted in
    # its place. Both are "no live internal representation right now" from
    # reconciliation's point of view, but the explanation says which.
    if candidates:
        refs = ", ".join(sorted(f"{t.reference} ({t.status.value})" for t in candidates))
        explanation = (
            f"External transaction {external.external_id} ({external.source_system}) for "
            f"{external.amount_minor / 100:.2f} {external.currency}: {len(candidates)} internal "
            f"transaction(s) reference it, but none is currently POSTED: {refs}."
        )
    else:
        explanation = (
            f"External transaction {external.external_id} ({external.source_system}) for "
            f"{external.amount_minor / 100:.2f} {external.currency} has no internal LEDGR "
            f"transaction with external_reference={external.external_id!r}."
        )
    return _Outcome(ReconciliationStatus.MISSING_INTERNAL, None, explanation, None)


def _find_missing_external(db: Session) -> list[tuple[Transaction, _Outcome]]:
    """Live internal transactions that name an external reference no
    imported external record actually has.

    Scoped to the same "live" definition `_classify` uses (POSTED,
    STANDARD-kind), so a reversed original isn't independently flagged
    alongside its replacement - each external reference is judged once, by
    its current standing representation.
    """
    live_referenced = (
        db.execute(
            select(Transaction).where(
                Transaction.external_reference.is_not(None),
                Transaction.status == TransactionStatus.POSTED,
                Transaction.kind == TransactionKind.STANDARD,
            )
        )
        .scalars()
        .all()
    )
    if not live_referenced:
        return []

    known_external_ids = set(db.execute(select(ExternalTransaction.external_id)).scalars().all())

    missing: list[tuple[Transaction, _Outcome]] = []
    for internal in live_referenced:
        if internal.external_reference in known_external_ids:
            continue
        explanation = (
            f"Internal transaction {internal.reference} names external_reference="
            f"{internal.external_reference!r} ({internal.total_debits_minor / 100:.2f} "
            f"{internal.currency}), but no imported external record has that id."
        )
        missing.append(
            (internal, _Outcome(ReconciliationStatus.MISSING_EXTERNAL, internal, explanation, None))
        )
    return missing


def run_reconciliation(db: Session, *, actor: str = "system") -> ReconciliationRun:
    """Compare every ingested external transaction against the ledger, both
    directions: every external record against the ledger, and every
    live, externally-referenced internal transaction against imported
    evidence.

    Read-only with respect to `transactions`/`ledger_entries`: every write
    here lands only in the three reconciliation tables, in one commit.
    Repeatable - each call produces a fresh, independent run and its own
    result rows, so a past run's findings are never overwritten. Never
    produces a silent guess: an internal reference shared by more than one
    currently-POSTED transaction is reported as AMBIGUOUS_INTERNAL_MATCH,
    not resolved by picking one.
    """
    externals = db.execute(select(ExternalTransaction)).scalars().all()

    run = ReconciliationRun(id=uuid.uuid4())
    db.add(run)
    db.flush()

    counts: dict[ReconciliationStatus, int] = dict.fromkeys(ReconciliationStatus, 0)

    for external in externals:
        outcome = _classify(db, external)
        counts[outcome.status] += 1
        db.add(
            ReconciliationResult(
                id=uuid.uuid4(),
                run_id=run.id,
                external_transaction_id=external.id,
                internal_transaction_id=outcome.internal.id if outcome.internal else None,
                status=outcome.status,
                explanation=outcome.explanation,
                difference_minor=outcome.difference_minor,
            )
        )

    for internal, outcome in _find_missing_external(db):
        counts[outcome.status] += 1
        db.add(
            ReconciliationResult(
                id=uuid.uuid4(),
                run_id=run.id,
                external_transaction_id=None,
                internal_transaction_id=internal.id,
                status=outcome.status,
                explanation=outcome.explanation,
                difference_minor=None,
            )
        )

    run.external_count = len(externals)
    run.matched_count = counts[ReconciliationStatus.MATCHED]
    run.mismatch_count = counts[ReconciliationStatus.AMOUNT_MISMATCH]
    run.missing_internal_count = counts[ReconciliationStatus.MISSING_INTERNAL]
    run.missing_external_count = counts[ReconciliationStatus.MISSING_EXTERNAL]
    run.ambiguous_count = counts[ReconciliationStatus.AMBIGUOUS_INTERNAL_MATCH]

    audit.record(
        db,
        event_type="reconciliation.run_completed",
        resource_type="reconciliation_run",
        resource_id=str(run.id),
        actor=actor,
        payload={
            "external_count": run.external_count,
            "matched_count": run.matched_count,
            "mismatch_count": run.mismatch_count,
            "missing_internal_count": run.missing_internal_count,
            "missing_external_count": run.missing_external_count,
            "ambiguous_count": run.ambiguous_count,
        },
    )
    db.commit()

    return load_run(db, run.id)


def load_run(db: Session, run_id: uuid.UUID) -> ReconciliationRun:
    run = db.execute(
        select(ReconciliationRun)
        .options(
            selectinload(ReconciliationRun.results).selectinload(
                ReconciliationResult.external_transaction
            ),
            selectinload(ReconciliationRun.results).selectinload(
                ReconciliationResult.internal_transaction
            ),
        )
        .where(ReconciliationRun.id == run_id)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise NotFound(f"reconciliation run {run_id} not found", details={"run_id": str(run_id)})
    return run


def list_runs(db: Session, *, limit: int = 20, offset: int = 0) -> tuple[list[ReconciliationRun], int]:
    total = db.execute(select(func.count(ReconciliationRun.id))).scalar_one()
    items = (
        db.execute(
            select(ReconciliationRun)
            .order_by(ReconciliationRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(items), int(total)


def get_result(db: Session, result_id: uuid.UUID) -> ReconciliationResult:
    result = db.execute(
        select(ReconciliationResult)
        .options(
            selectinload(ReconciliationResult.external_transaction),
            selectinload(ReconciliationResult.internal_transaction),
        )
        .where(ReconciliationResult.id == result_id)
    ).scalar_one_or_none()
    if result is None:
        raise NotFound(
            f"reconciliation result {result_id} not found", details={"result_id": str(result_id)}
        )
    return result


# --------------------------------------------------------------------------
# demo data - deterministic, isolated, safe to re-run
# --------------------------------------------------------------------------

_DEMO_SOURCE = "PAYMENT_PROVIDER_A"

# Fixed references so re-running the demo loader is a safe no-op: a
# DuplicateReference on the internal post, or ON CONFLICT DO NOTHING on the
# external import, both mean "already loaded" rather than an error.
_DEMO_INTERNAL_TRANSACTIONS = [
    # (reference, external_reference, amount_minor)
    ("RECON-DEMO-MATCH-1", "PAY-001", 500_000),
    ("RECON-DEMO-MATCH-2", "PAY-002", 250_000),
    ("RECON-DEMO-MATCH-3", "PAY-006", 300_000),
    ("RECON-DEMO-MATCH-4", "PAY-007", 15_000),
    ("RECON-DEMO-MISMATCH-1", "PAY-003", 450_000),  # booked 4500.00; external claims 5000.00
]

_DEMO_EXTERNAL_RECORDS = [
    ExternalRecord("PAY-001", 500_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 8, 18, 34), description="Processor settlement"),
    ExternalRecord("PAY-002", 250_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 8, 18, 40), description="Processor settlement"),
    ExternalRecord("PAY-003", 500_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 8, 18, 46), description="Processor settlement"),
    ExternalRecord("PAY-004", 80_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 8, 18, 52), description="Processor settlement"),
    ExternalRecord("PAY-005", 120_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 8, 18, 58), description="Processor settlement"),
    ExternalRecord("PAY-006", 300_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 9, 9, 10), description="Processor settlement"),
    ExternalRecord("PAY-007", 15_000, "USD", EntryDirection.DEBIT, datetime(2026, 9, 9, 9, 16), description="Processor settlement"),
    ExternalRecord("PAY-008", 99_900, "USD", EntryDirection.DEBIT, datetime(2026, 9, 9, 9, 22), description="Processor settlement"),
]


def load_demo_data(db: Session, *, cash_account_id: uuid.UUID, revenue_account_id: uuid.UUID) -> IngestResult:
    """Seed a small, deterministic reconciliation scenario: 4 exact matches,
    1 amount mismatch, 3 missing-internal. Safe to call more than once -
    every write here is idempotent.
    """
    for reference, external_reference, amount_minor in _DEMO_INTERNAL_TRANSACTIONS:
        try:
            posting_service.post_transaction(
                db,
                PostCommand(
                    description=f"Reconciliation demo: {external_reference}",
                    currency="USD",
                    entries=[
                        EntryCommand(cash_account_id, EntryDirection.DEBIT, amount_minor),
                        EntryCommand(revenue_account_id, EntryDirection.CREDIT, amount_minor),
                    ],
                    reference=reference,
                    external_reference=external_reference,
                    actor="reconciliation-demo",
                ),
            )
        except DuplicateReference:
            db.rollback()  # already loaded from a previous run - not an error

    return ingest_external_transactions(
        db, source_system=_DEMO_SOURCE, records=_DEMO_EXTERNAL_RECORDS
    )
