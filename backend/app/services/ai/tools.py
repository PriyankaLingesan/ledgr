"""Read-only ledger tools exposed to the model.

This is the entire surface the model can touch. Every function here calls an
existing, already-tested service function and returns plain JSON-safe data -
none of them accept a database session from the model (it never sees one),
none of them accept raw SQL, and none of them import anything from
`app.services.posting`. If a future change ever adds a write here, it will
be adding an import this module's own docstring says shouldn't exist -
`grep -n "services.posting\\|services.accounts.update\\|services.accounts.create"
app/services/ai/tools.py` should always come back empty, and a test asserts
exactly that.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core import money
from app.core.errors import NotFound
from app.models.enums import AccountType, TransactionStatus
from app.schemas.serialize import oriented_balance
from app.services import accounts as account_service
from app.services import balances, queries
from app.services.ai.provider import ToolSpec

_MAX_RESULTS = 20


def _money(amount_minor: int, currency: str) -> dict[str, Any]:
    return {
        "amount_minor": amount_minor,
        "currency": currency,
        "amount": money.format_amount(amount_minor, currency),
    }


def _transaction_summary(transaction) -> dict[str, Any]:
    return {
        "reference": transaction.reference,
        "description": transaction.description,
        "status": transaction.status.value,
        "kind": transaction.kind.value,
        "amount": _money(transaction.total_debits_minor, transaction.currency),
        "posted_at": transaction.posted_at.isoformat(),
        "effective_at": transaction.effective_at.isoformat(),
    }


def search_transactions(
    db: Session,
    *,
    query: str | None = None,
    currency: str | None = None,
    status: str | None = None,
    days: int | None = None,
    order: str = "posted_desc",
    limit: int = 10,
) -> dict[str, Any]:
    """Find posted transactions by free text, currency, status, and/or recency."""
    posted_from = None
    if days is not None:
        posted_from = datetime.now(UTC) - timedelta(days=max(0, days))

    status_enum = None
    if status:
        with contextlib.suppress(ValueError):
            status_enum = TransactionStatus(status.upper())

    items, total = queries.list_transactions(
        db,
        query=query,
        status=status_enum,
        currency=currency,
        posted_from=posted_from,
        order="amount_desc" if order == "amount_desc" else "posted_desc",
        limit=min(limit, _MAX_RESULTS),
        offset=0,
    )
    return {"total_matching": total, "transactions": [_transaction_summary(t) for t in items]}


def get_transaction(db: Session, *, reference: str) -> dict[str, Any]:
    """Look up one transaction by its reference (e.g. 'TXN-20260910-6994CD4E26')."""
    try:
        transaction = queries.get_transaction_by_reference(db, reference)
    except NotFound:
        return {"found": False, "reference": reference}

    debits = [e for e in transaction.entries if e.direction.value == "DEBIT"]
    credits = [e for e in transaction.entries if e.direction.value == "CREDIT"]
    return {
        "found": True,
        **_transaction_summary(transaction),
        "balanced": sum(e.amount_minor for e in debits) == sum(e.amount_minor for e in credits),
        "debits": [
            {
                "account_code": e.account.code,
                "account_name": e.account.name,
                "amount": _money(e.amount_minor, e.currency),
            }
            for e in debits
        ],
        "credits": [
            {
                "account_code": e.account.code,
                "account_name": e.account.name,
                "amount": _money(e.amount_minor, e.currency),
            }
            for e in credits
        ],
        "reversed": transaction.status.value == "REVERSED",
        "is_reversal_of": transaction.reverses.reference if transaction.reverses else None,
        "reversed_by": transaction.reversed_by.reference if transaction.reversed_by else None,
    }


def list_accounts(
    db: Session, *, query: str | None = None, account_type: str | None = None
) -> dict[str, Any]:
    """List accounts, optionally filtered by name/code text or account type."""
    type_enum = None
    if account_type:
        with contextlib.suppress(ValueError):
            type_enum = AccountType(account_type.upper())

    items, total = account_service.list_accounts(
        db, query=query, account_type=type_enum, limit=_MAX_RESULTS, offset=0
    )
    return {
        "total_matching": total,
        "accounts": [
            {
                "code": a.code,
                "name": a.name,
                "type": a.type.value,
                "currency": a.currency,
                "status": a.status.value,
            }
            for a in items
        ],
    }


def get_account_balance(db: Session, *, account_code: str) -> dict[str, Any]:
    """The current derived balance of one account, by its code."""
    try:
        account = account_service.get_account_by_code(db, account_code)
    except NotFound:
        return {"found": False, "account_code": account_code}

    derived = balances.derive_for_account(db, account.id)
    balance_minor = oriented_balance(
        account.normal_balance, derived.debits_minor, derived.credits_minor
    )
    return {
        "found": True,
        "account_code": account.code,
        "account_name": account.name,
        "normal_balance": account.normal_balance.value,
        "balance": _money(balance_minor, account.currency),
        "entry_count": derived.entry_count,
    }


def get_ledger_summary(db: Session, *, days: int | None = None) -> dict[str, Any]:
    """Posting activity over a window: count, total value, and largest transaction, per currency."""
    posted_from = None
    if days is not None:
        posted_from = (datetime.now(UTC) - timedelta(days=max(0, days))).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    summary = queries.ledger_summary(db, posted_from=posted_from)
    unbalanced = balances.unbalanced_transaction_ids(db, limit=1)

    return {
        "window_days": days,
        "total_transactions": summary.transaction_count,
        "all_transactions_balanced": len(unbalanced) == 0,
        "by_currency": [
            {
                "currency": row.currency,
                "transaction_count": row.transaction_count,
                "total_value": _money(row.total_minor, row.currency),
                "largest_transaction_reference": row.largest_transaction_reference,
                "largest_amount": _money(row.largest_amount_minor, row.currency),
            }
            for row in summary.by_currency
        ],
    }


# --------------------------------------------------------------------------
# Registry: what the model is told exists, and what actually runs.
# --------------------------------------------------------------------------

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="search_transactions",
        description=(
            "Search posted transactions by free text (matches description, reference, or "
            "external reference), currency, status, and/or how many days back to look. "
            "Set order='amount_desc' to find the largest transactions instead of the most recent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text to search for"},
                "currency": {"type": "string", "description": "ISO currency code, e.g. USD"},
                "status": {"type": "string", "enum": ["POSTED", "REVERSED"]},
                "days": {
                    "type": "integer",
                    "description": "Only transactions posted in the last N days",
                },
                "order": {"type": "string", "enum": ["posted_desc", "amount_desc"]},
                "limit": {"type": "integer", "description": "Max results, up to 20"},
            },
        },
    ),
    ToolSpec(
        name="get_transaction",
        description=(
            "Look up one transaction by its exact reference, with its full debit/credit entries."
        ),
        parameters={
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"],
        },
    ),
    ToolSpec(
        name="list_accounts",
        description=(
            "List accounts in the chart of accounts, optionally filtered by "
            "name/code text or account type."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "account_type": {
                    "type": "string",
                    "enum": ["ASSET", "LIABILITY", "EQUITY", "REVENUE", "EXPENSE"],
                },
            },
        },
    ),
    ToolSpec(
        name="get_account_balance",
        description=(
            "The current derived balance of one account, looked up by its code "
            "(e.g. 'CASH.OPERATING.USD')."
        ),
        parameters={
            "type": "object",
            "properties": {"account_code": {"type": "string"}},
            "required": ["account_code"],
        },
    ),
    ToolSpec(
        name="get_ledger_summary",
        description=(
            "Aggregate posting activity - transaction count, total value, largest transaction, "
            "per currency - over the last N days. Omit `days` for all-time."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "e.g. 1 for today, 7 for this week"}
            },
        },
    ),
]

_HANDLERS = {
    "search_transactions": search_transactions,
    "get_transaction": get_transaction,
    "list_accounts": list_accounts,
    "get_account_balance": get_account_balance,
    "get_ledger_summary": get_ledger_summary,
}


def run_tool(db: Session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one model-requested tool call to its (read-only) handler.

    `name` is checked against a fixed dict built at import time - there is no
    path from a model's output to an arbitrary Python callable, let alone SQL.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        return handler(db, **arguments)
    except TypeError as exc:
        return {"error": f"invalid arguments for '{name}': {exc}"}


__all__ = ["TOOL_SPECS", "run_tool"]
