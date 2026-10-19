"""Maker-checker: DRAFT -> PENDING_APPROVAL -> POSTED / REJECTED.

The properties that matter: a draft moves no money; approval goes through the
existing posting engine (same balances, same audit trail, same invariants);
the maker can't approve their own draft; and a ledger refusal at approval
time leaves nothing posted.
"""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models.audit import AuditEvent
from app.models.draft import TransactionDraft
from app.models.enums import AccountType, UserRole
from app.models.transaction import Transaction


def payload(cash, revenue, amount=250_00, **extra):
    return {
        "description": "Customer invoice settlement",
        "currency": "USD",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": amount},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": amount},
        ],
        **extra,
    }


def balance(client, account):
    return client.get(f"/api/v1/accounts/{account.id}/balance").json()["balance"]["amount_minor"]


def transaction_count(db):
    db.expire_all()
    return db.execute(select(func.count(Transaction.id))).scalar_one()


def make_pending(maker, cash, revenue, **kwargs):
    response = maker.post(
        "/api/v1/transaction-drafts?submit=true", json=payload(cash, revenue, **kwargs)
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# a draft is not a financial record
# --------------------------------------------------------------------------


def test_a_draft_moves_no_money_and_posts_nothing(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")

    draft = maker.post("/api/v1/transaction-drafts", json=payload(cash, revenue)).json()
    assert draft["status"] == "DRAFT"
    assert draft["total"]["amount_minor"] == 250_00
    assert transaction_count(db) == 0
    assert balance(maker, cash) == 0

    submitted = maker.post(f"/api/v1/transaction-drafts/{draft['id']}/submit").json()
    assert submitted["status"] == "PENDING_APPROVAL"
    assert submitted["submitted_at"] is not None
    assert transaction_count(db) == 0
    assert balance(maker, cash) == 0


def test_an_unbalanced_draft_is_refused_up_front(user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE)
    bad = payload(cash, revenue)
    bad["entries"][1]["amount_minor"] = 1
    assert maker.post("/api/v1/transaction-drafts", json=bad).status_code == 422


def test_a_draft_naming_an_unknown_account_or_wrong_currency_is_refused(
    user_client, make_account, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    eur = make_account("CASH.EUR", AccountType.ASSET, currency="EUR")
    maker = user_client(UserRole.FINANCE)

    assert maker.post("/api/v1/transaction-drafts", json=payload(cash, eur)).status_code == 422

    ghost = payload(cash, revenue)
    ghost["entries"][0]["account_id"] = "00000000-0000-0000-0000-000000000001"
    assert maker.post("/api/v1/transaction-drafts", json=ghost).status_code == 404


def test_only_the_maker_can_edit_or_submit_a_draft(user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    other = user_client(UserRole.FINANCE, email="other@example.com")
    draft = maker.post("/api/v1/transaction-drafts", json=payload(cash, revenue)).json()

    assert (
        other.put(
            f"/api/v1/transaction-drafts/{draft['id']}", json=payload(cash, revenue)
        ).status_code
        == 403
    )
    assert other.post(f"/api/v1/transaction-drafts/{draft['id']}/submit").status_code == 403

    edited = maker.put(
        f"/api/v1/transaction-drafts/{draft['id']}", json=payload(cash, revenue, amount=300_00)
    )
    assert edited.status_code == 200
    assert edited.json()["total"]["amount_minor"] == 300_00


# --------------------------------------------------------------------------
# approval uses the existing posting engine
# --------------------------------------------------------------------------


def test_approval_posts_through_the_posting_engine(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = make_pending(maker, cash, revenue)

    approved = checker.post(
        f"/api/v1/transaction-drafts/{draft['id']}/approve", json={"note": "ok"}
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "POSTED"
    assert body["decided_by"] == "checker@example.com"
    assert body["posted_transaction_id"]

    # A real, balanced ledger transaction now exists, with real balances.
    txn = checker.get(f"/api/v1/transactions/{body['posted_transaction_id']}").json()
    assert txn["balanced"] is True
    assert txn["total_debits"]["amount_minor"] == 250_00
    assert txn["actor"] == "checker@example.com"
    assert txn["metadata"]["maker"] == "maker@example.com"
    assert txn["metadata"]["draft_id"] == draft["id"]
    assert balance(checker, cash) == 250_00
    assert balance(checker, revenue) == 250_00

    # ... and the same audit event the direct posting path writes.
    db.expire_all()
    posted_events = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "transaction.posted"))
        .scalars()
        .all()
    )
    assert len(posted_events) == 1
    assert posted_events[0].resource_id == txn["id"]
    approved_events = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "draft.approved"))
        .scalars()
        .all()
    )
    assert len(approved_events) == 1


def test_the_maker_cannot_approve_their_own_draft(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    draft = make_pending(maker, cash, revenue)

    response = maker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "self_approval_forbidden"
    assert transaction_count(db) == 0
    assert (
        maker.get(f"/api/v1/transaction-drafts/{draft['id']}").json()["status"]
        == "PENDING_APPROVAL"
    )

    blocked = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "draft.self_approval_blocked"))
        .scalars()
        .all()
    )
    assert len(blocked) == 1


def test_an_admin_who_made_a_draft_cannot_approve_it_either(user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    admin = user_client(UserRole.ADMIN, email="boss@example.com")
    draft = make_pending(admin, cash, revenue)
    response = admin.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert response.status_code == 403


def test_roles_without_approval_rights_cannot_approve(
    user_client, role_client, db, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    draft = make_pending(maker, cash, revenue)

    for role in (UserRole.OPERATIONS, UserRole.AUDITOR):
        outsider = role_client(role)
        response = outsider.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
        assert response.status_code == 403, role
    assert transaction_count(db) == 0


def test_a_draft_cannot_be_approved_twice(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = make_pending(maker, cash, revenue)

    assert (
        checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={}).status_code
        == 200
    )
    again = checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert again.status_code == 409
    assert transaction_count(db) == 1


def test_concurrent_approvals_post_exactly_once(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    draft = make_pending(maker, cash, revenue)

    token_headers = []
    for i in range(4):
        approver = user_client(UserRole.FINANCE, email=f"approver{i}@example.com")
        token_headers.append(approver.headers["Authorization"])

    results: list[int] = []
    lock = threading.Lock()

    def approve(auth):
        with TestClient(app) as c:
            c.headers["Authorization"] = auth
            r = c.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
            with lock:
                results.append(r.status_code)

    threads = [threading.Thread(target=approve, args=(h,)) for h in token_headers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409, 409, 409]
    assert transaction_count(db) == 1
    assert balance(maker, cash) == 250_00


# --------------------------------------------------------------------------
# rejection
# --------------------------------------------------------------------------


def test_rejection_requires_a_reason_and_posts_nothing(user_client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = make_pending(maker, cash, revenue)

    assert (
        checker.post(
            f"/api/v1/transaction-drafts/{draft['id']}/reject", json={"note": ""}
        ).status_code
        == 422
    )
    assert (
        checker.post(f"/api/v1/transaction-drafts/{draft['id']}/reject", json={}).status_code == 422
    )

    rejected = checker.post(
        f"/api/v1/transaction-drafts/{draft['id']}/reject", json={"note": "wrong account"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["decision_note"] == "wrong account"
    assert transaction_count(db) == 0

    # Terminal: it can't be approved afterwards.
    assert (
        checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={}).status_code
        == 409
    )


def test_a_draft_that_was_never_submitted_cannot_be_approved(user_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = maker.post("/api/v1/transaction-drafts", json=payload(cash, revenue)).json()
    assert (
        checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={}).status_code
        == 409
    )


# --------------------------------------------------------------------------
# the ledger's own rules still apply at approval time
# --------------------------------------------------------------------------


def test_a_ledger_refusal_at_approval_leaves_the_draft_pending_and_posts_nothing(
    user_client, db, make_account
):
    # `vault` forbids going negative; crediting it more than it holds is refused.
    vault = make_account("VAULT.USD", AccountType.ASSET, allows_negative_balance=False)
    source = make_account("SRC.USD", AccountType.ASSET)
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")

    body = {
        "description": "overdraw the vault",
        "currency": "USD",
        "entries": [
            {"account_id": str(source.id), "direction": "DEBIT", "amount_minor": 500},
            {"account_id": str(vault.id), "direction": "CREDIT", "amount_minor": 500},
        ],
    }
    draft = maker.post("/api/v1/transaction-drafts?submit=true", json=body).json()
    response = checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_funds"

    assert transaction_count(db) == 0
    current = maker.get(f"/api/v1/transaction-drafts/{draft['id']}").json()
    assert current["status"] == "PENDING_APPROVAL"
    assert "insufficient_funds" in current["last_error"]


def test_the_database_forbids_a_posted_draft_without_a_transaction(
    db, user_client, cash_and_revenue
):
    """Belt and braces: the CHECK constraint, not just the service, ties
    status POSTED to a real ledger transaction."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    cash, revenue = cash_and_revenue
    maker = user_client(UserRole.FINANCE)
    draft_id = maker.post("/api/v1/transaction-drafts", json=payload(cash, revenue)).json()["id"]

    draft = db.get(TransactionDraft, draft_id)
    draft.status = "POSTED"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --------------------------------------------------------------------------
# organization policy
# --------------------------------------------------------------------------


def test_transactions_at_the_threshold_must_go_through_approval(client, db, org, cash_and_revenue):
    cash, revenue = cash_and_revenue
    set_threshold = client.patch(
        "/api/v1/organization", json={"approval_threshold_minor": 1_000_00}
    )
    assert set_threshold.status_code == 200

    small = client.post("/api/v1/transactions", json=payload(cash, revenue, amount=999_99))
    assert small.status_code == 201

    large = client.post("/api/v1/transactions", json=payload(cash, revenue, amount=1_000_00))
    assert large.status_code == 409
    assert large.json()["error"]["code"] == "approval_required"
    assert large.json()["error"]["details"]["approval_threshold_minor"] == 1_000_00
    assert transaction_count(db) == 1  # only the small one

    # The policy can be lifted again.
    assert (
        client.patch("/api/v1/organization", json={"clear_approval_threshold": True}).json()[
            "approval_threshold_minor"
        ]
        is None
    )


def test_approved_drafts_bypass_the_direct_posting_threshold_by_design(
    client, user_client, org, cash_and_revenue
):
    cash, revenue = cash_and_revenue
    client.patch("/api/v1/organization", json={"approval_threshold_minor": 1_000_00})
    maker = user_client(UserRole.FINANCE, email="maker@example.com")
    checker = user_client(UserRole.FINANCE, email="checker@example.com")
    draft = make_pending(maker, cash, revenue, amount=5_000_00)
    approved = checker.post(f"/api/v1/transaction-drafts/{draft['id']}/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == "POSTED"


def test_corrections_cannot_bypass_the_approval_threshold(client, org, cash_and_revenue):
    """`/correct` posts a new transaction, so it is subject to the same
    maker-checker policy as a direct posting - otherwise it would be a way
    around the control."""
    cash, revenue = cash_and_revenue
    original = client.post(
        "/api/v1/transactions", json=payload(cash, revenue, amount=100_00)
    ).json()
    client.patch("/api/v1/organization", json={"approval_threshold_minor": 1_000_00})

    response = client.post(
        f"/api/v1/transactions/{original['id']}/correct",
        json={
            "reason": "fix the amount",
            "entries": payload(cash, revenue, amount=5_000_00)["entries"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_required"
    # Nothing happened: the original is still live and unreversed.
    assert client.get(f"/api/v1/transactions/{original['id']}").json()["status"] == "POSTED"
