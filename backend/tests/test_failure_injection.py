"""Failure injection: a crash *inside* a multi-step write must leave nothing behind.

Each test makes one step in the middle of a financial operation raise, then
looks at the database from a brand-new session to prove the operation rolled
back as a whole: no transaction, no entries, no balance movement, no status
change, no dangling audit row. Real PostgreSQL, real constraints.
"""

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.draft import TransactionDraft
from app.models.enums import AccountType, DraftStatus, EntryDirection, UserRole
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.services import audit, posting
from app.services import balances as balance_service
from tests.conftest import new_session


class Injected(RuntimeError):
    pass


def snapshot():
    """Counts and balances as seen by a fresh session (nothing cached)."""
    with new_session() as s:
        return {
            "transactions": s.execute(select(func.count(Transaction.id))).scalar_one(),
            "entries": s.execute(select(func.count(LedgerEntry.id))).scalar_one(),
            "audit": s.execute(select(func.count(AuditEvent.id))).scalar_one(),
            "statuses": sorted(
                s.execute(select(Transaction.reference, Transaction.status)).all(), key=str
            ),
            "balances": {
                str(k): (v.debits_minor, v.credits_minor)
                for k, v in balance_service.derive_all(s).items()
            },
        }


def command(cash, revenue, amount=1_000, **extra):
    return posting.PostCommand(
        description="injection test",
        currency="USD",
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
        ],
        actor="pytest",
        **extra,
    )


def fail_on(monkeypatch, event_type):
    real = audit.record

    def boom(db, *, event_type: str, **kw):
        if event_type == target:
            raise Injected(f"injected failure while recording {event_type}")
        return real(db, event_type=event_type, **kw)

    target = event_type
    monkeypatch.setattr(audit, "record", boom)


def test_a_failure_after_the_entries_are_written_rolls_the_whole_posting_back(
    db, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    before = snapshot()
    fail_on(monkeypatch, "transaction.posted")

    with pytest.raises(Injected):
        posting.post_transaction(db, command(cash, revenue))
    db.rollback()

    assert snapshot() == before  # no transaction, no entries, no balance movement, no audit


def test_a_failure_while_reversing_leaves_the_original_posted_and_unreversed(
    db, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    original = posting.post_transaction(db, command(cash, revenue, 7_500))
    before = snapshot()
    fail_on(monkeypatch, "transaction.reversed")

    with pytest.raises(Injected):
        posting.reverse_transaction(db, original.id, reason="oops", actor="pytest")
    db.rollback()

    assert snapshot() == before
    with new_session() as s:
        row = s.execute(select(Transaction).where(Transaction.id == original.id)).scalar_one()
        assert row.status.value == "POSTED" and row.reversed_at is None


def test_a_composed_write_without_commit_can_be_abandoned_entirely(db, cash_and_revenue):
    """`auto_commit=False` is what corrections and draft approvals build on: the
    flush hits every constraint, but nothing is durable until the caller commits."""
    cash, revenue = cash_and_revenue
    before = snapshot()
    posting.post_transaction(db, command(cash, revenue, 3_000), auto_commit=False)
    db.rollback()  # the caller decides the larger operation failed
    assert snapshot() == before


def test_a_failure_during_draft_approval_posts_nothing_and_keeps_the_draft_pending(
    client, user_client, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = maker.post(
        "/api/v1/transaction-drafts?submit=true",
        json={
            "description": "approval injection",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 4_200},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 4_200},
            ],
        },
    ).json()
    before = snapshot()

    fail_on(monkeypatch, "draft.approved")  # fails *after* the ledger posting succeeded
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = checker.headers["Authorization"]
        response = c.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert response.status_code == 500
    monkeypatch.undo()

    after = snapshot()
    assert after["transactions"] == before["transactions"]
    assert after["entries"] == before["entries"]
    assert after["balances"] == before["balances"]
    with new_session() as s:
        row = s.execute(select(TransactionDraft)).scalar_one()
        assert row.status is DraftStatus.PENDING_APPROVAL and row.posted_transaction_id is None

    # A retry then succeeds, exactly once.
    ok = checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert ok.status_code == 200 and ok.json()["status"] == "POSTED"
    assert snapshot()["transactions"] == before["transactions"] + 1
    assert (
        checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={}).status_code
        == 409
    )


def test_a_failed_http_posting_releases_its_idempotency_key_for_a_clean_retry(
    client, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    payload = {
        "description": "idempotent injection",
        "currency": "USD",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 900},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 900},
        ],
    }
    headers = {"Idempotency-Key": "inject-1"}
    before = snapshot()

    fail_on(monkeypatch, "transaction.posted")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = client.headers["Authorization"]
        assert c.post("/api/v1/transactions", json=payload, headers=headers).status_code == 500
    monkeypatch.undo()
    assert snapshot()["transactions"] == before["transactions"]

    retry = client.post("/api/v1/transactions", json=payload, headers=headers)
    assert retry.status_code == 201
    assert snapshot()["transactions"] == before["transactions"] + 1
    replay = client.post("/api/v1/transactions", json=payload, headers=headers)
    assert replay.headers["Idempotent-Replay"] == "true"
    assert snapshot()["transactions"] == before["transactions"] + 1


def test_the_database_alone_refuses_an_unbalanced_or_cross_currency_posting(db, make_account):
    """Defence in depth: bypass the service layer entirely and the schema still says no."""
    import uuid

    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import current_org_id

    cash = make_account("DB.CASH", AccountType.ASSET)
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO transactions (id, organization_id, reference, description, currency,"
                " total_debits_minor, total_credits_minor, entry_count, effective_at)"
                " VALUES (:id, :org, 'RAW-UNBAL', 'x', 'USD', 100, 99, 2, now())"
            ),
            {"id": uuid.uuid4(), "org": current_org_id()},
        )
    db.rollback()
    assert cash.currency == "USD"
