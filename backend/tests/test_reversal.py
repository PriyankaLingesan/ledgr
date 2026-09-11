"""Invariants I11, I12: corrections are compensating transactions."""

import pytest

from app.core.errors import AlreadyReversed, NotReversible
from app.models.enums import (
    AccountType,
    EntryDirection,
    TransactionKind,
    TransactionStatus,
)
from app.services import balances, posting


@pytest.fixture
def posted(db, make_account):
    cash = make_account("REV.CASH.USD", AccountType.ASSET)
    wallet = make_account("REV.WALLET.USD", AccountType.LIABILITY)
    txn = posting.post_transaction(
        db,
        posting.PostCommand(
            description="Customer deposit",
            currency="USD",
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 500_00, "funds in"),
                posting.EntryCommand(wallet.id, EntryDirection.CREDIT, 500_00, "owed to customer"),
            ],
        ),
    )
    return txn, cash, wallet


def test_reversal_mirrors_every_entry(db, posted):
    original, cash, wallet = posted

    reversal = posting.reverse_transaction(
        db, original.id, reason="Deposit recalled", actor="ops.jain"
    )

    assert reversal.kind is TransactionKind.REVERSAL
    assert reversal.reverses_transaction_id == original.id
    assert reversal.reference.startswith("REV-")
    assert reversal.total_debits_minor == original.total_debits_minor

    directions = {e.account_id: e.direction for e in reversal.entries}
    assert directions[cash.id] is EntryDirection.CREDIT
    assert directions[wallet.id] is EntryDirection.DEBIT


def test_reversal_returns_balances_to_zero(db, posted):
    original, cash, wallet = posted
    posting.reverse_transaction(db, original.id, actor="pytest")

    for account in (cash, wallet):
        derived = balances.derive_for_account(db, account.id)
        assert derived.debits_minor == derived.credits_minor == 500_00
        assert derived.signed_minor == 0
        assert derived.entry_count == 2  # original + reversal, both preserved


def test_original_is_marked_reversed_but_keeps_its_entries(db, posted):
    original, *_ = posted
    posting.reverse_transaction(db, original.id, actor="pytest")

    refreshed = posting.load_transaction(db, original.id)
    assert refreshed.status is TransactionStatus.REVERSED
    assert refreshed.reversed_at is not None
    assert len(refreshed.entries) == 2
    assert refreshed.reversed_by is not None


def test_a_transaction_cannot_be_reversed_twice(db, posted):
    original, *_ = posted
    posting.reverse_transaction(db, original.id, actor="pytest")

    with pytest.raises(AlreadyReversed):
        posting.reverse_transaction(db, original.id, actor="pytest")
    db.rollback()

    from app.services import queries

    transactions, total = queries.list_transactions(db)
    assert total == 2  # the original and exactly one reversal


def test_a_reversal_cannot_itself_be_reversed(db, posted):
    original, *_ = posted
    reversal = posting.reverse_transaction(db, original.id, actor="pytest")

    with pytest.raises(NotReversible):
        posting.reverse_transaction(db, reversal.id, actor="pytest")


def test_reversal_bypasses_balance_floors(db, make_account):
    """A correction must always be possible, even below a balance floor.

    Refusing here would trap the ledger in a state it is not permitted to
    leave, which is worse than a temporarily negative constrained account.
    """
    vault = make_account("RV.VAULT.USD", AccountType.ASSET, allows_negative_balance=False)
    revenue = make_account("RV.REV.USD", AccountType.REVENUE)

    funding = posting.post_transaction(
        db,
        posting.PostCommand(
            description="Initial funding",
            currency="USD",
            entries=[
                posting.EntryCommand(vault.id, EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
            ],
        ),
    )
    posting.post_transaction(
        db,
        posting.PostCommand(
            description="Spend it all",
            currency="USD",
            entries=[
                posting.EntryCommand(revenue.id, EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(vault.id, EntryDirection.CREDIT, 100_00),
            ],
        ),
    )

    # Reversing the funding drives the vault to -100.00 and must still succeed.
    posting.reverse_transaction(db, funding.id, reason="Funding never arrived", actor="pytest")

    derived = balances.derive_for_account(db, vault.id)
    assert derived.debits_minor - derived.credits_minor == -100_00


def test_reversal_is_audited(db, posted):
    from app.services import queries

    original, *_ = posted
    posting.reverse_transaction(db, original.id, reason="bank recall", actor="ops.jain")

    events, _ = queries.list_audit_events(db, event_type="transaction.reversed")
    assert len(events) == 1
    assert events[0].actor == "ops.jain"
    assert events[0].payload["reason"] == "bank recall"
    assert events[0].resource_id == str(original.id)
