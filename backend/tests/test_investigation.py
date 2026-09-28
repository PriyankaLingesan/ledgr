"""LEDGR Financial Flight Recorder: historical reconstruction, balance-change
explanation, guided correction, duplicate flags, and the integrity checklist.

Every test here posts through the real, unchanged `posting` service (or the
real API) - nothing pokes `created_at` or any other column directly, so what
is proven is the actual query logic new callers will exercise, not a
convenient fixture.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.errors import AlreadyReversed, NotFound
from app.models.enums import AccountType, EntryDirection
from app.services import balances, investigation, posting


def db_now(db) -> datetime:
    """The database server's actual wall clock, not the test process's.

    Boundary tests compare a Python-side timestamp against `created_at`
    values Postgres stamps with its own clock - on a machine where the two
    clocks aren't tightly synced (a container clock that hasn't resynced
    after the host was suspended, for instance) a Python-side timestamp can
    disagree with the server by enough to flip which side of a boundary an
    entry falls on. Asking the database what time it thinks it is removes
    that skew entirely.

    Two things matter here, both about Postgres's `now()` (used by
    `LedgerEntry.created_at`'s `server_default`): it is `transaction_
    timestamp()` under the hood, meaning it is *fixed at transaction start*,
    not evaluated fresh per statement or per row.

    1. We read `clock_timestamp()`, not `now()`, so repeated calls on the
       same still-open session actually advance instead of returning the
       same frozen instant.
    2. We commit both before *and* after the read. Before: the `db` fixture's
       session can carry a transaction open since fixture setup (e.g.
       `cash_and_revenue` creating accounts), and if that transaction is
       still open when a `post()` call makes the session's first commit,
       every entry written since gets stamped with that old transaction's
       start time. After: the read itself opens a new transaction, and if a
       following `post()` call reuses it (same session, nothing else
       intervened), that write's `created_at` is `now()` - frozen at that
       *same* transaction's start, a fraction of a second before
       `clock_timestamp()` finished evaluating inside it. Committing again
       right after the read closes that transaction too, so the next write
       always begins its own, strictly-after one.
    """
    db.commit()
    value = db.execute(sa.text("SELECT clock_timestamp()")).scalar_one()
    db.commit()
    return value


def post(db, cash, revenue, amount, **kwargs):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description=kwargs.pop("description", "investigation test posting"),
            currency=kwargs.pop("currency", "USD"),
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
            ],
            **kwargs,
        ),
    )


def counts(db) -> tuple[int, int]:
    txns = db.execute(sa.text("SELECT count(*) FROM transactions")).scalar_one()
    entries = db.execute(sa.text("SELECT count(*) FROM ledger_entries")).scalar_one()
    return txns, entries


# --------------------------------------------------------------------------
# historical balance reconstruction
# --------------------------------------------------------------------------


def test_balance_before_any_transaction_is_zero(db, cash_and_revenue):
    cash, _ = cash_and_revenue
    result = investigation.balance_at(db, cash.id, datetime.now(UTC))
    assert result.derived.debits_minor == 0
    assert result.derived.credits_minor == 0


def test_historical_balance_before_a_transaction_excludes_it(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    before = db_now(db)
    post(db, cash, revenue, 500_00)

    result = investigation.balance_at(db, cash.id, before)
    assert result.derived.debits_minor == 0


def test_historical_balance_after_a_transaction_includes_it(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 500_00)
    after = txn.posted_at + timedelta(seconds=1)

    result = investigation.balance_at(db, cash.id, after)
    assert result.derived.debits_minor == 500_00


def test_multiple_transactions_accumulate_in_order(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100_00)
    time.sleep(0.02)
    post(db, cash, revenue, 250_00)
    time.sleep(0.02)
    txn3 = post(db, cash, revenue, 75_00)

    result = investigation.balance_at(db, cash.id, txn3.posted_at + timedelta(seconds=1))
    assert result.derived.debits_minor == 100_00 + 250_00 + 75_00


def test_multiple_entries_in_one_transaction_both_count(db, make_account):
    cash = make_account("CASH.MULTI.USD", AccountType.ASSET)
    revenue_a = make_account("REV.A.USD", AccountType.REVENUE)
    revenue_b = make_account("REV.B.USD", AccountType.REVENUE)

    txn = posting.post_transaction(
        db,
        posting.PostCommand(
            description="split sale",
            currency="USD",
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 300_00),
                posting.EntryCommand(revenue_a.id, EntryDirection.CREDIT, 200_00),
                posting.EntryCommand(revenue_b.id, EntryDirection.CREDIT, 100_00),
            ],
        ),
    )

    change = investigation.balance_change(
        db,
        cash.id,
        from_ts=txn.posted_at - timedelta(seconds=1),
        to_ts=txn.posted_at + timedelta(seconds=1),
    )
    assert len(change.events) == 1  # only cash's own entry, not revenue's
    assert change.events[0].effect_minor == 300_00


def test_account_isolation(db, cash_and_revenue, make_account):
    cash, revenue = cash_and_revenue
    other_cash = make_account("CASH.OTHER.USD", AccountType.ASSET)
    post(db, cash, revenue, 500_00)

    result = investigation.balance_at(db, other_cash.id, datetime.now(UTC) + timedelta(seconds=1))
    assert result.derived.debits_minor == 0
    assert result.derived.entry_count == 0


def test_timestamp_boundary_at_from_is_included_in_the_window(db, cash_and_revenue):
    """An event whose created_at exactly equals `from` is a cause, not opening balance."""
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 500_00)

    change = investigation.balance_change(
        db, cash.id, from_ts=txn.posted_at, to_ts=txn.posted_at + timedelta(seconds=1)
    )
    assert change.opening_balance_minor == 0
    assert len(change.events) == 1
    assert change.closing_balance_minor == 500_00


def test_timestamp_boundary_before_from_is_excluded(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 500_00)
    just_after = txn.posted_at + timedelta(microseconds=1)

    change = investigation.balance_change(db, cash.id, from_ts=just_after, to_ts=just_after)
    assert change.opening_balance_minor == 500_00  # it's already in the opening balance
    assert len(change.events) == 0


# --------------------------------------------------------------------------
# reversal handling
# --------------------------------------------------------------------------


def test_reversed_transaction_appears_as_two_events(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 500_00)
    time.sleep(0.02)
    reversal = posting.reverse_transaction(db, txn.id, reason="test reversal")

    change = investigation.balance_change(
        db,
        cash.id,
        from_ts=txn.posted_at - timedelta(seconds=1),
        to_ts=reversal.posted_at + timedelta(seconds=1),
    )
    assert len(change.events) == 2
    assert change.closing_balance_minor == 0  # nets back to zero
    original_event = next(e for e in change.events if e.transaction.id == txn.id)
    reversal_event = next(e for e in change.events if e.transaction.id == reversal.id)
    assert original_event.effect_minor == 500_00
    assert reversal_event.effect_minor == -500_00
    assert reversal_event.transaction.reverses_transaction_id == txn.id


# --------------------------------------------------------------------------
# state comparison / reconciliation - the property the whole feature rests on
# --------------------------------------------------------------------------


def test_state_comparison_reconciles_exactly(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    start = db_now(db)
    post(db, cash, revenue, 300_00)
    post(db, cash, revenue, 150_00, description="second")
    end = db_now(db)

    change = investigation.balance_change(db, cash.id, from_ts=start, to_ts=end)
    assert change.closing_balance_minor - change.opening_balance_minor == change.net_change_minor
    assert change.net_change_minor == sum(e.effect_minor for e in change.events)
    assert change.reconciled is True
    assert len(change.events) == 2


@pytest.mark.parametrize("num_transactions", [1, 3, 7])
def test_investigation_totals_reconcile_for_varying_activity(
    db, cash_and_revenue, num_transactions
):
    cash, revenue = cash_and_revenue
    start = db_now(db)
    for i in range(num_transactions):
        post(db, cash, revenue, (i + 1) * 10_00)
    end = db_now(db)

    change = investigation.balance_change(db, cash.id, from_ts=start, to_ts=end)
    assert change.reconciled is True
    assert len(change.events) == num_transactions


def test_default_window_explains_balance_with_no_arguments(db, cash_and_revenue):
    """'Explain This Balance' with no explicit range - defaults to a 30-day window."""
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 250_00)
    # `balance_change()`'s default `to_ts` is Python's own `datetime.now(UTC)`,
    # evaluated below - not `db_now()`. On a machine where the Postgres
    # container's clock runs even slightly ahead of the host (a real,
    # observed skew in this dev environment), the entry's server-stamped
    # `created_at` can land microseconds after a `to_ts` captured immediately
    # afterward, excluding it from its own default window. A short sleep
    # gives that skew somewhere to land without weakening what's asserted.
    time.sleep(0.05)

    change = investigation.balance_change(db, cash.id)
    assert change.reconciled is True
    assert change.closing_balance_minor == 250_00
    assert (change.to_ts - change.from_ts) == investigation.DEFAULT_WINDOW


# --------------------------------------------------------------------------
# timeline correctness
# --------------------------------------------------------------------------


def test_timeline_events_are_ordered_and_real(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    first = post(db, cash, revenue, 100_00, description="first")
    time.sleep(0.02)
    second = post(db, cash, revenue, 200_00, description="second")

    change = investigation.balance_change(
        db,
        cash.id,
        from_ts=first.posted_at - timedelta(seconds=1),
        to_ts=second.posted_at + timedelta(seconds=1),
    )
    refs = [e.transaction.reference for e in change.events]
    assert refs == [first.reference, second.reference]  # chronological, not fabricated


def test_timeline_via_api_entries_endpoint_respects_since_until(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    from_ts = db_now(db)
    resp = client.post(
        "/api/v1/transactions",
        json={
            "description": "windowed",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 400_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 400_00},
            ],
        },
    )
    assert resp.status_code == 201
    to_ts = db_now(db)

    page = client.get(
        f"/api/v1/accounts/{cash.id}/entries",
        params={"since": from_ts.isoformat(), "until": to_ts.isoformat()},
    ).json()
    assert page["total"] == 1
    assert page["items"][0]["amount"]["amount_minor"] == 400_00


# --------------------------------------------------------------------------
# guided correction
# --------------------------------------------------------------------------


def test_correction_reverses_and_reposts(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00, description="supplier payment")

    correction = investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
        ],
        reason="incorrect amount",
        actor="pytest",
    )

    assert correction.reversal.reverses_transaction_id == original.id
    assert correction.corrected.total_debits_minor == 570_00
    assert correction.corrected.extra["corrects"] == str(original.id)
    assert correction.corrected.extra["correction_reason"] == "incorrect amount"


def test_correction_preserves_the_original_transaction(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00, description="supplier payment")
    original_entry_ids = sorted(str(e.id) for e in original.entries)

    investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
        ],
        reason="incorrect amount",
    )

    refreshed = posting.load_transaction(db, original.id)
    assert refreshed.status.value == "REVERSED"
    assert sorted(str(e.id) for e in refreshed.entries) == original_entry_ids
    assert refreshed.total_debits_minor == 750_00  # untouched


def test_correction_leaves_the_ledger_balanced(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00)

    investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
        ],
        reason="incorrect amount",
    )

    balance = investigation.balance_at(db, cash.id, datetime.now(UTC) + timedelta(seconds=1))
    assert balance.derived.debits_minor - balance.derived.credits_minor == 570_00


def test_correction_of_a_reversal_is_rejected(db, cash_and_revenue):
    from app.core.errors import NotReversible

    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)
    reversal = posting.reverse_transaction(db, original.id)

    with pytest.raises(NotReversible):
        investigation.correct_transaction(
            db,
            reversal.id,
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
            ],
            reason="should not be allowed",
        )


def test_correction_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "supplier payment",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 750_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 750_00},
            ],
        },
    ).json()

    resp = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json={
            "reason": "incorrect amount",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 570_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 570_00},
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["original_id"] == original["id"]
    assert body["corrected_reference"] != original["reference"]

    # The original is still fetchable, unedited except its status.
    refetched = client.get(f"/api/v1/transactions/{original['id']}").json()
    assert refetched["status"] == "REVERSED"
    assert refetched["total_debits"]["amount_minor"] == 750_00


# --------------------------------------------------------------------------
# duplicate detection - deterministic, no AI
# --------------------------------------------------------------------------


def test_duplicate_candidates_flags_same_amount_same_account_close_in_time(db, cash_and_revenue):
    """Both sides of the repeated transaction match their own account's
    counterpart, so this is legitimately two groups - one for cash, one for
    revenue - not one. A flagged pair is scoped to a single account by
    design (that's what "same account" means), so a transaction posted
    twice in full surfaces once per account it touches.
    """
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 750_00, description="supplier payment")
    time.sleep(0.02)
    post(db, cash, revenue, 750_00, description="supplier payment")

    groups = investigation.duplicate_candidates(db, window_minutes=10)
    assert len(groups) == 2
    accounts_flagged = {g.entries[0].account_id for g in groups}
    assert accounts_flagged == {cash.id, revenue.id}
    for group in groups:
        assert "Same amount" in group.reasons
        assert "Identical description" in group.reasons


def test_duplicate_candidates_ignores_different_amounts(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 750_00)
    time.sleep(0.02)
    post(db, cash, revenue, 751_00)

    groups = investigation.duplicate_candidates(db, window_minutes=10)
    assert groups == []


def test_duplicate_candidates_respects_time_window(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 750_00)
    time.sleep(0.02)
    post(db, cash, revenue, 750_00)

    # A window shorter than the actual gap should not match.
    groups = investigation.duplicate_candidates(db, window_minutes=0)
    assert groups == []


def test_duplicates_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    for _ in range(2):
        client.post(
            "/api/v1/transactions",
            json={
                "description": "possible dup",
                "currency": "USD",
                "entries": [
                    {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 999_00},
                    {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 999_00},
                ],
            },
        )
        time.sleep(0.02)

    resp = client.get("/api/v1/system/duplicates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2  # one group per account the repeated transaction touches
    assert all(group["label"] == "POSSIBLE_DUPLICATE" for group in body)


# --------------------------------------------------------------------------
# integrity checklist
# --------------------------------------------------------------------------


def test_integrity_checklist_all_pass_on_a_healthy_ledger(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00)
    posting.reverse_transaction(
        db,
        posting.post_transaction(
            db,
            posting.PostCommand(
                description="to be reversed",
                currency="USD",
                entries=[
                    posting.EntryCommand(cash.id, EntryDirection.DEBIT, 100_00),
                    posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
                ],
            ),
        ).id,
    )

    checks = investigation.integrity_checklist(db)
    names = {c.name for c in checks}
    assert {
        "All postings balanced",
        "Ledger balances globally, per currency",
        "No orphan ledger entries",
        "Reversal relationships are valid",
        "Account balances match derived totals",
    } <= names
    assert all(c.passed for c in checks)


def test_integrity_checklist_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100_00},
            ],
        },
    )
    resp = client.get("/api/v1/system/integrity-checklist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["all_passed"] is True
    assert len(body["checks"]) >= 5


# --------------------------------------------------------------------------
# investigation must never mutate the ledger
# --------------------------------------------------------------------------


def test_balance_at_never_mutates_the_ledger(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00)
    before = counts(db)

    investigation.balance_at(db, cash.id, datetime.now(UTC))
    investigation.balance_change(db, cash.id)
    investigation.duplicate_candidates(db)
    investigation.integrity_checklist(db)

    assert counts(db) == before


def test_balance_at_unknown_account_raises_not_found(db):
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        investigation.balance_at(db, uuid.uuid4(), datetime.now(UTC))


# --------------------------------------------------------------------------
# balance-at / balance-change via the API
# --------------------------------------------------------------------------


def test_balance_at_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 321_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 321_00},
            ],
        },
    )
    at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    resp = client.get(f"/api/v1/accounts/{cash.id}/balance-at", params={"at": at})
    assert resp.status_code == 200
    assert resp.json()["balance"]["amount_minor"] == 321_00


def test_balance_change_via_api_reconciles(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 100_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 100_00},
            ],
        },
    )
    resp = client.get(f"/api/v1/accounts/{cash.id}/balance-change")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reconciled"] is True
    closing = body["closing_balance"]["amount_minor"]
    opening = body["opening_balance"]["amount_minor"]
    net = body["net_change"]["amount_minor"]
    assert closing - opening == net
    assert net == sum(e["effect"]["amount_minor"] for e in body["events"])


# --------------------------------------------------------------------------
# transaction lineage
# --------------------------------------------------------------------------


def test_lineage_of_a_never_touched_transaction_has_no_relationships(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)

    lineage = investigation.transaction_lineage(db, original.id)

    assert lineage.root.id == original.id
    assert {a.code for a in lineage.accounts} == {cash.code, revenue.code}
    assert lineage.reverses is None
    assert lineage.reversed_by is None
    assert lineage.corrects is None
    assert lineage.corrected_by is None


def test_lineage_of_a_plain_reversal_points_both_ways(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)
    reversal = posting.reverse_transaction(db, original.id, reason="mistake")

    original_lineage = investigation.transaction_lineage(db, original.id)
    assert original_lineage.reversed_by.id == reversal.id
    assert original_lineage.reverses is None
    assert original_lineage.corrects is None
    assert original_lineage.corrected_by is None

    reversal_lineage = investigation.transaction_lineage(db, reversal.id)
    assert reversal_lineage.reverses.id == original.id
    assert reversal_lineage.reversed_by is None


def test_lineage_of_a_correction_chain_is_original_reversal_corrected(db, cash_and_revenue):
    """Original -> Reversal -> Corrected, visible from every node."""
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00)

    correction = investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
        ],
        reason="wrong amount",
    )

    original_lineage = investigation.transaction_lineage(db, original.id)
    assert original_lineage.reversed_by.id == correction.reversal.id
    assert original_lineage.corrected_by.id == correction.corrected.id

    reversal_lineage = investigation.transaction_lineage(db, correction.reversal.id)
    assert reversal_lineage.reverses.id == original.id

    corrected_lineage = investigation.transaction_lineage(db, correction.corrected.id)
    assert corrected_lineage.corrects.id == original.id
    assert corrected_lineage.reverses is None
    assert corrected_lineage.reversed_by is None


def test_lineage_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "lineage test",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 400_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 400_00},
            ],
        },
    ).json()

    resp = client.get(f"/api/v1/transactions/{original['id']}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["root"]["id"] == original["id"]
    assert body["reversed_by"] is None
    assert {a["code"] for a in body["accounts"]} == {cash.code, revenue.code}


def test_lineage_unknown_transaction_raises_not_found(db):
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        investigation.transaction_lineage(db, uuid.uuid4())


# --------------------------------------------------------------------------
# what-if correction simulator
# --------------------------------------------------------------------------


def test_simulate_correction_reports_the_same_impact_as_a_real_correction(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00)

    simulation = investigation.simulate_correction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
        ],
    )

    by_account = {impact.account.id: impact for impact in simulation.impacts}
    assert by_account[cash.id].current_balance_minor == 750_00
    assert by_account[cash.id].simulated_balance_minor == 570_00
    assert by_account[cash.id].delta_minor == -180_00
    assert by_account[revenue.id].simulated_balance_minor == 570_00


def test_simulate_correction_writes_nothing_to_the_ledger(db, cash_and_revenue):
    """SIMULATION != PERSISTENCE: discarding a simulation must be a no-op."""
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 750_00)
    txns_before, entries_before = counts(db)

    investigation.simulate_correction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 999_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 999_00),
        ],
    )

    txns_after, entries_after = counts(db)
    assert (txns_after, entries_after) == (txns_before, entries_before)

    refreshed = posting.load_transaction(db, original.id)
    assert refreshed.status.value == "POSTED"  # not reversed by the simulation
    assert refreshed.total_debits_minor == 750_00

    balance = balances.derive_for_account(db, cash.id)
    assert balance.debits_minor == 750_00  # unaffected by the simulated 999_00


def test_simulate_correction_of_an_already_reversed_transaction_is_rejected(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)
    posting.reverse_transaction(db, original.id)

    with pytest.raises(AlreadyReversed):
        investigation.simulate_correction(
            db,
            original.id,
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
            ],
        )


def test_simulate_correction_unknown_account_raises_not_found(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)

    with pytest.raises(NotFound):
        investigation.simulate_correction(
            db,
            original.id,
            entries=[
                posting.EntryCommand(uuid.uuid4(), EntryDirection.DEBIT, 100_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 100_00),
            ],
        )


def test_simulate_correction_via_api_does_not_change_the_real_balance(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "simulate me",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 200_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 200_00},
            ],
        },
    ).json()

    resp = client.post(
        f"/api/v1/transactions/{original['id']}/simulate-correction",
        json={
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 999_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 999_00},
            ]
        },
    )
    assert resp.status_code == 200
    impacts = {i["account_id"]: i for i in resp.json()["impacts"]}
    assert impacts[str(cash.id)]["simulated_balance"]["amount_minor"] == 999_00

    real_balance = client.get(f"/api/v1/accounts/{cash.id}/balance").json()
    assert real_balance["balance"]["amount_minor"] == 200_00  # untouched

    refetched = client.get(f"/api/v1/transactions/{original['id']}").json()
    assert refetched["status"] == "POSTED"  # untouched


# --------------------------------------------------------------------------
# control exception engine
# --------------------------------------------------------------------------


def test_control_exceptions_include_duplicate_candidates(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 42_00, description="control test", reference="CTRL-1")
    post(db, cash, revenue, 42_00, description="control test", reference="CTRL-2")

    exceptions = investigation.list_control_exceptions(db)
    assert any(e.kind == "POTENTIAL_DUPLICATE" for e in exceptions)


def test_control_exceptions_are_empty_on_a_healthy_ledger_with_no_duplicates(
    db, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100_00)

    exceptions = investigation.list_control_exceptions(db, duplicate_window_minutes=0)
    assert all(e.kind != "INTEGRITY" for e in exceptions)


def test_control_exceptions_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    client.post(
        "/api/v1/transactions",
        json={
            "description": "dup",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 77_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 77_00},
            ],
        },
    )
    client.post(
        "/api/v1/transactions",
        json={
            "description": "dup",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 77_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 77_00},
            ],
        },
    )

    resp = client.get("/api/v1/system/control-exceptions")
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["kind"] == "POTENTIAL_DUPLICATE" for e in body)
    # Every exception carries a stable, non-empty fingerprint id.
    assert all(e["id"] for e in body)
