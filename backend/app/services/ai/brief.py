"""AI Ledger Brief: real numbers, computed without an LLM; a sentence, phrased by one.

`_stats()` calls the same aggregate query `search_transactions`'s "largest
transactions" answer and "Ask LEDGR" both use - it is not new arithmetic
invented for this widget. The model, when available, only turns those exact
numbers into one readable paragraph; when it isn't available, a plain-Python
template does the same job, so the dashboard widget works either way.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.errors import AIUnavailable
from app.schemas.ai import CurrencyActivityOut, LedgerBriefOut, LedgerBriefStatsOut
from app.schemas.common import Money
from app.services import balances, queries
from app.services.ai import provider as ai_provider
from app.services.ai.provider import AIMessage

_SYSTEM_PROMPT = """Turn the following already-computed ledger statistics into one short, plain \
paragraph (two to three sentences) for a finance dashboard. Use only the numbers given - do not \
add, estimate, or infer anything beyond them."""


def _window_start(days: int) -> datetime:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=days - 1) if days > 1 else start


def compute_stats(db: Session, days: int = 1) -> LedgerBriefStatsOut:
    summary = queries.ledger_summary(db, posted_from=_window_start(days))
    unbalanced = balances.unbalanced_transaction_ids(db, limit=1)

    return LedgerBriefStatsOut(
        window_days=days,
        total_transactions=summary.transaction_count,
        all_transactions_balanced=len(unbalanced) == 0,
        by_currency=[
            CurrencyActivityOut(
                currency=row.currency,
                transaction_count=row.transaction_count,
                total_value=Money.of(row.total_minor, row.currency),
                largest_transaction_reference=row.largest_transaction_reference,
                largest_amount=Money.of(row.largest_amount_minor, row.currency),
            )
            for row in summary.by_currency
        ],
    )


def template_summary(stats: LedgerBriefStatsOut) -> str:
    """The deterministic fallback used whenever no AI provider is configured."""
    if stats.total_transactions == 0:
        return "No transactions were posted in this window."

    parts = []
    for row in stats.by_currency:
        plural = "s" if row.transaction_count != 1 else ""
        piece = (
            f"{row.transaction_count} transaction{plural} totalling "
            f"{row.total_value.amount} {row.currency}"
        )
        if row.largest_transaction_reference:
            piece += (
                f", the largest being {row.largest_amount.amount} {row.currency} "
                f"({row.largest_transaction_reference})"
            )
        parts.append(piece)

    balance_note = (
        "All posted transactions were balanced."
        if stats.all_transactions_balanced
        else "Some posted transactions did not balance - see the integrity report."
    )
    return "; ".join(parts) + ". " + balance_note


def ledger_brief(db: Session, days: int = 1) -> LedgerBriefOut:
    stats = compute_stats(db, days)
    provider = ai_provider.get_provider()

    if provider is not None:
        try:
            completion = provider.complete(
                system=_SYSTEM_PROMPT,
                messages=[AIMessage(role="user", content=stats.model_dump_json())],
                max_tokens=300,
            )
            text = (completion.text or "").strip()
            if text:
                return LedgerBriefOut(
                    generated_at=datetime.now(UTC), is_ai_generated=True, summary=text, stats=stats
                )
        except AIUnavailable:
            pass  # fall through to the deterministic template below

    return LedgerBriefOut(
        generated_at=datetime.now(UTC),
        is_ai_generated=False,
        summary=template_summary(stats),
        stats=stats,
    )
