"""Invariants I5, I6, I12: posted history cannot be rewritten.

These tests deliberately bypass the service layer and issue raw SQL. The point
is that the guarantee holds even for a client that has no idea LEDGR's business
rules exist.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import InternalError, ProgrammingError

from app.db.session import SessionLocal
from app.models.enums import EntryDirection
from app.services import posting

DB_ERRORS = (InternalError, ProgrammingError)


@pytest.fixture
def posted(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description="Immutability fixture",
            currency="USD",
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, 200_00),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 200_00),
            ],
        ),
    )


def _raw(sql: str, **params):
    with SessionLocal() as session:
        session.execute(sa.text(sql), params)
        session.commit()


def test_ledger_entry_cannot_be_updated(posted):
    entry_id = posted.entries[0].id
    with pytest.raises(DB_ERRORS) as excinfo:
        _raw("UPDATE ledger_entries SET amount_minor = 1 WHERE id = :id", id=entry_id)
    assert "append-only" in str(excinfo.value)


def test_ledger_entry_cannot_be_deleted(posted):
    entry_id = posted.entries[0].id
    with pytest.raises(DB_ERRORS):
        _raw("DELETE FROM ledger_entries WHERE id = :id", id=entry_id)

    with SessionLocal() as session:
        remaining = session.execute(
            sa.text("SELECT count(*) FROM ledger_entries WHERE transaction_id = :t"),
            {"t": posted.id},
        ).scalar_one()
    assert remaining == 2


def test_transaction_cannot_be_deleted(posted):
    with pytest.raises(DB_ERRORS):
        _raw("DELETE FROM transactions WHERE id = :id", id=posted.id)


def test_transaction_financial_fields_are_frozen(posted):
    with pytest.raises(DB_ERRORS) as excinfo:
        _raw(
            "UPDATE transactions SET total_debits_minor = 1, total_credits_minor = 1 "
            "WHERE id = :id",
            id=posted.id,
        )
    assert "immutable" in str(excinfo.value)


def test_transaction_description_is_frozen(posted):
    """Even a harmless-looking edit is refused: history is history."""
    with pytest.raises(DB_ERRORS):
        _raw("UPDATE transactions SET description = 'edited' WHERE id = :id", id=posted.id)


def test_audit_events_are_append_only(posted):
    with pytest.raises(DB_ERRORS):
        _raw("UPDATE audit_events SET actor = 'someone-else'")
    with pytest.raises(DB_ERRORS):
        _raw("DELETE FROM audit_events")


def test_unbalanced_row_cannot_be_inserted_directly(db, cash_and_revenue):
    """The CHECK constraint backs up invariant I1 at the storage layer."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        _raw(
            """
            INSERT INTO transactions
                (id, reference, description, currency, status, kind,
                 total_debits_minor, total_credits_minor, entry_count,
                 effective_at, actor)
            VALUES
                (gen_random_uuid(), 'TXN-BAD', 'forced', 'USD', 'POSTED', 'STANDARD',
                 100, 90, 2, now(), 'attacker')
            """
        )


def test_status_transition_to_reversed_is_permitted(db, cash_and_revenue, posted):
    """The one mutation history allows, applied through the service."""
    from app.models.enums import TransactionStatus

    reversal = posting.reverse_transaction(db, posted.id, reason="test", actor="pytest")
    refreshed = posting.load_transaction(db, posted.id)

    assert refreshed.status is TransactionStatus.REVERSED
    assert refreshed.reversed_at is not None
    assert reversal.reverses_transaction_id == posted.id
    # The original's entries are untouched.
    assert len(refreshed.entries) == 2
    assert refreshed.total_debits_minor == 200_00
