"""The posting engine.

Everything that makes LEDGR a ledger rather than a table of rows lives here:

* a transaction is written in exactly one database transaction (I7),
* debits must equal credits before anything is inserted (I1),
* every account touched is locked in a deterministic order so concurrent
  postings against the same account cannot interleave (I9, balance limits),
* corrections are compensating transactions, never edits (I12).

Concurrency model
-----------------
READ COMMITTED plus explicit row locks. Accounts are locked one at a time in
ascending UUID order: a global lock ordering is what makes deadlock impossible
between two transactions that touch overlapping account sets. Taking the locks
individually (rather than one `IN (...) FOR UPDATE`) means the order is ours,
not the query planner's.

The alternative - SERIALIZABLE isolation - would also be correct, but it pushes
retry handling onto every caller and serialises far more than the accounts
actually involved. Explicit per-account locks keep contention proportional to
real contention: two transactions touching disjoint accounts never block.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core import money
from app.core.config import settings
from app.core.errors import (
    AccountNotPostable,
    AlreadyReversed,
    CurrencyMismatch,
    DuplicateReference,
    InsufficientFunds,
    InvalidAmount,
    NotFound,
    NotReversible,
    UnbalancedTransaction,
    UnsupportedCurrency,
    ValidationFailed,
)
from app.models.account import Account, AccountBalance
from app.models.enums import (
    AccountStatus,
    EntryDirection,
    NormalBalance,
    TransactionKind,
    TransactionStatus,
)
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.services import audit


@dataclass
class EntryCommand:
    account_id: uuid.UUID
    direction: EntryDirection
    amount_minor: int
    memo: str | None = None


@dataclass
class PostCommand:
    description: str
    currency: str
    entries: list[EntryCommand]
    reference: str | None = None
    effective_at: datetime | None = None
    external_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    request_id: str | None = None
    idempotency_key: str | None = None


def generate_reference(prefix: str = "TXN") -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:10].upper()}"


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def _validate_shape(cmd: PostCommand) -> tuple[int, int]:
    """Pure checks that need no database state. Returns (debits, credits)."""
    currency = cmd.currency.upper()
    if not money.is_supported(currency):
        raise UnsupportedCurrency(
            f"currency {currency} is not supported",
            details={"supported": list(money.SUPPORTED_CURRENCIES)},
        )

    if len(cmd.entries) < 2:
        raise ValidationFailed("a double-entry transaction needs at least two entries")
    if len(cmd.entries) > settings.max_entries_per_transaction:
        raise ValidationFailed(
            f"a transaction may not exceed {settings.max_entries_per_transaction} entries"
        )

    for index, entry in enumerate(cmd.entries):
        if not isinstance(entry.amount_minor, int) or isinstance(entry.amount_minor, bool):
            raise InvalidAmount(f"entry {index}: amount must be an integer of minor units")
        if entry.amount_minor <= 0:
            raise InvalidAmount(
                f"entry {index}: amount must be positive - direction carries the sign",
                details={"entry_index": index, "amount_minor": entry.amount_minor},
            )

    debits = sum(e.amount_minor for e in cmd.entries if e.direction is EntryDirection.DEBIT)
    credits = sum(e.amount_minor for e in cmd.entries if e.direction is EntryDirection.CREDIT)

    if debits == 0 or credits == 0:
        raise UnbalancedTransaction(
            "a transaction needs at least one debit and one credit entry",
            details={"total_debits_minor": debits, "total_credits_minor": credits},
        )
    if debits != credits:
        raise UnbalancedTransaction(
            "total debits must equal total credits",
            details={
                "total_debits_minor": debits,
                "total_credits_minor": credits,
                "difference_minor": debits - credits,
                "currency": currency,
            },
        )
    return debits, credits


def _lock_accounts(db: Session, account_ids: set[uuid.UUID]) -> dict[uuid.UUID, Account]:
    """Acquire row locks in ascending id order - a global lock ordering."""
    locked: dict[uuid.UUID, Account] = {}
    for account_id in sorted(account_ids, key=str):
        account = db.execute(
            select(Account).where(Account.id == account_id).with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise NotFound(
                f"account {account_id} does not exist", details={"account_id": str(account_id)}
            )
        locked[account_id] = account
    return locked


def _load_balance_rows(db: Session, account_ids: set[uuid.UUID]) -> dict[uuid.UUID, AccountBalance]:
    """Read the cache rows *after* locking, so we never act on a stale copy."""
    rows = (
        db.execute(
            select(AccountBalance)
            .where(AccountBalance.account_id.in_(account_ids))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    balances = {row.account_id: row for row in rows}
    for account_id in account_ids:
        if account_id not in balances:
            # Only reachable for accounts created outside the service layer.
            row = AccountBalance(account_id=account_id)
            db.add(row)
            balances[account_id] = row
    return balances


# --------------------------------------------------------------------------
# core write path
# --------------------------------------------------------------------------


def _write_transaction(
    db: Session,
    cmd: PostCommand,
    *,
    kind: TransactionKind = TransactionKind.STANDARD,
    reverses_transaction_id: uuid.UUID | None = None,
    enforce_balance_limits: bool = True,
) -> Transaction:
    """Insert a transaction and its entries. Does not commit.

    The caller owns the unit of work so that reversal can flip the original's
    status inside the same atomic write as the compensating entries.
    """
    currency = cmd.currency.upper()
    debits, credits = _validate_shape(cmd)

    account_ids = {e.account_id for e in cmd.entries}
    accounts = _lock_accounts(db, account_ids)

    for account in accounts.values():
        if account.currency != currency:
            raise CurrencyMismatch(
                f"account {account.code} is denominated in {account.currency}, "
                f"transaction is in {currency}",
                details={
                    "account_id": str(account.id),
                    "account_currency": account.currency,
                    "transaction_currency": currency,
                },
            )
        if account.status is not AccountStatus.ACTIVE:
            raise AccountNotPostable(
                f"account {account.code} is {account.status.value} and cannot receive entries",
                details={"account_id": str(account.id), "status": account.status.value},
            )

    transaction = Transaction(
        reference=cmd.reference
        or generate_reference("REV" if kind is TransactionKind.REVERSAL else "TXN"),
        description=cmd.description,
        currency=currency,
        status=TransactionStatus.POSTED,
        kind=kind,
        total_debits_minor=debits,
        total_credits_minor=credits,
        entry_count=len(cmd.entries),
        effective_at=cmd.effective_at or datetime.now(UTC),
        reverses_transaction_id=reverses_transaction_id,
        actor=cmd.actor,
        request_id=cmd.request_id,
        idempotency_key=cmd.idempotency_key,
        external_reference=cmd.external_reference,
        extra=cmd.metadata or {},
    )
    db.add(transaction)
    db.flush()  # assign transaction.id / seq

    entries: list[LedgerEntry] = []
    for index, item in enumerate(cmd.entries):
        entry = LedgerEntry(
            transaction_id=transaction.id,
            account_id=item.account_id,
            direction=item.direction,
            amount_minor=item.amount_minor,
            currency=currency,
            entry_index=index,
            memo=item.memo,
        )
        db.add(entry)
        entries.append(entry)
    db.flush()  # assign entry.seq, and let CHECK constraints fire now

    _apply_to_balance_cache(
        db,
        accounts=accounts,
        entries=entries,
        enforce_balance_limits=enforce_balance_limits,
    )
    return transaction


def _apply_to_balance_cache(
    db: Session,
    *,
    accounts: dict[uuid.UUID, Account],
    entries: list[LedgerEntry],
    enforce_balance_limits: bool,
) -> None:
    """Fold the new entries into the cached balances, under the account locks.

    This is also where balance limits are enforced: the check and the write
    happen while the account row is locked, so two concurrent withdrawals
    cannot both observe sufficient funds.
    """
    balances = _load_balance_rows(db, set(accounts))

    for entry in entries:
        row = balances[entry.account_id]
        if entry.direction is EntryDirection.DEBIT:
            row.debits_minor += entry.amount_minor
        else:
            row.credits_minor += entry.amount_minor
        row.entry_count += 1
        row.last_entry_seq = entry.seq

    if not enforce_balance_limits:
        return

    for account_id, account in accounts.items():
        if account.allows_negative_balance:
            continue
        row = balances[account_id]
        if account.normal_balance is NormalBalance.DEBIT:
            resulting = row.debits_minor - row.credits_minor
        else:
            resulting = row.credits_minor - row.debits_minor
        if resulting < 0:
            raise InsufficientFunds(
                f"account {account.code} would fall to "
                f"{money.format_amount(resulting, account.currency)} {account.currency}",
                details={
                    "account_id": str(account_id),
                    "account_code": account.code,
                    "resulting_balance_minor": resulting,
                    "currency": account.currency,
                },
            )


def post_transaction(db: Session, cmd: PostCommand) -> Transaction:
    """Post a balanced transaction atomically."""
    transaction = _write_transaction(db, cmd)

    audit.record(
        db,
        event_type="transaction.posted",
        resource_type="transaction",
        resource_id=str(transaction.id),
        actor=cmd.actor,
        request_id=cmd.request_id,
        payload={
            "reference": transaction.reference,
            "currency": transaction.currency,
            "total_minor": transaction.total_debits_minor,
            "entry_count": transaction.entry_count,
            "idempotency_key": cmd.idempotency_key,
            "accounts": sorted({str(e.account_id) for e in cmd.entries}),
        },
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _translate_integrity_error(exc, reference=cmd.reference) from exc

    return load_transaction(db, transaction.id)


def reverse_transaction(
    db: Session,
    transaction_id: uuid.UUID,
    *,
    reason: str | None = None,
    reference: str | None = None,
    effective_at: datetime | None = None,
    actor: str = "system",
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> Transaction:
    """Reverse a posted transaction with a mirrored compensating entry set.

    The original is never touched beyond a POSTED -> REVERSED status flip; its
    entries stay exactly as they were posted. Auditors need to see both the
    mistake and the correction.
    """
    original = db.execute(
        select(Transaction).where(Transaction.id == transaction_id).with_for_update()
    ).scalar_one_or_none()
    if original is None:
        raise NotFound(f"transaction {transaction_id} not found")

    if original.kind is TransactionKind.REVERSAL:
        raise NotReversible(
            "a reversal cannot itself be reversed - post a new correcting transaction instead",
            details={"transaction_id": str(transaction_id)},
        )
    if original.status is TransactionStatus.REVERSED:
        raise AlreadyReversed(
            f"transaction {original.reference} has already been reversed",
            details={"transaction_id": str(transaction_id)},
        )

    entries = (
        db.execute(
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == transaction_id)
            .order_by(LedgerEntry.entry_index)
        )
        .scalars()
        .all()
    )
    if not entries:  # pragma: no cover - impossible for a posted transaction
        raise ValidationFailed("transaction has no entries to reverse")

    mirrored = [
        EntryCommand(
            account_id=entry.account_id,
            direction=(
                EntryDirection.CREDIT
                if entry.direction is EntryDirection.DEBIT
                else EntryDirection.DEBIT
            ),
            amount_minor=entry.amount_minor,
            memo=f"Reversal of entry {entry.entry_index} on {original.reference}",
        )
        for entry in entries
    ]

    cmd = PostCommand(
        description=reason or f"Reversal of {original.reference}: {original.description}",
        currency=original.currency,
        entries=mirrored,
        reference=reference,
        effective_at=effective_at or datetime.now(UTC),
        external_reference=original.external_reference,
        metadata={"reverses": str(original.id), "reason": reason}
        if reason
        else {"reverses": str(original.id)},
        actor=actor,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )

    reversal = _write_transaction(
        db,
        cmd,
        kind=TransactionKind.REVERSAL,
        reverses_transaction_id=original.id,
        # A correction must always be possible. Refusing to reverse because the
        # result dips below a balance floor would trap the ledger in a state it
        # is not allowed to leave.
        enforce_balance_limits=False,
    )

    original.status = TransactionStatus.REVERSED
    original.reversed_at = datetime.now(UTC)

    audit.record(
        db,
        event_type="transaction.reversed",
        resource_type="transaction",
        resource_id=str(original.id),
        actor=actor,
        request_id=request_id,
        payload={
            "original_reference": original.reference,
            "reversal_reference": reversal.reference,
            "reversal_id": str(reversal.id),
            "reason": reason,
        },
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The unique index on reverses_transaction_id is the real guard against
        # two concurrent reversals; the row lock above merely makes it rare.
        raise _translate_integrity_error(
            exc, reference=reference, transaction_id=transaction_id
        ) from exc

    return load_transaction(db, reversal.id)


def _translate_integrity_error(
    exc: IntegrityError,
    *,
    reference: str | None = None,
    transaction_id: uuid.UUID | None = None,
) -> Exception:
    text = str(getattr(exc, "orig", exc))
    if "uq_transactions_reverses_transaction_id" in text or "reverses_transaction_id" in text:
        return AlreadyReversed(
            "transaction has already been reversed",
            details={"transaction_id": str(transaction_id) if transaction_id else None},
        )
    if "uq_transactions_reference" in text or "reference" in text:
        return DuplicateReference(
            f"transaction reference {reference!r} is already in use",
            details={"reference": reference},
        )
    if "transaction_is_balanced" in text:  # pragma: no cover - defence in depth
        return UnbalancedTransaction("database rejected an unbalanced transaction")
    return exc


def load_transaction(db: Session, transaction_id: uuid.UUID) -> Transaction:
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
