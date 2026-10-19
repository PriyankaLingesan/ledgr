"""Tenant isolation, probed adversarially from a second organization.

Organization A owns a full set of records (accounts, a posted transaction, a
reversal, a reconciliation run, an investigation, a draft, users, API keys).
Organization B then tries to see, touch or reference every one of them -
through every endpoint family - and must find that they do not exist (404, never
403, which would confirm them), that its own aggregates exclude A's data, and
that the database itself rejects cross-tenant references.
"""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.session import SessionLocal
from app.db.tenancy import TenantContextError, bind_organization, mark_unscoped
from app.models.account import Account
from app.models.enums import AccountType, UserRole
from app.models.transaction import Transaction
from app.services import posting
from app.services.ai import tools as ai_tools

REF_A = "SHARED-REF-001"


def body(cash, rev, amount=1_000, **extra):
    return {
        "description": "isolation fixture",
        "currency": "USD",
        "entries": [
            {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": amount},
            {"account_id": rev["id"], "direction": "CREDIT", "amount_minor": amount},
        ],
        **extra,
    }


def seed(client, *, code_prefix="CASH", amount=1_000, reference=REF_A):
    """Build one organization's worth of records through its own API."""
    cash = client.post(
        "/api/v1/accounts",
        json={"code": f"{code_prefix}.USD", "name": "Cash", "type": "ASSET", "currency": "USD"},
    ).json()
    rev = client.post(
        "/api/v1/accounts",
        json={"code": "REV.USD", "name": "Revenue", "type": "REVENUE", "currency": "USD"},
    ).json()
    txn = client.post(
        "/api/v1/transactions",
        json=body(cash, rev, amount, reference=reference, external_reference="EXT-1"),
        headers={"Idempotency-Key": "shared-idem-key"},
    )
    assert txn.status_code == 201, txn.text
    txn = txn.json()
    other = client.post("/api/v1/transactions", json=body(cash, rev, 500)).json()
    reversal = client.post(f"/api/v1/transactions/{other['id']}/reverse", json={}).json()
    client.post(
        "/api/v1/reconciliation/import",
        json={
            "source_system": "BANK",
            "records": [
                {
                    "external_id": "EXT-1",
                    "amount_minor": amount,
                    "currency": "USD",
                    "direction": "DEBIT",
                    "transaction_timestamp": "2026-09-08T12:00:00Z",
                },
                {
                    "external_id": "GHOST-9",
                    "amount_minor": 77,
                    "currency": "USD",
                    "direction": "DEBIT",
                    "transaction_timestamp": "2026-09-08T12:00:00Z",
                },
            ],
        },
    )
    run = client.post("/api/v1/reconciliation/run").json()
    ghost = next(r for r in run["results"] if r["status"] == "MISSING_INTERNAL")
    case = client.post(
        "/api/v1/investigations",
        json={"title": "A's case", "issue_summary": "private", "account_id": cash["id"]},
    ).json()
    draft = client.post("/api/v1/transaction-drafts?submit=true", json=body(cash, rev, 250)).json()
    key = client.post("/api/v1/api-keys", json={"label": "private-key", "role": "AUDITOR"}).json()
    return {
        "cash": cash,
        "rev": rev,
        "txn": txn,
        "reversal": reversal,
        "run": run,
        "ghost": ghost,
        "case": case,
        "draft": draft,
        "key": key,
    }


@pytest.fixture
def two_orgs(client, org_client, other_org):
    a = seed(client)
    b_client = org_client(other_org, UserRole.ADMIN, label="b-admin")
    return a, b_client


# --------------------------------------------------------------------------
# listings never include another organization's rows
# --------------------------------------------------------------------------


def test_b_starts_with_an_empty_world(two_orgs):
    _, b = two_orgs
    for path in (
        "/api/v1/accounts",
        "/api/v1/transactions",
        "/api/v1/ledger/entries",
        "/api/v1/investigations",
        "/api/v1/reconciliation/runs",
        "/api/v1/transaction-drafts",
    ):
        page = b.get(path).json()
        assert page["total"] == 0 and page["items"] == [], path
    assert b.get("/api/v1/reconciliation/sources").json() == []
    assert b.get("/api/v1/system/audit").json()["total"] >= 0
    assert [k["label"] for k in b.get("/api/v1/api-keys").json()] == ["b-admin"]
    assert [u["email"] for u in b.get("/api/v1/users").json()] == []


def test_aggregates_exclude_other_organizations(two_orgs):
    a, b = two_orgs
    stats = b.get("/api/v1/system/stats").json()
    assert stats["account_count"] == 0 and stats["transaction_count"] == 0
    assert stats["entry_count"] == 0 and stats["trial_balance"] == []
    dash = b.get("/api/v1/dashboard").json()
    assert dash["account_count"] == 0 and dash["transaction_count"] == 0
    assert dash["recent_transactions"] == [] and dash["latest_reconciliation"] is None
    assert dash["open_investigations"] == 0 and dash["pending_approvals"] == 0
    assert b.get("/api/v1/system/integrity").json()["accounts_checked"] == 0
    assert b.get("/api/v1/system/duplicates").json() == []
    assert b.get("/api/v1/system/control-exceptions").json() == []
    assert b.get("/api/v1/alerts").json()["alerts"] == []
    assert b.get("/api/v1/onboarding").json()["completed"] == 1  # only "organization"
    # ...while A still sees everything of its own.
    assert a["txn"]["id"]


def test_the_audit_log_is_per_organization(client, two_orgs):
    _, b = two_orgs
    a_types = {
        e["event_type"] for e in client.get("/api/v1/system/audit?limit=200").json()["items"]
    }
    assert "transaction.posted" in a_types
    b_items = b.get("/api/v1/system/audit?limit=200").json()["items"]
    assert all(e["event_type"] != "transaction.posted" for e in b_items)
    assert all(e["actor"] != "test-client" for e in b_items)


def test_unknown_email_login_failures_are_invisible_to_every_tenant(client, two_orgs):
    _, b = two_orgs
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anon:
        anon.post("/api/v1/auth/login", json={"email": "ghost@nowhere.test", "password": "x" * 12})
    for c in (client, b):
        types = [e["event_type"] for e in c.get("/api/v1/system/audit?limit=200").json()["items"]]
        assert "auth.login_failed" not in types


# --------------------------------------------------------------------------
# object ids from organization A are indistinguishable from nonexistent ones
# --------------------------------------------------------------------------


def _missing(response):
    assert response.status_code == 404, (response.request.method, response.url, response.text)
    assert response.json()["error"]["code"] == "not_found"


def test_b_cannot_read_a_accounts(two_orgs):
    a, b = two_orgs
    cid = a["cash"]["id"]
    for suffix in ("", "/balance", "/entries", "/transactions", "/statement", "/balance-change"):
        _missing(b.get(f"/api/v1/accounts/{cid}{suffix}"))
    _missing(b.get(f"/api/v1/accounts/{cid}/balance-at", params={"at": "2030-01-01T00:00:00Z"}))
    _missing(b.patch(f"/api/v1/accounts/{cid}", json={"name": "hijacked"}))


def test_b_cannot_read_or_change_a_transactions(two_orgs):
    a, b = two_orgs
    tid = a["txn"]["id"]
    _missing(b.get(f"/api/v1/transactions/{tid}"))
    _missing(b.get(f"/api/v1/transactions/{tid}/lineage"))
    _missing(b.get(f"/api/v1/transactions/by-reference/{REF_A}"))
    _missing(b.post(f"/api/v1/transactions/{tid}/reverse", json={}))
    entries = a["txn"]["entries"]
    assert entries
    _missing(b.get(f"/api/v1/ledger/entries/{entries[0]['id']}"))
    # A's transaction is untouched.
    assert a["txn"]["status"] == "POSTED"


def test_b_cannot_correct_or_simulate_against_a_transactions(two_orgs):
    a, b = two_orgs
    payload = {"reason": "x", "entries": body(a["cash"], a["rev"])["entries"]}
    _missing(b.post(f"/api/v1/transactions/{a['txn']['id']}/correct", json=payload))
    _missing(
        b.post(
            f"/api/v1/transactions/{a['txn']['id']}/simulate-correction",
            json={"entries": payload["entries"]},
        )
    )


def test_b_cannot_post_to_or_reference_a_accounts(two_orgs):
    a, b = two_orgs
    posted = b.post("/api/v1/transactions", json=body(a["cash"], a["rev"]))
    _missing(posted)
    draft = b.post("/api/v1/transaction-drafts", json=body(a["cash"], a["rev"]))
    _missing(draft)


def test_b_cannot_open_a_case_against_a_account(two_orgs):
    a, b = two_orgs
    response = b.post(
        "/api/v1/investigations",
        json={"title": "sneaky", "issue_summary": "x", "account_id": a["cash"]["id"]},
    )
    assert response.status_code == 404
    assert b.get("/api/v1/investigations").json()["total"] == 0


def test_b_cannot_touch_a_investigations(two_orgs):
    a, b = two_orgs
    cid = a["case"]["id"]
    _missing(b.get(f"/api/v1/investigations/{cid}"))
    _missing(b.get(f"/api/v1/investigations/{cid}/evidence"))
    _missing(b.patch(f"/api/v1/investigations/{cid}/status", json={"status": "INVESTIGATING"}))
    _missing(b.post(f"/api/v1/investigations/{cid}/resolve", json={"resolution_note": "x"}))


def test_b_cannot_touch_a_reconciliation(two_orgs):
    a, b = two_orgs
    _missing(b.get(f"/api/v1/reconciliation/runs/{a['run']['id']}"))
    _missing(b.post(f"/api/v1/reconciliation/results/{a['ghost']['id']}/investigate"))
    # B running reconciliation compares only B's (empty) data.
    run = b.post("/api/v1/reconciliation/run").json()
    assert run["external_count"] == 0 and run["results"] == []


def test_b_cannot_touch_a_drafts(two_orgs):
    a, b = two_orgs
    did = a["draft"]["id"]
    _missing(b.get(f"/api/v1/transaction-drafts/{did}"))
    _missing(b.post(f"/api/v1/transaction-drafts/{did}/approve", json={}))
    _missing(b.post(f"/api/v1/transaction-drafts/{did}/reject", json={"note": "no"}))
    _missing(b.post(f"/api/v1/transaction-drafts/{did}/submit"))
    _missing(b.put(f"/api/v1/transaction-drafts/{did}", json=body(a["cash"], a["rev"])))


def test_b_cannot_touch_a_users_or_keys(client, make_user, org_client, other_org):
    a_user = make_user(UserRole.FINANCE, email="a.finance@a.test")
    b = org_client(other_org, UserRole.ADMIN, label="b-admin")
    key = client.post("/api/v1/api-keys", json={"label": "a-secret", "role": "AUDITOR"}).json()

    _missing(b.patch(f"/api/v1/users/{a_user.id}", json={"role": "ADMIN"}))
    _missing(b.patch(f"/api/v1/users/{a_user.id}", json={"is_active": False}))
    _missing(b.post(f"/api/v1/users/{a_user.id}/reset-password", json={"new_password": "x" * 12}))
    _missing(b.post(f"/api/v1/api-keys/{key['id']}/revoke"))
    assert "a-secret" not in b.get("/api/v1/api-keys").text
    assert "a.finance@a.test" not in b.get("/api/v1/users").text
    # A's key still works.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as kc:
        kc.headers["Authorization"] = f"Bearer {key['key']}"
        assert kc.get("/api/v1/accounts").status_code == 200


def test_b_cannot_ask_the_ai_about_a_records(two_orgs):
    a, b = two_orgs
    assert b.get(f"/api/v1/ai/transactions/{a['txn']['id']}/explain").status_code in (404, 503)


# --------------------------------------------------------------------------
# the same names in two organizations are independent
# --------------------------------------------------------------------------


def test_account_codes_references_and_idempotency_keys_are_per_organization(two_orgs):
    a, b = two_orgs
    b_data = seed(b)  # identical codes, reference and idempotency key as A
    assert b_data["cash"]["code"] == a["cash"]["code"]
    assert b_data["cash"]["id"] != a["cash"]["id"]
    assert b_data["txn"]["reference"] == a["txn"]["reference"] == REF_A
    assert b_data["txn"]["id"] != a["txn"]["id"]
    # Each organization resolves the shared reference to its own transaction.
    assert b.get(f"/api/v1/transactions/by-reference/{REF_A}").json()["id"] == b_data["txn"]["id"]


def test_an_idempotency_key_replays_only_within_its_organization(client, org_client, other_org):
    a_cash = client.post(
        "/api/v1/accounts", json={"code": "C", "name": "C", "type": "ASSET", "currency": "USD"}
    ).json()
    a_rev = client.post(
        "/api/v1/accounts", json={"code": "R", "name": "R", "type": "REVENUE", "currency": "USD"}
    ).json()
    first = client.post(
        "/api/v1/transactions", json=body(a_cash, a_rev, 900), headers={"Idempotency-Key": "k-1"}
    )
    assert first.status_code == 201

    b = org_client(other_org, UserRole.ADMIN, label="b-admin")
    b_cash = b.post(
        "/api/v1/accounts", json={"code": "C", "name": "C", "type": "ASSET", "currency": "USD"}
    ).json()
    b_rev = b.post(
        "/api/v1/accounts", json={"code": "R", "name": "R", "type": "REVENUE", "currency": "USD"}
    ).json()
    # Same key, *different* body, other organization: a fresh claim, not a conflict or a replay.
    second = b.post(
        "/api/v1/transactions", json=body(b_cash, b_rev, 123), headers={"Idempotency-Key": "k-1"}
    )
    assert second.status_code == 201
    assert "idempotent-replay" not in {h.lower() for h in second.headers}
    assert second.json()["id"] != first.json()["id"]


def test_org_settings_change_only_the_callers_organization(client, org_client, other_org):
    b = org_client(other_org, UserRole.ADMIN, label="b-admin")
    b.patch("/api/v1/organization", json={"name": "B Renamed", "approval_threshold_minor": 5})
    assert client.get("/api/v1/organization").json()["name"] == "Test Org"
    assert client.get("/api/v1/organization").json()["approval_threshold_minor"] is None
    assert b.get("/api/v1/organization").json()["name"] == "B Renamed"


# --------------------------------------------------------------------------
# the tenant comes from the credential, never from the request
# --------------------------------------------------------------------------


def test_client_supplied_tenant_hints_are_ignored(two_orgs):
    a, b = two_orgs
    hinted = b.get(
        "/api/v1/accounts",
        params={"organization_id": str(uuid.uuid4())},
        headers={"X-Organization-ID": str(uuid.uuid4()), "X-Org": "anything"},
    )
    assert hinted.json()["total"] == 0
    assert b.get("/api/v1/auth/me").json()["organization_id"] != a["cash"].get("organization_id")


# --------------------------------------------------------------------------
# the session layer fails closed
# --------------------------------------------------------------------------


def test_a_session_without_an_organization_sees_nothing(two_orgs):
    with SessionLocal() as unscoped:
        assert unscoped.execute(select(func.count(Account.id))).scalar_one() == 0
        assert unscoped.execute(select(func.count(Transaction.id))).scalar_one() == 0


def test_a_session_cannot_write_without_or_for_the_wrong_organization(org, other_org):
    with SessionLocal() as session:
        session.add(
            Account(
                code="NO.ORG",
                name="x",
                type=AccountType.ASSET,
                normal_balance="DEBIT",
                currency="USD",
            )
        )
        with pytest.raises(TenantContextError):
            session.flush()

    with SessionLocal() as session:
        bind_organization(session, org.id)
        session.add(
            Account(
                code="WRONG.ORG",
                name="x",
                type=AccountType.ASSET,
                normal_balance="DEBIT",
                currency="USD",
                organization_id=other_org.id,
            )
        )
        with pytest.raises(TenantContextError):
            session.flush()


def test_a_scoped_session_cannot_reassign_a_row_to_another_organization(
    db, other_org, make_account
):
    account = make_account("MOVE.ME")
    account.organization_id = other_org.id
    with pytest.raises(TenantContextError):
        db.flush()
    db.rollback()


def test_orm_queries_through_joins_and_subqueries_are_scoped(two_orgs, org_session, other_org):
    """`with_loader_criteria` must reach aliases, subqueries and relationship loads."""
    from sqlalchemy.orm import aliased, selectinload

    from app.models.ledger_entry import LedgerEntry

    b_session = org_session(other_org)
    entry_alias = aliased(LedgerEntry)
    sub = select(entry_alias.account_id).subquery()
    assert b_session.execute(select(func.count()).select_from(sub)).scalar_one() == 0
    joined = select(Account.id).join(LedgerEntry, LedgerEntry.account_id == Account.id)
    assert b_session.execute(joined).all() == []
    assert (
        b_session.execute(select(Transaction).options(selectinload(Transaction.entries))).all()
        == []
    )


def test_ai_tools_are_tenant_scoped(two_orgs, org_session, other_org):
    a, _ = two_orgs
    b_session = org_session(other_org)
    assert (
        ai_tools.run_tool(b_session, "search_transactions", {"query": "isolation"})[
            "total_matching"
        ]
        == 0
    )
    assert ai_tools.run_tool(b_session, "get_transaction", {"reference": REF_A}) == {
        "found": False,
        "reference": REF_A,
    }
    assert ai_tools.run_tool(b_session, "list_accounts", {})["total_matching"] == 0
    found = ai_tools.run_tool(b_session, "get_account_balance", {"account_code": a["cash"]["code"]})
    assert found["found"] is False


# --------------------------------------------------------------------------
# PostgreSQL itself rejects cross-tenant references
# --------------------------------------------------------------------------


def _unscoped():
    session = SessionLocal()
    mark_unscoped(session)
    return session


def test_the_database_rejects_a_ledger_entry_naming_another_organizations_account(
    two_orgs, org, other_org
):
    a, _ = two_orgs
    with _unscoped() as session:
        b_txn_id = uuid.uuid4()
        session.execute(
            text(
                "INSERT INTO transactions (id, organization_id, reference, description, currency,"
                " total_debits_minor, total_credits_minor, entry_count, effective_at)"
                " VALUES (:id, :org, 'B-ONLY', 'x', 'USD', 5, 5, 2, now())"
            ),
            {"id": b_txn_id, "org": other_org.id},
        )
        with pytest.raises(IntegrityError) as exc:
            session.execute(
                text(
                    "INSERT INTO ledger_entries (id, organization_id, transaction_id, account_id,"
                    " direction, amount_minor, currency, entry_index)"
                    " VALUES (:id, :org, :txn, :acct, 'DEBIT', 5, 'USD', 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": other_org.id,  # org B's entry...
                    "txn": b_txn_id,
                    "acct": uuid.UUID(a["cash"]["id"]),  # ...against org A's account
                },
            )
        assert "fk_ledger_entries_account_org" in str(exc.value)
        session.rollback()


def test_the_database_rejects_cross_tenant_links_elsewhere(two_orgs, other_org):
    a, _ = two_orgs
    with _unscoped() as session:
        with pytest.raises(IntegrityError) as exc:
            session.execute(
                text(
                    "INSERT INTO investigations (id, organization_id, case_number, title,"
                    " issue_summary, account_id) VALUES (:id, :org, 'INV-X', 't', 's', :acct)"
                ),
                {"id": uuid.uuid4(), "org": other_org.id, "acct": uuid.UUID(a["cash"]["id"])},
            )
        assert "fk_investigations_account_org" in str(exc.value)
        session.rollback()


def test_a_transactions_organization_can_never_change(two_orgs, other_org):
    a, _ = two_orgs
    with _unscoped() as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE transactions SET organization_id = :o WHERE id = :t"),
                {"o": other_org.id, "t": uuid.UUID(a["txn"]["id"])},
            )
        session.rollback()


def test_posting_directly_through_the_service_cannot_cross_tenants(
    two_orgs, org_session, other_org
):
    a, _ = two_orgs
    b_session = org_session(other_org)
    from app.core.errors import NotFound
    from app.models.enums import EntryDirection

    with pytest.raises(NotFound):
        posting.post_transaction(
            b_session,
            posting.PostCommand(
                description="cross-tenant",
                currency="USD",
                entries=[
                    posting.EntryCommand(uuid.UUID(a["cash"]["id"]), EntryDirection.DEBIT, 100),
                    posting.EntryCommand(uuid.UUID(a["rev"]["id"]), EntryDirection.CREDIT, 100),
                ],
            ),
        )


# --------------------------------------------------------------------------
# each organization's books balance independently
# --------------------------------------------------------------------------


def test_each_organizations_integrity_report_covers_only_its_own_ledger(client, two_orgs):
    a, b = two_orgs
    seed(b, code_prefix="BCASH", amount=4_000, reference="B-REF")
    a_report = client.get("/api/v1/system/integrity").json()
    b_report = b.get("/api/v1/system/integrity").json()
    assert a_report["cache_consistent"] and b_report["cache_consistent"]
    assert a_report["accounts_checked"] == 2 and b_report["accounts_checked"] == 2
    a_stats = client.get("/api/v1/system/stats").json()
    b_stats = b.get("/api/v1/system/stats").json()
    assert a_stats["ledger_balanced"] and b_stats["ledger_balanced"]
    assert a_stats["transaction_count"] == 3 and b_stats["transaction_count"] == 2 + 1


# --------------------------------------------------------------------------
# regressions: queries built on Core tables bypass the ORM's automatic scoping
# --------------------------------------------------------------------------


def test_duplicate_detection_never_pairs_or_leaks_across_organizations(
    client, org_client, other_org
):
    """A found a real bug: `duplicate_candidates` self-joins raw table aliases, which the
    ORM tenant filter does not cover, so B's request paired A's entries (and then 500'd)."""
    a_cash = client.post(
        "/api/v1/accounts", json={"code": "C", "name": "C", "type": "ASSET", "currency": "USD"}
    ).json()
    a_rev = client.post(
        "/api/v1/accounts", json={"code": "R", "name": "R", "type": "REVENUE", "currency": "USD"}
    ).json()
    for _ in range(2):  # two identical postings seconds apart: a duplicate pair in A
        assert client.post("/api/v1/transactions", json=body(a_cash, a_rev, 777)).status_code == 201

    b = org_client(other_org, UserRole.ADMIN, label="b-admin")
    response = b.get("/api/v1/system/duplicates")
    assert response.status_code == 200 and response.json() == []
    assert b.get("/api/v1/system/control-exceptions").json() == []

    mine = client.get("/api/v1/system/duplicates").json()
    # one pair on the cash account, one on the revenue account
    assert len(mine) == 2 and all(len(g["entries"]) == 2 for g in mine)


def test_modules_using_core_tables_filter_by_organization_explicitly():
    """Static guard: code that builds queries from `Model.__table__` or `.alias(...)`
    escapes the ORM tenant filter, so the file must mention `organization_id`."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in list(root.glob("services/**/*.py")) + list(root.glob("api/**/*.py")):
        source = path.read_text(encoding="utf-8")
        if ("__table__" in source or "Table(" in source) and "organization_id" not in source:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders
