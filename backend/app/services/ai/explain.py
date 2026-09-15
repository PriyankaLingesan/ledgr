"""Explain Transaction: plain-language narration of one real, posted transaction.

The prompt contains only fields read from the actual transaction - nothing is
invented, and the model is explicitly told to use only what's given.
"""

import uuid

from sqlalchemy.orm import Session

from app.core import money
from app.core.errors import AIUnavailable
from app.models.enums import EntryDirection
from app.schemas.ai import ExplainTransactionOut
from app.services import queries
from app.services.ai import provider as ai_provider
from app.services.ai.provider import AIMessage

_SYSTEM_PROMPT = """Explain this real, already-posted ledger transaction in two or three plain \
sentences for someone without accounting training. Use only the facts given below - do not add, \
guess, or infer anything not present. State amounts with their currency exactly as given, and \
confirm explicitly that debits equal credits."""


def explain_transaction(db: Session, transaction_id: uuid.UUID) -> ExplainTransactionOut:
    provider = ai_provider.get_provider()
    if provider is None:
        raise AIUnavailable("AI features are unavailable because no AI provider is configured")

    transaction = queries.get_transaction(db, transaction_id)

    lines = [
        f"Reference: {transaction.reference}",
        f"Description: {transaction.description}",
        f"Status: {transaction.status.value}",
        f"Kind: {transaction.kind.value}",
        f"Currency: {transaction.currency}",
    ]
    for entry in transaction.entries:
        amount = money.format_amount(entry.amount_minor, entry.currency)
        lines.append(
            f"{entry.direction.value}: {entry.account.name} ({entry.account.code}) "
            f"- {amount} {entry.currency}" + (f" [memo: {entry.memo}]" if entry.memo else "")
        )

    debits = sum(e.amount_minor for e in transaction.entries if e.direction is EntryDirection.DEBIT)
    credits = sum(
        e.amount_minor for e in transaction.entries if e.direction is EntryDirection.CREDIT
    )
    lines.append(
        f"Total debits: {money.format_amount(debits, transaction.currency)} {transaction.currency}"
    )
    credits_amount = money.format_amount(credits, transaction.currency)
    lines.append(f"Total credits: {credits_amount} {transaction.currency}")
    lines.append(f"Balanced: {debits == credits}")

    completion = provider.complete(
        system=_SYSTEM_PROMPT,
        messages=[AIMessage(role="user", content="\n".join(lines))],
        max_tokens=300,
    )
    explanation = (completion.text or "").strip() or (
        "This transaction could not be explained by the model, but its data is shown above."
    )

    return ExplainTransactionOut(
        transaction_id=transaction.id, reference=transaction.reference, explanation=explanation
    )
