"""Role-based authorization, enforced server-side.

Each role is exercised through the real HTTP API - both as a signed-in human
(session token) and as an API key - against endpoints that read, write and
administer. A 403 must come from the server regardless of what a frontend
chooses to show.
"""

import re
import uuid

import pytest
from sqlalchemy import select

from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.models.audit import AuditEvent
from app.models.enums import UserRole


def txn_payload(cash, revenue, amount=1_000):
    return {
        "description": "authorization test",
        "currency": "USD",
        "entries": [
            {"account_id": str(cash.id), "direction": "DEBIT", "amount_minor": amount},
            {"account_id": str(revenue.id), "direction": "CREDIT", "amount_minor": amount},
        ],
    }


ACCOUNT = {"code": "AUTHZ.NEW.USD", "name": "New", "type": "ASSET", "currency": "USD"}

READ_PATHS = [
    "/api/v1/accounts",
    "/api/v1/transactions",
    "/api/v1/ledger/entries",
    "/api/v1/investigations",
    "/api/v1/reconciliation/runs",
    "/api/v1/transaction-drafts",
    "/api/v1/system/audit",
    "/api/v1/system/stats",
    "/api/v1/system/health/detail",
    "/api/v1/dashboard",
    "/api/v1/alerts",
    "/api/v1/organization",
    "/api/v1/onboarding",
]
ADMIN_ONLY_PATHS = ["/api/v1/users", "/api/v1/api-keys"]


# --------------------------------------------------------------------------
# the matrix itself
# --------------------------------------------------------------------------


def test_auditor_holds_no_write_permission():
    writes = {
        p for p in Permission if not p.value.endswith(":read") and p not in (Permission.AUDIT_READ,)
    }
    assert not (ROLE_PERMISSIONS[UserRole.AUDITOR] & writes)


def test_only_admin_manages_users_keys_and_the_organization():
    admin_only = {Permission.USERS_MANAGE, Permission.API_KEYS_MANAGE, Permission.ORG_MANAGE}
    for role, perms in ROLE_PERMISSIONS.items():
        if role in (UserRole.OWNER, UserRole.ADMIN):
            assert admin_only <= perms
        else:
            assert not (admin_only & perms), role


def test_operations_cannot_post_or_approve_money_movement():
    perms = ROLE_PERMISSIONS[UserRole.OPERATIONS]
    assert Permission.TRANSACTIONS_POST not in perms
    assert Permission.DRAFTS_APPROVE not in perms
    assert Permission.DRAFTS_CREATE not in perms
    assert Permission.RECONCILIATION_RUN in perms
    assert Permission.INVESTIGATIONS_WRITE in perms


# --------------------------------------------------------------------------
# AUDITOR: read-only, over HTTP, as both a user and an API key
# --------------------------------------------------------------------------


@pytest.fixture(params=["user", "apikey"])
def make_client(request, user_client, role_client):
    """Run each scenario for a human session and for an API key."""

    def _make(role):
        return user_client(role) if request.param == "user" else role_client(role)

    return _make


def test_auditor_can_read_everything_an_auditor_should(make_client):
    auditor = make_client(UserRole.AUDITOR)
    for path in READ_PATHS:
        assert auditor.get(path).status_code == 200, path


def test_auditor_cannot_create_or_post_anything(make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    auditor = make_client(UserRole.AUDITOR)

    assert auditor.post("/api/v1/transactions", json=txn_payload(cash, revenue)).status_code == 403
    assert auditor.post("/api/v1/accounts", json=ACCOUNT).status_code == 403
    assert (
        auditor.post("/api/v1/transaction-drafts", json=txn_payload(cash, revenue)).status_code
        == 403
    )
    assert auditor.post("/api/v1/reconciliation/run").status_code == 403
    assert auditor.post("/api/v1/reconciliation/import-demo").status_code == 403
    assert (
        auditor.post(
            "/api/v1/investigations", json={"title": "t", "issue_summary": "s"}
        ).status_code
        == 403
    )
    assert auditor.post(f"/api/v1/transactions/{uuid.uuid4()}/reverse", json={}).status_code == 403
    assert (
        auditor.post(f"/api/v1/transaction-drafts/{uuid.uuid4()}/approve", json={}).status_code
        == 403
    )
    assert auditor.post("/api/v1/ai/ask", json={"question": "hi"}).status_code == 403
    for path in ADMIN_ONLY_PATHS:
        assert auditor.get(path).status_code == 403


def test_a_refused_posting_leaves_the_ledger_untouched(client, make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    auditor = make_client(UserRole.AUDITOR)
    auditor.post("/api/v1/transactions", json=txn_payload(cash, revenue))
    assert client.get("/api/v1/transactions").json()["total"] == 0
    assert client.get(f"/api/v1/accounts/{cash.id}/balance").json()["entry_count"] == 0


def test_permission_denials_are_audited(db, make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    auditor = make_client(UserRole.AUDITOR)
    auditor.post("/api/v1/transactions", json=txn_payload(cash, revenue))
    db.expire_all()
    denied = (
        db.execute(select(AuditEvent).where(AuditEvent.event_type == "auth.permission_denied"))
        .scalars()
        .all()
    )
    assert len(denied) == 1
    assert denied[0].payload["permission"] == "transactions:post"
    assert denied[0].payload["role"] == "AUDITOR"


def test_the_403_body_names_the_missing_permission(make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    body = (
        make_client(UserRole.AUDITOR)
        .post("/api/v1/transactions", json=txn_payload(cash, revenue))
        .json()
    )
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["details"]["required_permission"] == "transactions:post"


# --------------------------------------------------------------------------
# OPERATIONS
# --------------------------------------------------------------------------


def test_operations_can_investigate_and_reconcile_but_not_post(make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    ops = make_client(UserRole.OPERATIONS)

    for path in READ_PATHS:
        assert ops.get(path).status_code == 200, path

    assert ops.post("/api/v1/reconciliation/run").status_code == 201
    case = ops.post("/api/v1/investigations", json={"title": "look", "issue_summary": "closely"})
    assert case.status_code == 201

    assert ops.post("/api/v1/transactions", json=txn_payload(cash, revenue)).status_code == 403
    assert ops.post("/api/v1/accounts", json=ACCOUNT).status_code == 403
    assert (
        ops.post("/api/v1/transaction-drafts", json=txn_payload(cash, revenue)).status_code == 403
    )
    for path in ADMIN_ONLY_PATHS:
        assert ops.get(path).status_code == 403


# --------------------------------------------------------------------------
# FINANCE
# --------------------------------------------------------------------------


def test_finance_can_post_create_accounts_and_draft(make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    finance = make_client(UserRole.FINANCE)

    assert finance.post("/api/v1/accounts", json=ACCOUNT).status_code == 201
    posted = finance.post("/api/v1/transactions", json=txn_payload(cash, revenue))
    assert posted.status_code == 201
    assert (
        finance.post("/api/v1/transaction-drafts", json=txn_payload(cash, revenue)).status_code
        == 201
    )
    assert finance.post("/api/v1/reconciliation/run").status_code == 201


def test_finance_cannot_administer(make_client):
    finance = make_client(UserRole.FINANCE)
    for path in ADMIN_ONLY_PATHS:
        assert finance.get(path).status_code == 403
    assert finance.patch("/api/v1/organization", json={"name": "Hijacked"}).status_code == 403
    assert (
        finance.post("/api/v1/api-keys", json={"label": "sneaky", "role": "ADMIN"}).status_code
        == 403
    )


# --------------------------------------------------------------------------
# ADMIN, and unauthenticated access
# --------------------------------------------------------------------------


def test_admin_can_do_everything_above(make_client, cash_and_revenue):
    cash, revenue = cash_and_revenue
    admin = make_client(UserRole.ADMIN)
    for path in READ_PATHS + ADMIN_ONLY_PATHS:
        assert admin.get(path).status_code == 200, path
    assert admin.post("/api/v1/transactions", json=txn_payload(cash, revenue)).status_code == 201


def test_every_non_public_route_rejects_anonymous_callers():
    """Walks the real route table: any route that isn't explicitly public must
    answer 401 to an unauthenticated caller - so a new endpoint added without
    `require(...)` fails here."""
    from fastapi.testclient import TestClient

    from app.main import app

    public = {
        ("GET", "/api/v1/system/health"),
        ("GET", "/api/v1/system/live"),
        ("GET", "/api/v1/system/ready"),
        ("GET", "/api/v1/auth/config"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/login"),
    }
    schema = app.openapi()["paths"]
    with TestClient(app) as anon:
        checked = 0
        for path, operations in schema.items():
            if not path.startswith("/api/v1"):
                continue
            concrete = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
            for method in operations:
                if (method.upper(), path) in public:
                    continue
                response = anon.request(method.upper(), concrete)
                assert response.status_code == 401, (method, path, response.status_code)
                checked += 1
        assert checked > 40
