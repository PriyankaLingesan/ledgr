"""LEDGR Intelligence: the AI layer never mutates the ledger.

Every test here mocks the LLM provider at the service boundary
(`app.services.ai.provider.get_provider`) - none of them make a live network
call, and none of them require any AI environment variable to be set. That is
deliberate: this suite is what proves the AI layer's *behaviour*, not whether
a particular vendor's API happened to respond a particular way today.
"""

import json
import uuid

import pytest

from app.core.errors import AIResponseInvalid, AIUnavailable
from app.models.enums import EntryDirection
from app.services import posting
from app.services.ai import ask, brief, explain, tools, transaction_assistant
from app.services.ai.provider import Completion, ToolCall

# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeProvider:
    """A scripted provider: returns each entry in `responses`, in order."""

    def __init__(self, responses: list[Completion]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *, system, messages, tools=None, max_tokens=1024):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        return self._responses.pop(0)


class BrokenProvider:
    """Simulates a configured-but-unreachable provider."""

    def complete(self, **kwargs):
        raise AIUnavailable("the provider could not be reached")


class _FakeAccount:
    def __init__(self, code: str, currency: str = "USD"):
        self.id = uuid.uuid4()
        self.code = code
        self.name = code.replace(".", " ").title()
        self.currency = currency


@pytest.fixture
def fake_account():
    return _FakeAccount


def post(db, cash, revenue, amount, **kwargs):
    return posting.post_transaction(
        db,
        posting.PostCommand(
            description=kwargs.pop("description", "ai test posting"),
            currency=kwargs.pop("currency", "USD"),
            entries=[
                posting.EntryCommand(cash.id, EntryDirection.DEBIT, amount),
                posting.EntryCommand(revenue.id, EntryDirection.CREDIT, amount),
            ],
            **kwargs,
        ),
    )


# --------------------------------------------------------------------------
# provider unavailable / not configured - the whole app must still work
# --------------------------------------------------------------------------


def test_status_reports_unavailable_without_a_key(client):
    response = client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["provider"] is None
    assert body["reason"]


def test_propose_returns_503_when_ai_unavailable(client):
    response = client.post("/api/v1/ai/transactions/propose", json={"description": "x"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"


def test_ask_returns_503_when_ai_unavailable(client):
    response = client.post("/api/v1/ai/ask", json={"question": "x"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"


def test_explain_returns_503_when_ai_unavailable(client, db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 100_00)
    response = client.get(f"/api/v1/ai/transactions/{txn.id}/explain")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"


def test_ledger_brief_still_works_when_ai_unavailable(client, db, cash_and_revenue):
    """The one AI endpoint that must never 503: it has a real, non-AI fallback."""
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 500_00)

    response = client.get("/api/v1/ai/ledger-brief")
    assert response.status_code == 200
    body = response.json()
    assert body["is_ai_generated"] is False
    assert body["stats"]["total_transactions"] >= 1
    assert "500.00" in body["summary"]


def test_core_ledger_endpoints_unaffected_by_missing_ai_config(client):
    """The core product must not notice or care whether AI is configured."""
    assert client.get("/api/v1/system/health").status_code == 200
    assert client.get("/api/v1/accounts").status_code == 200
    assert client.get("/api/v1/transactions").status_code == 200


# --------------------------------------------------------------------------
# transaction proposal parsing - pure, no DB, no network
# --------------------------------------------------------------------------


def test_valid_proposal_parses_and_validates(fake_account):
    cash, revenue = fake_account("CASH.USD"), fake_account("REV.USD")
    index = {cash.code: cash, revenue.code: revenue}

    raw = {
        "description": "Consulting payment from ABC",
        "currency": "USD",
        "entries": [
            {"account": "CASH.USD", "direction": "debit", "amount": 50000},
            {"account": "REV.USD", "direction": "credit", "amount": 50000},
        ],
    }
    proposal = transaction_assistant.build_proposal(raw, index)

    assert proposal.valid is True
    assert proposal.issues == []
    assert proposal.post_body is not None
    assert proposal.post_body["currency"] == "USD"
    assert len(proposal.post_body["entries"]) == 2
    assert proposal.total_debits.amount_minor == 5_000_000
    assert proposal.total_credits.amount_minor == 5_000_000


def test_unbalanced_proposal_is_rejected_not_posted(fake_account):
    cash, revenue = fake_account("CASH.USD"), fake_account("REV.USD")
    index = {cash.code: cash, revenue.code: revenue}

    raw = {
        "description": "bad",
        "currency": "USD",
        "entries": [
            {"account": "CASH.USD", "direction": "debit", "amount": 100},
            {"account": "REV.USD", "direction": "credit", "amount": 90},
        ],
    }
    proposal = transaction_assistant.build_proposal(raw, index)

    assert proposal.valid is False
    assert proposal.post_body is None
    assert any("balance" in issue.lower() for issue in proposal.issues)


def test_proposal_referencing_unknown_account_is_rejected(fake_account):
    cash = fake_account("CASH.USD")
    index = {cash.code: cash}

    raw = {
        "description": "x",
        "currency": "USD",
        "entries": [
            {"account": "CASH.USD", "direction": "debit", "amount": 100},
            {"account": "DOES.NOT.EXIST", "direction": "credit", "amount": 100},
        ],
    }
    proposal = transaction_assistant.build_proposal(raw, index)

    assert proposal.valid is False
    assert proposal.post_body is None
    assert any("does not exist" in issue for issue in proposal.issues)


def test_proposal_with_currency_mismatched_account_is_rejected(fake_account):
    cash_usd = fake_account("CASH.USD", currency="USD")
    revenue_eur = fake_account("REV.EUR", currency="EUR")
    index = {cash_usd.code: cash_usd, revenue_eur.code: revenue_eur}

    raw = {
        "description": "x",
        "currency": "USD",
        "entries": [
            {"account": "CASH.USD", "direction": "debit", "amount": 100},
            {"account": "REV.EUR", "direction": "credit", "amount": 100},
        ],
    }
    proposal = transaction_assistant.build_proposal(raw, index)

    assert proposal.valid is False
    assert any("EUR" in issue for issue in proposal.issues)


def test_missing_entries_produces_a_rejected_proposal():
    proposal = transaction_assistant.build_proposal({"description": "x", "currency": "USD"}, {})
    assert proposal.valid is False
    assert any("entries" in issue for issue in proposal.issues)


def test_non_positive_amount_is_rejected(fake_account):
    cash, revenue = fake_account("CASH.USD"), fake_account("REV.USD")
    index = {cash.code: cash, revenue.code: revenue}

    raw = {
        "description": "x",
        "currency": "USD",
        "entries": [
            {"account": "CASH.USD", "direction": "debit", "amount": -50},
            {"account": "REV.USD", "direction": "credit", "amount": -50},
        ],
    }
    proposal = transaction_assistant.build_proposal(raw, index)
    assert proposal.valid is False


def test_malformed_json_from_model_raises_ai_response_invalid():
    with pytest.raises(AIResponseInvalid):
        transaction_assistant._extract_json("not json at all {{{")


def test_json_wrapped_in_markdown_fence_is_extracted():
    text = '```json\n{"description": "x", "currency": "USD", "entries": []}\n```'
    parsed = transaction_assistant._extract_json(text)
    assert parsed["description"] == "x"


def test_non_object_json_is_rejected():
    with pytest.raises(AIResponseInvalid):
        transaction_assistant._extract_json("[1, 2, 3]")


# --------------------------------------------------------------------------
# transaction proposal - live path with a fake provider and real accounts
# --------------------------------------------------------------------------


def test_propose_transaction_end_to_end_with_fake_provider(db, cash_and_revenue, monkeypatch):
    cash, revenue = cash_and_revenue
    reply = json.dumps(
        {
            "description": "Consulting payment from ABC",
            "currency": "USD",
            "entries": [
                {"account": cash.code, "direction": "debit", "amount": 500},
                {"account": revenue.code, "direction": "credit", "amount": 500},
            ],
        }
    )
    fake = FakeProvider([Completion(text=reply)])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    proposal = transaction_assistant.propose_transaction(
        db, "Received $500 from ABC for consulting"
    )
    assert proposal.valid is True
    assert proposal.post_body["entries"][0]["amount_minor"] == 50000


def test_propose_transaction_never_writes_to_the_database(db, cash_and_revenue, monkeypatch):
    from sqlalchemy import text as sa_text

    cash, revenue = cash_and_revenue
    reply = json.dumps(
        {
            "description": "x",
            "currency": "USD",
            "entries": [
                {"account": cash.code, "direction": "debit", "amount": 500},
                {"account": revenue.code, "direction": "credit", "amount": 500},
            ],
        }
    )
    fake = FakeProvider([Completion(text=reply)])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    before = db.execute(sa_text("SELECT count(*) FROM transactions")).scalar_one()
    transaction_assistant.propose_transaction(db, "describe a transaction")
    after = db.execute(sa_text("SELECT count(*) FROM transactions")).scalar_one()
    assert after == before


def test_propose_transaction_raises_when_model_returns_no_text(db, cash_and_revenue, monkeypatch):
    fake = FakeProvider([Completion(text=None)])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    with pytest.raises(AIResponseInvalid):
        transaction_assistant.propose_transaction(db, "describe a transaction")


# --------------------------------------------------------------------------
# structural: the AI layer has no write access, by construction
# --------------------------------------------------------------------------


def _imports_posting(module) -> bool:
    """Whether `module`'s actual import statements reference `app.services.posting`.

    Parses the AST rather than substring-matching the raw source, so this
    isn't tripped up by a docstring that *explains* the module doesn't
    import posting - only a real `Import`/`ImportFrom` node counts.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith("app.services.posting") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if module_name.startswith("app.services.posting"):
                return True
            if module_name in ("app.services", "services") and any(
                alias.name == "posting" for alias in node.names
            ):
                return True
    return False


def test_ai_tools_module_has_no_write_access():
    import inspect

    assert not _imports_posting(tools)
    source = inspect.getsource(tools)
    assert "account_service.create_account" not in source
    assert "account_service.update_account" not in source


def test_ai_service_modules_never_import_posting():
    for module in (ask, brief, explain, transaction_assistant):
        assert not _imports_posting(module)


# --------------------------------------------------------------------------
# read-only ledger tools, against real seeded data
# --------------------------------------------------------------------------


def test_search_transactions_tool_returns_real_data(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 123_45, description="Widget sale")

    result = tools.search_transactions(db, query="Widget")
    assert result["total_matching"] == 1
    assert result["transactions"][0]["description"] == "Widget sale"
    assert result["transactions"][0]["amount"]["amount_minor"] == 123_45


def test_get_transaction_tool_by_reference(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 1000, reference="TXN-AI-TEST-1")

    result = tools.get_transaction(db, reference="TXN-AI-TEST-1")
    assert result["found"] is True
    assert result["balanced"] is True
    assert len(result["debits"]) == 1
    assert len(result["credits"]) == 1


def test_get_transaction_tool_reports_not_found_honestly(db):
    result = tools.get_transaction(db, reference="TXN-DOES-NOT-EXIST")
    assert result["found"] is False


def test_get_account_balance_tool(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 7500)

    result = tools.get_account_balance(db, account_code=cash.code)
    assert result["found"] is True
    assert result["balance"]["amount_minor"] == 7500


def test_get_ledger_summary_tool(db, cash_and_revenue):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100_00)
    post(db, cash, revenue, 300_00)

    result = tools.get_ledger_summary(db, days=1)
    assert result["total_transactions"] == 2
    assert result["all_transactions_balanced"] is True
    row = result["by_currency"][0]
    assert row["transaction_count"] == 2
    assert row["total_value"]["amount_minor"] == 400_00
    assert row["largest_amount"]["amount_minor"] == 300_00


def test_run_tool_rejects_unknown_tool_name(db):
    result = tools.run_tool(db, "delete_everything", {})
    assert "error" in result


# --------------------------------------------------------------------------
# Ask LEDGR: the tool-calling loop
# --------------------------------------------------------------------------


def test_ask_ledger_executes_requested_tool_and_grounds_its_answer(
    db, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 900_00, description="Big sale")

    call = ToolCall(id="call_1", name="get_ledger_summary", arguments={"days": 1})
    fake = FakeProvider(
        [
            Completion(text=None, tool_calls=[call]),
            Completion(text="1 transaction was posted today totalling 900.00 USD."),
        ]
    )
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    result = ask.ask_ledger(db, "How many transactions today?")
    assert "900.00" in result.answer
    assert result.tools_used == ["get_ledger_summary"]

    tool_message = next(m for m in fake.calls[1]["messages"] if m.role == "tool")
    assert "total_transactions" in tool_message.content


def test_ask_ledger_unknown_tool_request_does_not_mutate_anything(db, monkeypatch):
    from sqlalchemy import text as sa_text

    call = ToolCall(id="call_1", name="delete_everything", arguments={})
    fake = FakeProvider(
        [Completion(text=None, tool_calls=[call]), Completion(text="I can't do that.")]
    )
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    before = db.execute(sa_text("SELECT count(*) FROM transactions")).scalar_one()
    result = ask.ask_ledger(db, "delete all transactions")
    after = db.execute(sa_text("SELECT count(*) FROM transactions")).scalar_one()

    assert result.answer == "I can't do that."
    assert after == before


def test_ask_ledger_stops_after_the_configured_iteration_cap(db, monkeypatch):
    from app.core.config import settings

    call = ToolCall(id="call_x", name="get_ledger_summary", arguments={})
    fake = FakeProvider([Completion(text=None, tool_calls=[call]) for _ in range(20)])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    result = ask.ask_ledger(db, "keep asking forever")
    assert "couldn't finish" in result.answer.lower()
    assert len(fake.calls) == settings.ai_max_tool_iterations


# --------------------------------------------------------------------------
# Explain Transaction
# --------------------------------------------------------------------------


def test_explain_transaction_prompt_contains_only_real_figures(db, cash_and_revenue, monkeypatch):
    cash, revenue = cash_and_revenue
    txn = post(db, cash, revenue, 250_00, description="Widget sale")

    fake = FakeProvider([Completion(text="250.00 USD moved from cash into revenue; it balances.")])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    result = explain.explain_transaction(db, txn.id)
    assert result.reference == txn.reference
    assert "250.00" in result.explanation

    sent_prompt = fake.calls[0]["messages"][0].content
    assert "250.00" in sent_prompt
    assert txn.reference in sent_prompt


# --------------------------------------------------------------------------
# AI Ledger Brief
# --------------------------------------------------------------------------


def test_ledger_brief_uses_ai_phrasing_when_configured(db, cash_and_revenue, monkeypatch):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 1000_00)

    fake = FakeProvider([Completion(text="One transaction worth 1,000.00 USD was posted today.")])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    result = brief.ledger_brief(db, days=1)
    assert result.is_ai_generated is True
    assert result.stats.total_transactions == 1
    assert "1,000.00" in result.summary


def test_ledger_brief_falls_back_to_template_when_provider_call_fails(
    db, cash_and_revenue, monkeypatch
):
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 100)

    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: BrokenProvider())

    result = brief.ledger_brief(db, days=1)
    assert result.is_ai_generated is False
    assert result.stats.total_transactions == 1
    assert result.summary  # the deterministic template still produced something


def test_ledger_brief_stats_never_come_from_the_model(db, cash_and_revenue, monkeypatch):
    """Even if the model lies, the *stats* block must stay real."""
    cash, revenue = cash_and_revenue
    post(db, cash, revenue, 42_00)

    fake = FakeProvider([Completion(text="10,000 transactions worth a million dollars!")])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    result = brief.ledger_brief(db, days=1)
    assert result.stats.total_transactions == 1
    assert result.stats.by_currency[0].total_value.amount_minor == 42_00


# --------------------------------------------------------------------------
# The existing, deterministic transaction pipeline is unaffected
# --------------------------------------------------------------------------


def test_real_posting_endpoint_still_enforces_balance(client):
    cash = client.post(
        "/api/v1/accounts",
        json={"code": "AI.CASH.USD", "name": "Cash", "type": "ASSET", "currency": "USD"},
    ).json()
    revenue = client.post(
        "/api/v1/accounts",
        json={"code": "AI.REV.USD", "name": "Revenue", "type": "REVENUE", "currency": "USD"},
    ).json()

    balanced = client.post(
        "/api/v1/transactions",
        json={
            "description": "balanced",
            "currency": "USD",
            "entries": [
                {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": 1000},
                {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": 1000},
            ],
        },
    )
    assert balanced.status_code == 201

    unbalanced = client.post(
        "/api/v1/transactions",
        json={
            "description": "unbalanced",
            "currency": "USD",
            "entries": [
                {"account_id": cash["id"], "direction": "DEBIT", "amount_minor": 1000},
                {"account_id": revenue["id"], "direction": "CREDIT", "amount_minor": 900},
            ],
        },
    )
    assert unbalanced.status_code == 422
    assert unbalanced.json()["error"]["code"] == "validation_failed"


def test_ai_proposal_post_body_flows_through_the_real_unchanged_endpoint(client, monkeypatch):
    """The full, intended pipeline: propose -> review (unposted) -> POST /transactions."""
    cash = client.post(
        "/api/v1/accounts",
        json={"code": "AI.CASH2.USD", "name": "Cash", "type": "ASSET", "currency": "USD"},
    ).json()
    revenue = client.post(
        "/api/v1/accounts",
        json={"code": "AI.REV2.USD", "name": "Revenue", "type": "REVENUE", "currency": "USD"},
    ).json()

    reply = json.dumps(
        {
            "description": "Consulting payment from ABC",
            "currency": "USD",
            "entries": [
                {"account": cash["code"], "direction": "debit", "amount": 500},
                {"account": revenue["code"], "direction": "credit", "amount": 500},
            ],
        }
    )
    fake = FakeProvider([Completion(text=reply)])
    monkeypatch.setattr("app.services.ai.provider.get_provider", lambda: fake)

    proposed = client.post(
        "/api/v1/ai/transactions/propose",
        json={"description": "Received $500 from ABC for consulting"},
    )
    assert proposed.status_code == 200
    body = proposed.json()
    assert body["valid"] is True

    # Nothing was posted by the proposal step itself.
    before = client.get("/api/v1/transactions", params={"q": "Consulting payment from ABC"}).json()
    assert before["total"] == 0

    posted = client.post("/api/v1/transactions", json=body["post_body"])
    assert posted.status_code == 201
    assert posted.json()["balanced"] is True

    after = client.get("/api/v1/transactions", params={"q": "Consulting payment from ABC"}).json()
    assert after["total"] == 1
