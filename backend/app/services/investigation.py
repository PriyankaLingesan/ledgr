"""Financial investigation: explaining how a balance came to be what it is.

Nothing here is a second source of truth. Every figure is derived from
`ledger_entries` at read time, the same table `balances.py` reads - this
module only adds *time-windowed* and *cross-account* views over that one
table, plus two read-only orchestrations (reverse-then-repost for a guided
correction, a self-join for duplicate detection) built entirely from
functions `posting.py` and `balances.py` already expose and already test.

Nothing in this module writes a ledger entry directly. `correct_transaction`
calls `posting.reverse_transaction` and `posting.post_transaction` - the same
two functions the API already exposes - so a correction is bound by exactly
the same invariants (I1-I13) as any hand-posted transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import AlreadyReversed, NotFound, NotReversible
from app.db.tenancy import require_organization
from app.models.account import Account, AccountBalance
from app.models.enums import EntryDirection, NormalBalance, TransactionKind, TransactionStatus
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.services import accounts as account_service
from app.services import balances
from app.services import posting as posting_service
from app.services.balances import DerivedBalance
from app.services.posting import EntryCommand, PostCommand

DEFAULT_WINDOW = timedelta(days=30)


def _signed_effect(direction: EntryDirection, amount_minor: int, normal: NormalBalance) -> int:
    """+amount if this entry moves the balance the way the account normally grows."""
    same_side = (direction is EntryDirection.DEBIT) == (normal is NormalBalance.DEBIT)
    return amount_minor if same_side else -amount_minor


# --------------------------------------------------------------------------
# balance-at-a-point-in-time
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountBalanceAt:
    account: Account
    as_of: datetime
    derived: DerivedBalance


def balance_at(db: Session, account_id: uuid.UUID, as_of: datetime) -> AccountBalanceAt:
    account = account_service.get_account(db, account_id)
    derived = balances.derive_for_account_as_of(db, account_id, as_of, inclusive=True)
    return AccountBalanceAt(account=account, as_of=as_of, derived=derived)


# --------------------------------------------------------------------------
# balance change over a window - the core of the Flight Recorder
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEvent:
    entry: LedgerEntry
    transaction: Transaction
    effect_minor: int  # signed, in the account's own normal orientation
    running_balance_minor: int  # balance immediately after this event, same orientation


@dataclass(frozen=True)
class BalanceChange:
    account: Account
    from_ts: datetime
    to_ts: datetime
    opening: DerivedBalance
    closing: DerivedBalance
    events: list[LedgerEvent]

    @property
    def opening_balance_minor(self) -> int:
        return _oriented(self.account.normal_balance, self.opening)

    @property
    def closing_balance_minor(self) -> int:
        return _oriented(self.account.normal_balance, self.closing)

    @property
    def net_change_minor(self) -> int:
        return self.closing_balance_minor - self.opening_balance_minor

    @property
    def reconciled(self) -> bool:
        """Opening + the sum of every shown event must equal closing, exactly.

        Not asserted - *computed* twice, independently, from two different
        queries (one aggregate, one row-by-row), and compared. If a future
        change to either path ever disagrees, this reports False instead of
        silently displaying a number that doesn't add up.
        """
        return self.opening_balance_minor + sum(e.effect_minor for e in self.events) == (
            self.closing_balance_minor
        )


def _oriented(normal: NormalBalance, derived: DerivedBalance) -> int:
    if normal is NormalBalance.DEBIT:
        return derived.debits_minor - derived.credits_minor
    return derived.credits_minor - derived.debits_minor


def balance_change(
    db: Session,
    account_id: uuid.UUID,
    *,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> BalanceChange:
    """Opening balance, every event in the window, and the resulting closing
    balance - the shared engine behind "Explain This Balance", the financial
    timeline, "why did my balance change", and state comparison. They are the
    same question asked with different defaults for `from_ts`/`to_ts`.
    """
    account = account_service.get_account(db, account_id)
    to_ts = to_ts or datetime.now(UTC)
    from_ts = from_ts or (to_ts - DEFAULT_WINDOW)
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    opening = balances.derive_for_account_as_of(db, account_id, from_ts, inclusive=False)
    closing = balances.derive_for_account_as_of(db, account_id, to_ts, inclusive=True)

    rows = (
        db.execute(
            select(LedgerEntry)
            .options(selectinload(LedgerEntry.transaction).selectinload(Transaction.reversed_by))
            .where(
                LedgerEntry.account_id == account_id,
                LedgerEntry.created_at >= from_ts,
                LedgerEntry.created_at <= to_ts,
            )
            .order_by(LedgerEntry.seq.asc())
        )
        .scalars()
        .all()
    )

    events: list[LedgerEvent] = []
    running = _oriented(account.normal_balance, opening)
    for entry in rows:
        effect = _signed_effect(entry.direction, entry.amount_minor, account.normal_balance)
        running += effect
        events.append(
            LedgerEvent(
                entry=entry,
                transaction=entry.transaction,
                effect_minor=effect,
                running_balance_minor=running,
            )
        )

    return BalanceChange(
        account=account,
        from_ts=from_ts,
        to_ts=to_ts,
        opening=opening,
        closing=closing,
        events=events,
    )


# --------------------------------------------------------------------------
# guided correction - reverse the original, post a fresh one, link them
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Correction:
    original: Transaction
    reversal: Transaction
    corrected: Transaction


def correct_transaction(
    db: Session,
    transaction_id: uuid.UUID,
    *,
    entries: list[EntryCommand],
    reason: str,
    description: str | None = None,
    reference: str | None = None,
    actor: str = "system",
    request_id: str | None = None,
) -> Correction:
    """Fix a mistake without touching history.

    This is not a new posting mechanism: it is `posting.reverse_transaction`
    immediately followed by `posting.post_transaction`, both already fully
    validated, idempotent-capable, and invariant-checked on their own. The
    only thing added here is the paper trail linking the three transactions -
    `metadata.corrects` / `metadata.corrected_by` - stored in the same `extra`
    JSONB column every transaction already has, not a new table.

    The two writes happen inside one database transaction (`auto_commit=False`
    on both, one `db.commit()` here): either the reversal and its replacement
    both land, or neither does. Without this, a failure between the two
    writes - the corrected entries failing validation, the process dying -
    could leave the original reversed with no replacement, a state nothing
    could safely auto-repair (the ledger is append-only; a committed reversal
    cannot be un-reversed). Atomicity means that state cannot occur: a failed
    correction leaves the original exactly as it was, still POSTED, safe to
    retry from scratch.
    """
    original = posting_service.load_transaction(db, transaction_id)

    try:
        reversal = posting_service.reverse_transaction(
            db,
            transaction_id,
            reason=f"Correction: {reason}",
            actor=actor,
            request_id=request_id,
            auto_commit=False,
        )

        cmd = PostCommand(
            description=description or original.description,
            currency=original.currency,
            entries=entries,
            reference=reference,
            external_reference=original.external_reference,
            metadata={
                "corrects": str(original.id),
                "corrects_reference": original.reference,
                "correction_reason": reason,
                "reversal_id": str(reversal.id),
            },
            actor=actor,
            request_id=request_id,
        )
        corrected = posting_service.post_transaction(db, cmd, auto_commit=False)

        db.commit()
    except Exception:
        db.rollback()
        raise

    return Correction(
        original=posting_service.load_transaction(db, original.id),
        reversal=posting_service.load_transaction(db, reversal.id),
        corrected=posting_service.load_transaction(db, corrected.id),
    )


# --------------------------------------------------------------------------
# deterministic duplicate detection - no AI, no fraud verdicts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateGroup:
    reasons: list[str]
    entries: list[LedgerEntry]


def duplicate_candidates(
    db: Session, *, window_minutes: int = 10, limit: int = 20
) -> list[DuplicateGroup]:
    """Pairs of postings that look like the same payment sent twice.

    Purely structural: same account, same signed amount, same direction,
    posted within `window_minutes` of each other, on different transactions.
    This is a prompt to look closer, never a verdict - it is deliberately
    reported as "requires investigation", and the operator decides.
    """
    # Core table aliases are NOT covered by the ORM's automatic tenant scoping
    # (see app.db.tenancy), so the organization filter is applied explicitly -
    # on both sides of the self-join.
    organization_id = require_organization(db)
    a = LedgerEntry.__table__.alias("a")
    b = LedgerEntry.__table__.alias("b")

    rows = db.execute(
        select(a.c.id, b.c.id)
        .select_from(a.join(b, a.c.account_id == b.c.account_id))
        .where(
            a.c.organization_id == organization_id,
            b.c.organization_id == organization_id,
            a.c.id < b.c.id,
            a.c.transaction_id != b.c.transaction_id,
            a.c.amount_minor == b.c.amount_minor,
            a.c.direction == b.c.direction,
            a.c.currency == b.c.currency,
            or_(
                and_(
                    a.c.created_at <= b.c.created_at,
                    b.c.created_at - a.c.created_at <= timedelta(minutes=window_minutes),
                ),
                and_(
                    b.c.created_at < a.c.created_at,
                    a.c.created_at - b.c.created_at <= timedelta(minutes=window_minutes),
                ),
            ),
        )
        .limit(limit)
    ).all()

    if not rows:
        return []

    entry_ids: set[uuid.UUID] = set()
    for first_id, second_id in rows:
        entry_ids.add(first_id)
        entry_ids.add(second_id)

    entries_by_id = {
        e.id: e
        for e in db.execute(
            select(LedgerEntry)
            .options(selectinload(LedgerEntry.transaction), selectinload(LedgerEntry.account))
            .where(LedgerEntry.id.in_(entry_ids))
        )
        .scalars()
        .all()
    }

    groups: list[DuplicateGroup] = []
    for first_id, second_id in rows:
        first, second = entries_by_id[first_id], entries_by_id[second_id]
        reasons = ["Same account", "Same amount", "Same direction", "Posted close together in time"]
        if (first.transaction.description or "").strip().lower() == (
            second.transaction.description or ""
        ).strip().lower():
            reasons.append("Identical description")
        groups.append(DuplicateGroup(reasons=reasons, entries=[first, second]))
    return groups


# --------------------------------------------------------------------------
# integrity checklist - names, in plain language, what LEDGR already checks
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrityCheck:
    name: str
    passed: bool
    detail: str


def integrity_checklist(db: Session) -> list[IntegrityCheck]:
    """Every check here is a real, already-implemented invariant, re-verified
    from the actual data - not a static list of claims. `trial_balance` and
    `unbalanced_transaction_ids` are the exact functions `/system/integrity`
    already uses; the two reversal/orphan checks are new queries, but they
    verify properties the database itself already enforces via the
    `reverses_transaction_id` unique index and the ledger_entries foreign key
    - this endpoint is what makes that enforcement visible, not what performs it.
    """
    checks: list[IntegrityCheck] = []

    unbalanced = balances.unbalanced_transaction_ids(db, limit=1)
    checks.append(
        IntegrityCheck(
            name="All postings balanced",
            passed=not unbalanced,
            detail="Every transaction's debits equal its credits."
            if not unbalanced
            else f"{len(unbalanced)}+ transaction(s) do not net to zero.",
        )
    )

    trial = balances.trial_balance(db)
    trial_ok = all(row.balanced for row in trial)
    checks.append(
        IntegrityCheck(
            name="Ledger balances globally, per currency",
            passed=trial_ok,
            detail="Total debits equal total credits in every currency in use."
            if trial_ok
            else "At least one currency's total debits and credits disagree.",
        )
    )

    orphan_count = db.execute(
        select(LedgerEntry.id)
        .outerjoin(Transaction, LedgerEntry.transaction_id == Transaction.id)
        .where(Transaction.id.is_(None))
        .limit(1)
    ).first()
    checks.append(
        IntegrityCheck(
            name="No orphan ledger entries",
            passed=orphan_count is None,
            detail="Every ledger entry belongs to an existing transaction."
            if orphan_count is None
            else "Found a ledger entry with no owning transaction.",
        )
    )

    Reverser = Transaction.__table__.alias("reverser")
    no_reverser_points_back = (
        select(Reverser.c.id)
        .where(
            Reverser.c.reverses_transaction_id == Transaction.id,
            Reverser.c.organization_id == Transaction.organization_id,
        )
        .exists()
    )
    bad_reversals = db.execute(
        select(Transaction.id)
        .where(
            or_(
                # A REVERSED transaction must have some other transaction
                # whose reverses_transaction_id points back at it.
                and_(
                    Transaction.status == TransactionStatus.REVERSED,
                    ~no_reverser_points_back,
                ),
                # A REVERSAL transaction must always name what it reverses.
                and_(
                    Transaction.kind == TransactionKind.REVERSAL,
                    Transaction.reverses_transaction_id.is_(None),
                ),
            )
        )
        .limit(1)
    ).first()
    checks.append(
        IntegrityCheck(
            name="Reversal relationships are valid",
            passed=bad_reversals is None,
            detail="Every REVERSED transaction has a matching reversal, and every "
            "reversal names the transaction it reverses."
            if bad_reversals is None
            else "Found a reversal relationship that does not point both ways.",
        )
    )

    derived = balances.derive_all(db)
    cached = {row.account_id: row for row in db.execute(select(AccountBalance)).scalars().all()}
    consistent = all(
        cached.get(account_id) is not None
        and cached[account_id].debits_minor == d.debits_minor
        and cached[account_id].credits_minor == d.credits_minor
        for account_id, d in derived.items()
    )
    checks.append(
        IntegrityCheck(
            name="Account balances match derived totals",
            passed=consistent,
            detail="Every account's cached balance equals the sum of its own ledger entries."
            if consistent
            else "At least one account's cached balance diverges from its entries.",
        )
    )

    return checks


# --------------------------------------------------------------------------
# transaction lineage - where an event came from, and what happened to it
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionLineage:
    root: Transaction
    accounts: list[Account]
    reverses: Transaction | None  # `root` is a reversal: the transaction it reverses
    reversed_by: Transaction | None  # the transaction that reverses `root`
    corrects: Transaction | None  # `root` is a correction: the original it replaces
    corrected_by: Transaction | None  # the transaction that later corrected `root`


def transaction_lineage(db: Session, transaction_id: uuid.UUID) -> TransactionLineage:
    """Where this financial event came from, what it touched, and what
    happened to it afterward.

    Every edge here is a real relationship already on the schema or already
    written by `correct_transaction`, never inferred: `reverses`/`reversed_by`
    are the `reverses_transaction_id` foreign key and its inverse; `corrects`/
    `corrected_by` are the `metadata.corrects` paper trail a correction writes
    into `extra`, read directly off (`corrects`) or found by searching for it
    (`corrected_by`) - the same JSONB column, no new table.
    """
    root = posting_service.load_transaction(db, transaction_id)

    reverses = (
        posting_service.load_transaction(db, root.reverses_transaction_id)
        if root.reverses_transaction_id is not None
        else None
    )
    reversed_by = (
        posting_service.load_transaction(db, root.reversed_by.id)
        if root.reversed_by is not None
        else None
    )

    corrects_id = (root.extra or {}).get("corrects")
    corrects = posting_service.load_transaction(db, uuid.UUID(corrects_id)) if corrects_id else None

    corrected_by_row = db.execute(
        select(Transaction.id).where(Transaction.extra["corrects"].astext == str(transaction_id))
    ).first()
    corrected_by = (
        posting_service.load_transaction(db, corrected_by_row[0]) if corrected_by_row else None
    )

    accounts = sorted({entry.account for entry in root.entries}, key=lambda a: a.code)

    return TransactionLineage(
        root=root,
        accounts=accounts,
        reverses=reverses,
        reversed_by=reversed_by,
        corrects=corrects,
        corrected_by=corrected_by,
    )


# --------------------------------------------------------------------------
# what-if correction - simulate the impact without writing anything
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulatedAccountImpact:
    account: Account
    current_balance_minor: int  # oriented, from the real ledger, right now
    simulated_balance_minor: int  # oriented, as it would be after CORRECT
    delta_minor: int


@dataclass(frozen=True)
class CorrectionSimulation:
    original: Transaction
    impacts: list[SimulatedAccountImpact]


def simulate_correction(
    db: Session,
    transaction_id: uuid.UUID,
    *,
    entries: list[EntryCommand],
) -> CorrectionSimulation:
    """What `correct_transaction` would produce, computed without writing
    anything - no transaction, no ledger entry, no balance-cache change.

    Reuses the exact same signed-effect math `balance_change` uses for real
    postings (`_signed_effect`/`_oriented`), applied in memory to two
    hypothetical entry sets: the mirror image of the original (what reversing
    it would remove) and the proposed replacement (what posting it would
    add). This function issues only `SELECT`s; discarding its result changes
    nothing on its own - only `POST /transactions/{id}/correct` ever writes.
    """
    original = posting_service.load_transaction(db, transaction_id)
    if original.kind is TransactionKind.REVERSAL:
        raise NotReversible(
            "a reversal cannot itself be corrected - it has nothing to reverse",
            details={"transaction_id": str(transaction_id)},
        )
    if original.status is TransactionStatus.REVERSED:
        raise AlreadyReversed(
            f"transaction {original.reference} has already been reversed",
            details={"transaction_id": str(transaction_id)},
        )

    account_ids = {e.account_id for e in original.entries} | {e.account_id for e in entries}
    accounts = {
        a.id: a
        for a in db.execute(select(Account).where(Account.id.in_(account_ids))).scalars().all()
    }
    missing = account_ids - set(accounts)
    if missing:
        raise NotFound(
            "one or more accounts in the proposed correction do not exist",
            details={"account_ids": sorted(str(a) for a in missing)},
        )

    delta: dict[uuid.UUID, int] = dict.fromkeys(account_ids, 0)
    for e in original.entries:
        opposite = (
            EntryDirection.CREDIT if e.direction is EntryDirection.DEBIT else EntryDirection.DEBIT
        )
        delta[e.account_id] += _signed_effect(
            opposite, e.amount_minor, accounts[e.account_id].normal_balance
        )
    for e in entries:
        delta[e.account_id] += _signed_effect(
            e.direction, e.amount_minor, accounts[e.account_id].normal_balance
        )

    impacts: list[SimulatedAccountImpact] = []
    for account_id in sorted(account_ids, key=str):
        account = accounts[account_id]
        current = _oriented(account.normal_balance, balances.derive_for_account(db, account_id))
        impacts.append(
            SimulatedAccountImpact(
                account=account,
                current_balance_minor=current,
                simulated_balance_minor=current + delta[account_id],
                delta_minor=delta[account_id],
            )
        )

    return CorrectionSimulation(original=original, impacts=impacts)


# --------------------------------------------------------------------------
# control exceptions - the real signals LEDGR can already detect, unified
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlException:
    kind: str  # "POTENTIAL_DUPLICATE" | "INTEGRITY"
    title: str
    detail: str
    account_id: uuid.UUID | None
    transaction_ids: list[uuid.UUID]
    detected_at: datetime


def list_control_exceptions(
    db: Session, *, duplicate_window_minutes: int = 10
) -> list[ControlException]:
    """Every currently-open control exception LEDGR's own checks support.

    No invented anomaly scoring and no magnitude thresholds beyond what
    `duplicate_candidates` and `integrity_checklist` already compute - this
    is exactly those two real, already-tested signals, reshaped into one list
    an operator can act on. Nothing here is ever labelled fraud.
    """
    exceptions: list[ControlException] = []
    now = datetime.now(UTC)

    for group in duplicate_candidates(db, window_minutes=duplicate_window_minutes, limit=50):
        exceptions.append(
            ControlException(
                kind="POTENTIAL_DUPLICATE",
                title="Potential duplicate posting",
                detail="; ".join(group.reasons),
                account_id=group.entries[0].account_id if group.entries else None,
                transaction_ids=sorted({e.transaction_id for e in group.entries}, key=str),
                detected_at=now,
            )
        )

    for check in integrity_checklist(db):
        if not check.passed:
            exceptions.append(
                ControlException(
                    kind="INTEGRITY",
                    title=check.name,
                    detail=check.detail,
                    account_id=None,
                    transaction_ids=[],
                    detected_at=now,
                )
            )

    return exceptions
