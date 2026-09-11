"""Invariants I9, I13: derived balances, cache honesty, trial balance."""

import pytest

from app.core.errors import InsufficientFunds
from app.models.enums import AccountType, EntryDirection, NormalBalance
from app.schemas.serialize import oriented_balance
from app.services import balances, posting


def post(db, debit_account, credit_account, amount, currency="USD"):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description="balance test",
            currency=currency,
            entries=[
                posting.EntryCommand(debit_account.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(credit_account.id, EntryDirection.CREDIT, amount),
            ],
        ),
    )


def test_balance_is_derived_from_entries(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 300_00)
    post(db, cash, revenue, 150_00)
    post(db, revenue, cash, 50_00)  # a refund

    derived = balances.derive_for_account(db, cash.id)
    assert derived.debits_minor == 450_00
    assert derived.credits_minor == 50_00
    assert derived.entry_count == 3
    # ASSET: debit-normal, so the balance reads positive at 400.00
    assert (
        oriented_balance(NormalBalance.DEBIT, derived.debits_minor, derived.credits_minor) == 400_00
    )


def test_credit_normal_accounts_read_positive(db, make_account):
    """A liability with money owed shows a positive balance, not a negative."""
    cash = make_account("CASH2.USD", AccountType.ASSET)
    wallet = make_account("WALLET.USD", AccountType.LIABILITY)

    post(db, cash, wallet, 900_00)
    derived = balances.derive_for_account(db, wallet.id)

    assert derived.credits_minor == 900_00
    assert (
        oriented_balance(NormalBalance.CREDIT, derived.debits_minor, derived.credits_minor)
        == 900_00
    )
    # Debit-positive orientation is the one that sums to zero ledger-wide.
    assert derived.signed_minor == -900_00


def test_cache_matches_derived_balance(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    for amount in (10_00, 25_50, 999_99):
        post(db, cash, revenue, amount)

    db.refresh(cash)
    derived = balances.derive_for_account(db, cash.id)
    cache = cash.balance_cache

    assert (cache.debits_minor, cache.credits_minor) == (
        derived.debits_minor,
        derived.credits_minor,
    )
    assert cache.entry_count == derived.entry_count
    assert cache.last_entry_seq == derived.last_entry_seq


def test_trial_balance_nets_to_zero(db, make_account):
    usd_cash = make_account("TB.CASH.USD", AccountType.ASSET)
    usd_rev = make_account("TB.REV.USD", AccountType.REVENUE)
    eur_cash = make_account("TB.CASH.EUR", AccountType.ASSET, currency="EUR")
    eur_rev = make_account("TB.REV.EUR", AccountType.REVENUE, currency="EUR")

    post(db, usd_cash, usd_rev, 1234_56)
    post(db, usd_rev, usd_cash, 34_56)
    post(db, eur_cash, eur_rev, 9_99, currency="EUR")

    rows = {row.currency: row for row in balances.trial_balance(db)}
    assert set(rows) == {"USD", "EUR"}
    for row in rows.values():
        assert row.balanced
        assert row.difference_minor == 0
    assert rows["USD"].debits_minor == 1269_12
    assert balances.unbalanced_transaction_ids(db) == []


def test_balance_floor_is_enforced(db, make_account):
    """An account that forbids negative balances refuses the entry that breaches it."""
    vault = make_account("VAULT2.USD", AccountType.ASSET, allows_negative_balance=False)
    revenue = make_account("REV3.USD", AccountType.REVENUE)

    post(db, vault, revenue, 100_00)

    with pytest.raises(InsufficientFunds) as excinfo:
        post(db, revenue, vault, 100_01)
    db.rollback()

    assert excinfo.value.details["resulting_balance_minor"] == -1
    derived = balances.derive_for_account(db, vault.id)
    assert derived.debits_minor - derived.credits_minor == 100_00


def test_exact_drain_to_zero_is_allowed(db, make_account):
    vault = make_account("VAULT3.USD", AccountType.ASSET, allows_negative_balance=False)
    revenue = make_account("REV4.USD", AccountType.REVENUE)

    post(db, vault, revenue, 100_00)
    post(db, revenue, vault, 100_00)

    derived = balances.derive_for_account(db, vault.id)
    assert derived.debits_minor - derived.credits_minor == 0


def test_unconstrained_accounts_may_go_negative(db, make_account):
    """Most accounts legitimately carry either sign; only opted-in ones don't."""
    clearing = make_account("CLEARING.USD", AccountType.ASSET, allows_negative_balance=True)
    revenue = make_account("REV5.USD", AccountType.REVENUE)

    post(db, revenue, clearing, 75_00)
    derived = balances.derive_for_account(db, clearing.id)
    assert derived.debits_minor - derived.credits_minor == -75_00
