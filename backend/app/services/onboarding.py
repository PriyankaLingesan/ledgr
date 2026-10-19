"""First-run guidance and demo data.

The checklist is computed from real state (does an account exist? has a
transaction been posted?), never stored, so it can't drift from reality.
Demo data goes through the same services as everything else - accounts via
`create_account`, transactions via `posting.post_transaction`, external
records via the reconciliation importer - there is no back door.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.tenancy import require_organization
from app.models.account import Account
from app.models.enums import AccountType
from app.models.reconciliation import ExternalTransaction, ReconciliationRun
from app.models.transaction import Transaction
from app.models.user import User
from app.services import accounts as account_service
from app.services import audit, reconciliation, users

# The two accounts the reconciliation demo scenario posts against.
DEMO_ACCOUNTS = [
    ("CASH.OPERATING.USD", "Operating Bank Account", AccountType.ASSET, False),
    ("REV.FEES.USD", "Platform Fee Revenue", AccountType.REVENUE, True),
]


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    description: str
    done: bool
    action_path: str | None


def _count(db: Session, model) -> int:
    return int(db.execute(select(func.count(model.id))).scalar_one())


def checklist(db: Session) -> tuple[str | None, list[Step]]:
    organization_id = require_organization(db)
    org = users.get_organization(db, organization_id)
    steps = [
        Step(
            "organization",
            "Set up your organization",
            "Name, base currency and your first administrator.",
            org is not None,
            "/admin/settings",
        ),
        Step(
            "teammate",
            "Invite a second person",
            "Maker-checker needs two people: one proposes a transaction, another approves it.",
            int(
                db.execute(
                    select(func.count(User.id)).where(User.organization_id == organization_id)
                ).scalar_one()
            )
            >= 2,
            "/admin/users",
        ),
        Step(
            "account",
            "Create your first account",
            "Accounts hold the ledger entries every balance is derived from.",
            _count(db, Account) > 0,
            "/accounts",
        ),
        Step(
            "transaction",
            "Post your first transaction",
            "A balanced double-entry transaction - debits equal credits.",
            _count(db, Transaction) > 0,
            "/transactions/new",
        ),
        Step(
            "external_data",
            "Import external records",
            "Bank or processor records to reconcile the ledger against (demo data is available).",
            _count(db, ExternalTransaction) > 0,
            "/reconciliation",
        ),
        Step(
            "reconciliation_run",
            "Run a reconciliation",
            "Compare imported records with the ledger and review the exceptions.",
            _count(db, ReconciliationRun) > 0,
            "/reconciliation",
        ),
    ]
    return (org.name if org else None), steps


def ensure_demo_accounts(
    db: Session, *, actor: str, request_id: str | None
) -> tuple[Account, Account, list[str]]:
    created: list[str] = []
    found: dict[str, Account] = {}
    for code, name, account_type, allows_negative in DEMO_ACCOUNTS:
        try:
            found[code] = account_service.get_account_by_code(db, code)
        except NotFound:
            found[code] = account_service.create_account(
                db,
                code=code,
                name=name,
                account_type=account_type,
                currency="USD",
                allows_negative_balance=allows_negative,
                actor=actor,
                request_id=request_id,
            )
            created.append(code)
    return found["CASH.OPERATING.USD"], found["REV.FEES.USD"], created


def load_demo_reconciliation(
    db: Session, *, actor: str, request_id: str | None
) -> tuple[reconciliation.IngestResult, list[str]]:
    cash, revenue, created = ensure_demo_accounts(db, actor=actor, request_id=request_id)
    result = reconciliation.load_demo_data(
        db, cash_account_id=cash.id, revenue_account_id=revenue.id
    )
    audit.record(
        db,
        event_type="demo.loaded",
        resource_type="reconciliation",
        actor=actor,
        request_id=request_id,
        payload={
            "imported": result.imported,
            "skipped_duplicates": result.skipped_duplicates,
            "accounts_created": created,
        },
    )
    db.commit()
    return result, created
