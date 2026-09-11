"""System, statistics, integrity and audit endpoints.

Every figure served here is derived from the ledger. There are no counters
maintained on the side and nothing is estimated.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select, text

from app.api.deps import DbSession, PaginationParams
from app.core import money
from app.core.config import settings
from app.models.account import Account, AccountBalance
from app.models.enums import AccountStatus, TransactionStatus
from app.models.ledger_entry import LedgerEntry
from app.models.transaction import Transaction
from app.schemas.common import Money, Page
from app.schemas.system import (
    AccountTypeCount,
    AuditEventOut,
    CurrencyOut,
    DailyVolume,
    HealthOut,
    IntegrityIssue,
    IntegrityReport,
    SystemStatsOut,
    TrialBalanceRow,
)
from app.services import balances, queries

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db: DbSession) -> HealthOut:
    try:
        db.execute(text("SELECT 1"))
        database = "up"
    except Exception:  # pragma: no cover - exercised only when the DB is down
        database = "down"
    return HealthOut(
        status="ok" if database == "up" else "degraded",
        service=settings.app_name,
        version="1.0.0",
        environment=settings.environment,
        database=database,
    )


@router.get("/currencies", response_model=list[CurrencyOut])
def currencies() -> list[CurrencyOut]:
    """Supported currencies and their minor-unit exponents.

    The UI formats every amount from this, so major/minor conversion is defined
    in exactly one place across the stack.
    """
    return [
        CurrencyOut(
            code=code,
            name=money.CURRENCY_NAMES.get(code, code),
            exponent=money.CURRENCY_EXPONENTS[code],
        )
        for code in money.SUPPORTED_CURRENCIES
    ]


def _trial_balance_rows(db: DbSession) -> list[TrialBalanceRow]:
    return [
        TrialBalanceRow(
            currency=row.currency,
            debits=Money.of(row.debits_minor, row.currency),
            credits=Money.of(row.credits_minor, row.currency),
            difference=Money.of(row.difference_minor, row.currency),
            balanced=row.balanced,
            entry_count=row.entry_count,
        )
        for row in balances.trial_balance(db)
    ]


@router.get("/stats", response_model=SystemStatsOut)
def stats(db: DbSession, days: int = Query(default=14, ge=1, le=90)) -> SystemStatsOut:
    account_count = db.execute(select(func.count(Account.id))).scalar_one()
    active_count = db.execute(
        select(func.count(Account.id)).where(Account.status == AccountStatus.ACTIVE)
    ).scalar_one()
    by_type = db.execute(
        select(Account.type, func.count(Account.id)).group_by(Account.type).order_by(Account.type)
    ).all()

    transaction_count = db.execute(select(func.count(Transaction.id))).scalar_one()
    reversed_count = db.execute(
        select(func.count(Transaction.id)).where(Transaction.status == TransactionStatus.REVERSED)
    ).scalar_one()
    entry_count = db.execute(select(func.count(LedgerEntry.id))).scalar_one()

    trial = _trial_balance_rows(db)

    return SystemStatsOut(
        generated_at=datetime.now(UTC),
        account_count=int(account_count),
        active_account_count=int(active_count),
        accounts_by_type=[
            AccountTypeCount(type=row[0].value, count=int(row[1])) for row in by_type
        ],
        transaction_count=int(transaction_count),
        reversed_transaction_count=int(reversed_count),
        entry_count=int(entry_count),
        trial_balance=trial,
        ledger_balanced=all(row.balanced for row in trial),
        daily_volume=[
            DailyVolume(
                day=row.day,
                currency=row.currency,
                transaction_count=row.transaction_count,
                posted=Money.of(row.posted_minor, row.currency),
            )
            for row in balances.daily_volume(db, days=days)
        ],
    )


@router.get("/integrity", response_model=IntegrityReport)
def integrity(db: DbSession) -> IntegrityReport:
    """Re-derive every balance from ledger entries and compare with the cache.

    This is the check that keeps the cache honest: if it ever disagrees with
    the entries, the entries win and this endpoint says so out loud.
    """
    derived = balances.derive_all(db)
    cached = {row.account_id: row for row in db.execute(select(AccountBalance)).scalars().all()}
    codes = dict(db.execute(select(Account.id, Account.code)).all())

    issues: list[IntegrityIssue] = []
    account_ids = set(derived) | set(cached)
    for account_id in account_ids:
        d = derived.get(account_id)
        c = cached.get(account_id)
        derived_debits = d.debits_minor if d else 0
        derived_credits = d.credits_minor if d else 0
        cached_debits = c.debits_minor if c else 0
        cached_credits = c.credits_minor if c else 0
        code = codes.get(account_id, "?")
        if cached_debits != derived_debits:
            issues.append(
                IntegrityIssue(
                    account_id=account_id,
                    account_code=code,
                    field="debits_minor",
                    cached=cached_debits,
                    derived=derived_debits,
                )
            )
        if cached_credits != derived_credits:
            issues.append(
                IntegrityIssue(
                    account_id=account_id,
                    account_code=code,
                    field="credits_minor",
                    cached=cached_credits,
                    derived=derived_credits,
                )
            )

    trial = balances.trial_balance(db)
    unbalanced = balances.unbalanced_transaction_ids(db)

    return IntegrityReport(
        checked_at=datetime.now(UTC),
        accounts_checked=len(account_ids),
        cache_consistent=not issues,
        trial_balance_balanced=all(row.balanced for row in trial),
        unbalanced_transaction_ids=unbalanced,
        issues=issues,
    )


@router.get("/audit", response_model=Page[AuditEventOut])
def audit_events(
    db: DbSession,
    page: PaginationParams,
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> Page[AuditEventOut]:
    items, total = queries.list_audit_events(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[AuditEventOut](
        items=[
            AuditEventOut(
                id=e.id,
                seq=e.seq,
                event_type=e.event_type,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                actor=e.actor,
                request_id=e.request_id,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in items
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
