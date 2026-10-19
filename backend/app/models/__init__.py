"""SQLAlchemy models.

Imported eagerly so that `Base.metadata` is complete for Alembic autogenerate
and for test schema creation.
"""

from app.db.base import Base
from app.models.account import Account, AccountBalance
from app.models.audit import AuditEvent
from app.models.auth import ApiKey
from app.models.casework import InvestigationNote, ReconciliationDecision
from app.models.draft import TransactionDraft
from app.models.enums import (
    NORMAL_BALANCE_BY_TYPE,
    AccountStatus,
    AccountType,
    DraftStatus,
    EntryDirection,
    IdempotencyStatus,
    InvestigationPriority,
    InvestigationSource,
    InvestigationStatus,
    MatchDecision,
    NormalBalance,
    ReconciliationStatus,
    TransactionKind,
    TransactionStatus,
    UserRole,
)
from app.models.idempotency import IdempotencyRecord
from app.models.investigation import Investigation
from app.models.ledger_entry import LedgerEntry
from app.models.reconciliation import ExternalTransaction, ReconciliationResult, ReconciliationRun
from app.models.transaction import Transaction
from app.models.user import Organization, User, UserSession

__all__ = [
    "NORMAL_BALANCE_BY_TYPE",
    "Account",
    "AccountBalance",
    "AccountStatus",
    "AccountType",
    "ApiKey",
    "AuditEvent",
    "Base",
    "EntryDirection",
    "ExternalTransaction",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "DraftStatus",
    "Investigation",
    "InvestigationNote",
    "InvestigationPriority",
    "InvestigationSource",
    "InvestigationStatus",
    "LedgerEntry",
    "MatchDecision",
    "NormalBalance",
    "ReconciliationDecision",
    "ReconciliationResult",
    "ReconciliationRun",
    "ReconciliationStatus",
    "Organization",
    "Transaction",
    "TransactionDraft",
    "TransactionKind",
    "TransactionStatus",
    "User",
    "UserRole",
    "UserSession",
]
