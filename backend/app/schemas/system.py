"""System, integrity and audit schemas."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import Money


class HealthOut(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database: str


class CurrencyOut(BaseModel):
    code: str
    name: str
    exponent: int


class TrialBalanceRow(BaseModel):
    """Σ debits vs Σ credits across the entire ledger for one currency.

    In a correct double-entry system the difference is always zero. This is the
    cheapest end-to-end proof that the ledger has not been corrupted.
    """

    currency: str
    debits: Money
    credits: Money
    difference: Money
    balanced: bool
    entry_count: int


class AccountTypeCount(BaseModel):
    type: str
    count: int


class DailyVolume(BaseModel):
    day: date
    currency: str
    transaction_count: int
    posted: Money


class SystemStatsOut(BaseModel):
    generated_at: datetime
    account_count: int
    active_account_count: int
    accounts_by_type: list[AccountTypeCount]
    transaction_count: int
    reversed_transaction_count: int
    entry_count: int
    trial_balance: list[TrialBalanceRow]
    ledger_balanced: bool
    daily_volume: list[DailyVolume]


class IntegrityIssue(BaseModel):
    account_id: uuid.UUID
    account_code: str
    field: str
    cached: int
    derived: int


class IntegrityReport(BaseModel):
    checked_at: datetime
    accounts_checked: int
    cache_consistent: bool
    trial_balance_balanced: bool
    unbalanced_transaction_ids: list[uuid.UUID]
    issues: list[IntegrityIssue]


class ErrorPathCount(BaseModel):
    path: str
    count: int


class LatestReconciliationRunOut(BaseModel):
    run_number: int
    created_at: datetime
    missing_internal_count: int
    missing_external_count: int
    mismatch_count: int
    ambiguous_count: int


class OperationalSnapshotOut(BaseModel):
    """One call for "is anything wrong right now" - composes existing
    checks (integrity, the latest reconciliation run) with in-process
    request counters. Not a metrics exporter: single-process, resets on
    restart, appropriate for a service with no multi-instance story.
    """

    generated_at: datetime
    uptime_seconds: float
    total_requests: int
    requests_by_status_class: dict[str, int]
    top_error_paths: list[ErrorPathCount]
    ledger_balanced: bool
    integrity_all_passed: bool
    open_investigation_count: int
    latest_reconciliation_run: LatestReconciliationRunOut | None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    seq: int
    event_type: str
    resource_type: str
    resource_id: str | None
    actor: str
    request_id: str | None
    payload: dict[str, Any]
    created_at: datetime
