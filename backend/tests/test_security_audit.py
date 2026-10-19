"""Regression tests for the v1.0 security audit.

Each test pins a defect found while auditing the diff, or a property the audit
relied on and that nothing else asserted (every route authenticated, request
metrics bounded and tenant-neutral, AI endpoints metered).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import metrics
from app.core.config import settings
from app.main import app
from app.models.enums import UserRole
from tests.conftest import TEST_PASSWORD, make_organization

API = "/api/v1"

# The only endpoints reachable without credentials, and why.
PUBLIC_ROUTES = {
    ("GET", f"{API}/system/health"),  # container healthcheck
    ("GET", f"{API}/system/live"),  # liveness probe
    ("GET", f"{API}/system/ready"),  # readiness probe (no tenant data)
    ("GET", f"{API}/auth/config"),  # sign-in screen: is signup offered
    ("POST", f"{API}/auth/register"),  # public signup (rate limited)
    ("POST", f"{API}/auth/login"),  # sign in (rate limited, lockout)
}


def _concrete(path: str) -> str:
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


def _api_routes():
    """Every (method, path) the API publishes. Taken from the OpenAPI document
    rather than `app.routes`: this FastAPI nests included routers lazily, so
    walking `app.routes` finds almost nothing (and a sweep over it would pass
    vacuously)."""
    found = []
    for path, item in app.openapi()["paths"].items():
        for method in item:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                found.append((method.upper(), path))
    assert len(found) > 80, f"route discovery found only {len(found)} routes"
    return found


def test_every_api_route_requires_authentication_except_the_documented_public_ones():
    open_routes = []
    with TestClient(app, raise_server_exceptions=False) as anon:
        for method, path in sorted(_api_routes()):
            if (method, path) in PUBLIC_ROUTES:
                continue
            response = anon.request(method, _concrete(path))
            if response.status_code != 401:
                open_routes.append((method, path, response.status_code))
    assert open_routes == [], f"routes reachable without credentials: {open_routes}"


def test_the_public_route_allowlist_is_accurate():
    """Guards the allowlist itself: every entry must be a real route, so a
    renamed endpoint cannot silently leave a stale 'public' exemption behind."""
    existing = set(_api_routes())
    assert existing >= PUBLIC_ROUTES


def test_every_cookie_authenticated_write_demands_a_csrf_token(make_user):
    """Sweep: with a valid session cookie but no X-CSRF-Token, no state-changing
    route may succeed (they must all stop at 403 before doing anything)."""
    user = make_user(UserRole.OWNER)
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post(f"{API}/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert login.status_code == 200 and "Authorization" not in client.headers
    leaks = []
    for method, path in sorted(_api_routes()):
        if method in {"GET"} or (method, path) in PUBLIC_ROUTES:
            continue
        if path.endswith("/auth/logout"):
            continue  # also CSRF-protected; kept out only so the session survives the loop
        response = client.request(method, _concrete(path))
        if response.status_code != 403:
            leaks.append((method, path, response.status_code))
    assert leaks == [], f"cookie-authenticated writes accepted without CSRF: {leaks}"


# --------------------------------------------------------------------------
# request ids
# --------------------------------------------------------------------------


def test_an_oversized_client_request_id_cannot_break_a_write(client):
    """`X-Request-ID` is client supplied and stored in 64-character columns
    (audit events, transactions). It used to be echoed verbatim, so a long one
    made the audit insert fail and the write return 500."""
    response = client.post(
        f"{API}/accounts",
        headers={"X-Request-ID": "x" * 500},
        json={"code": "RID.USD", "name": "Rid", "type": "ASSET", "currency": "USD"},
    )
    assert response.status_code == 201, response.text
    assert len(response.headers["X-Request-ID"]) <= 64


@pytest.mark.parametrize(
    "hostile", ["id with spaces", "<script>alert(1)</script>", "a/b;c", "x" * 65]
)
def test_a_hostile_request_id_is_replaced_not_echoed(client, hostile):
    response = client.get(f"{API}/system/live", headers={"X-Request-ID": hostile})
    assert response.status_code == 200
    echoed = response.headers["X-Request-ID"]
    assert echoed != hostile
    assert echoed.replace("-", "").isalnum()


def test_a_well_formed_client_request_id_is_still_honoured(client):
    response = client.get(f"{API}/system/live", headers={"X-Request-ID": "trace-123.abc_DEF"})
    assert response.headers["X-Request-ID"] == "trace-123.abc_DEF"


# --------------------------------------------------------------------------
# metrics: bounded, and free of other tenants' identifiers
# --------------------------------------------------------------------------


def test_request_metrics_do_not_grow_with_the_number_of_distinct_urls(client):
    """The per-path error counter used to be keyed by raw URL, so anonymous
    requests for endless distinct 404 paths grew process memory without bound."""
    with TestClient(app, raise_server_exceptions=False) as anon:
        for i in range(metrics.MAX_TRACKED_PATHS + 50):
            anon.get(f"/no/such/route/{i}")
    assert metrics.tracked_path_count() <= metrics.MAX_TRACKED_PATHS + 1
    top = client.get(f"{API}/system/metrics").json()["top_error_paths"]
    assert all("/no/such/route/" not in row["path"] for row in top)


def test_metrics_never_contain_another_organizations_resource_ids(org_client, other_org):
    """Error paths are shared process-wide, so they must be route templates:
    a raw `/transactions/<uuid>` would hand one tenant another tenant's ids."""
    secret_id = uuid.uuid4()
    me = org_client(other_org)
    me.get(f"{API}/transactions/{secret_id}")  # 404 -> counted
    viewer_org = make_organization("Viewer Org")
    viewer = org_client(viewer_org)
    snapshot = viewer.get(f"{API}/system/metrics").text
    detail = viewer.get(f"{API}/system/health/detail").text
    assert str(secret_id) not in snapshot
    assert str(secret_id) not in detail
    # (top-N output may rank it out; the counter itself must hold the template)
    assert metrics.error_count_for("/api/v1/transactions/{transaction_id}") >= 1


# --------------------------------------------------------------------------
# AI endpoints
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/ai/ask", {"question": "how many transactions are there?"}),
        ("POST", "/ai/transactions/propose", {"description": "paid rent 100 USD"}),
        ("GET", f"/ai/transactions/{uuid.uuid4()}/explain", None),
        ("GET", "/ai/ledger-brief", None),
    ],
)
def test_every_metered_ai_endpoint_is_rate_limited_per_caller(
    client, monkeypatch, method, path, payload
):
    monkeypatch.setattr(settings, "ai_rate_limit_per_minute", 2)
    statuses = [client.request(method, f"{API}{path}", json=payload).status_code for _ in range(4)]
    assert 429 in statuses[2:], statuses
    assert 429 not in statuses[:2], statuses


def test_ledger_brief_rejects_absurd_windows(client):
    assert client.get(f"{API}/ai/ledger-brief?days=0").status_code == 422
    assert client.get(f"{API}/ai/ledger-brief?days=1000000000").status_code == 422
    assert client.get(f"{API}/ai/ledger-brief?days=7").status_code == 200


def test_ai_endpoints_cannot_move_money_even_if_a_model_asks_to(client, cash_and_revenue):
    """The propose endpoint returns a body for a human to submit; calling it
    must leave the ledger untouched."""
    before = client.get(f"{API}/transactions").json()["total"]
    client.post(f"{API}/ai/transactions/propose", json={"description": "pay 50 USD to rent"})
    assert client.get(f"{API}/transactions").json()["total"] == before


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_a_wildcard_cors_origin_is_refused_because_cookies_are_used():
    """`allow_credentials=True` with `*` would let any website call the API
    with the visitor's session cookie attached."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(cors_origins="*")
    with pytest.raises(ValidationError):
        Settings(cors_origins="http://localhost:8080, *")
    assert Settings(cors_origins="http://a.example,http://b.example").cors_origins == [
        "http://a.example",
        "http://b.example",
    ]


# --------------------------------------------------------------------------
# generic object-level isolation sweep
# --------------------------------------------------------------------------

_BODIES = {
    ("PATCH", "/accounts/{account_id}"): {"name": "hijacked"},
    ("POST", "/ai/investigations/{case_id}/assist"): {"task": "SUMMARIZE"},
    ("POST", "/investigations/{case_id}/notes"): {"body": "intruder note"},
    ("POST", "/investigations/{case_id}/resolve"): {"resolution_note": "intruder"},
    ("PATCH", "/investigations/{case_id}/status"): {"status": "RESOLVED"},
    ("POST", "/reconciliation/results/{result_id}/decisions"): {
        "decision": "NOT_A_MATCH",
        "reason": "intruder",
    },
    ("POST", "/reconciliation/results/{result_id}/investigate"): {},
    ("PUT", "/transaction-drafts/{draft_id}"): {
        "description": "x",
        "currency": "USD",
        "entries": [
            {"account_id": str(uuid.uuid4()), "direction": "DEBIT", "amount_minor": 1},
            {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 1},
        ],
    },
    ("POST", "/transaction-drafts/{draft_id}/reject"): {"note": "intruder"},
    ("POST", "/transactions/{transaction_id}/correct"): {
        "reason": "intruder",
        "entries": [
            {"account_id": str(uuid.uuid4()), "direction": "DEBIT", "amount_minor": 1},
            {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 1},
        ],
    },
    ("POST", "/transactions/{transaction_id}/simulate-correction"): {
        "description": "x",
        "currency": "USD",
        "entries": [
            {"account_id": str(uuid.uuid4()), "direction": "DEBIT", "amount_minor": 1},
            {"account_id": str(uuid.uuid4()), "direction": "CREDIT", "amount_minor": 1},
        ],
    },
    ("PATCH", "/users/{user_id}"): {"role": "AUDITOR"},
    ("POST", "/users/{user_id}/reset-password"): {"new_password": "intruder-password-1"},
}


class _ModelMustNotBeCalled:
    """A provider that fails the test if anything reaches it."""

    def __getattr__(self, name):
        raise AssertionError(f"the model was reached ({name}) for another organization's record")


def test_no_id_addressed_route_serves_or_changes_another_organizations_record(
    client, org_client, other_org, monkeypatch
):
    # AI "configured": the tenant check must still answer 404 *before* any model call.
    from app.services.ai import provider as ai_provider

    monkeypatch.setattr(ai_provider, "get_provider", lambda config=None: _ModelMustNotBeCalled())
    from tests.test_tenant_isolation import seed

    a = seed(client)
    entry_id = client.get(f"{API}/ledger/entries").json()["items"][0]["id"]
    user_a = client.post(
        f"{API}/users",
        json={
            "email": "victim@a.example",
            "full_name": "Victim",
            "password": "victim-password-1",
            "role": "FINANCE",
        },
    ).json()
    values = {
        "account_id": a["cash"]["id"],
        "entry_id": entry_id,
        "transaction_id": a["txn"]["id"],
        "reference": a["txn"]["reference"],
        "case_id": a["case"]["id"],
        "result_id": a["ghost"]["id"],
        "run_id": a["run"]["run"]["id"] if "run" in a["run"] else a["run"]["id"],
        "draft_id": a["draft"]["id"],
        "key_id": a["key"]["id"],
        "user_id": user_a["id"],
        "exercise_id": "ex1",
    }

    def snapshot():
        return (
            client.get(f"{API}/system/stats").json()["transaction_count"],
            client.get(f"{API}/investigations/{a['case']['id']}").json()["status"],
            client.get(f"{API}/accounts/{a['cash']['id']}").json()["name"],
            client.get(f"{API}/transaction-drafts/{a['draft']['id']}").json()["status"],
            [u["is_active"] for u in client.get(f"{API}/users").json()],
            [k["is_active"] for k in client.get(f"{API}/api-keys").json()],
            client.get(f"{API}/investigations/{a['case']['id']}/notes").json(),
            client.get(f"{API}/reconciliation/results/{a['ghost']['id']}/decisions").json(),
        )

    before = snapshot()
    intruder = org_client(other_org, UserRole.ADMIN, label="intruder")
    bad, outcomes = [], {}
    for method, path in _api_routes():
        if "{" not in path:
            continue
        template = path.removeprefix(API)
        concrete = path
        for name in {n for n in values if "{" + n + "}" in path}:
            concrete = concrete.replace("{" + name + "}", values[name])
        assert "{" not in concrete, f"no fixture value for a parameter in {path}"
        body_json = _BODIES.get((method, template))
        if body_json is None and method in {"POST", "PUT", "PATCH"}:
            body_json = {}
        if template.endswith("/balance-at"):
            concrete += "?at=2026-09-08T12:00:00Z"
        response = intruder.request(method, concrete, json=body_json)
        outcomes[(method, template)] = response.status_code
        # 404 = "no such record" (what another tenant's record must look like).
        # 403 is acceptable only where the role check itself fires first.
        if response.status_code not in (404, 403):
            bad.append((method, template, response.status_code, response.text[:120]))
    assert bad == [], f"cross-organization access not refused with 404/403: {bad}"
    assert sum(1 for s in outcomes.values() if s == 404) >= 30, outcomes
    assert snapshot() == before, "a cross-organization call changed the other tenant's data"


def test_incoherent_session_cookie_settings_are_refused_at_startup():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(session_cookie_samesite="none", session_cookie_secure=False)
    with pytest.raises(ValidationError):
        Settings(session_cookie_samesite="sometimes")
    assert Settings(session_cookie_samesite="none", session_cookie_secure=True)
    assert Settings(session_cookie_samesite="lax", session_cookie_secure=False)


# --------------------------------------------------------------------------
# AI: the complete set of service calls it may make
# --------------------------------------------------------------------------

# Every service function the AI package calls. All are reads, except
# `audit.record`, which only appends the "copilot was used" event. A new call
# fails this test until someone reviews it and adds it here on purpose.
_AI_ALLOWED_SERVICE_CALLS = {
    ("balances", "unbalanced_transaction_ids"),
    ("balances", "derive_for_account"),
    ("queries", "ledger_summary"),
    ("queries", "get_transaction"),
    ("queries", "get_transaction_by_reference"),
    ("queries", "list_transactions"),
    ("audit", "record"),
    ("casework", "audit_events_for"),
    ("casework", "list_decisions"),
    ("casework", "list_notes"),
    ("casework", "related_to_transaction"),
    ("casework", "suggest_matches"),
    ("investigation", "transaction_lineage"),
    ("investigation_cases", "case_evidence"),
    ("reconciliation", "get_result"),
    ("account_service", "get_account_by_code"),
    ("account_service", "list_accounts"),
}


def test_the_ai_package_calls_only_reviewed_read_only_service_functions():
    import ast
    from pathlib import Path

    service_modules = {
        "balances",
        "queries",
        "audit",
        "casework",
        "investigation",
        "investigation_cases",
        "reconciliation",
        "account_service",
        "posting",
        "drafts",
        "users",
        "accounts",
        "auth",
        "idempotency",
    }
    root = Path(__file__).resolve().parents[1] / "app" / "services" / "ai"
    used = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in service_modules
            ):
                used.add((node.value.id, node.attr))
    assert used, "the scan found no service calls - it is not looking at the right files"
    assert used <= _AI_ALLOWED_SERVICE_CALLS, sorted(used - _AI_ALLOWED_SERVICE_CALLS)
