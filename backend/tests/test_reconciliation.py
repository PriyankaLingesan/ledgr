"""External reconciliation: deterministic comparison against real internal
transactions via `Transaction.external_reference` - no AI, no fuzzy
matching. Every test here posts through the real `posting` service and
reconciles through the real `reconciliation` service; nothing pokes the
database directly.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.enums import EntryDirection, ReconciliationStatus
from app.models.reconciliation import ReconciliationResult, ReconciliationRun
from app.services import investigation, posting, reconciliation


def post(db, cash, revenue, amount, **kwargs):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description=kwargs.pop("description", "reconciliation test posting"),
            currency=kwargs.pop("currency", "USD"),
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
            ],
            **kwargs,
        ),
    )


def external(external_id, amount_minor, **kwargs):
    return reconciliation.ExternalRecord(
        external_id=external_id,
        amount_minor=amount_minor,
        currency=kwargs.pop("currency", "USD"),
        direction=kwargs.pop("direction", EntryDirection.DEBIT),
        transaction_timestamp=kwargs.pop("transaction_timestamp", datetime(2026, 1, 1, 12, 0)),
        **kwargs,
    )


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------


def test_ingestion_persists_external_transactions(db):
    result = reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-100", 1_000_00)]
    )
    assert result.imported == 1
    assert result.skipped_duplicates == 0


def test_repeated_ingestion_does_not_duplicate_external_records(db):
    records = [external("PAY-101", 500_00), external("PAY-102", 750_00)]

    first = reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=records
    )
    second = reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=records
    )

    assert first.imported == 2
    assert second.imported == 0
    assert second.skipped_duplicates == 2

    run = reconciliation.run_reconciliation(db)
    assert run.external_count == 2  # not 4


def test_same_external_id_from_a_different_source_is_not_a_duplicate(db):
    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-200", 100_00)]
    )
    result = reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_B", records=[external("PAY-200", 100_00)]
    )
    assert result.imported == 1  # identity is (source_system, external_id), not external_id alone


# --------------------------------------------------------------------------
# reconciliation outcomes
# --------------------------------------------------------------------------


def test_exact_match(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00, external_reference="PAY-300")
    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-300", 500_00)]
    )

    run = reconciliation.run_reconciliation(db)

    assert run.matched_count == 1
    assert run.mismatch_count == 0
    assert run.missing_internal_count == 0
    result = run.results[0]
    assert result.status is ReconciliationStatus.MATCHED
    assert "matched internal transaction" in result.explanation


def test_amount_mismatch(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 450_00, external_reference="PAY-301")  # booked 450.00
    reconciliation.ingest_external_transactions(
        db,
        source_system="PAYMENT_PROVIDER_A",
        records=[external("PAY-301", 500_00)],  # claims 500.00
    )

    run = reconciliation.run_reconciliation(db)

    assert run.mismatch_count == 1
    result = run.results[0]
    assert result.status is ReconciliationStatus.AMOUNT_MISMATCH
    assert result.difference_minor == 50_00
    assert "Difference" in result.explanation


def test_missing_internal(db):
    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-302", 800_00)]
    )

    run = reconciliation.run_reconciliation(db)

    assert run.missing_internal_count == 1
    result = run.results[0]
    assert result.status is ReconciliationStatus.MISSING_INTERNAL
    assert result.internal_transaction_id is None
    assert "no internal LEDGR transaction" in result.explanation


def test_a_full_run_produces_all_three_mandatory_outcomes(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00, external_reference="PAY-400")
    post(db, cash, revenue, 450_00, external_reference="PAY-401")

    reconciliation.ingest_external_transactions(
        db,
        source_system="PAYMENT_PROVIDER_A",
        records=[
            external("PAY-400", 500_00),  # matches exactly
            external("PAY-401", 500_00),  # internal booked 450.00 - mismatch
            external("PAY-402", 200_00),  # no internal transaction at all
        ],
    )

    run = reconciliation.run_reconciliation(db)

    assert run.external_count == 3
    assert run.matched_count == 1
    assert run.mismatch_count == 1
    assert run.missing_internal_count == 1
    statuses = {r.external_transaction.external_id: r.status for r in run.results}
    assert statuses["PAY-400"] is ReconciliationStatus.MATCHED
    assert statuses["PAY-401"] is ReconciliationStatus.AMOUNT_MISMATCH
    assert statuses["PAY-402"] is ReconciliationStatus.MISSING_INTERNAL


# --------------------------------------------------------------------------
# reconciliation is read-only with respect to the ledger
# --------------------------------------------------------------------------


def test_reconciliation_does_not_mutate_the_internal_ledger(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 450_00, external_reference="PAY-500")
    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-500", 500_00)]
    )

    from app.services import balances

    before = balances.derive_for_account(db, cash.id)

    reconciliation.run_reconciliation(db)  # an AMOUNT_MISMATCH run

    after = balances.derive_for_account(db, cash.id)
    refreshed = posting.load_transaction(db, original.id)

    assert before == after
    assert refreshed.status.value == "POSTED"  # never touched
    assert refreshed.total_debits_minor == 450_00  # never touched


def test_reconciliation_is_repeatable_and_each_run_is_independent(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100_00, external_reference="PAY-600")
    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-600", 100_00)]
    )

    first_run = reconciliation.run_reconciliation(db)
    second_run = reconciliation.run_reconciliation(db)

    assert first_run.id != second_run.id
    assert first_run.matched_count == second_run.matched_count == 1

    items, total = reconciliation.list_runs(db)
    assert total == 2
    assert {r.id for r in items} == {first_run.id, second_run.id}


# --------------------------------------------------------------------------
# matching integrity: external_reference is not unique on Transaction
# --------------------------------------------------------------------------


def test_two_unrelated_live_transactions_sharing_a_reference_are_ambiguous(db, cash_and_revenue):
    """The exact bug a naive `LIMIT 1` match would hide: two genuinely
    different, both-POSTED, both-STANDARD transactions happen to share an
    external_reference (a data-entry mistake - nothing in the schema
    prevents it). Reconciliation must say so, not silently pick one.
    """
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00, external_reference="PAY-900", reference="TXN-A")
    post(db, cash, revenue, 500_00, external_reference="PAY-900", reference="TXN-B")

    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-900", 500_00)]
    )

    run = reconciliation.run_reconciliation(db)

    assert run.ambiguous_count == 1
    result = run.results[0]
    assert result.status is ReconciliationStatus.AMBIGUOUS_INTERNAL_MATCH
    assert result.internal_transaction_id is None
    assert "TXN-A" in result.explanation and "TXN-B" in result.explanation


def test_a_corrected_transaction_still_matches_cleanly_not_ambiguously(db, cash_and_revenue):
    """The critical non-regression case: `correct_transaction` deliberately
    copies `external_reference` onto the replacement, so after a correction
    there ARE two (or three) transactions sharing one reference - the
    original (REVERSED) and its replacement (POSTED). This must resolve
    cleanly to the replacement, not be reported as ambiguous: only the
    replacement is "live" (POSTED + STANDARD).
    """
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 400_00, external_reference="PAY-901")

    investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 500_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 500_00),
        ],
        reason="amount was wrong",
    )

    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-901", 500_00)]
    )

    run = reconciliation.run_reconciliation(db)

    assert run.ambiguous_count == 0
    assert run.matched_count == 1
    result = run.results[0]
    assert result.status is ReconciliationStatus.MATCHED
    # Matched against the *replacement*, not the reversed original.
    refreshed_original = posting.load_transaction(db, original.id)
    assert result.internal_transaction_id != original.id
    assert refreshed_original.status.value == "REVERSED"


def test_a_plain_reversal_with_no_replacement_is_missing_internal(db, cash_and_revenue):
    """Reversed, and nothing posted in its place: no live internal
    representation exists for this external reference any more.
    """
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 300_00, external_reference="PAY-902")
    posting.reverse_transaction(db, original.id, reason="fully reversed, no replacement")

    reconciliation.ingest_external_transactions(
        db, source_system="PAYMENT_PROVIDER_A", records=[external("PAY-902", 300_00)]
    )

    run = reconciliation.run_reconciliation(db)

    assert run.missing_internal_count == 1
    result = run.results[0]
    assert result.status is ReconciliationStatus.MISSING_INTERNAL
    assert "none is currently POSTED" in result.explanation


# --------------------------------------------------------------------------
# MISSING_EXTERNAL: a live internal transaction with no external evidence
# --------------------------------------------------------------------------


def test_missing_external(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 250_00, external_reference="PAY-910")

    run = reconciliation.run_reconciliation(db)  # no external evidence imported at all

    assert run.missing_external_count == 1
    result = next(r for r in run.results if r.status is ReconciliationStatus.MISSING_EXTERNAL)
    assert result.external_transaction_id is None
    assert result.internal_transaction is not None
    assert "PAY-910" in result.explanation


def test_missing_external_does_not_flag_transactions_without_an_external_reference(
    db, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100_00)  # no external_reference at all

    run = reconciliation.run_reconciliation(db)

    assert run.missing_external_count == 0


def test_missing_external_does_not_double_flag_a_reversed_original(db, cash_and_revenue):
    """Only the live (POSTED, STANDARD) transaction is checked for external
    evidence - the reversed original sharing the same reference isn't an
    independent second gap.
    """
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 200_00, external_reference="PAY-911")
    investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 220_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 220_00),
        ],
        reason="amount correction",
    )
    # No external evidence imported for PAY-911 at all.

    run = reconciliation.run_reconciliation(db)

    missing = [r for r in run.results if r.status is ReconciliationStatus.MISSING_EXTERNAL]
    assert len(missing) == 1  # not 2 (original + replacement)


# --------------------------------------------------------------------------
# a result must always anchor to at least one real record (DB-enforced)
# --------------------------------------------------------------------------


def test_a_reconciliation_result_cannot_point_at_neither_record(db):
    """The CHECK constraint added alongside the nullable external_transaction_id -
    bypasses the service layer entirely to prove the database itself refuses
    an orphaned result, the same discipline `test_immutability.py` applies
    to the ledger tables.
    """
    run = ReconciliationRun(id=uuid.uuid4())
    db.add(run)
    db.flush()

    orphan = ReconciliationResult(
        id=uuid.uuid4(),
        run_id=run.id,
        external_transaction_id=None,
        internal_transaction_id=None,
        status=ReconciliationStatus.MISSING_INTERNAL,
        explanation="should be impossible",
    )
    db.add(orphan)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------
# via the API
# --------------------------------------------------------------------------


def test_reconciliation_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "api reconciliation test",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 500_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 500_00},
            ],
            "external_reference": "PAY-700",
        },
    )
    assert original.status_code == 201

    imported = client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "PAYMENT_PROVIDER_A",
            "records": [
                {
                    "external_id": "PAY-700",
                    "amount_minor": 500_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T12:00:00Z",
                },
                {
                    "external_id": "PAY-701",
                    "amount_minor": 900_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T12:00:00Z",
                },
            ],
        },
    )
    assert imported.status_code == 201
    assert imported.json()["imported"] == 2

    # Repeating the same import must not duplicate.
    replay = client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "PAYMENT_PROVIDER_A",
            "records": [
                {
                    "external_id": "PAY-700",
                    "amount_minor": 500_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T12:00:00Z",
                }
            ],
        },
    )
    assert replay.json()["imported"] == 0
    assert replay.json()["skipped_duplicates"] == 1

    run = client.post("/api/v1/reconciliation/run")
    assert run.status_code == 201
    body = run.json()
    assert body["external_count"] == 2
    assert body["matched_count"] == 1
    assert body["missing_internal_count"] == 1

    fetched = client.get(f"/api/v1/reconciliation/runs/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    listed = client.get("/api/v1/reconciliation/runs")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


# --------------------------------------------------------------------------
# integration with the existing investigation system
# --------------------------------------------------------------------------


def test_investigate_a_missing_internal_exception_opens_a_case(client, cash_and_revenue):
    imported = client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "PAYMENT_PROVIDER_A",
            "records": [
                {
                    "external_id": "PAY-800",
                    "amount_minor": 300_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T12:00:00Z",
                }
            ],
        },
    )
    assert imported.status_code == 201

    run = client.post("/api/v1/reconciliation/run").json()
    result = run["results"][0]
    assert result["status"] == "MISSING_INTERNAL"

    case = client.post(f"/api/v1/reconciliation/results/{result['id']}/investigate")
    assert case.status_code == 201
    body = case.json()
    assert body["source"] == "RECONCILIATION"
    assert "PAY-800" in body["issue_summary"]
    assert body["case_number"].startswith("INV-")

    # It's a real case in the SAME investigation system - not a parallel one.
    fetched = client.get(f"/api/v1/investigations/{body['id']}")
    assert fetched.status_code == 200


def test_investigate_an_amount_mismatch_links_the_internal_transaction_for_lineage(
    client, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions",
        json={
            "description": "mismatch source",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": 450_00},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": 450_00},
            ],
            "external_reference": "PAY-801",
        },
    ).json()

    client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "PAYMENT_PROVIDER_A",
            "records": [
                {
                    "external_id": "PAY-801",
                    "amount_minor": 500_00,
                    "currency": "USD",
                    "transaction_timestamp": "2026-01-01T12:00:00Z",
                }
            ],
        },
    )
    run = client.post("/api/v1/reconciliation/run").json()
    result = run["results"][0]
    assert result["status"] == "AMOUNT_MISMATCH"

    case = client.post(f"/api/v1/reconciliation/results/{result['id']}/investigate").json()
    assert case["source_reference"] == original["id"]

    evidence = client.get(f"/api/v1/investigations/{case['id']}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["lineage"]["root"]["id"] == original["id"]
