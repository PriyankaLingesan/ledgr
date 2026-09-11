"""Concurrency behaviour under real, simultaneous database sessions.

Each worker gets its own connection, so these exercise the actual PostgreSQL
locking behaviour rather than a simulation of it.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import AlreadyReversed, IdempotentRequestInFlight, InsufficientFunds
from app.db.session import SessionLocal
from app.models.enums import AccountType, EntryDirection
from app.services import balances, idempotency, posting

pytestmark = pytest.mark.concurrency


def _post(debit_id, credit_id, amount, description="concurrent"):
    """Run one posting in its own session, as a separate request would."""
    session = SessionLocal()
    try:
        posting.post_transaction(
            session,
            posting.PostCommand(
                description=description,
                currency="USD",
                entries=[
                    posting.EntryCommand(debit_id, EntryDirection.DEBIT, amount),
                    posting.EntryCommand(credit_id, EntryDirection.CREDIT, amount),
                ],
            ),
        )
        return "ok"
    except InsufficientFunds:
        session.rollback()
        return "insufficient_funds"
    finally:
        session.close()


def test_concurrent_postings_keep_the_balance_cache_exact(db, make_account):
    """Eight simultaneous writers against one account, no lost updates."""
    cash = make_account("CC.CASH.USD", AccountType.ASSET)
    revenue = make_account("CC.REV.USD", AccountType.REVENUE)

    workers = 8
    amount = 10_00
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)  # maximise real contention
        return _post(cash.id, revenue.id, amount)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))

    assert results.count("ok") == workers

    derived = balances.derive_for_account(db, cash.id)
    assert derived.debits_minor == workers * amount
    assert derived.entry_count == workers

    db.expire_all()
    from app.services import accounts as account_service

    refreshed = account_service.get_account(db, cash.id)
    assert refreshed.balance_cache.debits_minor == derived.debits_minor
    assert refreshed.balance_cache.entry_count == derived.entry_count


def test_balance_floor_holds_under_concurrent_withdrawals(db, make_account):
    """The classic double-spend: two withdrawals racing for the same funds.

    Both would pass a check-then-write done outside a lock. Because the check
    happens while the account row is locked, exactly one can win.
    """
    vault = make_account("CC.VAULT.USD", AccountType.ASSET, allows_negative_balance=False)
    revenue = make_account("CC.REV2.USD", AccountType.REVENUE)

    posting.post_transaction(
        db,
        posting.PostCommand(
            description="fund the vault",
            currency="USD",
            entries=[
                posting.EntryCommand(vault.id, EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
            ],
        ),
    )

    workers = 4
    withdrawal = 60_00  # only one can possibly succeed
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)
        return _post(revenue.id, vault.id, withdrawal, "withdrawal")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))

    assert results.count("ok") == 1
    assert results.count("insufficient_funds") == workers - 1

    derived = balances.derive_for_account(db, vault.id)
    balance = derived.debits_minor - derived.credits_minor
    assert balance == 40_00
    assert balance >= 0


def test_disjoint_accounts_do_not_block_each_other(db, make_account):
    """Lock scope is per account, so unrelated postings run in parallel."""
    pairs = [
        (
            make_account(f"CC.A{i}.USD", AccountType.ASSET),
            make_account(f"CC.B{i}.USD", AccountType.REVENUE),
        )
        for i in range(6)
    ]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda pair: _post(pair[0].id, pair[1].id, 5_00), pairs))

    assert results == ["ok"] * 6
    for row in balances.trial_balance(db):
        assert row.balanced


def test_concurrent_reversals_produce_exactly_one(db, make_account):
    """Invariant I11 under a race, enforced by the unique index."""
    cash = make_account("CC.RCASH.USD", AccountType.ASSET)
    revenue = make_account("CC.RREV.USD", AccountType.REVENUE)
    original = posting.post_transaction(
        db,
        posting.PostCommand(
            description="to be reversed",
            currency="USD",
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 75_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 75_00),
            ],
        ),
    )

    workers = 4
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)
        session = SessionLocal()
        try:
            posting.reverse_transaction(session, original.id, actor="race")
            return "ok"
        except AlreadyReversed:
            session.rollback()
            return "already_reversed"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))

    assert results.count("ok") == 1
    assert results.count("already_reversed") == workers - 1

    for account in (cash, revenue):
        derived = balances.derive_for_account(db, account.id)
        assert derived.signed_minor == 0


def test_concurrent_claims_of_the_same_new_key_exactly_one_wins():
    """Invariant I10 under a genuine race: N threads claim one fresh key.

    Regression coverage for the rowcount bug: misreading a successful
    `INSERT ... ON CONFLICT DO NOTHING` as a conflict made every claimant see
    itself as arriving second, so *nobody* ever won the race and every
    request - concurrent or not - was rejected as already in-flight. With the
    fix, exactly one of these simultaneous claimants wins; the rest correctly
    back off with `IdempotentRequestInFlight`.
    """
    key = str(uuid.uuid4())
    request_hash = idempotency.fingerprint({"scenario": "concurrent-claim"})
    workers = 8
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)
        try:
            result = idempotency.claim(
                scope=idempotency.SCOPE_POST_TRANSACTION,
                key=key,
                request_hash=request_hash,
            )
            return "claimed" if result is None else "replayed"
        except IdempotentRequestInFlight:
            return "in_flight"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))

    assert results.count("claimed") == 1
    assert results.count("in_flight") == workers - 1
    assert results.count("replayed") == 0
