"""Invariants I1-I8: what the posting engine must never allow."""

import uuid

import pytest
import sqlalchemy as sa

from app.core.errors import (
    AccountNotPostable,
    CurrencyMismatch,
    InvalidAmount,
    NotFound,
    UnbalancedTransaction,
    UnsupportedCurrency,
    ValidationFailed,
)
from app.db.session import SessionLocal
from app.models.enums import AccountStatus, AccountType, EntryDirection, TransactionStatus
from app.services import posting


def entry(account, direction, amount, memo=None):
    return posting.EntryCommand(
        account_id=account.id, direction=direction, amount_minor=amount, memo=memo
    )


def command(entries, **kwargs):
    return posting.PostCommand(
        description=kwargs.pop("description", "test posting"),
        currency=kwargs.pop("currency", "USD"),
        entries=entries,
        **kwargs,
    )


def test_balanced_transaction_posts(db, cash_and_revenue):
    cash, revenue = cash_and_revenue

    txn = posting.post_transaction(
        db,
        command(
            [
                entry(cash, EntryDirection.DEBIT, 125_00),
                entry(revenue, EntryDirection.CREDIT, 125_00),
            ],
            description="Consulting fee received",
        ),
    )

    assert txn.status is TransactionStatus.POSTED
    assert txn.total_debits_minor == txn.total_credits_minor == 125_00
    assert txn.entry_count == 2
    assert txn.reference.startswith("TXN-")
    assert [e.entry_index for e in txn.entries] == [0, 1]
    # Global sequence is assigned by the database, not the application.
    assert all(e.seq > 0 for e in txn.entries)


def test_multi_leg_transaction_posts(db, make_account):
    """A settlement with three legs still has to net to zero."""
    bank = make_account("BANK.USD", AccountType.ASSET)
    clearing = make_account("CLEAR.USD", AccountType.ASSET)
    fee = make_account("EXP.FEE.USD", AccountType.EXPENSE)

    txn = posting.post_transaction(
        db,
        command(
            [
                entry(bank, EntryDirection.DEBIT, 996_00),
                entry(fee, EntryDirection.DEBIT, 4_00),
                entry(clearing, EntryDirection.CREDIT, 1000_00),
            ],
            description="Processor settlement",
        ),
    )
    assert txn.entry_count == 3
    assert txn.total_debits_minor == 1000_00


def test_unbalanced_transaction_is_rejected(db, cash_and_revenue):
    cash, revenue = cash_and_revenue

    with pytest.raises(UnbalancedTransaction) as excinfo:
        posting.post_transaction(
            db,
            command(
                [
                    entry(cash, EntryDirection.DEBIT, 100_00),
                    entry(revenue, EntryDirection.CREDIT, 99_00),
                ]
            ),
        )
    assert excinfo.value.details["difference_minor"] == 100
    assert _transaction_count() == 0
    assert _entry_count() == 0


def test_single_sided_transaction_is_rejected(db, make_account):
    a = make_account("A.USD")
    b = make_account("B.USD")

    with pytest.raises(UnbalancedTransaction):
        posting.post_transaction(
            db,
            command(
                [
                    entry(a, EntryDirection.DEBIT, 50_00),
                    entry(b, EntryDirection.DEBIT, 50_00),
                ]
            ),
        )


def test_single_entry_transaction_is_rejected(db, cash_and_revenue):
    cash, _ = cash_and_revenue
    with pytest.raises(ValidationFailed):
        posting.post_transaction(db, command([entry(cash, EntryDirection.DEBIT, 10_00)]))


@pytest.mark.parametrize("amount", [0, -1, -5000])
def test_non_positive_amounts_are_rejected(db, cash_and_revenue, amount):
    cash, revenue = cash_and_revenue
    with pytest.raises(InvalidAmount):
        posting.post_transaction(
            db,
            command(
                [
                    entry(cash, EntryDirection.DEBIT, amount),
                    entry(revenue, EntryDirection.CREDIT, abs(amount) or 1),
                ]
            ),
        )


def test_currency_must_match_every_account(db, make_account):
    usd = make_account("CASH.USD", AccountType.ASSET, currency="USD")
    eur = make_account("CASH.EUR", AccountType.ASSET, currency="EUR")

    with pytest.raises(CurrencyMismatch) as excinfo:
        posting.post_transaction(
            db,
            command(
                [
                    entry(usd, EntryDirection.DEBIT, 10_00),
                    entry(eur, EntryDirection.CREDIT, 10_00),
                ],
                currency="USD",
            ),
        )
    assert excinfo.value.details["account_currency"] == "EUR"
    assert _transaction_count() == 0


def test_unsupported_currency_is_rejected(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    with pytest.raises(UnsupportedCurrency):
        posting.post_transaction(
            db,
            command(
                [
                    entry(cash, EntryDirection.DEBIT, 1_00),
                    entry(revenue, EntryDirection.CREDIT, 1_00),
                ],
                currency="XYZ",
            ),
        )


@pytest.mark.parametrize("status", [AccountStatus.FROZEN, AccountStatus.CLOSED])
def test_inactive_accounts_cannot_receive_entries(db, cash_and_revenue, status):
    cash, revenue = cash_and_revenue
    cash.status = status
    db.commit()

    with pytest.raises(AccountNotPostable):
        posting.post_transaction(
            db,
            command(
                [
                    entry(cash, EntryDirection.DEBIT, 10_00),
                    entry(revenue, EntryDirection.CREDIT, 10_00),
                ]
            ),
        )
    assert _entry_count() == 0


def test_unknown_account_is_rejected(db, cash_and_revenue):
    cash, _ = cash_and_revenue
    ghost = posting.EntryCommand(
        account_id=uuid.uuid4(), direction=EntryDirection.CREDIT, amount_minor=10_00
    )

    with pytest.raises(NotFound):
        posting.post_transaction(db, command([entry(cash, EntryDirection.DEBIT, 10_00), ghost]))
    assert _transaction_count() == 0


def test_duplicate_reference_is_rejected(db, cash_and_revenue):
    from app.core.errors import DuplicateReference

    cash, revenue = cash_and_revenue
    legs = [
        entry(cash, EntryDirection.DEBIT, 5_00),
        entry(revenue, EntryDirection.CREDIT, 5_00),
    ]
    posting.post_transaction(db, command(legs, reference="TXN-FIXED-1"))

    with pytest.raises(DuplicateReference):
        posting.post_transaction(db, command(legs, reference="TXN-FIXED-1"))
    assert _transaction_count() == 1


def test_failed_posting_leaves_no_partial_entries(db, make_account):
    """I7: a rejection that happens *after* rows are written must roll back.

    Insufficient funds is detected after the entries have been INSERTed and
    flushed, so this exercises the real rollback path rather than a pre-flight
    validation short-circuit.
    """
    from app.core.errors import InsufficientFunds

    vault = make_account("VAULT.USD", AccountType.ASSET, allows_negative_balance=False)
    revenue = make_account("REV2.USD", AccountType.REVENUE)

    with pytest.raises(InsufficientFunds):
        posting.post_transaction(
            db,
            command(
                [
                    entry(vault, EntryDirection.CREDIT, 900_00),
                    entry(revenue, EntryDirection.DEBIT, 900_00),
                ]
            ),
        )
    db.rollback()

    # Verified from a *different* connection: nothing was committed anywhere.
    assert _transaction_count() == 0
    assert _entry_count() == 0
    assert _balance_row(vault.id) == (0, 0)


# --- helpers reading through an independent connection --------------------


def _scalar(sql: str, **params) -> int:
    with SessionLocal() as session:
        return int(session.execute(sa.text(sql), params).scalar_one())


def _transaction_count() -> int:
    return _scalar("SELECT count(*) FROM transactions")


def _entry_count() -> int:
    return _scalar("SELECT count(*) FROM ledger_entries")


def _balance_row(account_id) -> tuple[int, int]:
    with SessionLocal() as session:
        row = session.execute(
            sa.text(
                "SELECT debits_minor, credits_minor FROM account_balances WHERE account_id = :id"
            ),
            {"id": account_id},
        ).one()
        return int(row[0]), int(row[1])
