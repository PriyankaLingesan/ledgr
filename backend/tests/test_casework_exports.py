"""Investigation notes and reports, reconciliation suggestions/decisions, CSV
export, related-record links, and the liveness/readiness split."""

import csv
import io
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models.enums import UserRole


def post(client, cash, rev, amount, **extra):
    r = client.post(
        "/api/v1/transactions",
        json={
            "description": extra.pop("description", "casework fixture"),
            "currency": "USD",
            "entries": [
                {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": amount},
                {"account_id": str(rev.id), "direction": "CREDIT", "amount_minor": amount},
            ],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def import_records(client, *records):
    r = client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "BANK",
            "records": [
                {
                    "external_id": ext,
                    "amount_minor": amt,
                    "currency": "USD",
                    "direction": "DEBIT",
                    "transaction_timestamp": "2026-09-08T12:00:00Z",
                }
                for ext, amt in records
            ],
        },
    )
    assert r.status_code == 201, r.text


def run_recon(client):
    return client.post("/api/v1/reconciliation/run").json()


def result_for(run, external_id):
    return next(
        r
        for r in run["results"]
        if (r["external_transaction"] or {}).get("external_id") == external_id
    )


# --------------------------------------------------------------------------
# investigation notes + evidence report
# --------------------------------------------------------------------------


def make_case(client, **kw):
    return client.post(
        "/api/v1/investigations", json={"title": "Wire discrepancy", "issue_summary": "check", **kw}
    ).json()


def test_notes_are_appended_attributed_and_audited(client, db):
    case = make_case(client)
    first = client.post(
        f"/api/v1/investigations/{case['id']}/notes", json={"body": "Called the bank."}
    )
    assert first.status_code == 201 and first.json()["author"] == "test-client"
    client.post(f"/api/v1/investigations/{case['id']}/notes", json={"body": "Bank confirms."})

    notes = client.get(f"/api/v1/investigations/{case['id']}/notes").json()
    assert [n["body"] for n in notes] == ["Called the bank.", "Bank confirms."]
    events = client.get(
        "/api/v1/system/audit", params={"event_type": "investigation.note_added"}
    ).json()
    assert events["total"] == 2


def test_empty_notes_are_refused_and_auditors_cannot_write_them(client, role_client):
    case = make_case(client)
    assert (
        client.post(f"/api/v1/investigations/{case['id']}/notes", json={"body": "   "}).status_code
        == 422
    )
    auditor = role_client(UserRole.AUDITOR)
    assert (
        auditor.post(f"/api/v1/investigations/{case['id']}/notes", json={"body": "x"}).status_code
        == 403
    )
    assert auditor.get(f"/api/v1/investigations/{case['id']}/notes").status_code == 200


def test_notes_cannot_be_edited_or_deleted_even_in_the_database(client, db):
    case = make_case(client)
    client.post(f"/api/v1/investigations/{case['id']}/notes", json={"body": "permanent"})
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE investigation_notes SET body = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM investigation_notes"))
    db.rollback()


def test_the_case_report_is_assembled_from_real_records(client, cash_and_revenue):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 450_000, external_reference="PAY-1")
    import_records(client, ("PAY-1", 500_000))
    run = run_recon(client)
    res = result_for(run, "PAY-1")
    case = client.post(f"/api/v1/reconciliation/results/{res['id']}/investigate").json()
    client.post(
        f"/api/v1/investigations/{case['id']}/notes", json={"body": "Provider claims 5000.00"}
    )

    response = client.get(f"/api/v1/investigations/{case['id']}/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert f"{case['case_number']}-evidence.md" in response.headers["content-disposition"]
    text_body = response.text
    assert case["case_number"] in text_body
    assert "AMOUNT_MISMATCH" in text_body
    assert "Provider claims 5000.00" in text_body
    assert "[PASS]" in text_body
    assert "investigation.created" in text_body
    assert "AI-written" in text_body


# --------------------------------------------------------------------------
# reconciliation: suggestions and decisions
# --------------------------------------------------------------------------


def test_suggestions_are_ranked_explained_and_never_applied(client, db, cash_and_revenue):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 70_000, description="Settlement PAY-77 batch")
    post(client, cash, rev, 70_000, description="Unrelated same amount")
    post(client, cash, rev, 12_345, description="Something else entirely")
    import_records(client, ("PAY-77", 70_000))
    res = result_for(run_recon(client), "PAY-77")
    assert res["status"] == "MISSING_INTERNAL"

    suggestions = client.get(f"/api/v1/reconciliation/results/{res['id']}/suggestions").json()
    assert len(suggestions) == 2
    assert suggestions[0]["description"] == "Settlement PAY-77 batch"
    assert suggestions[0]["score"] > suggestions[1]["score"]
    assert any("PAY-77" in r for r in suggestions[0]["reasons"])
    assert all(s["requires_review"] for s in suggestions)
    assert 12_345 not in {s["amount"]["amount_minor"] for s in suggestions}

    # Asking for suggestions changed nothing: the result is still MISSING_INTERNAL.
    again = client.get(f"/api/v1/reconciliation/runs/{run_id(client)}").json()
    assert result_for(again, "PAY-77")["status"] == "MISSING_INTERNAL"


def run_id(client):
    return client.get("/api/v1/reconciliation/runs").json()["items"][0]["id"]


def test_a_decision_is_recorded_without_changing_the_result_or_the_ledger(
    client, db, cash_and_revenue
):
    cash, rev = cash_and_revenue
    txn = post(client, cash, rev, 70_000, description="Settlement PAY-77 batch")
    import_records(client, ("PAY-77", 70_000))
    res = result_for(run_recon(client), "PAY-77")
    before = client.get("/api/v1/system/stats").json()["transaction_count"]

    decision = client.post(
        f"/api/v1/reconciliation/results/{res['id']}/decisions",
        json={
            "decision": "CONFIRMED_MATCH",
            "internal_transaction_id": txn["id"],
            "reason": "Reference in the description matches PAY-77.",
        },
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["decided_by"] == "test-client"

    rerun = client.get(f"/api/v1/reconciliation/runs/{run_id(client)}").json()
    shown = result_for(rerun, "PAY-77")
    assert shown["status"] == "MISSING_INTERNAL"  # the deterministic verdict is untouched
    assert shown["latest_decision"]["decision"] == "CONFIRMED_MATCH"
    assert client.get("/api/v1/system/stats").json()["transaction_count"] == before
    audit_events = client.get(
        "/api/v1/system/audit", params={"event_type": "reconciliation.decision_recorded"}
    ).json()
    assert audit_events["total"] == 1


def test_decisions_are_validated(client, cash_and_revenue, make_account):

    cash, rev = cash_and_revenue
    txn = post(client, cash, rev, 70_000)
    import_records(client, ("PAY-77", 70_000))
    res = result_for(run_recon(client), "PAY-77")
    url = f"/api/v1/reconciliation/results/{res['id']}/decisions"

    assert client.post(url, json={"decision": "CONFIRMED_MATCH", "reason": "x"}).status_code == 422
    assert (
        client.post(
            url,
            json={
                "decision": "CONFIRMED_MATCH",
                "internal_transaction_id": str(uuid.uuid4()),
                "reason": "x",
            },
        ).status_code
        == 404
    )
    assert client.post(url, json={"decision": "NOT_A_MATCH", "reason": "  "}).status_code == 422
    # Reversed transactions cannot be confirmed as a match.
    client.post(f"/api/v1/transactions/{txn['id']}/reverse", json={})
    reversed_try = client.post(
        url,
        json={"decision": "CONFIRMED_MATCH", "internal_transaction_id": txn["id"], "reason": "x"},
    )
    assert reversed_try.status_code == 422
    ok = client.post(url, json={"decision": "NOT_A_MATCH", "reason": "No such settlement exists."})
    assert ok.status_code == 201


def test_matched_results_need_no_decision_and_auditors_cannot_decide(
    client, role_client, cash_and_revenue
):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 5_000, external_reference="PAY-1")
    import_records(client, ("PAY-1", 5_000))
    res = result_for(run_recon(client), "PAY-1")
    assert res["status"] == "MATCHED"
    url = f"/api/v1/reconciliation/results/{res['id']}/decisions"
    assert client.post(url, json={"decision": "NOT_A_MATCH", "reason": "x"}).status_code == 422
    assert (
        role_client(UserRole.AUDITOR)
        .post(url, json={"decision": "NOT_A_MATCH", "reason": "x"})
        .status_code
        == 403
    )


def test_decisions_cannot_be_rewritten(client, db, cash_and_revenue):
    import_records(client, ("PAY-9", 1_000))
    res = result_for(run_recon(client), "PAY-9")
    client.post(
        f"/api/v1/reconciliation/results/{res['id']}/decisions",
        json={"decision": "NOT_A_MATCH", "reason": "nothing there"},
    )
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE reconciliation_decisions SET reason = 'edited'"))
    db.rollback()


# --------------------------------------------------------------------------
# related records
# --------------------------------------------------------------------------


def test_related_links_reconciliation_cases_and_decisions(client, cash_and_revenue):
    cash, rev = cash_and_revenue
    txn = post(client, cash, rev, 450_000, external_reference="PAY-1")
    import_records(client, ("PAY-1", 500_000))
    res = result_for(run_recon(client), "PAY-1")
    case = client.post(f"/api/v1/reconciliation/results/{res['id']}/investigate").json()

    related = client.get(f"/api/v1/transactions/{txn['id']}/related").json()
    assert related["reconciliation"][0]["external_id"] == "PAY-1"
    assert related["reconciliation"][0]["status"] == "AMOUNT_MISMATCH"
    assert [c["case_number"] for c in related["investigations"]] == [case["case_number"]]
    assert related["decisions"] == []
    assert client.get(f"/api/v1/transactions/{uuid.uuid4()}/related").status_code == 404


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------


def rows(response):
    return list(csv.DictReader(io.StringIO(response.text)))


def test_export_matches_the_list_filters_and_is_audited(client, cash_and_revenue):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 1_000, description="Alpha")
    post(client, cash, rev, 2_000, description="Beta")
    response = client.get("/api/v1/transactions/export", params={"q": "Beta"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    exported = rows(response)
    assert [r["description"] for r in exported] == ["Beta"]
    assert exported[0]["amount"] == "20.00" and exported[0]["amount_minor"] == "2000"

    events = client.get("/api/v1/system/audit", params={"event_type": "export.transactions"}).json()
    assert events["total"] == 1 and events["items"][0]["payload"]["rows"] == 1


def test_export_neutralises_spreadsheet_formulas(client, cash_and_revenue):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 1_000, description='=HYPERLINK("http://evil.test","click")')
    post(client, cash, rev, 1_000, description="+SUM(A1:A9)")
    exported = rows(client.get("/api/v1/transactions/export"))
    descriptions = {r["description"] for r in exported}
    assert descriptions == {'\'=HYPERLINK("http://evil.test","click")', "'+SUM(A1:A9)"}


def test_export_is_capped_and_says_so(client, cash_and_revenue, monkeypatch):
    from app.api.v1 import transactions as txn_routes

    cash, rev = cash_and_revenue
    for _ in range(3):
        post(client, cash, rev, 100)
    monkeypatch.setattr(txn_routes, "_EXPORT_LIMIT", 2)
    response = client.get("/api/v1/transactions/export")
    assert len(rows(response)) == 2
    assert response.headers["x-export-truncated"] == "true"


def test_export_requires_read_permission_and_is_tenant_scoped(
    client, role_client, org_client, other_org, cash_and_revenue
):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 100)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        assert anon.get("/api/v1/transactions/export").status_code == 401
    assert role_client(UserRole.AUDITOR).get("/api/v1/transactions/export").status_code == 200
    other = org_client(other_org)
    assert rows(other.get("/api/v1/transactions/export")) == []


# --------------------------------------------------------------------------
# liveness / readiness / integrity are different things
# --------------------------------------------------------------------------


def test_liveness_and_readiness_are_public_and_distinct():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        assert anon.get("/api/v1/system/live").json() == {"status": "ok"}
        ready = anon.get("/api/v1/system/ready")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["schema"] in ("ok", "unknown")
        assert "integrity" not in body["checks"]  # per-organization, so not public


def test_readiness_reports_a_schema_that_is_behind(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.v1 import system
    from app.main import app

    monkeypatch.setattr(system, "_expected_schema_head", lambda: "9999")
    with TestClient(app) as anon:
        response = anon.get("/api/v1/system/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["schema"] == "behind"


# --------------------------------------------------------------------------
# dashboard semantics
# --------------------------------------------------------------------------


def test_dashboard_states_its_window_and_counts_failed_operations(client, cash_and_revenue):
    cash, rev = cash_and_revenue
    post(client, cash, rev, 1_000)
    post(client, cash, rev, 2_500)
    ghost = {
        "description": "to nowhere",
        "currency": "USD",
        "entries": [
            {"account_id": str(uuid.uuid4()), "direction": "DEBIT", "amount_minor": 5},
            {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 5},
        ],
    }
    assert client.post("/api/v1/transactions", json=ghost).status_code == 404

    dash = client.get("/api/v1/dashboard").json()
    assert dash["volume_window_days"] == 30
    assert dash["posted_volume"] == [
        {"currency": "USD", "transaction_count": 2, "posted_minor": 3_500}
    ]
    assert dash["failed_operations_24h"]["rejected_postings"] == 1
    assert dash["failed_operations_24h"]["refused_approvals"] == 0


def test_report_and_ai_context_include_duplicate_candidates(client, cash_and_revenue, monkeypatch):
    """Regression (found by the live demo): a case with duplicate candidates made the
    report and the copilot context crash because they read `reference` off a ledger entry."""
    from app.services.ai import provider as ai_provider

    cash, rev = cash_and_revenue
    for _ in range(2):  # identical postings seconds apart -> duplicate candidates
        post(client, cash, rev, 4_242, description="twin payment")
    case = client.post(
        "/api/v1/investigations",
        json={
            "title": "Twin payments",
            "issue_summary": "two identical",
            "account_id": str(cash.id),
        },
    ).json()

    report = client.get(f"/api/v1/investigations/{case['id']}/report")
    assert report.status_code == 200
    assert "Potential duplicates" in report.text and "TXN-" in report.text

    class Echo:
        def complete(self, *, system, messages, tools=None, max_tokens=1024, json_mode=False):
            from app.services.ai.provider import Completion

            self.prompt = messages[0].content
            return Completion(
                text=json.dumps(
                    {
                        "summary": "ok",
                        "verified_facts": [],
                        "hypotheses": [],
                        "missing_evidence": [],
                        "suggested_next_steps": [],
                        "document": None,
                        "sources_used": [],
                        "evidence_sufficient": True,
                    }
                )
            )

    echo = Echo()
    monkeypatch.setattr(ai_provider, "get_provider", lambda config=None: echo)
    response = client.post(
        f"/api/v1/ai/investigations/{case['id']}/assist", json={"task": "SUMMARIZE"}
    )
    assert response.status_code == 200
    assert "duplicate_candidates" in echo.prompt and "TXN-" in echo.prompt
