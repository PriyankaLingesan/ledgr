"""Investigation case lifecycle: create, list, evidence, status, resolve.

A case carries no money of its own - every assertion about balances or
duplicates here is proving that `case_evidence` correctly delegates to the
same, already-tested `investigation` functions, not re-testing their math.
"""

import uuid

import pytest

from app.core.errors import NotFound, ValidationFailed
from app.models.enums import EntryDirection, InvestigationSource, InvestigationStatus
from app.services import investigation, investigation_cases, posting


def post(db, cash, revenue, amount, **kwargs):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description=kwargs.pop("description", "case test posting"),
            currency=kwargs.pop("currency", "USD"),
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
            ],
            **kwargs,
        ),
    )


# --------------------------------------------------------------------------
# create / list / get
# --------------------------------------------------------------------------


def test_create_case_assigns_a_sequential_case_number(db):
    first = investigation_cases.create_case(
        db, title="First case", issue_summary="something looked odd"
    )
    second = investigation_cases.create_case(
        db, title="Second case", issue_summary="something else looked odd"
    )

    assert first.case_number.startswith("INV-")
    assert first.case_number != second.case_number
    assert first.status is InvestigationStatus.OPEN
    assert first.source is InvestigationSource.MANUAL


def test_create_case_with_account_and_window(db, cash_and_revenue):
    cash, _ = cash_and_revenue
    case = investigation_cases.create_case(
        db,
        title="Balance looks wrong",
        issue_summary="cash balance is higher than expected",
        source=InvestigationSource.FLIGHT_RECORDER,
        account_id=cash.id,
    )
    assert case.account_id == cash.id
    assert case.source is InvestigationSource.FLIGHT_RECORDER


def test_get_unknown_case_raises_not_found(db):
    with pytest.raises(NotFound):
        investigation_cases.get_case(db, uuid.uuid4())


def test_list_cases_filters_by_status(db):
    open_case = investigation_cases.create_case(db, title="Open one", issue_summary="x")
    resolved_case = investigation_cases.create_case(db, title="Resolved one", issue_summary="y")
    investigation_cases.resolve_case(db, resolved_case.id, resolution_note="handled")

    open_items, open_total = investigation_cases.list_cases(db, status=InvestigationStatus.OPEN)
    assert open_total == 1
    assert open_items[0].id == open_case.id

    resolved_items, resolved_total = investigation_cases.list_cases(
        db, status=InvestigationStatus.RESOLVED
    )
    assert resolved_total == 1
    assert resolved_items[0].id == resolved_case.id


def test_list_cases_filters_by_account(db, cash_and_revenue, make_account):
    cash, revenue = cash_and_revenue
    other = make_account("CASE.OTHER.USD")
    investigation_cases.create_case(db, title="On cash", issue_summary="x", account_id=cash.id)
    investigation_cases.create_case(db, title="On other", issue_summary="y", account_id=other.id)

    items, total = investigation_cases.list_cases(db, account_id=cash.id)
    assert total == 1
    assert items[0].account_id == cash.id


# --------------------------------------------------------------------------
# status transitions
# --------------------------------------------------------------------------


def test_status_moves_open_to_investigating(db):
    case = investigation_cases.create_case(db, title="x", issue_summary="y")
    updated = investigation_cases.update_status(
        db, case.id, status=InvestigationStatus.INVESTIGATING
    )
    assert updated.status is InvestigationStatus.INVESTIGATING
    assert updated.resolved_at is None


def test_status_move_to_resolved_stamps_resolved_at(db):
    case = investigation_cases.create_case(db, title="x", issue_summary="y")
    updated = investigation_cases.update_status(db, case.id, status=InvestigationStatus.RESOLVED)
    assert updated.status is InvestigationStatus.RESOLVED
    assert updated.resolved_at is not None


def test_status_can_reopen_a_resolved_case(db):
    case = investigation_cases.create_case(db, title="x", issue_summary="y")
    investigation_cases.update_status(db, case.id, status=InvestigationStatus.RESOLVED)

    reopened = investigation_cases.update_status(
        db, case.id, status=InvestigationStatus.INVESTIGATING
    )
    assert reopened.status is InvestigationStatus.INVESTIGATING
    assert reopened.resolved_at is None


def test_status_cannot_jump_from_resolved_to_open(db):
    case = investigation_cases.create_case(db, title="x", issue_summary="y")
    investigation_cases.update_status(db, case.id, status=InvestigationStatus.RESOLVED)

    with pytest.raises(ValidationFailed):
        investigation_cases.update_status(db, case.id, status=InvestigationStatus.OPEN)


# --------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------


def test_resolve_case_sets_note_and_status(db):
    case = investigation_cases.create_case(db, title="x", issue_summary="y")
    resolved = investigation_cases.resolve_case(
        db, case.id, resolution_note="turned out to be a timing difference, no action needed"
    )
    assert resolved.status is InvestigationStatus.RESOLVED
    assert resolved.resolution_note.startswith("turned out")
    assert resolved.resolved_at is not None


def test_resolve_case_links_a_correction_transaction(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)
    correction = investigation.correct_transaction(
        db,
        original.id,
        entries=[
            posting.EntryCommand(cash.id, EntryDirection.DEBIT, 400_00),
            posting.EntryCommand(revenue.id, EntryDirection.CREDIT, 400_00),
        ],
        reason="found via investigation",
    )

    case = investigation_cases.create_case(
        db, title="Wrong amount", issue_summary="posted amount was too high", account_id=cash.id
    )
    resolved = investigation_cases.resolve_case(
        db,
        case.id,
        resolution_note="corrected the amount",
        correction_transaction_id=correction.corrected.id,
    )
    assert resolved.correction_transaction_id == correction.corrected.id


# --------------------------------------------------------------------------
# evidence assembly
# --------------------------------------------------------------------------


def test_evidence_includes_balance_change_for_an_account_scoped_case(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 250_00)

    case = investigation_cases.create_case(
        db, title="Cash review", issue_summary="checking recent activity", account_id=cash.id
    )
    evidence = investigation_cases.case_evidence(db, case.id)

    assert evidence.balance_change is not None
    assert evidence.balance_change.account.id == cash.id
    assert evidence.balance_change.reconciled is True


def test_evidence_has_no_balance_change_for_a_ledger_wide_case(db):
    case = investigation_cases.create_case(
        db, title="Ledger-wide integrity", issue_summary="general health check"
    )
    evidence = investigation_cases.case_evidence(db, case.id)
    assert evidence.balance_change is None
    assert evidence.integrity  # always populated, ledger-wide


def test_evidence_duplicate_candidates_are_scoped_to_the_case_account(
    db, cash_and_revenue, make_account
):
    cash, revenue = cash_and_revenue
    other_cash = make_account("CASE.DUP.OTHER.USD")

    post(db, cash, revenue, 88_00, description="dup", reference="EV-DUP-1")
    post(db, cash, revenue, 88_00, description="dup", reference="EV-DUP-2")
    post(db, other_cash, revenue, 99_00, description="dup2", reference="EV-DUP-3")
    post(db, other_cash, revenue, 99_00, description="dup2", reference="EV-DUP-4")

    case = investigation_cases.create_case(
        db, title="Cash duplicates", issue_summary="check cash", account_id=cash.id
    )
    evidence = investigation_cases.case_evidence(db, case.id)

    touched_accounts = {
        e.account_id for group in evidence.duplicate_candidates for e in group.entries
    }
    assert touched_accounts == {cash.id}


def test_evidence_includes_lineage_when_source_reference_is_a_transaction(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    original = post(db, cash, revenue, 500_00)

    case = investigation_cases.create_case(
        db,
        title="Possible duplicate",
        issue_summary="flagged by the control exception engine",
        source=InvestigationSource.DUPLICATE,
        source_reference=str(original.id),
        account_id=cash.id,
    )
    evidence = investigation_cases.case_evidence(db, case.id)

    assert evidence.lineage is not None
    assert evidence.lineage.root.id == original.id


def test_evidence_tolerates_a_non_transaction_source_reference(db):
    case = investigation_cases.create_case(
        db,
        title="Manual note",
        issue_summary="a freeform note, not a transaction id",
        source_reference="not-a-uuid",
    )
    evidence = investigation_cases.case_evidence(db, case.id)
    assert evidence.lineage is None


# --------------------------------------------------------------------------
# via the API
# --------------------------------------------------------------------------


def test_case_lifecycle_via_api(client, cash_and_revenue):
    cash, revenue = cash_and_revenue

    created = client.post(
        "/api/v1/investigations",
        json={
            "title": "API case",
            "issue_summary": "exercised end to end",
            "account_id": str(cash.id),
        },
    )
    assert created.status_code == 201
    case_id = created.json()["id"]
    assert created.json()["case_number"].startswith("INV-")

    listed = client.get("/api/v1/investigations", params={"status": "OPEN"})
    assert listed.status_code == 200
    assert any(item["id"] == case_id for item in listed.json()["items"])

    evidence = client.get(f"/api/v1/investigations/{case_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["case"]["id"] == case_id

    moved = client.patch(
        f"/api/v1/investigations/{case_id}/status", json={"status": "INVESTIGATING"}
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "INVESTIGATING"

    resolved = client.post(
        f"/api/v1/investigations/{case_id}/resolve",
        json={"resolution_note": "confirmed not an issue"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["resolved_at"] is not None


def test_get_unknown_case_via_api_returns_404(client):
    resp = client.get(f"/api/v1/investigations/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_invalid_status_transition_via_api_returns_422(client):
    created = client.post(
        "/api/v1/investigations", json={"title": "x", "issue_summary": "y"}
    ).json()
    client.post(f"/api/v1/investigations/{created['id']}/resolve", json={"resolution_note": "done"})

    resp = client.patch(f"/api/v1/investigations/{created['id']}/status", json={"status": "OPEN"})
    assert resp.status_code == 422
