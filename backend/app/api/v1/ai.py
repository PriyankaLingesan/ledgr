"""LEDGR Intelligence endpoints.

None of these can post a transaction, edit an account, or write a ledger
entry - every route here either reads (via `app.services.ai.tools`, itself
read-only) or returns a *proposal* the frontend must still submit to the
existing `POST /transactions` for it to become real. If a route in this file
ever imports `app.services.posting`, that is a bug this file's own docstring
says shouldn't exist.
"""

import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    RequestId,
    rate_limit_per_principal,
    require,
)
from app.core.config import settings
from app.core.errors import ValidationFailed
from app.core.permissions import Permission
from app.schemas.ai import (
    AIStatusOut,
    AskLedgerOut,
    AskLedgerRequest,
    CopilotOut,
    CopilotRequest,
    ExplainTransactionOut,
    LedgerBriefOut,
    TransactionAssistRequest,
    TransactionProposalOut,
)
from app.services import audit
from app.services.ai import ask as ask_service
from app.services.ai import brief as brief_service
from app.services.ai import copilot, transaction_assistant
from app.services.ai import explain as explain_service
from app.services.ai import provider as ai_provider

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(require(Permission.AI_USE))])

ai_rate_limit = rate_limit_per_principal("ai", limit=lambda: settings.ai_rate_limit_per_minute)


@router.get("/status", response_model=AIStatusOut)
def ai_status() -> AIStatusOut:
    """Whether the frontend should offer AI features at all right now.

    Calls `ai_provider.get_provider()` via the module, not a direct
    `from ... import get_provider` - the same reasoning every other AI
    service module already follows: `unittest.mock`/`monkeypatch` patch the
    name in the module it lives in, and a direct import binds a local copy
    that a patch there would never reach.
    """
    if ai_provider.get_provider() is not None:
        return AIStatusOut(available=True, provider=settings.ai_provider)
    return AIStatusOut(
        available=False,
        provider=None,
        reason="AI features are unavailable because no AI provider is configured.",
    )


@router.post(
    "/transactions/propose",
    response_model=TransactionProposalOut,
    dependencies=[Depends(ai_rate_limit)],
)
def propose_transaction(payload: TransactionAssistRequest, db: DbSession) -> TransactionProposalOut:
    """A draft only. Posting still requires `POST /transactions` with `post_body`."""
    return transaction_assistant.propose_transaction(db, payload.description)


@router.post("/ask", response_model=AskLedgerOut, dependencies=[Depends(ai_rate_limit)])
def ask_ledger(payload: AskLedgerRequest, db: DbSession) -> AskLedgerOut:
    return ask_service.ask_ledger(db, payload.question)


@router.get(
    "/transactions/{transaction_id}/explain",
    response_model=ExplainTransactionOut,
    dependencies=[Depends(ai_rate_limit)],
)
def explain_transaction(
    transaction_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> ExplainTransactionOut:
    result = explain_service.explain_transaction(db, transaction_id)
    # Metadata only (never the prompt or the answer): model use shows in the audit log.
    audit.record(
        db,
        event_type="ai.copilot_used",
        resource_type="transaction",
        resource_id=str(transaction_id),
        actor=principal.label,
        request_id=request_id,
        payload={"task": "EXPLAIN_TRANSACTION"},
    )
    db.commit()
    return result


@router.get("/ledger-brief", response_model=LedgerBriefOut, dependencies=[Depends(ai_rate_limit)])
def ledger_brief(db: DbSession, days: int = Query(default=1, ge=1, le=90)) -> LedgerBriefOut:
    """Real facts always; AI phrasing only when a provider is configured.

    Unlike the other AI routes, this one never 503s - `brief_service` falls
    back to a deterministic template sentence when AI is unavailable, so the
    dashboard widget always has something true to show.
    """
    return brief_service.ledger_brief(db, days=days)


def _copilot_out(result: copilot.CopilotResult) -> CopilotOut:
    return CopilotOut(
        task=result.task,
        summary=result.summary,
        verified_facts=result.verified_facts,  # type: ignore[arg-type]
        hypotheses=result.hypotheses,  # type: ignore[arg-type]
        missing_evidence=result.missing_evidence,
        suggested_next_steps=result.suggested_next_steps,
        document=result.document,
        evidence_sufficient=result.evidence_sufficient,
        sources=result.sources,  # type: ignore[arg-type]
        ungrounded_claims_moved=result.ungrounded_claims_moved,
    )


@router.post(
    "/investigations/{case_id}/assist",
    response_model=CopilotOut,
    dependencies=[
        Depends(require(Permission.INVESTIGATIONS_READ)),
        Depends(ai_rate_limit),
    ],
)
def assist_investigation(
    case_id: uuid.UUID,
    payload: CopilotRequest,
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
) -> CopilotOut:
    """Summarise a case, list missing evidence, suggest next steps, or draft a
    resolution/report - grounded in the case's real records, with every claim
    cited, and returned as a draft for review. Changes nothing."""
    try:
        task = copilot.CopilotTask(payload.task.upper())
    except ValueError as exc:
        raise ValidationFailed(
            "unknown task", details={"allowed": [t.value for t in copilot.CopilotTask]}
        ) from exc
    return _copilot_out(
        copilot.assist_investigation(db, case_id, task, principal=principal, request_id=request_id)
    )


@router.post(
    "/reconciliation/results/{result_id}/explain",
    response_model=CopilotOut,
    dependencies=[
        Depends(require(Permission.RECONCILIATION_READ)),
        Depends(ai_rate_limit),
    ],
)
def explain_reconciliation_result(
    result_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> CopilotOut:
    """Why is this record unmatched? Deterministic candidates are presented as
    hypotheses for review; the reconciliation engine stays authoritative."""
    return _copilot_out(
        copilot.explain_reconciliation_result(
            db, result_id, principal=principal, request_id=request_id
        )
    )


@router.post(
    "/transactions/{transaction_id}/history",
    response_model=CopilotOut,
    dependencies=[
        Depends(require(Permission.TRANSACTIONS_READ)),
        Depends(ai_rate_limit),
    ],
)
def explain_transaction_history(
    transaction_id: uuid.UUID, db: DbSession, principal: CurrentPrincipal, request_id: RequestId
) -> CopilotOut:
    return _copilot_out(
        copilot.explain_transaction_history(
            db, transaction_id, principal=principal, request_id=request_id
        )
    )
