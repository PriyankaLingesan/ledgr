"""Maker-checker drafts, alerts, statements, dashboard and onboarding schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import DraftStatus, EntryDirection
from app.schemas.common import Money
from app.schemas.system import AuditEventOut

# --- maker-checker --------------------------------------------------------


class DraftEntryOut(BaseModel):
    account_id: uuid.UUID
    account_code: str | None
    direction: EntryDirection
    amount: Money
    memo: str | None


class DraftOut(BaseModel):
    id: uuid.UUID
    seq: int
    status: DraftStatus
    description: str
    currency: str
    reference: str | None
    external_reference: str | None
    effective_at: datetime | None
    entries: list[DraftEntryOut]
    total: Money
    created_by: str
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None
    decided_by: str | None
    decision_note: str | None
    last_error: str | None
    posted_transaction_id: uuid.UUID | None
    # Convenience for the UI, computed server-side for the *calling* identity:
    # the server still enforces both on the action itself.
    is_mine: bool = False
    can_approve: bool = False


class DraftDecision(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class DraftRejection(BaseModel):
    note: str = Field(min_length=1, max_length=500)


# --- alerts -----------------------------------------------------------------


class AlertOut(BaseModel):
    id: str
    rule: str
    severity: str
    title: str
    detail: str
    count: int
    last_seen: datetime | None
    link: str | None
    evidence: dict[str, Any]


class AlertsOut(BaseModel):
    generated_at: datetime
    window_hours: int
    counts: dict[str, int]
    alerts: list[AlertOut]


# --- statements ---------------------------------------------------------------


class StatementRowOut(BaseModel):
    entry_id: uuid.UUID
    seq: int
    transaction_id: uuid.UUID
    reference: str
    description: str
    transaction_status: str
    transaction_kind: str
    actor: str
    posted_at: datetime
    direction: EntryDirection
    debit: Money | None
    credit: Money | None
    running_balance: Money
    memo: str | None


class StatementAccountOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    type: str
    currency: str
    status: str
    normal_balance: str


class StatementOut(BaseModel):
    account: StatementAccountOut
    opening_balance: Money
    closing_balance: Money
    current_balance: Money
    total_debits: Money
    total_credits: Money
    items: list[StatementRowOut]
    total: int
    limit: int
    offset: int


# --- dashboard --------------------------------------------------------------------


class DashboardTransaction(BaseModel):
    id: uuid.UUID
    reference: str
    description: str
    amount: Money
    status: str
    kind: str
    actor: str
    posted_at: datetime


class DashboardInvestigation(BaseModel):
    id: uuid.UUID
    case_number: str
    title: str
    status: str
    priority: str
    created_at: datetime


class DashboardReconciliation(BaseModel):
    run_id: uuid.UUID
    run_number: int
    run_at: datetime
    external_count: int
    matched: int
    mismatched: int
    missing_internal: int
    missing_external: int
    ambiguous: int
    exceptions: int


class DashboardOut(BaseModel):
    generated_at: datetime
    # accounts & activity
    account_count: int
    active_account_count: int
    transaction_count: int
    transactions_last_24h: int
    recent_transactions: list[DashboardTransaction]
    daily_volume: list[dict[str, Any]]
    pending_approvals: int
    # Posted value over the last `volume_window_days` days, per currency. In
    # double entry the debit total of a transaction is its value, so total
    # debits == total credits == this figure; reversed transactions are excluded.
    volume_window_days: int
    posted_volume: list[dict[str, Any]]
    # Counted from the audit trail over the last 24 hours.
    failed_operations_24h: dict[str, int]
    # investigations
    open_investigations: int
    recent_investigations: list[DashboardInvestigation]
    # reconciliation
    latest_reconciliation: DashboardReconciliation | None
    # integrity & health
    ledger_balanced: bool
    integrity_all_passed: bool
    integrity_checks_failed: list[str]
    database: str
    alert_counts: dict[str, int]
    top_alerts: list[AlertOut]
    recent_audit: list[AuditEventOut]


# --- onboarding ----------------------------------------------------------------------


class OnboardingStep(BaseModel):
    key: str
    title: str
    description: str
    done: bool
    action_path: str | None = None


class OnboardingOut(BaseModel):
    organization_name: str | None
    steps: list[OnboardingStep]
    completed: int
    total: int


class OnboardingResult(BaseModel):
    message: str
    created: list[str] = Field(default_factory=list)
