"""Seed a realistic ledger.

Every row this script creates goes through the same posting service the API
uses, so the seeded data obeys every invariant - there is no back door that
writes entries directly. Balances are tracked as it goes so the accounts that
forbid negative balances never receive an entry that would breach that rule.

    python -m app.scripts.seed --if-empty
    python -m app.scripts.seed --reset
"""

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.core.money import to_minor
from app.db.session import session_scope
from app.models.account import Account
from app.models.enums import AccountType, EntryDirection
from app.models.transaction import Transaction
from app.services import accounts as account_service
from app.services import posting

RANDOM_SEED = 20260908

# (code, name, type, currency, allows_negative_balance)
CHART_OF_ACCOUNTS = [
    # Assets
    ("CASH.OPERATING.USD", "Operating Bank Account", AccountType.ASSET, "USD", False),
    ("CASH.CLEARING.USD", "Processor Clearing Account", AccountType.ASSET, "USD", True),
    ("CASH.RESERVE.USD", "Regulatory Reserve Account", AccountType.ASSET, "USD", False),
    ("AR.CUSTOMERS.USD", "Accounts Receivable", AccountType.ASSET, "USD", True),
    ("CASH.OPERATING.EUR", "Operating Bank Account (EUR)", AccountType.ASSET, "EUR", False),
    # Liabilities - customer money the platform holds but does not own
    ("WALLET.CUST.10001", "Customer Wallet - Arden Freight", AccountType.LIABILITY, "USD", False),
    ("WALLET.CUST.10002", "Customer Wallet - Bellweather Co", AccountType.LIABILITY, "USD", False),
    ("WALLET.CUST.10003", "Customer Wallet - Corvus Labs", AccountType.LIABILITY, "USD", False),
    ("WALLET.CUST.10004", "Customer Wallet - Dunlin Retail", AccountType.LIABILITY, "USD", False),
    ("WALLET.CUST.10005", "Customer Wallet - Estuary Health", AccountType.LIABILITY, "USD", False),
    ("WALLET.CUST.20001", "Customer Wallet - Fjord Systems", AccountType.LIABILITY, "EUR", False),
    ("LIAB.PAYABLE.USD", "Accounts Payable", AccountType.LIABILITY, "USD", True),
    ("LIAB.TAX.USD", "Tax Payable", AccountType.LIABILITY, "USD", True),
    # Equity
    ("EQ.CONTRIBUTED.USD", "Contributed Capital", AccountType.EQUITY, "USD", True),
    ("EQ.CONTRIBUTED.EUR", "Contributed Capital (EUR)", AccountType.EQUITY, "EUR", True),
    ("EQ.RETAINED.USD", "Retained Earnings", AccountType.EQUITY, "USD", True),
    # Revenue
    ("REV.FEES.USD", "Platform Fee Revenue", AccountType.REVENUE, "USD", True),
    ("REV.INTEREST.USD", "Interest Income", AccountType.REVENUE, "USD", True),
    ("REV.FEES.EUR", "Platform Fee Revenue (EUR)", AccountType.REVENUE, "EUR", True),
    # Expenses
    ("EXP.PROCESSING.USD", "Payment Processing Costs", AccountType.EXPENSE, "USD", True),
    ("EXP.PAYROLL.USD", "Payroll", AccountType.EXPENSE, "USD", True),
    ("EXP.INFRA.USD", "Infrastructure", AccountType.EXPENSE, "USD", True),
]

USD_WALLETS = [code for code, *_ in CHART_OF_ACCOUNTS if code.startswith("WALLET.CUST.1")]


def _entry(account_id, direction: EntryDirection, amount_minor: int, memo: str | None = None):
    return posting.EntryCommand(
        account_id=account_id, direction=direction, amount_minor=amount_minor, memo=memo
    )


def seed(reset: bool = False, if_empty: bool = False) -> None:
    rng = random.Random(RANDOM_SEED)

    with session_scope() as db:
        existing = db.execute(select(func.count(Account.id))).scalar_one()
        transactions = db.execute(select(func.count(Transaction.id))).scalar_one()

        if reset:
            # DELETE is blocked by the immutability triggers, and rightly so.
            # Reseeding is a development action that discards the whole ledger
            # rather than editing it: TRUNCATE, which fires no row triggers.
            print("resetting ledger data", flush=True)
            db.execute(
                text(
                    "TRUNCATE audit_events, idempotency_keys, ledger_entries, "
                    "transactions, account_balances, accounts RESTART IDENTITY CASCADE"
                )
            )
            db.commit()
            existing = 0
            transactions = 0
        elif if_empty and (existing or transactions):
            print(
                f"ledger already populated ({existing} accounts, "
                f"{transactions} transactions) - skipping",
                flush=True,
            )
            return
        elif existing:
            print("accounts already exist; use --reset to rebuild", flush=True)
            return

    accounts: dict[str, Account] = {}
    with session_scope() as db:
        for code, name, account_type, currency, allows_negative in CHART_OF_ACCOUNTS:
            account = account_service.create_account(
                db,
                code=code,
                name=name,
                account_type=account_type,
                currency=currency,
                allows_negative_balance=allows_negative,
                actor="seed",
                metadata={"seeded": True},
            )
            accounts[code] = account
        print(f"created {len(accounts)} accounts", flush=True)

    ids = {code: account.id for code, account in accounts.items()}
    # Mirror of the balances the ledger will derive, so the seeder never asks
    # for a posting that a balance-constrained account would refuse.
    wallet_balance: dict[str, int] = dict.fromkeys(USD_WALLETS, 0)
    wallet_balance["WALLET.CUST.20001"] = 0
    cash_usd = 0
    cash_eur = 0
    clearing = 0

    now = datetime.now(UTC)
    start = now - timedelta(days=27)
    posted = 0

    with session_scope() as db:
        # --- opening capital ------------------------------------------------
        opening = to_minor("750000.00", "USD")
        posting.post_transaction(
            db,
            posting.PostCommand(
                description="Opening capital contribution",
                currency="USD",
                reference="TXN-OPENING-USD",
                effective_at=start,
                entries=[
                    _entry(ids["CASH.OPERATING.USD"], EntryDirection.DEBIT, opening),
                    _entry(ids["EQ.CONTRIBUTED.USD"], EntryDirection.CREDIT, opening),
                ],
                actor="seed",
            ),
        )
        cash_usd += opening
        posted += 1

        opening_eur = to_minor("120000.00", "EUR")
        posting.post_transaction(
            db,
            posting.PostCommand(
                description="Opening capital contribution (EUR entity)",
                currency="EUR",
                reference="TXN-OPENING-EUR",
                effective_at=start,
                entries=[
                    _entry(ids["CASH.OPERATING.EUR"], EntryDirection.DEBIT, opening_eur),
                    _entry(ids["EQ.CONTRIBUTED.EUR"], EntryDirection.CREDIT, opening_eur),
                ],
                actor="seed",
            ),
        )
        cash_eur += opening_eur
        posted += 1

        # --- regulatory reserve --------------------------------------------
        reserve = to_minor("100000.00", "USD")
        posting.post_transaction(
            db,
            posting.PostCommand(
                description="Transfer to regulatory reserve account",
                currency="USD",
                effective_at=start + timedelta(hours=6),
                entries=[
                    _entry(ids["CASH.RESERVE.USD"], EntryDirection.DEBIT, reserve),
                    _entry(ids["CASH.OPERATING.USD"], EntryDirection.CREDIT, reserve),
                ],
                actor="seed",
            ),
        )
        cash_usd -= reserve
        posted += 1

        reversible: list = []

        for day in range(27):
            when = start + timedelta(days=day, hours=rng.randint(8, 19))

            # Customer deposits land in the processor clearing account first.
            for _ in range(rng.randint(2, 4)):
                wallet = rng.choice(USD_WALLETS)
                amount = rng.randrange(25_000, 900_000, 500)
                txn = posting.post_transaction(
                    db,
                    posting.PostCommand(
                        description=f"Inbound deposit for {accounts[wallet].name.split(' - ')[-1]}",
                        currency="USD",
                        effective_at=when + timedelta(minutes=rng.randint(0, 200)),
                        external_reference=f"dep_{rng.randrange(10**9, 10**10)}",
                        entries=[
                            _entry(
                                ids["CASH.CLEARING.USD"],
                                EntryDirection.DEBIT,
                                amount,
                                "Funds received from processor",
                            ),
                            _entry(ids[wallet], EntryDirection.CREDIT, amount, "Customer deposit"),
                        ],
                        actor="seed",
                        metadata={"channel": rng.choice(["ach", "card", "wire"])},
                    ),
                )
                wallet_balance[wallet] += amount
                clearing += amount
                posted += 1
                if day > 3 and rng.random() < 0.06:
                    reversible.append(txn.id)

            # Platform fee: revenue recognised against the customer's wallet.
            for _ in range(rng.randint(1, 3)):
                wallet = rng.choice(USD_WALLETS)
                fee = rng.randrange(150, 4_000, 25)
                if wallet_balance[wallet] < fee:
                    continue
                posting.post_transaction(
                    db,
                    posting.PostCommand(
                        description="Platform fee",
                        currency="USD",
                        effective_at=when + timedelta(minutes=rng.randint(0, 200)),
                        entries=[
                            _entry(ids[wallet], EntryDirection.DEBIT, fee, "Fee charged to wallet"),
                            _entry(ids["REV.FEES.USD"], EntryDirection.CREDIT, fee),
                        ],
                        actor="seed",
                    ),
                )
                wallet_balance[wallet] -= fee
                posted += 1

            # Processor settlement: clearing balance moves to the bank, net of
            # the processor's own cut, which is an expense in the same posting.
            if clearing > 50_000 and rng.random() < 0.65:
                gross = int(clearing * rng.uniform(0.5, 0.9))
                gross -= gross % 100
                cost = max(100, int(gross * 0.004))
                cost -= cost % 25
                net = gross - cost
                posting.post_transaction(
                    db,
                    posting.PostCommand(
                        description="Processor settlement",
                        currency="USD",
                        effective_at=when + timedelta(hours=1),
                        external_reference=f"stl_{rng.randrange(10**9, 10**10)}",
                        entries=[
                            _entry(
                                ids["CASH.OPERATING.USD"],
                                EntryDirection.DEBIT,
                                net,
                                "Net settled to bank",
                            ),
                            _entry(
                                ids["EXP.PROCESSING.USD"],
                                EntryDirection.DEBIT,
                                cost,
                                "Processor fee withheld",
                            ),
                            _entry(
                                ids["CASH.CLEARING.USD"],
                                EntryDirection.CREDIT,
                                gross,
                                "Clearing balance released",
                            ),
                        ],
                        actor="seed",
                    ),
                )
                clearing -= gross
                cash_usd += net
                posted += 1

            # Customer withdrawals.
            if rng.random() < 0.7:
                wallet = rng.choice(USD_WALLETS)
                available = min(wallet_balance[wallet], cash_usd)
                if available > 20_000:
                    amount = rng.randrange(10_000, max(20_001, int(available * 0.6)), 500)
                    posting.post_transaction(
                        db,
                        posting.PostCommand(
                            description="Customer withdrawal",
                            currency="USD",
                            effective_at=when + timedelta(hours=2),
                            external_reference=f"wdr_{rng.randrange(10**9, 10**10)}",
                            entries=[
                                _entry(
                                    ids[wallet],
                                    EntryDirection.DEBIT,
                                    amount,
                                    "Payout to customer bank",
                                ),
                                _entry(ids["CASH.OPERATING.USD"], EntryDirection.CREDIT, amount),
                            ],
                            actor="seed",
                        ),
                    )
                    wallet_balance[wallet] -= amount
                    cash_usd -= amount
                    posted += 1

            # EUR activity, so the ledger is genuinely multi-currency.
            if rng.random() < 0.35:
                amount = rng.randrange(20_000, 250_000, 500)
                posting.post_transaction(
                    db,
                    posting.PostCommand(
                        description="Inbound deposit (EUR)",
                        currency="EUR",
                        effective_at=when,
                        entries=[
                            _entry(ids["CASH.OPERATING.EUR"], EntryDirection.DEBIT, amount),
                            _entry(ids["WALLET.CUST.20001"], EntryDirection.CREDIT, amount),
                        ],
                        actor="seed",
                    ),
                )
                wallet_balance["WALLET.CUST.20001"] += amount
                cash_eur += amount
                posted += 1

                fee = max(100, int(amount * 0.009))
                if wallet_balance["WALLET.CUST.20001"] >= fee:
                    posting.post_transaction(
                        db,
                        posting.PostCommand(
                            description="Platform fee (EUR)",
                            currency="EUR",
                            effective_at=when + timedelta(minutes=30),
                            entries=[
                                _entry(ids["WALLET.CUST.20001"], EntryDirection.DEBIT, fee),
                                _entry(ids["REV.FEES.EUR"], EntryDirection.CREDIT, fee),
                            ],
                            actor="seed",
                        ),
                    )
                    wallet_balance["WALLET.CUST.20001"] -= fee
                    posted += 1

            # Weekly operating costs.
            if day % 7 == 5:
                payroll = to_minor("48250.00", "USD")
                infra = to_minor("6120.00", "USD")
                total = payroll + infra
                if cash_usd > total:
                    posting.post_transaction(
                        db,
                        posting.PostCommand(
                            description="Weekly operating costs",
                            currency="USD",
                            effective_at=when,
                            entries=[
                                _entry(ids["EXP.PAYROLL.USD"], EntryDirection.DEBIT, payroll),
                                _entry(ids["EXP.INFRA.USD"], EntryDirection.DEBIT, infra),
                                _entry(ids["CASH.OPERATING.USD"], EntryDirection.CREDIT, total),
                            ],
                            actor="seed",
                        ),
                    )
                    cash_usd -= total
                    posted += 1

        # --- a couple of genuine corrections --------------------------------
        reversals = 0
        for transaction_id in reversible[:3]:
            posting.reverse_transaction(
                db,
                transaction_id,
                reason="Deposit recalled by originating bank",
                actor="seed",
            )
            reversals += 1
            posted += 1

    print(f"posted {posted} transactions ({reversals} reversals)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the LEDGR database")
    parser.add_argument("--reset", action="store_true", help="truncate existing data first")
    parser.add_argument(
        "--if-empty", action="store_true", help="no-op when the ledger already has data"
    )
    args = parser.parse_args()
    seed(reset=args.reset, if_empty=args.if_empty)
    return 0


if __name__ == "__main__":
    sys.exit(main())
