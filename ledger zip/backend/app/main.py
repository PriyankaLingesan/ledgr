"""FastAPI application factory."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import LedgrError
from app.core.logging import configure_logging, request_id_var

logger = logging.getLogger("ledgr.api")

DESCRIPTION = """
LEDGR is a double-entry ledger. Money is recorded as balanced sets of debit and
credit entries; account balances are *derived* from those entries rather than
stored as a mutable running total.

**Guarantees**

* every posted transaction balances - total debits equal total credits
* ledger entries are append-only; corrections are posted as reversals
* posting is atomic - all entries are written or none are
* writes are idempotent when an `Idempotency-Key` header is supplied
* concurrent postings against the same account are serialised by row locks
"""


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(
        title="LEDGR API",
        version="1.0.0",
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Idempotent-Replay", "Location"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Correlate logs, responses and audit rows with one request id."""
        incoming = request.headers.get("X-Request-ID")
        rid = incoming or uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = rid
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    def error_response(
        status_code: int, code: str, message: str, details: dict | None, request: Request
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or {},
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(LedgrError)
    async def handle_ledgr_error(request: Request, exc: LedgrError) -> JSONResponse:
        logger.warning(
            "domain_error",
            extra={"code": exc.code, "detail": exc.message, "path": request.url.path},
        )
        return error_response(exc.status_code, exc.code, exc.message, exc.details, request)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            422,
            "validation_failed",
            "the request body failed validation",
            {
                "errors": [
                    {
                        "location": list(err.get("loc", [])),
                        "message": err.get("msg"),
                        "type": err.get("type"),
                    }
                    for err in exc.errors()
                ]
            },
            request,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 400: "bad_request"}
        return error_response(
            exc.status_code,
            codes.get(exc.status_code, "http_error"),
            str(exc.detail),
            None,
            request,
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # A constraint the service layer did not anticipate. The database kept
        # the ledger correct; the API should not pretend the write succeeded.
        logger.error("integrity_error", extra={"detail": str(exc.orig)})
        return error_response(
            409, "constraint_violation", "the write violated a database constraint", None, request
        )

    @app.exception_handler(OperationalError)
    async def handle_operational_error(request: Request, exc: OperationalError) -> JSONResponse:
        text = str(exc.orig)
        if "lock timeout" in text or "canceling statement" in text:
            return error_response(
                503,
                "lock_timeout",
                "the accounts involved are busy; retry with the same idempotency key",
                None,
                request,
            )
        logger.error("operational_error", extra={"detail": text})
        return error_response(503, "database_unavailable", "database is unavailable", None, request)

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": "1.0.0",
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
