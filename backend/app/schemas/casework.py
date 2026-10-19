"""Notes, match suggestions/decisions and related-record views."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import MatchDecision, ReconciliationStatus
from app.schemas.common import Money


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class NoteOut(BaseModel):
    id: uuid.UUID
    author: str
    body: str
    created_at: datetime


class SuggestionOut(BaseModel):
    transaction_id: uuid.UUID
    reference: str
    description: str
    amount: Money
    effective_at: datetime
    score: int
    reasons: list[str]
    # Always true: a suggestion is never applied automatically.
    requires_review: bool = True


class DecisionCreate(BaseModel):
    decision: MatchDecision
    internal_transaction_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)


class DecisionOut(BaseModel):
    id: uuid.UUID
    decision: MatchDecision
    internal_transaction_id: uuid.UUID | None
    reason: str
    decided_by: str
    created_at: datetime


class RelatedExternalOut(BaseModel):
    result_id: uuid.UUID
    run_number: int
    status: ReconciliationStatus
    source_system: str | None
    external_id: str | None
    explanation: str


class RelatedCaseOut(BaseModel):
    id: uuid.UUID
    case_number: str
    title: str
    status: str
    priority: str


class RelatedOut(BaseModel):
    """Everything in the system that points at one transaction."""

    reconciliation: list[RelatedExternalOut]
    investigations: list[RelatedCaseOut]
    decisions: list[DecisionOut]
