"""External reconciliation: request/response shapes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import EntryDirection, ReconciliationStatus
from app.schemas.common import Money


class ExternalTransactionImport(BaseModel):
    external_id: str = Field(min_length=1, max_length=120)
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    direction: EntryDirection = EntryDirection.DEBIT
    transaction_timestamp: datetime
    account_reference: str | None = Field(default=None, max_length=160)
    status: str = Field(default="SETTLED", max_length=40)
    description: str = Field(default="", max_length=2000)


class ExternalTransactionImportRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=80)
    records: list[ExternalTransactionImport] = Field(min_length=1, max_length=500)


class ImportResultOut(BaseModel):
    imported: int
    skipped_duplicates: int


class ExternalTransactionOut(BaseModel):
    id: uuid.UUID
    source_system: str
    external_id: str
    account_reference: str | None
    amount: Money
    direction: EntryDirection
    status: str
    transaction_timestamp: datetime
    description: str


class ReconciliationResultOut(BaseModel):
    id: uuid.UUID
    status: ReconciliationStatus
    explanation: str
    difference: Money | None
    # None only for MISSING_EXTERNAL: a live internal transaction with no
    # external record to point at.
    external_transaction: ExternalTransactionOut | None
    internal_transaction_id: uuid.UUID | None
    internal_reference: str | None
    internal_amount: Money | None


class ReconciliationRunSummaryOut(BaseModel):
    id: uuid.UUID
    run_number: int
    created_at: datetime
    external_count: int
    matched_count: int
    mismatch_count: int
    missing_internal_count: int
    missing_external_count: int
    ambiguous_count: int


class ReconciliationRunOut(ReconciliationRunSummaryOut):
    results: list[ReconciliationResultOut]
