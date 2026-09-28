"""Investigation case: request/response shapes.

A case carries no money of its own - see `app.models.investigation` - so
nothing here is a `Money` field except by way of the evidence bundle it
points at.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import InvestigationSource, InvestigationStatus
from app.schemas.account import AccountSummary
from app.schemas.investigation import (
    BalanceChangeOut,
    DuplicateGroupOut,
    IntegrityCheckOut,
    TransactionLineageOut,
)


class InvestigationCaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    issue_summary: str = Field(min_length=1)
    source: InvestigationSource = InvestigationSource.MANUAL
    source_reference: str | None = Field(default=None, max_length=200)
    account_id: uuid.UUID | None = None
    window_from: datetime | None = None
    window_to: datetime | None = None


class InvestigationCaseStatusUpdate(BaseModel):
    status: InvestigationStatus


class InvestigationCaseResolve(BaseModel):
    resolution_note: str = Field(min_length=1)
    correction_transaction_id: uuid.UUID | None = None


class InvestigationCaseOut(BaseModel):
    id: uuid.UUID
    case_number: str
    title: str
    issue_summary: str
    status: InvestigationStatus
    source: InvestigationSource
    source_reference: str | None
    account: AccountSummary | None
    window_from: datetime | None
    window_to: datetime | None
    resolved_at: datetime | None
    resolution_note: str | None
    correction_transaction_id: uuid.UUID | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class InvestigationCaseEvidenceOut(BaseModel):
    case: InvestigationCaseOut
    balance_change: BalanceChangeOut | None
    duplicate_candidates: list[DuplicateGroupOut]
    integrity: list[IntegrityCheckOut]
    lineage: TransactionLineageOut | None
