"""Explicit ORM -> API serializers.

Written by hand rather than via `from_attributes` because every monetary field
has to be rendered against its own currency exponent, and because the API shape
should be free to diverge from the storage shape (`extra` -> `metadata`).
"""

from datetime import UTC, datetime

from app.models.account import Account
from app.models.enums import EntryDirection, NormalBalance
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.schemas.account import (
    AccountBalanceOut,
    AccountOut,
    AccountSummary,
    BalanceCacheOut,
)
from app.schemas.common import Money
from app.schemas.transaction import (
    LedgerEntryOut,
    TransactionOut,
    TransactionSummary,
)


def oriented_balance(normal_balance: NormalBalance, debits: int, credits: int) -> int:
    """Balance in the account's own normal orientation.

    A liability with 500 credited and 100 debited reads as 400, not -400.
    """
    if normal_balance is NormalBalance.DEBIT:
        return debits - credits
    return credits - debits


def account_summary(account: Account) -> AccountSummary:
    return AccountSummary(
        id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        normal_balance=account.normal_balance,
        currency=account.currency,
        status=account.status,
    )


def account_out(account: Account) -> AccountOut:
    cache = account.balance_cache
    debits = cache.debits_minor if cache else 0
    credits = cache.credits_minor if cache else 0
    balance = oriented_balance(account.normal_balance, debits, credits)
    return AccountOut(
        id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        normal_balance=account.normal_balance,
        currency=account.currency,
        status=account.status,
        description=account.description,
        allows_negative_balance=account.allows_negative_balance,
        metadata=account.extra or {},
        created_at=account.created_at,
        updated_at=account.updated_at,
        balance=Money.of(balance, account.currency),
        debits=Money.of(debits, account.currency),
        credits=Money.of(credits, account.currency),
        entry_count=cache.entry_count if cache else 0,
    )


def account_balance_out(
    account: Account,
    *,
    derived_debits: int,
    derived_credits: int,
    derived_entry_count: int,
    derived_last_seq: int | None,
) -> AccountBalanceOut:
    cache = account.balance_cache
    cached_debits = cache.debits_minor if cache else 0
    cached_credits = cache.credits_minor if cache else 0
    currency = account.currency
    balance = oriented_balance(account.normal_balance, derived_debits, derived_credits)

    return AccountBalanceOut(
        account_id=account.id,
        account_code=account.code,
        currency=currency,
        normal_balance=account.normal_balance,
        balance=Money.of(balance, currency),
        signed_balance=Money.of(derived_debits - derived_credits, currency),
        debits=Money.of(derived_debits, currency),
        credits=Money.of(derived_credits, currency),
        entry_count=derived_entry_count,
        last_entry_seq=derived_last_seq,
        derived_at=datetime.now(UTC),
        cache=BalanceCacheOut(
            balance=Money.of(
                oriented_balance(account.normal_balance, cached_debits, cached_credits), currency
            ),
            debits=Money.of(cached_debits, currency),
            credits=Money.of(cached_credits, currency),
            entry_count=cache.entry_count if cache else 0,
            last_entry_seq=cache.last_entry_seq if cache else None,
            updated_at=cache.updated_at if cache else None,
        ),
        cache_consistent=(cached_debits == derived_debits and cached_credits == derived_credits),
    )


def transaction_summary(transaction: Transaction) -> TransactionSummary:
    return TransactionSummary(
        id=transaction.id,
        reference=transaction.reference,
        description=transaction.description,
        status=transaction.status,
        kind=transaction.kind,
        currency=transaction.currency,
        posted_at=transaction.posted_at,
        effective_at=transaction.effective_at,
    )


def ledger_entry_out(
    entry: LedgerEntry,
    *,
    with_account: bool = True,
    with_transaction: bool = False,
) -> LedgerEntryOut:
    return LedgerEntryOut(
        id=entry.id,
        seq=entry.seq,
        transaction_id=entry.transaction_id,
        account_id=entry.account_id,
        direction=entry.direction,
        amount=Money.of(entry.amount_minor, entry.currency),
        entry_index=entry.entry_index,
        memo=entry.memo,
        created_at=entry.created_at,
        account=account_summary(entry.account) if with_account else None,
        transaction=transaction_summary(entry.transaction) if with_transaction else None,
    )


def transaction_out(transaction: Transaction, *, with_entries: bool = True) -> TransactionOut:
    entries = list(transaction.entries) if with_entries else []

    # Trust nothing: recompute the balance assertion from the stored entries so
    # the detail view proves the invariant instead of echoing a cached total.
    debits = sum(e.amount_minor for e in entries if e.direction is EntryDirection.DEBIT)
    credits = sum(e.amount_minor for e in entries if e.direction is EntryDirection.CREDIT)
    balanced = (
        debits == credits == transaction.total_debits_minor
        if with_entries
        else transaction.total_debits_minor == transaction.total_credits_minor
    )

    reversed_by = getattr(transaction, "reversed_by", None)

    return TransactionOut(
        id=transaction.id,
        seq=transaction.seq,
        reference=transaction.reference,
        description=transaction.description,
        currency=transaction.currency,
        status=transaction.status,
        kind=transaction.kind,
        total_debits=Money.of(transaction.total_debits_minor, transaction.currency),
        total_credits=Money.of(transaction.total_credits_minor, transaction.currency),
        balanced=balanced,
        entry_count=transaction.entry_count,
        effective_at=transaction.effective_at,
        posted_at=transaction.posted_at,
        reversed_at=transaction.reversed_at,
        reverses_transaction_id=transaction.reverses_transaction_id,
        reversed_by_transaction_id=reversed_by.id if reversed_by is not None else None,
        actor=transaction.actor,
        request_id=transaction.request_id,
        idempotency_key=transaction.idempotency_key,
        external_reference=transaction.external_reference,
        metadata=transaction.extra or {},
        entries=[
            ledger_entry_out(entry, with_account=True, with_transaction=False) for entry in entries
        ],
    )
