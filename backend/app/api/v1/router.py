"""v1 API surface."""

from fastapi import APIRouter

from app.api.v1 import accounts, ai, investigations, ledger, reconciliation, system, transactions

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(accounts.router)
api_router.include_router(transactions.router)
api_router.include_router(ledger.router)
api_router.include_router(investigations.router)
api_router.include_router(reconciliation.router)
api_router.include_router(ai.router)
