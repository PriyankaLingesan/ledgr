"""v1 API surface.

Authorization model: every route except `/system/health` and the public
`/auth/*` entry points (setup status, setup, login) declares the permission it
needs via `require(...)`; the role -> permission matrix is in
`app.core.permissions` and is enforced on the server for every request.
"""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    ai,
    auth,
    demo,
    drafts,
    investigations,
    ledger,
    operations,
    reconciliation,
    system,
    transactions,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(operations.router)
api_router.include_router(accounts.router)
api_router.include_router(transactions.router)
api_router.include_router(drafts.router)
api_router.include_router(ledger.router)
api_router.include_router(investigations.router)
api_router.include_router(reconciliation.router)
api_router.include_router(ai.router)
api_router.include_router(demo.router)
