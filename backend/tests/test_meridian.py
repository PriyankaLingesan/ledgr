"""Meridian Commerce Pvt. Ltd. - the fictional demonstration organization.

One module-scoped world (seeded once, ~20 s) is shared by every test here; the
last test works through the learning guide the way a person would, so it mutates
the world and must stay last. Everything goes through the real HTTP API and the
real services against PostgreSQL.
"""

import csv
import io
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.session import SessionLocal, session_scope
from app.demo import guide as guide_service
from app.demo import meridian_scenario as scn
from app.demo import meridian_seed as ms
from app.main import app
from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.enums import AccountType, EntryDirection
from app.models.transaction import Transaction
from app.services import accounts as account_service
from app.services import posting
from tests.conftest import make_organization, truncate_everything

pytestmark = pytest.mark.shared_world

AS_OF = date(2026, 10, 16)
PASSWORD = "meridian-test-only-pw"
API = "/api/v1"


def _fingerprint(org_id):
    with session_scope(org_id) as db:
        return (
            db.execute(select(func.count(Account.id))).scalar_one(),
            db.execute(select(func.count(Transaction.id))).scalar_one(),
            db.execute(select(func.count(AuditEvent.id))).scalar_one(),
        )


@pytest.fixture(scope="module")
def world():
    truncate_everything()
    # An unrelated organization with real data: the seed must never touch it.
    other = make_organization("Existing Co")
    with session_scope(other.id) as db:
        cash = account_service.create_account(
            db, code="CASH.USD", name="Cash", account_type=AccountType.ASSET, currency="USD"
        )
        rev = account_service.create_account(
            db, code="REV.USD", name="Revenue", account_type=AccountType.REVENUE, currency="USD"
        )
        posting.post_transaction(
            db,
            posting.PostCommand(
                description="existing sale",
                currency="USD",
                entries=[
                    posting.EntryCommand(cash.id, EntryDirection.DEBIT, 5_000),
                    posting.EntryCommand(rev.id, EntryDirection.CREDIT, 5_000),
                ],
                reference="EXISTING-1",
            ),
        )
    before = _fingerprint(other.id)
    report = ms.seed(AS_OF, log=lambda *_: None)
    ms.configure_users(PASSWORD)
    return SimpleNamespace(
        org_id=report.organization_id,
        report=report,
        sc=scn.build_scenario(AS_OF),
        other=other,
        other_before=before,
    )


@pytest.fixture
def people(world):
    opened: list[TestClient] = []

    def _client(key: str) -> TestClient:
        c = TestClient(app)
        r = c.post(
            f"{API}/auth/login", json={"email": scn.PERSONAS[key].email, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        opened.append(c)
        return c

    yield _client
    for c in opened:
        c.close()


def _codes(client):
    items = client.get(f"{API}/accounts", params={"limit": 200}).json()["items"]
    return {a["code"]: a["id"] for a in items}


# --------------------------------------------------------------------------
# the scenario itself
# --------------------------------------------------------------------------


def test_scenario_is_deterministic_and_internally_valid():
    a, b = scn.build_scenario(AS_OF), scn.build_scenario(AS_OF)

    def shape(sc):
        return [
            (
                p.ref,
                p.day,
                p.description,
                p.mode,
                [(leg.account, leg.side, leg.amount) for leg in p.legs],
            )
            if isinstance(p, scn.Posting)
            else (p.ref, p.original_ref, p.day, p.reason)
            for p in sc.postings
        ]

    assert shape(a) == shape(b)
    assert scn.statement_csv(a.bank_records) == scn.statement_csv(b.bank_records)
    assert a.facts == b.facts

    postings = [p for p in a.postings if isinstance(p, scn.Posting)]
    for p in postings:  # every posting is balanced double entry
        assert sum(x.amount for x in p.legs if x.side == "D") == sum(
            x.amount for x in p.legs if x.side == "C"
        )
        assert len({leg.account for leg in p.legs}) == len(p.legs), p.ref
    refs = [p.ref for p in a.postings]
    assert len(refs) == len(set(refs)), "references must be unique"

    reversals = [p for p in a.postings if isinstance(p, scn.Reversal)]
    posted = [p for p in postings if p.mode in ("direct", "draft")]
    assert 150 <= len(posted) + len(reversals) <= 300
    assert 40 <= len(a.bank_records) <= 80
    # Maker-checker policy is applied consistently by the seed, too.
    for p in posted:
        if p.story != "opening":
            assert (p.mode == "draft") == (p.total >= scn.APPROVAL_THRESHOLD_MINOR), p.ref
    # Accounts that forbid negative balances never go negative (the builder enforces it).
    for code, _n, _t, allow_negative, _d in scn.CHART_OF_ACCOUNTS:
        if not allow_negative:
            assert a.balances[code] >= 0, code

    # Moving the anchor date moves the calendar, never the logic.
    c = scn.build_scenario(date(2027, 3, 1))
    assert [(p.ref, getattr(p, "total", None)) for p in c.postings] == [
        (p.ref, getattr(p, "total", None)) for p in a.postings
    ]
    assert [r.amount for r in c.bank_records] == [r.amount for r in a.bank_records]


def test_personas_are_synthetic_and_cover_every_role():
    assert {p.role for p in scn.PERSONAS.values()} == {"OWNER", "FINANCE", "OPERATIONS", "AUDITOR"}
    assert all(p.email.endswith(".test") for p in scn.PERSONAS.values())  # reserved, undeliverable
    assert scn.PERSONAS["maker"].email != scn.PERSONAS["checker"].email


# --------------------------------------------------------------------------
# the seeded database
# --------------------------------------------------------------------------


def test_the_organization_is_marked_and_isolated_from_existing_data(world, people):
    owner = people("owner")
    me = owner.get(f"{API}/auth/me").json()
    assert me["organization_name"] == scn.ORG_NAME
    assert me["organization_demo_scenario"] == scn.SCENARIO_KEY
    assert owner.get(f"{API}/organization").json()["demo_scenario"] == scn.SCENARIO_KEY
    assert owner.get(f"{API}/organization").json()["base_currency"] == "INR"
    assert (
        owner.get(f"{API}/organization").json()["approval_threshold_minor"]
        == scn.APPROVAL_THRESHOLD_MINOR
    )
    # The unrelated organization is byte-for-byte untouched.
    assert _fingerprint(world.other.id) == world.other_before


def test_every_ledger_invariant_holds_for_the_seeded_books(world, people):
    owner = people("owner")
    with SessionLocal() as db:
        bad_debits = db.execute(
            text(
                "SELECT count(*) FROM transactions t WHERE t.organization_id = :o AND "
                "t.total_debits_minor <> (SELECT coalesce(sum(amount_minor),0) FROM ledger_entries e "
                "WHERE e.transaction_id = t.id AND e.direction = 'DEBIT')"
            ),
            {"o": world.org_id},
        ).scalar()
        bad_balance = db.execute(
            text(
                "SELECT count(*) FROM transactions t WHERE t.organization_id = :o AND "
                "t.total_debits_minor <> t.total_credits_minor"
            ),
            {"o": world.org_id},
        ).scalar()
        currencies = (
            db.execute(
                text("SELECT DISTINCT currency FROM ledger_entries WHERE organization_id = :o"),
                {"o": world.org_id},
            )
            .scalars()
            .all()
        )
        restricted_negative = (
            db.execute(
                text(
                    "SELECT a.code FROM accounts a JOIN ledger_entries e ON e.account_id = a.id "
                    "WHERE a.organization_id = :o AND a.allows_negative_balance = false "
                    "GROUP BY a.id, a.code, a.normal_balance "
                    "HAVING (CASE WHEN a.normal_balance = 'DEBIT' THEN 1 ELSE -1 END) * "
                    "sum(CASE WHEN e.direction = 'DEBIT' THEN e.amount_minor ELSE -e.amount_minor END) < 0"
                ),
                {"o": world.org_id},
            )
            .scalars()
            .all()
        )
        txn_count = db.execute(
            text("SELECT count(*) FROM transactions WHERE organization_id = :o"),
            {"o": world.org_id},
        ).scalar()
    assert bad_debits == 0 and bad_balance == 0
    assert currencies == ["INR"]
    assert restricted_negative == []
    assert 150 <= txn_count <= 300

    integrity = owner.get(f"{API}/system/integrity").json()
    assert integrity["cache_consistent"] and integrity["trial_balance_balanced"]
    assert integrity["unbalanced_transaction_ids"] == []
    checklist = owner.get(f"{API}/system/integrity-checklist").json()
    assert checklist["all_passed"], checklist
    stats = owner.get(f"{API}/system/stats").json()
    assert stats["ledger_balanced"] and stats["account_count"] == len(scn.CHART_OF_ACCOUNTS)


def test_the_opening_position_is_balanced_and_matches_the_design(world, people):
    owner = people("owner")
    opening = owner.get(f"{API}/transactions/by-reference/{world.sc.opening.ref}").json()
    entries = opening["entries"]
    assert len(entries) == 10
    debit = sum(e["amount"]["amount_minor"] for e in entries if e["direction"] == "DEBIT")
    credit = sum(e["amount"]["amount_minor"] for e in entries if e["direction"] == "CREDIT")
    assert debit == credit == scn.inr(22_625_000)
    by_code = {e["account"]["code"]: e["amount"]["amount_minor"] for e in entries}
    assert by_code["INV.STOCK"] == scn.inr(9_500_000)
    assert by_code["EQ.SHARE"] + by_code["EQ.RETAINED"] == scn.inr(10_225_000)


def test_balances_and_dashboard_are_derived_from_the_seeded_records(world, people):
    owner = people("owner")
    codes = _codes(owner)
    # Every account's balance, derived by the API from ledger entries, equals what the
    # scenario's own independent simulation computed from the *planned* postings: the two
    # pending/rejected drafts never touched the ledger, and every reversal mirrored exactly.
    for code, planned in world.sc.balances.items():
        reported = owner.get(f"{API}/accounts/{codes[code]}/balance").json()
        assert reported["signed_balance"]["amount_minor"] == planned, code
        assert reported["currency"] == "INR"

    dash = owner.get(f"{API}/dashboard").json()
    with SessionLocal() as db:
        n_txn = db.execute(
            text("SELECT count(*) FROM transactions WHERE organization_id = :o"),
            {"o": world.org_id},
        ).scalar()
        n_open = db.execute(
            text(
                "SELECT count(*) FROM investigations WHERE organization_id = :o AND status <> 'RESOLVED'"
            ),
            {"o": world.org_id},
        ).scalar()
        n_pending = db.execute(
            text(
                "SELECT count(*) FROM transaction_drafts WHERE organization_id = :o "
                "AND status = 'PENDING_APPROVAL'"
            ),
            {"o": world.org_id},
        ).scalar()
    assert dash["transaction_count"] == n_txn
    assert dash["account_count"] == len(scn.CHART_OF_ACCOUNTS)
    assert dash["open_investigations"] == n_open == 5
    assert dash["pending_approvals"] == n_pending == 1
    assert dash["ledger_balanced"] and dash["integrity_all_passed"]
    latest = dash["latest_reconciliation"]
    assert latest is not None and latest["external_count"] == len(world.sc.s1())
    # Posted volume is real: INR, and history older than a day exists.
    assert any(v["currency"] == "INR" for v in dash["posted_volume"])
    assert dash["transactions_last_24h"] < n_txn


def test_a_simulated_history_gives_filters_something_to_filter(world, people):
    owner = people("owner")
    first = owner.get(f"{API}/transactions", params={"limit": 5}).json()
    assert first["total"] >= 150
    # Date filters split the history rather than returning everything or nothing.
    mid = (AS_OF - timedelta(days=90)).isoformat() + "T00:00:00Z"
    older = owner.get(f"{API}/transactions", params={"posted_to": mid, "limit": 1}).json()["total"]
    newer = owner.get(f"{API}/transactions", params={"posted_from": mid, "limit": 1}).json()[
        "total"
    ]
    assert older > 20 and newer > 20 and older + newer == first["total"]
    # Text search finds a person-sized needle.
    hits = owner.get(f"{API}/transactions", params={"q": "Shakti Bearings", "limit": 100}).json()
    assert 2 <= hits["total"] <= 20
    # Pagination is stable.
    page1 = owner.get(f"{API}/transactions", params={"limit": 20, "offset": 0}).json()["items"]
    page2 = owner.get(f"{API}/transactions", params={"limit": 20, "offset": 20}).json()["items"]
    assert not {t["id"] for t in page1} & {t["id"] for t in page2}


def test_seed_is_idempotent_and_refuses_to_move_history(world):
    before = _fingerprint(world.org_id)
    report = ms.seed(AS_OF, log=lambda *_: None)
    assert report.created == {}
    assert _fingerprint(world.org_id) == before
    with pytest.raises(ms.SeedError):
        ms.seed(AS_OF + timedelta(days=1), log=lambda *_: None)
    assert _fingerprint(world.org_id) == before


# --------------------------------------------------------------------------
# reconciliation exceptions (the engine stays authoritative)
# --------------------------------------------------------------------------


def _first_run(client):
    runs = client.get(f"{API}/reconciliation/runs").json()["items"]
    return client.get(f"{API}/reconciliation/runs/{runs[-1]['id']}").json()


def _by_external(run):
    return {
        r["external_transaction"]["external_id"]: r
        for r in run["results"]
        if r["external_transaction"]
    }


def test_the_seeded_run_contains_every_designed_exception(world, people):
    ops = people("ops")
    run = _first_run(ops)
    f = world.sc.facts
    ext = _by_external(run)
    assert run["external_count"] == len(world.sc.s1())
    assert (
        run["mismatch_count"],
        run["missing_internal_count"],
        run["missing_external_count"],
    ) == (2, 4, 3)
    assert run["ambiguous_count"] == 1
    # 44 bank records: 7 are exceptions on the bank side (2 mismatches, 4 missing from the
    # ledger, 1 ambiguous); the other 3 exceptions are ledger entries with no bank record.
    assert run["matched_count"] == run["external_count"] - 7

    assert ext[f["fee_s1_ext"]]["status"] == "MISSING_INTERNAL"  # a bank fee nobody recorded
    assert ext[f["unapplied_ext"]]["status"] == "MISSING_INTERNAL"  # credit with no ledger entry
    assert ext[f["dupext_ext"]]["status"] == "MATCHED"  # first of two bank debits
    assert ext[f["dupext_twin_ext"]]["status"] == "MISSING_INTERNAL"  # the duplicate bank record
    assert ext[f["ambig_ext"]]["status"] == "AMBIGUOUS_INTERNAL_MATCH"
    assert ext[f["ambig_ext"]]["internal_transaction_id"] is None  # the engine never guesses
    tds = ext[f["tds_ext"]]
    assert tds["status"] == "AMOUNT_MISMATCH" and tds["difference"]["amount_minor"] == -scn.inr(
        10_000
    )
    disc = ext[f["discount_ext"]]
    assert disc["status"] == "AMOUNT_MISMATCH" and disc["difference"]["amount_minor"] == -scn.inr(
        5_000
    )
    internal_only = {
        r["internal_reference"]: r for r in run["results"] if r["status"] == "MISSING_EXTERNAL"
    }
    assert set(internal_only) == {f["refmis_receipt"], f["notcredited_receipt"], f["timing_cheque"]}
    assert all(
        ext[k]["status"] == "MATCHED"
        for k in list(ext)
        if k
        not in {
            f["fee_s1_ext"],
            f["unapplied_ext"],
            f["dupext_twin_ext"],
            f["ambig_ext"],
            f["tds_ext"],
            f["discount_ext"],
            f["refmis_bank_ext"],
        }
    )


def test_a_recorded_decision_annotates_but_never_changes_the_engines_verdict(world, people):
    ops = people("ops")
    f = world.sc.facts
    result = _by_external(_first_run(ops))[f["refmis_bank_ext"]]
    assert result["status"] == "MISSING_INTERNAL"  # still the engine's verdict
    decision = result["latest_decision"]
    assert decision["decision"] == "CONFIRMED_MATCH" and len(decision["reason"]) > 30
    assert decision["decided_by"] == scn.PERSONAS["ops"].email
    # Nothing was posted to "force" the reconciliation: no transaction carries the bank's id.
    assert ops.get(f"{API}/transactions", params={"q": f["refmis_bank_ext"]}).json()["total"] == 0
    # The ambiguous result has no decision at all - it needs a person.
    assert _by_external(_first_run(ops))[f["ambig_ext"]]["latest_decision"] is None


def test_unresolved_exceptions_have_no_silent_postings(world):
    f = world.sc.facts
    with session_scope(world.org_id) as db:
        for key in ("unapplied_ext", "dupext_twin_ext", "refmis_bank_ext", "fee_s2_ext"):
            n = db.execute(
                select(func.count(Transaction.id)).where(Transaction.external_reference == f[key])
            ).scalar_one()
            assert n == 0, key
        # The S1 fee *was* recorded - by a person, after the run, with the bank's reference.
        fix = db.execute(
            select(Transaction).where(Transaction.external_reference == f["fee_s1_ext"])
        ).scalar_one()
        assert fix.reference == f["fee_s1_fix"] and fix.actor == scn.PERSONAS["maker"].email


# --------------------------------------------------------------------------
# casework
# --------------------------------------------------------------------------


def test_cases_have_evidence_notes_and_honest_status(world, people):
    ops = people("ops")
    cases = ops.get(f"{API}/investigations", params={"limit": 50}).json()["items"]
    assert len(cases) == 8
    by_status = {}
    for c in cases:
        by_status.setdefault(c["status"], []).append(c)
    assert len(by_status["RESOLVED"]) == 3
    assert len(by_status["OPEN"]) + len(by_status["INVESTIGATING"]) == 5
    assert {c["priority"] for c in cases} >= {"LOW", "MEDIUM", "HIGH"}
    for c in cases:
        notes = ops.get(f"{API}/investigations/{c['id']}/notes").json()
        assert notes, c["case_number"]  # every case carries at least one reasoned note
        assert ops.get(f"{API}/investigations/{c['id']}/evidence").status_code == 200
        assert "Next action" in " ".join(n["body"] for n in notes) or c["status"] == "RESOLVED"
    for c in by_status["RESOLVED"]:
        assert c["resolution_note"] and len(c["resolution_note"]) > 30
    # The corrected receipt is linked as the resolution of the TDS case.
    tds = next(c for c in by_status["RESOLVED"] if "TDS" in c["title"])
    fixed = ops.get(f"{API}/transactions/by-reference/{world.sc.facts['tds_receipt_fixed']}").json()
    assert tds["correction_transaction_id"] == fixed["id"]
    # State machine: a resolved case cannot jump straight back to OPEN.
    resolved = by_status["RESOLVED"][0]
    r = ops.patch(f"{API}/investigations/{resolved['id']}/status", json={"status": "OPEN"})
    assert r.status_code == 422, r.text


def test_the_reversal_chain_is_traceable(world, people):
    owner = people("owner")
    f = world.sc.facts
    rv = owner.get(f"{API}/transactions/by-reference/{f['reversal_demo_ref']}").json()
    original = owner.get(f"{API}/transactions/by-reference/{f['reversal_demo_original']}").json()
    assert rv["kind"] == "REVERSAL" and original["status"] == "REVERSED"
    assert rv["reverses_transaction_id"] == original["id"]
    mirrored = {
        (e["account_id"], e["amount"]["amount_minor"]): e["direction"] for e in rv["entries"]
    }
    for e in original["entries"]:  # every entry reversed side-for-side; original entries untouched
        opposite = "CREDIT" if e["direction"] == "DEBIT" else "DEBIT"
        assert mirrored[(e["account_id"], e["amount"]["amount_minor"])] == opposite


# --------------------------------------------------------------------------
# approvals and roles
# --------------------------------------------------------------------------


def test_maker_checker_rules_hold_on_the_seeded_drafts(world, people):
    maker, checker, ops, auditor, owner = (
        people(k) for k in ("maker", "checker", "ops", "auditor", "owner")
    )
    pending = owner.get(f"{API}/transaction-drafts", params={"status": "PENDING_APPROVAL"}).json()[
        "items"
    ]
    assert len(pending) == 1 and pending[0]["created_by"] == scn.PERSONAS["maker"].email
    draft_id = pending[0]["id"]
    # The maker cannot approve her own proposal; an analyst and an auditor cannot approve at all.
    r = maker.post(f"{API}/transaction-drafts/{draft_id}/approve", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "self_approval_forbidden"
    assert ops.post(f"{API}/transaction-drafts/{draft_id}/approve", json={}).status_code == 403
    assert auditor.post(f"{API}/transaction-drafts/{draft_id}/approve", json={}).status_code == 403
    # Nothing moved.
    still = owner.get(f"{API}/transaction-drafts/{draft_id}").json()
    assert still["status"] == "PENDING_APPROVAL" and still["posted_transaction_id"] is None

    posted = owner.get(
        f"{API}/transaction-drafts", params={"status": "POSTED", "limit": 100}
    ).json()["items"]
    assert len(posted) == 15
    assert all(
        d["decided_by"] == scn.PERSONAS["checker"].email and d["decided_by"] != d["created_by"]
        for d in posted
    )
    rejected = owner.get(f"{API}/transaction-drafts", params={"status": "REJECTED"}).json()["items"]
    assert len(rejected) == 1 and "GST invoice" in rejected[0]["decision_note"]

    # Direct posting at/above the threshold is refused for a finance user: it needs a draft.
    codes = _codes(owner)
    big = {
        "description": "policy probe",
        "currency": "INR",
        "entries": [
            {
                "account_id": codes["INV.STOCK"],
                "direction": "DEBIT",
                "amount_minor": scn.APPROVAL_THRESHOLD_MINOR,
            },
            {
                "account_id": codes["AP.TRADE"],
                "direction": "CREDIT",
                "amount_minor": scn.APPROVAL_THRESHOLD_MINOR,
            },
        ],
    }
    r = maker.post(f"{API}/transactions", json=big)
    assert r.status_code == 409 and r.json()["error"]["code"] == "approval_required"
    # An auditor can read everything and write nothing.
    assert auditor.post(f"{API}/transactions", json=big).status_code == 403
    assert auditor.post(f"{API}/transaction-drafts", json=big).status_code == 403
    assert auditor.get(f"{API}/system/audit").status_code == 200


def test_another_organization_sees_none_of_meridian(world, people, org_client):
    owner = people("owner")
    stranger = org_client(world.other)
    txn = owner.get(f"{API}/transactions", params={"limit": 1}).json()["items"][0]
    run = _first_run(owner)
    case = owner.get(f"{API}/investigations", params={"limit": 1}).json()["items"][0]
    account = next(iter(_codes(owner).values()))
    for path in (
        f"/transactions/{txn['id']}",
        f"/reconciliation/runs/{run['id']}",
        f"/investigations/{case['id']}",
        f"/accounts/{account}",
        f"/transactions/by-reference/{world.sc.opening.ref}",
    ):
        assert stranger.get(f"{API}{path}").status_code == 404, path
    assert stranger.get(f"{API}/transactions").json()["total"] == 1  # only its own "existing sale"
    assert stranger.get(f"{API}/reconciliation/runs").json()["items"] == []
    assert stranger.get(f"{API}/demo/guide").status_code == 404  # it is not a demonstration
    assert stranger.get(f"{API}/demo/guide/sample-statement.csv").status_code == 404
    assert stranger.post(f"{API}/demo/guide/ex1/verify", json={"answer": "1"}).status_code == 404
    # ...and Meridian cannot see the stranger's records either.
    assert owner.get(f"{API}/transactions", params={"q": "existing sale"}).json()["total"] == 0


# --------------------------------------------------------------------------
# the guide: access and the sample statement
# --------------------------------------------------------------------------


def test_the_guide_is_gated_by_login_role_and_demo_status(world, people):
    anon = TestClient(app)
    assert anon.get(f"{API}/demo/guide").status_code == 401
    assert anon.get(f"{API}/demo/guide/sample-statement.csv").status_code == 401
    auditor = people("auditor")
    g = auditor.get(f"{API}/demo/guide").json()
    assert g["organization_name"] == scn.ORG_NAME and len(g["exercises"]) == 12
    assert g["completed_count"] == 0 and g["total_count"] == 12
    assert {p["key"] for p in g["personas"]} == set(scn.PERSONAS)
    assert all(p["active"] for p in g["personas"])
    # A read-only role may read the guide but cannot record progress.
    assert (
        auditor.post(f"{API}/demo/guide/ex1/verify", json={"answer": "9500000"}).status_code == 403
    )
    # No answers are ever included in the guide payload.
    blob = str(g)
    for secret in (world.sc.facts["receipt_sundaram"], world.sc.facts["reversal_demo_original"]):
        assert secret not in blob


def test_the_sample_statement_is_in_the_import_wizards_format(world, people):
    owner = people("owner")
    r = owner.get(f"{API}/demo/guide/sample-statement.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == [
        "external_id",
        "amount",
        "currency",
        "direction",
        "timestamp",
        "description",
    ]
    assert len(rows) == len(world.sc.s2()) and len({x["external_id"] for x in rows}) == len(rows)
    assert all(x["currency"] == "INR" and x["direction"] in ("credit", "debit") for x in rows)
    assert all(float(x["amount"]) > 0 for x in rows)
    # None of it is in the system yet: the learner imports it.
    existing = owner.get(f"{API}/reconciliation/sources").json()
    assert sum(s["record_count"] for s in existing) == len(world.sc.s1())


def test_the_exported_csv_equals_the_endpoint_payload(world, people):
    owner = people("owner")
    expected = scn.statement_csv(world.sc.s2())
    assert owner.get(f"{API}/demo/guide/sample-statement.csv").text == expected


# --------------------------------------------------------------------------
# the learning guide, worked through like a person would (MUTATES the world: keep last)
# --------------------------------------------------------------------------


def _import_payload(rows):
    return {
        "source_system": scn.STATEMENT_SOURCE,
        "records": [
            {
                "external_id": r["external_id"],
                "amount_minor": round(float(r["amount"]) * 100),
                "currency": r["currency"],
                "direction": r["direction"].upper(),
                "transaction_timestamp": r["timestamp"],
                "description": r["description"],
            }
            for r in rows
        ],
    }


def _verify(client, ex, answer=None):
    r = client.post(f"{API}/demo/guide/{ex}/verify", json={"answer": answer})
    assert r.status_code == 200, r.text
    return r.json()


def _completions(client):
    return client.get(
        f"{API}/system/audit", params={"event_type": "demo.exercise_completed", "limit": 100}
    ).json()["total"]


def test_the_learning_guide_flow_end_to_end(world, people):
    owner, maker, checker, ops = (people(k) for k in ("owner", "maker", "checker", "ops"))
    f = world.sc.facts
    codes = _codes(owner)

    guide = owner.get(f"{API}/demo/guide").json()
    assert guide["completed_count"] == 0 and guide["ai_available"] is False

    # --- nothing is "completed" by looking: every unfinished exercise fails with a reason ----------
    for ex in ("ex4", "ex5", "ex6", "ex7", "ex8", "ex9", "ex12"):
        v = _verify(owner, ex)
        assert v["passed"] is False and len(v["message"]) > 20, ex
    for ex, wrong in (
        ("ex1", "123"),
        ("ex2", "MCP-RC-9999"),
        ("ex3", "0"),
        ("ex10", "MCP-EX-0000"),
        ("ex11", "0"),
    ):
        assert _verify(owner, ex, wrong)["passed"] is False, ex
    assert _verify(owner, "ex1", "")["passed"] is False
    assert _completions(owner) == 0

    # --- 1: the opening position ------------------------------------------------------------
    assert _verify(owner, "ex1", "Rs 95,00,000")["passed"] is True
    # --- 2: find a receipt -------------------------------------------------------------------
    found = owner.get(f"{API}/transactions", params={"q": "Sundaram", "limit": 100}).json()["items"]
    assert f["receipt_sundaram"] in {t["reference"] for t in found}
    assert _verify(owner, "ex2", f["receipt_sundaram"].lower())["passed"] is True
    # --- 3: search and filter ------------------------------------------------------------------
    start = (
        datetime.combine(AS_OF - timedelta(days=60), time.min, tzinfo=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    end = datetime.combine(AS_OF, time.max, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    count = owner.get(
        f"{API}/transactions",
        params={
            "q": "SwiftHaul",
            "status": "POSTED",
            "posted_from": start,
            "posted_to": end,
            "limit": 1,
        },
    ).json()["total"]
    assert count >= 3
    assert _verify(owner, "ex3", str(count))["passed"] is True

    # --- 4: idempotent retry -------------------------------------------------------------------
    key = "learning-lab-0001"
    body = {
        "description": "Idempotency lab: petty cash top-up",
        "currency": "INR",
        "entries": [
            {"account_id": codes["CASH.PETTY"], "direction": "DEBIT", "amount_minor": 100_000},
            {"account_id": codes["BANK.DNB.OPS"], "direction": "CREDIT", "amount_minor": 100_000},
        ],
    }
    first = maker.post(f"{API}/transactions", json=body, headers={"Idempotency-Key": key})
    again = maker.post(f"{API}/transactions", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 201 and again.headers.get("Idempotent-Replay") == "true"
    assert again.json()["id"] == first.json()["id"]
    assert maker.get(f"{API}/transactions", params={"q": "Idempotency lab"}).json()["total"] == 1
    changed = {**body, "description": "a different request, same key"}
    assert (
        maker.post(
            f"{API}/transactions", json=changed, headers={"Idempotency-Key": key}
        ).status_code
        == 409
    )
    assert _verify(owner, "ex4")["passed"] is False  # the check is about YOUR retry...
    assert _verify(maker, "ex4")["passed"] is True  # ...and the maker did one

    # --- 5: import the statement, then reconcile -------------------------------------------------
    rows = list(
        csv.DictReader(io.StringIO(owner.get(f"{API}/demo/guide/sample-statement.csv").text))
    )
    partial = ops.post(f"{API}/reconciliation/import", json=_import_payload(rows[:5]))
    assert partial.status_code == 201 and partial.json()["imported"] == 5
    assert _verify(owner, "ex5")["passed"] is False  # five of the file is not the file
    done = ops.post(f"{API}/reconciliation/import", json=_import_payload(rows)).json()
    assert (
        done["imported"] == len(rows) - 5 and done["skipped_duplicates"] == 5
    )  # idempotent ingestion
    assert _verify(owner, "ex5")["passed"] is True
    run2 = ops.post(f"{API}/reconciliation/run").json()
    ext2 = _by_external(run2)
    s2_ids = {r["external_id"] for r in rows}
    exceptions_in_s2 = {k for k in s2_ids if ext2[k]["status"] != "MATCHED"}
    assert exceptions_in_s2 == {f["fee_s2_ext"]}  # only the unrecorded bank fee
    assert ext2["CHQ-004512"]["status"] == "MATCHED"  # the S1 timing difference has cleared
    assert ext2[f["fee_s1_ext"]]["status"] == "MATCHED"  # fixed by the casework posting
    assert ext2[f["tds_ext"]]["status"] == "MATCHED"  # corrected receipt now equals the bank credit

    # --- 6: record the unmatched bank fee ---------------------------------------------------------
    fee = {
        "description": "Bank charges - NEFT/RTGS and account maintenance incl. GST",
        "currency": "INR",
        "external_reference": f["fee_s2_ext"],
        "entries": [
            {"account_id": codes["EXP.BANKFEES"], "direction": "DEBIT", "amount_minor": 59_000},
            {"account_id": codes["BANK.DNB.OPS"], "direction": "CREDIT", "amount_minor": 59_000},
        ],
    }
    wrong = {
        **fee,
        "entries": [
            {"account_id": codes["EXP.MISC"], "direction": "DEBIT", "amount_minor": 59_000},
            {"account_id": codes["BANK.DNB.OPS"], "direction": "CREDIT", "amount_minor": 59_000},
        ],
    }
    assert (
        maker.post(
            f"{API}/transactions", json=wrong, headers={"Idempotency-Key": "fee-wrong"}
        ).status_code
        == 201
    )
    # A transaction carrying the reference but booked to the wrong account does not pass...
    assert _verify(owner, "ex6")["passed"] is False
    # ...so reverse it and record it properly: corrections are reversals, never edits.
    wrong_txn = maker.get(
        f"{API}/transactions", params={"q": "NEFT/RTGS and account", "limit": 5}
    ).json()["items"][0]
    assert (
        maker.post(
            f"{API}/transactions/{wrong_txn['id']}/reverse", json={"reason": "wrong expense head"}
        ).status_code
        == 201
    )
    assert (
        maker.post(
            f"{API}/transactions", json=fee, headers={"Idempotency-Key": "fee-right"}
        ).status_code
        == 201
    )
    assert _verify(owner, "ex6")["passed"] is True
    run3 = ops.post(f"{API}/reconciliation/run").json()
    assert _by_external(run3)[f["fee_s2_ext"]]["status"] == "MATCHED"

    # --- 7: resolve the ambiguous match with evidence ---------------------------------------------------
    amb = _by_external(run3)[f["ambig_ext"]]
    assert amb["status"] == "AMBIGUOUS_INTERNAL_MATCH"
    sugg = ops.get(f"{API}/reconciliation/results/{amb['id']}/suggestions").json()
    assert {s["reference"] for s in sugg} >= {
        f["ambig_original"],
        f["ambig_duplicate"],
    }
    dup = owner.get(f"{API}/transactions/by-reference/{f['ambig_duplicate']}").json()
    orig = owner.get(f"{API}/transactions/by-reference/{f['ambig_original']}").json()
    r = ops.post(
        f"{API}/reconciliation/results/{amb['id']}/decisions",
        json={
            "decision": "CONFIRMED_MATCH",
            "internal_transaction_id": dup["id"],
            "reason": "first guess",
        },
    )
    assert r.status_code == 201
    bad = _verify(owner, "ex7")
    assert bad["passed"] is False and "evidence" in bad["message"].lower()
    r = ops.post(
        f"{API}/reconciliation/results/{amb['id']}/decisions",
        json={
            "decision": "CONFIRMED_MATCH",
            "internal_transaction_id": orig["id"],
            "reason": "The second entry is described as re-keyed from the bank advice: a duplicate of the first.",
        },
    )
    assert r.status_code == 201
    assert _verify(owner, "ex7")["passed"] is True
    # The engine is still authoritative: the decision changed nothing about the verdict...
    assert (
        _by_external(ops.get(f"{API}/reconciliation/runs/{run3['id']}").json())[f["ambig_ext"]][
            "status"
        ]
        == "AMBIGUOUS_INTERNAL_MATCH"
    )
    # ...the books are fixed by reversing the duplicate, and the next run agrees.
    assert (
        maker.post(
            f"{API}/transactions/{dup['id']}/reverse", json={"reason": "duplicate entry"}
        ).status_code
        == 201
    )
    run4 = ops.post(f"{API}/reconciliation/run").json()
    assert _by_external(run4)[f["ambig_ext"]]["status"] == "MATCHED"

    # --- 8: open a case and resolve it with a reason ------------------------------------------------
    disc = _by_external(run4)[f["discount_ext"]]
    case = ops.post(f"{API}/reconciliation/results/{disc['id']}/investigate").json()
    assert _verify(owner, "ex8")["passed"] is False  # open, not resolved
    assert (
        ops.post(
            f"{API}/investigations/{case['id']}/notes",
            json={"body": "Asked Orient Polymers for their credit note."},
        ).status_code
        == 201
    )
    r = ops.post(f"{API}/investigations/{case['id']}/resolve", json={"resolution_note": "ok"})
    assert r.status_code == 200
    assert _verify(owner, "ex8")["passed"] is False  # resolved, but not with a reason
    assert (
        ops.patch(
            f"{API}/investigations/{case['id']}/status", json={"status": "INVESTIGATING"}
        ).status_code
        == 200
    )
    r = ops.post(
        f"{API}/investigations/{case['id']}/resolve",
        json={
            "resolution_note": "Supplier took a 1.5% prompt-payment discount (Rs 5,000); credit note requested and to be booked on receipt."
        },
    )
    assert r.status_code == 200
    assert _verify(owner, "ex8")["passed"] is True

    # --- 9: a sensitive operation through maker-checker -------------------------------------------------
    sensitive = {
        "description": "Stock purchase above the approval threshold",
        "currency": "INR",
        "entries": [
            {
                "account_id": codes["INV.STOCK"],
                "direction": "DEBIT",
                "amount_minor": scn.inr(1_200_000),
            },
            {
                "account_id": codes["AP.TRADE"],
                "direction": "CREDIT",
                "amount_minor": scn.inr(1_200_000),
            },
        ],
    }
    assert (
        maker.post(f"{API}/transactions", json=sensitive).json()["error"]["code"]
        == "approval_required"
    )
    draft = maker.post(f"{API}/transaction-drafts?submit=true", json=sensitive)
    assert draft.status_code == 201
    did = draft.json()["id"]
    assert _verify(owner, "ex9")["passed"] is False  # proposed is not approved
    assert maker.post(f"{API}/transaction-drafts/{did}/approve", json={}).status_code == 403
    assert ops.post(f"{API}/transaction-drafts/{did}/approve", json={}).status_code == 403
    approved = checker.post(
        f"{API}/transaction-drafts/{did}/approve", json={"note": "verified against the bill"}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "POSTED"
    assert _verify(owner, "ex9")["passed"] is True

    # --- 10: trace a reversal back to its original ----------------------------------------------------------
    assert _verify(owner, "ex10", f["reversal_demo_original"])["passed"] is True
    # --- 11: the integrity check ------------------------------------------------------------------------------
    checks = owner.get(f"{API}/system/integrity-checklist").json()["checks"]
    assert checks and all(c["passed"] for c in checks)
    assert _verify(owner, "ex11", str(sum(1 for c in checks if c["passed"])))["passed"] is True
    # --- 12: live AI is honestly reported as unavailable ----------------------------------------------------------
    v = _verify(owner, "ex12")
    assert v["passed"] is False and "not configured" in v["message"]

    # --- progress is per person, persisted in the audit log, and the books still balance -----------------------------
    done_owner = people("owner").get(f"{API}/demo/guide").json()
    assert {e["id"] for e in done_owner["exercises"] if e["completed"]} == {
        "ex1",
        "ex2",
        "ex3",
        "ex5",
        "ex6",
        "ex7",
        "ex8",
        "ex9",
        "ex10",
        "ex11",
    }
    assert done_owner["completed_count"] == 10
    assert maker.get(f"{API}/demo/guide").json()["completed_count"] == 1
    final = owner.get(f"{API}/system/integrity").json()
    assert final["cache_consistent"] and final["trial_balance_balanced"]
    assert owner.get(f"{API}/system/integrity-checklist").json()["all_passed"]
    # Re-verifying a completed exercise is harmless and records nothing twice.
    before = _completions(owner)
    assert _verify(owner, "ex1", "9500000")["passed"] is True
    assert _completions(owner) == before


def test_guide_service_rejects_unknown_exercises_and_oversized_answers(world, people):
    owner = people("owner")
    assert owner.post(f"{API}/demo/guide/ex99/verify", json={"answer": "x"}).status_code == 404
    assert owner.post(f"{API}/demo/guide/ex1/verify", json={"answer": "9" * 500}).status_code == 422
    assert [f"ex{n}" for n in range(1, 13)] == guide_service.EXERCISE_IDS
