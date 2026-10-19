"""Invariant I10, extended to guided correction.

`POST /transactions/{id}/correct` used to have no idempotency-key protection
at all: a retried request (a double-click, a network retry) would reverse an
already-corrected transaction a second time. This file proves the fix from
both directions - the HTTP-level idempotency wrapper (repeated / concurrent
requests, linkage) and the atomicity it depends on at the service layer
(a failed correction leaves the original untouched, safe to retry).
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from app.core.errors import AlreadyReversed, NotFound
from app.models.enums import AccountType, EntryDirection
from app.services import investigation, posting
from tests.conftest import new_session

pytestmark = pytest.mark.concurrency


def post(db, cash, revenue, amount, **kwargs):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description=kwargs.pop("description", "correction idempotency test"),
            currency=kwargs.pop("currency", "USD"),
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
            ],
            **kwargs,
        ),
    )


def correction_payload(cash_id, revenue_id, amount=570_00, reason="incorrect amount"):
    return {
        "reason": reason,
        "entries": [
            {"account_id": str(cash_id), "direction": "DEBIT", "amount_minor": amount},
            {"account_id": str(revenue_id), "direction": "CREDIT", "amount_minor": amount},
        ],
    }


# --------------------------------------------------------------------------
# 1. repeated correction request (double-click, client retry)
# --------------------------------------------------------------------------


def test_repeated_correction_request_replays_instead_of_duplicating(client, cash_and_revenue):
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

    key = str(uuid.uuid4())
    payload = correction_payload(cash.id, revenue.id)

    first = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    second = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=payload,
        headers={"Idempotency-Key": key},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json() == second.json()

    # Exactly one reversal and one corrected transaction were created - not two.
    listing = client.get("/api/v1/transactions").json()
    assert listing["total"] == 3  # original + one reversal + one corrected


def test_same_correction_key_with_a_different_body_is_refused(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 500_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 500_00},
            ],
        },
    ).json()

    key = str(uuid.uuid4())
    client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id, amount=100_00),
        headers={"Idempotency-Key": key},
    )
    conflicting = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id, amount=200_00),
        headers={"Idempotency-Key": key},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_key_reused"


# --------------------------------------------------------------------------
# 2. concurrent correction requests
# --------------------------------------------------------------------------


def test_concurrent_corrections_of_the_same_transaction_produce_exactly_one(db, make_account):
    cash = make_account("CIDX.CASH.USD", AccountType.ASSET)
    revenue = make_account("CIDX.REV.USD", AccountType.REVENUE)
    original = post(db, cash, revenue, 750_00)

    workers = 5
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)
        session = new_session()
        try:
            investigation.correct_transaction(
                session,
                original.id,
                entries=[
                    posting.EntryCommand(cash.id, EntryDirection.DEBIT, 570_00),
                    posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 570_00),
                ],
                reason="race",
                actor="race",
            )
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

    refreshed = posting.load_transaction(db, original.id)
    assert refreshed.status.value == "REVERSED"
    # Exactly one reversal exists (the unique index on reverses_transaction_id
    # is what actually guarantees this; the row lock in reverse_transaction
    # merely makes the race rare).
    reversal_count = db.execute(
        sa.text("SELECT count(*) FROM transactions WHERE reverses_transaction_id = :id"),
        {"id": original.id},
    ).scalar_one()
    assert reversal_count == 1


def test_concurrent_correction_requests_via_http_with_the_same_key(client, cash_and_revenue):
    """The HTTP-level race: N simultaneous retries of one logical request."""
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "http race",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 300_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 300_00},
            ],
        },
    ).json()

    key = str(uuid.uuid4())
    payload = correction_payload(cash.id, revenue.id, amount=250_00)
    workers = 5
    barrier = threading.Barrier(workers)

    def worker(_):
        barrier.wait(timeout=10)
        resp = client.post(
            f"/api/v1/transactions/{original['id']}/correct",
            json=payload,
            headers={"Idempotency-Key": key},
        )
        return resp.status_code, resp.json()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(worker, range(workers)))

    statuses = [status for status, _ in results]
    # Every request that reaches a final answer must agree on which
    # corrected transaction won - no caller can ever see two different ids
    # for the same key.
    ok_bodies = {body["corrected_id"] for status, body in results if status == 201}
    assert len(ok_bodies) == 1
    assert all(s in (201, 409) for s in statuses)


# --------------------------------------------------------------------------
# 3. correcting an already-reversed transaction
# --------------------------------------------------------------------------


def test_correcting_an_already_reversed_transaction_is_rejected(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 500_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 500_00},
            ],
        },
    ).json()

    reversed_resp = client.post(
        f"/api/v1/transactions/{original['id']}/reverse", json={"reason": "plain reversal"}
    )
    assert reversed_resp.status_code == 201

    correction = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id),
    )
    assert correction.status_code == 409
    assert correction.json()["error"]["code"] == "already_reversed"


# --------------------------------------------------------------------------
# 4. failed correction rollback: atomicity across reverse + repost
# --------------------------------------------------------------------------


def test_failed_correction_leaves_the_original_untouched(db, cash_and_revenue):
    """The new entries name an account that does not exist - the post half
    of the correction fails. Because both writes are one atomic transaction,
    the reversal half must not have landed either: the original stays POSTED.
    """
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)

    with pytest.raises(NotFound):
        investigation.correct_transaction(
            db,
            original.id,
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 400_00),
                posting.EntryCommand(uuid.uuid4(), EntryDirection.CREDIT, 400_00),
            ],
            reason="will fail",
        )

    refreshed = posting.load_transaction(db, original.id)
    assert refreshed.status.value == "POSTED"  # not reversed - the whole write rolled back

    # No orphan reversal was left behind either.
    reversal_count = db.execute(
        sa.text("SELECT count(*) FROM transactions WHERE reverses_transaction_id = :id"),
        {"id": original.id},
    ).scalar_one()
    assert reversal_count == 0


def test_retry_after_a_failed_correction_succeeds_cleanly(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 500_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 500_00},
            ],
        },
    ).json()

    key = str(uuid.uuid4())
    broken_payload = {
        "reason": "will fail",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 400_00},
            {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 400_00},
        ],
    }
    failed = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=broken_payload,
        headers={"Idempotency-Key": key},
    )
    assert failed.status_code == 404

    # Same key, now with a valid body: the key was released, and the
    # original is still POSTED, so this must succeed - not 409, not crash.
    retried = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id),
        headers={"Idempotency-Key": key},
    )
    assert retried.status_code == 201


# --------------------------------------------------------------------------
# 5. successful correction
# --------------------------------------------------------------------------


def test_successful_correction_returns_all_three_transaction_ids(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 750_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 750_00},
            ],
        },
    ).json()

    resp = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["original_id"] == original["id"]
    assert body["reversal_id"] != original["id"]
    assert body["corrected_id"] != original["id"]
    assert body["corrected_id"] != body["reversal_id"]


# --------------------------------------------------------------------------
# 6. correction linkage
# --------------------------------------------------------------------------


def test_correction_linkage_is_visible_end_to_end(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 750_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 750_00},
            ],
        },
    ).json()

    correction = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json=correction_payload(cash.id, revenue.id),
    ).json()

    # The reversal names the original via the real FK.
    reversal = client.get(f"/api/v1/transactions/{correction['reversal_id']}").json()
    assert reversal["reverses_transaction_id"] == original["id"]

    # The corrected transaction names the original via metadata.
    corrected = client.get(f"/api/v1/transactions/{correction['corrected_id']}").json()
    assert corrected["metadata"]["corrects"] == original["id"]
    assert corrected["metadata"]["reversal_id"] == correction["reversal_id"]

    # The original, refetched, shows it was reversed - status only, entries untouched.
    refetched = client.get(f"/api/v1/transactions/{original['id']}").json()
    assert refetched["status"] == "REVERSED"
    assert refetched["reversed_by_transaction_id"] == correction["reversal_id"]
    assert refetched["total_debits"]["amount_minor"] == 750_00

    # The lineage endpoint ties all three together in both directions.
    original_lineage = client.get(f"/api/v1/transactions/{original['id']}/lineage").json()
    assert original_lineage["reversed_by"]["id"] == correction["reversal_id"]
    assert original_lineage["corrected_by"]["id"] == correction["corrected_id"]

    corrected_lineage = client.get(
        f"/api/v1/transactions/{correction['corrected_id']}/lineage"
    ).json()
    assert corrected_lineage["corrects"]["id"] == original["id"]
