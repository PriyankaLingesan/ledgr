"""Investigation Center and Reconciliation Workspace behavior over the API.

The rules under test: investigators inspect evidence but never modify ledger
history; reconciliation never "fixes" the ledger to force a match; an
ambiguous match stays ambiguous; and every exception can be drilled into.
"""

from sqlalchemy import func, select

from app.models.enums import UserRole
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction


def post(client, cash, revenue, amount, **extra):
    response = client.post(
        "/api/v1/transactions",
        json={
            "description": "workspace test",
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": amount},
                {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": amount},
            ],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def ledger_fingerprint(db):
    db.expire_all()
    return (
        db.execute(select(func.count(Transaction.id))).scalar_one(),
        db.execute(select(func.count(LedgerEntry.id))).scalar_one(),
        db.execute(select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0))).scalar_one(),
    )


def import_records(client, *records):
    response = client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "BANK_FEED",
            "records": [
                {
                    "external_id": ext_id,
                    "amount_minor": amount,
                    "currency": "USD",
                    "direction": "DEBIT",
                    "transaction_timestamp": "2026-09-08T12:00:00Z",
                }
                for ext_id, amount in records
            ],
        },
    )
    assert response.status_code == 201, response.text


def results_by_status(run):
    out = {}
    for r in run["results"]:
        out.setdefault(r["status"], []).append(r)
    return out


# --------------------------------------------------------------------------
# reconciliation workspace
# --------------------------------------------------------------------------


def test_each_exception_can_be_drilled_into_with_both_sides(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 450_000, external_reference="PAY-1")  # booked 4500.00
    post(client, cash, revenue, 100_000, external_reference="PAY-OK")
    import_records(client, ("PAY-1", 500_000), ("PAY-OK", 100_000), ("PAY-GHOST", 99_900))

    run = client.post("/api/v1/reconciliation/run").json()
    by_status = results_by_status(run)
    assert (
        run["matched_count"] == 1
        and run["mismatch_count"] == 1
        and run["missing_internal_count"] == 1
    )

    mismatch = by_status["AMOUNT_MISMATCH"][0]
    assert mismatch["external_transaction"]["source_system"] == "BANK_FEED"
    assert mismatch["external_transaction"]["external_id"] == "PAY-1"
    assert mismatch["external_transaction"]["amount"]["amount_minor"] == 500_000
    assert mismatch["internal_amount"]["amount_minor"] == 450_000
    assert mismatch["difference"]["amount_minor"] == 50_000
    assert mismatch["internal_reference"]
    assert mismatch["explanation"]

    missing = by_status["MISSING_INTERNAL"][0]
    assert missing["internal_transaction_id"] is None
    assert missing["external_transaction"]["external_id"] == "PAY-GHOST"


def test_run_results_can_be_filtered_by_status(client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 100, external_reference="A-1")
    import_records(client, ("A-1", 100), ("A-2", 200))
    run = client.post("/api/v1/reconciliation/run").json()

    only_missing = client.get(
        f"/api/v1/reconciliation/runs/{run['id']}", params={"status": "MISSING_INTERNAL"}
    ).json()
    assert [r["status"] for r in only_missing["results"]] == ["MISSING_INTERNAL"]
    # Summary counts describe the whole run, not the filtered view.
    assert only_missing["matched_count"] == 1


def test_sources_list_every_imported_system(client):
    import_records(client, ("S-1", 100), ("S-2", 200))
    sources = client.get("/api/v1/reconciliation/sources").json()
    assert sources == [
        {
            "source_system": "BANK_FEED",
            "record_count": 2,
            "latest_transaction_at": sources[0]["latest_transaction_at"],
            "last_imported_at": sources[0]["last_imported_at"],
        }
    ]


def test_ambiguous_matches_stay_ambiguous_through_the_api(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 500_00, external_reference="PAY-900", reference="TXN-A")
    post(client, cash, revenue, 500_00, external_reference="PAY-900", reference="TXN-B")
    import_records(client, ("PAY-900", 500_00))

    before = ledger_fingerprint(db)
    first = client.post("/api/v1/reconciliation/run").json()
    second = client.post("/api/v1/reconciliation/run").json()

    for run in (first, second):
        assert run["ambiguous_count"] == 1
        assert run["matched_count"] == 0
        result = run["results"][0]
        assert result["status"] == "AMBIGUOUS_INTERNAL_MATCH"
        assert result["internal_transaction_id"] is None  # no guess was made
    assert ledger_fingerprint(db) == before


def test_opening_a_case_for_an_exception_does_not_change_the_ledger_or_the_result(
    client, db, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 450_000, external_reference="PAY-1")
    import_records(client, ("PAY-1", 500_000))
    run = client.post("/api/v1/reconciliation/run").json()
    result = run["results"][0]
    before = ledger_fingerprint(db)

    case = client.post(f"/api/v1/reconciliation/results/{result['id']}/investigate")
    assert case.status_code == 201
    body = case.json()
    assert body["source"] == "RECONCILIATION"
    assert body["priority"] == "HIGH"  # an amount mismatch
    assert body["reconciliation_result_id"] == result["id"]

    assert ledger_fingerprint(db) == before
    reread = client.get(f"/api/v1/reconciliation/runs/{run['id']}").json()["results"][0]
    assert reread["status"] == "AMOUNT_MISMATCH"  # not "fixed" by investigating
    assert reread["investigation_case_number"] == body["case_number"]


def test_investigating_the_same_exception_twice_returns_the_existing_case(client, cash_and_revenue):
    import_records(client, ("ORPHAN-1", 700))
    run = client.post("/api/v1/reconciliation/run").json()
    result_id = run["results"][0]["id"]

    first = client.post(f"/api/v1/reconciliation/results/{result_id}/investigate").json()
    second = client.post(f"/api/v1/reconciliation/results/{result_id}/investigate").json()
    assert first["id"] == second["id"]
    assert client.get("/api/v1/investigations").json()["total"] == 1


# --------------------------------------------------------------------------
# investigation center
# --------------------------------------------------------------------------


def test_case_evidence_shows_amounts_difference_lineage_and_audit_events(
    client, db, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    posted = post(client, cash, revenue, 450_000, external_reference="PAY-1")
    import_records(client, ("PAY-1", 500_000))
    run = client.post("/api/v1/reconciliation/run").json()
    case = client.post(
        f"/api/v1/reconciliation/results/{run['results'][0]['id']}/investigate"
    ).json()
    before = ledger_fingerprint(db)

    evidence = client.get(f"/api/v1/investigations/{case['id']}/evidence").json()

    recon = evidence["reconciliation"]
    assert recon["external_transaction"]["amount"]["amount_minor"] == 500_000
    assert recon["internal_amount"]["amount_minor"] == 450_000
    assert recon["difference"]["amount_minor"] == 50_000

    assert evidence["lineage"]["root"]["id"] == posted["id"]
    kinds = {e["event_type"] for e in evidence["audit_events"]}
    assert "investigation.created" in kinds  # the case's own history
    assert "transaction.posted" in kinds  # the triggering transaction's trail
    assert evidence["integrity"], "the integrity checklist is always included"
    assert ledger_fingerprint(db) == before  # reading evidence changes nothing


def test_resolving_a_case_never_edits_ledger_history(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(client, cash, revenue, 100)
    case = client.post(
        "/api/v1/investigations",
        json={"title": "Review", "issue_summary": "check", "priority": "CRITICAL"},
    ).json()
    assert case["priority"] == "CRITICAL"
    before = ledger_fingerprint(db)

    resolved = client.post(
        f"/api/v1/investigations/{case['id']}/resolve", json={"resolution_note": "reviewed, fine"}
    ).json()
    assert resolved["status"] == "RESOLVED"
    assert ledger_fingerprint(db) == before


def test_investigations_filter_by_status_priority_and_search(client):
    client.post(
        "/api/v1/investigations",
        json={"title": "Wire mismatch", "issue_summary": "a", "priority": "HIGH"},
    )
    client.post(
        "/api/v1/investigations",
        json={"title": "Fee review", "issue_summary": "b", "priority": "LOW"},
    )

    assert client.get("/api/v1/investigations", params={"priority": "HIGH"}).json()["total"] == 1
    assert client.get("/api/v1/investigations", params={"q": "fee"}).json()["total"] == 1
    assert client.get("/api/v1/investigations", params={"status": "RESOLVED"}).json()["total"] == 0
    assert client.get("/api/v1/investigations", params={"q": "INV-0001"}).json()["total"] == 1


def test_investigation_status_machine_is_still_enforced(client):
    case = client.post("/api/v1/investigations", json={"title": "t", "issue_summary": "s"}).json()
    bad = client.patch(f"/api/v1/investigations/{case['id']}/status", json={"status": "OPEN"})
    assert bad.status_code == 200  # OPEN -> OPEN is a no-op
    client.patch(f"/api/v1/investigations/{case['id']}/status", json={"status": "INVESTIGATING"})
    back = client.patch(f"/api/v1/investigations/{case['id']}/status", json={"status": "OPEN"})
    assert back.status_code == 422  # work has started; it cannot go back to OPEN


def test_operations_can_work_cases_and_auditors_can_only_look(role_client, client):
    case = client.post("/api/v1/investigations", json={"title": "t", "issue_summary": "s"}).json()
    ops = role_client(UserRole.OPERATIONS)
    auditor = role_client(UserRole.AUDITOR)

    assert auditor.get(f"/api/v1/investigations/{case['id']}/evidence").status_code == 200
    assert (
        auditor.patch(
            f"/api/v1/investigations/{case['id']}/status", json={"status": "INVESTIGATING"}
        ).status_code
        == 403
    )
    assert (
        auditor.post(
            f"/api/v1/investigations/{case['id']}/resolve", json={"resolution_note": "x"}
        ).status_code
        == 403
    )

    assert (
        ops.patch(
            f"/api/v1/investigations/{case['id']}/status", json={"status": "INVESTIGATING"}
        ).status_code
        == 200
    )
