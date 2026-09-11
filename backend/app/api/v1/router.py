"""v1 API surface."""

from fastapi import APIRouter

from app.api.v1 import accounts, ledger, system, transactions

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(accounts.router)
api_router.include_router(transactions.router)
api_router.include_router(ledger.router)
