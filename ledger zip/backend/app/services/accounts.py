"""Account lifecycle."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core import money
from app.core.errors import Conflict, NotFound, UnsupportedCurrency, ValidationFailed
from app.models.account import Account, AccountBalance
from app.models.enums import NORMAL_BALANCE_BY_TYPE, AccountStatus, AccountType
from app.services import audit


def create_account(
    db: Session,
    *,
    code: str,
    name: str,
    account_type: AccountType,
    currency: str,
    description: str | None = None,
    allows_negative_balance: bool = True,
    metadata: dict[str, Any] | None = None,
    actor: str = "system",
    request_id: str | None = None,
) -> Account:
    currency = currency.upper()
    if not money.is_supported(currency):
        raise UnsupportedCurrency(
            f"currency {currency} is not supported",
            details={"supported": list(money.SUPPORTED_CURRENCIES)},
        )

    account = Account(
        code=code,
        name=name,
        type=account_type,
        # Derived, never client-supplied: the accounting equation decides which
        # side an account family increases on, not the caller.
        normal_balance=NORMAL_BALANCE_BY_TYPE[account_type],
        currency=currency,
        description=description,
        status=AccountStatus.ACTIVE,
        allows_negative_balance=allows_negative_balance,
        extra=metadata or {},
    )
    account.balance_cache = AccountBalance(debits_minor=0, credits_minor=0, entry_count=0)
    db.add(account)

    audit.record(
        db,
        event_type="account.created",
        resource_type="account",
        resource_id=str(account.id),
        actor=actor,
        request_id=request_id,
        payload={"code": code, "type": account_type.value, "currency": currency},
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict(f"account code {code!r} already exists") from exc

    db.refresh(account)
    return account


def get_account(db: Session, account_id: uuid.UUID) -> Account:
    account = db.execute(
        select(Account).options(selectinload(Account.balance_cache)).where(Account.id == account_id)
    ).scalar_one_or_none()
    if account is None:
        raise NotFound(f"account {account_id} not found")
    return account


def get_account_by_code(db: Session, code: str) -> Account:
    account = db.execute(
        select(Account).options(selectinload(Account.balance_cache)).where(Account.code == code)
    ).scalar_one_or_none()
    if account is None:
        raise NotFound(f"account with code {code!r} not found")
    return account


def list_accounts(
    db: Session,
    *,
    query: str | None = None,
    account_type: AccountType | None = None,
    status: AccountStatus | None = None,
    currency: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Account], int]:
    filters = []
    if query:
        pattern = f"%{query.strip()}%"
        filters.append(or_(Account.code.ilike(pattern), Account.name.ilike(pattern)))
    if account_type is not None:
        filters.append(Account.type == account_type)
    if status is not None:
        filters.append(Account.status == status)
    if currency:
        filters.append(Account.currency == currency.upper())

    total = db.execute(
        select(func.count(Account.id)).where(*filters)
        if filters
        else select(func.count(Account.id))
    ).scalar_one()

    stmt = (
        select(Account)
        .options(selectinload(Account.balance_cache))
        .order_by(Account.code)
        .limit(limit)
        .offset(offset)
    )
    if filters:
        stmt = stmt.where(*filters)

    return list(db.execute(stmt).scalars().all()), int(total)


def update_account(
    db: Session,
    account_id: uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    status: AccountStatus | None = None,
    allows_negative_balance: bool | None = None,
    metadata: dict[str, Any] | None = None,
    actor: str = "system",
    request_id: str | None = None,
) -> Account:
    account = get_account(db, account_id)
    changes: dict[str, Any] = {}

    if name is not None and name != account.name:
        changes["name"] = name
        account.name = name
    if description is not None and description != account.description:
        changes["description"] = description
        account.description = description
    if allows_negative_balance is not None:
        changes["allows_negative_balance"] = allows_negative_balance
        account.allows_negative_balance = allows_negative_balance
    if metadata is not None:
        changes["metadata"] = metadata
        account.extra = metadata
    if status is not None and status != account.status:
        if account.status is AccountStatus.CLOSED:
            # Closing is terminal. Re-opening an account would let entries land
            # after a period was reconciled and signed off.
            raise ValidationFailed("a CLOSED account cannot change status")
        changes["status"] = status.value
        account.status = status

    if changes:
        audit.record(
            db,
            event_type="account.updated",
            resource_type="account",
            resource_id=str(account.id),
            actor=actor,
            request_id=request_id,
            payload=changes,
        )
    db.commit()
    db.refresh(account)
    return account
