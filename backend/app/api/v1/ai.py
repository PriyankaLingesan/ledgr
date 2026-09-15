"""LEDGR Intelligence endpoints.

None of these can post a transaction, edit an account, or write a ledger
entry - every route here either reads (via `app.services.ai.tools`, itself
read-only) or returns a *proposal* the frontend must still submit to the
existing `POST /transactions` for it to become real. If a route in this file
ever imports `app.services.posting`, that is a bug this file's own docstring
says shouldn't exist.
"""

import uuid

from fastapi import APIRouter

from app.api.deps import DbSession
from app.core.config import settings
from app.schemas.ai import (
    AIStatusOut,
    AskLedgerOut,
    AskLedgerRequest,
    ExplainTransactionOut,
    LedgerBriefOut,
    TransactionAssistRequest,
    TransactionProposalOut,
)
from app.services.ai import ask as ask_service
from app.services.ai import brief as brief_service
from app.services.ai import explain as explain_service
from app.services.ai import transaction_assistant
from app.services.ai.provider import get_provider

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status", response_model=AIStatusOut)
def ai_status() -> AIStatusOut:
    """Whether the frontend should offer AI features at all right now."""
    if get_provider() is not None:
        return AIStatusOut(available=True, provider=settings.ai_provider)
    return AIStatusOut(
        available=False,
        provider=None,
        reason="AI features are unavailable because no AI provider is configured.",
    )


@router.post("/transactions/propose", response_model=TransactionProposalOut)
def propose_transaction(payload: TransactionAssistRequest, db: DbSession) -> TransactionProposalOut:
    """A draft only. Posting still requires `POST /transactions` with `post_body`."""
    return transaction_assistant.propose_transaction(db, payload.description)


@router.post("/ask", response_model=AskLedgerOut)
def ask_ledger(payload: AskLedgerRequest, db: DbSession) -> AskLedgerOut:
    return ask_service.ask_ledger(db, payload.question)


@router.get("/transactions/{transaction_id}/explain", response_model=ExplainTransactionOut)
def explain_transaction(transaction_id: uuid.UUID, db: DbSession) -> ExplainTransactionOut:
    return explain_service.explain_transaction(db, transaction_id)


@router.get("/ledger-brief", response_model=LedgerBriefOut)
def ledger_brief(db: DbSession, days: int = 1) -> LedgerBriefOut:
    """Real facts always; AI phrasing only when a provider is configured.

    Unlike the other AI routes, this one never 503s - `brief_service` falls
    back to a deterministic template sentence when AI is unavailable, so the
    dashboard widget always has something true to show.
    """
    return brief_service.ledger_brief(db, days=days)
