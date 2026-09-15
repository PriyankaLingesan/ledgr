"""AI transaction assistant: natural language -> a reviewable, unposted proposal.

The model is asked for strict JSON in a fixed shape. Its output is parsed,
account codes are resolved against the real chart of accounts (an existing
service call), and the result is validated with the exact same
`TransactionCreate` schema the real posting endpoint uses - so "the AI thinks
this is balanced" and "the backend will accept this" are checked by the same
code, not two independent opinions that could disagree.

Nothing in this module imports `app.services.posting`. A proposal is data;
turning it into ledger entries is still, and only, `POST /transactions`.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import ValidationError

from app.core import money
from app.core.errors import AIResponseInvalid, AIUnavailable
from app.models.enums import EntryDirection
from app.schemas.ai import ProposedEntryOut, TransactionProposalOut
from app.schemas.common import Money
from app.schemas.transaction import EntryInput, TransactionCreate
from app.services import accounts as account_service
from app.services.ai import provider as ai_provider
from app.services.ai.provider import AIMessage

_SYSTEM_PROMPT = """You convert a plain-language description of a financial event into a \
double-entry bookkeeping proposal for LEDGR, a ledger system.

Rules:
- Respond with ONLY a single JSON object - no prose, no markdown fences.
- The object shape is exactly:
  {"description": string, "currency": ISO-4217 code, "entries": [
    {"account": string, "direction": "debit"|"credit", "amount": number}, ...
  ]}
- Every entry's "account" must be one of the account codes listed below - \
never invent a code.
- "amount" is a plain positive number in major units (e.g. 50000 for INR 50,000), \
the same value for every entry - double-entry means both sides move by the same amount.
- Include at least one debit entry and at least one credit entry.
- currency must be the ISO-4217 code implied by the description (e.g. Rs/₹ -> INR, \
$ -> USD); if genuinely ambiguous, use USD."""


class _AccountLike(Protocol):
    id: Any
    code: str
    name: str
    currency: str


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AIResponseInvalid(
            "the model's response was not valid JSON", details={"raw": text[:500]}
        ) from exc
    if not isinstance(parsed, dict):
        raise AIResponseInvalid("the model's response was not a JSON object")
    return parsed


def build_proposal(
    raw: dict[str, Any], account_index: dict[str, _AccountLike]
) -> TransactionProposalOut:
    """Pure transformation: model JSON + resolved accounts -> a validated proposal.

    Deliberately takes no database session - this is the function the AI
    transaction-proposal tests exercise directly, with fake account objects,
    so "does parsing behave correctly" never depends on a live model or a
    live database.
    """
    issues: list[str] = []
    description = str(raw.get("description") or "").strip() or "AI-suggested transaction"
    currency = str(raw.get("currency") or "").upper().strip()
    raw_entries = raw.get("entries")

    if not currency:
        issues.append("the model did not specify a currency")
    if not isinstance(raw_entries, list) or len(raw_entries) < 2:
        issues.append("the model did not return at least two entries")
        raw_entries = raw_entries if isinstance(raw_entries, list) else []

    entries_out: list[ProposedEntryOut] = []
    entry_inputs: list[EntryInput] = []

    for item in raw_entries:
        if not isinstance(item, dict):
            issues.append("one of the entries was not a valid object")
            continue

        code = str(item.get("account") or "").strip()
        direction_raw = str(item.get("direction") or "").strip().upper()
        account = account_index.get(code)

        if account is None:
            issues.append(f"account '{code}' does not exist in the chart of accounts")
            continue
        if direction_raw not in ("DEBIT", "CREDIT"):
            issues.append(f"entry for '{code}' has an invalid direction")
            continue
        if currency and account.currency != currency:
            issues.append(f"account '{code}' is denominated in {account.currency}, not {currency}")
            continue

        try:
            amount_minor = money.to_minor(item.get("amount"), currency or account.currency)
        except (ValueError, TypeError):
            issues.append(f"entry for '{code}' has an invalid amount")
            continue
        if amount_minor <= 0:
            issues.append(f"entry for '{code}' must have a positive amount")
            continue

        direction = EntryDirection.DEBIT if direction_raw == "DEBIT" else EntryDirection.CREDIT
        entries_out.append(
            ProposedEntryOut(
                account_id=account.id,
                account_code=account.code,
                account_name=account.name,
                direction=direction,
                amount=Money.of(amount_minor, currency or account.currency),
            )
        )
        entry_inputs.append(
            EntryInput(account_id=account.id, direction=direction, amount_minor=amount_minor)
        )

    post_body: dict[str, Any] | None = None
    if not issues and currency and len(entry_inputs) >= 2:
        try:
            # The exact schema `POST /transactions` will apply. If this
            # rejects the proposal, so would the real endpoint - better to
            # say so now than let the user discover it after clicking Post.
            validated = TransactionCreate(
                description=description, currency=currency, entries=entry_inputs
            )
            post_body = validated.model_dump(mode="json")
        except ValidationError as exc:
            issues.append("the proposed entries do not form a balanced, valid transaction")
            issues.extend(str(err["msg"]) for err in exc.errors()[:3])

    total_debits = sum(e.amount_minor for e in entry_inputs if e.direction is EntryDirection.DEBIT)
    total_credits = sum(
        e.amount_minor for e in entry_inputs if e.direction is EntryDirection.CREDIT
    )
    display_currency = currency or "USD"

    return TransactionProposalOut(
        valid=not issues and post_body is not None,
        issues=issues,
        description=description,
        currency=display_currency,
        entries=entries_out,
        total_debits=Money.of(total_debits, display_currency) if entries_out else None,
        total_credits=Money.of(total_credits, display_currency) if entries_out else None,
        post_body=post_body,
    )


def propose_transaction(db, description: str) -> TransactionProposalOut:
    """The live path: real accounts, real provider, then `build_proposal`."""
    provider = ai_provider.get_provider()
    if provider is None:
        raise AIUnavailable("AI features are unavailable because no AI provider is configured")

    items, _ = account_service.list_accounts(db, limit=200, offset=0)
    account_index: dict[str, _AccountLike] = {a.code: a for a in items}
    account_lines = "\n".join(f"- {a.code} ({a.type.value}, {a.currency}): {a.name}" for a in items)

    prompt = (
        f"Accounts available:\n{account_lines}\n\nDescribe this as a transaction:\n{description}"
    )
    completion = provider.complete(
        system=_SYSTEM_PROMPT,
        messages=[AIMessage(role="user", content=prompt)],
        max_tokens=600,
    )
    if not completion.text:
        raise AIResponseInvalid("the model returned no proposal")

    raw = _extract_json(completion.text)
    return build_proposal(raw, account_index)
