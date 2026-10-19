"""Application configuration.

All configuration is environment-driven so the same image runs locally, in CI
and on ECS without code changes.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- service identity -------------------------------------------------
    app_name: str = "LEDGR"
    environment: str = Field(default="local")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # --- database ---------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://ledgr:ledgr@localhost:5433/ledgr",
        description=(
            "SQLAlchemy URL. Must be PostgreSQL: the ledger relies on row-level "
            "locking, partial unique indexes and immutability triggers."
        ),
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_statement_timeout_ms: int = 15000
    db_lock_timeout_ms: int = 5000
    sql_echo: bool = False

    # --- api --------------------------------------------------------------
    api_prefix: str = "/api/v1"
    # `NoDecode`: pydantic-settings otherwise tries to JSON-decode any `list`
    # typed env var *before* our own `mode="before"` validator ever runs -
    # "http://a,http://b" isn't valid JSON, so without this the app fails to
    # boot the moment CORS_ORIGINS is set to the comma-separated form
    # docker-compose.yml and the README both document.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:4173"]
    )

    # --- authentication -----------------------------------------------------
    # Sessions are opaque server-side tokens (see app.services.auth). Absolute
    # lifetime caps how long a stolen token is useful; idle timeout ends
    # abandoned sessions sooner.
    session_ttl_minutes: int = 8 * 60
    session_idle_minutes: int = 60
    # Consecutive failed logins before an account is locked, and for how long.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    min_password_length: int = 10
    # Public self-service signup creates a new organization with its own
    # OWNER. Turn it off (ALLOW_SIGNUP=false) on a deployment that should only
    # admit people an owner invites; set SIGNUP_TOKEN to require an invite code.
    allow_signup: bool = True
    signup_token: str = ""
    # Session cookie. SameSite=Lax works when the console and API share a site
    # (localhost, or one parent domain); a console on a different registrable
    # domain needs SameSite=None with Secure=true (HTTPS).
    session_cookie_name: str = "ledgr_session"
    session_cookie_samesite: str = "lax"
    session_cookie_secure: bool = False
    # Rate limits for the unauthenticated endpoints (per client IP, in-process).
    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_hour: int = 10
    # Optional bootstrap: `app.scripts.seed` creates the demo organization's
    # owner from these (a random password is generated and printed once if unset).
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # --- operational alert thresholds (deterministic rules, no AI) ----------
    alert_window_hours: int = 24
    alert_large_transaction_minor: int = 100_000_00  # 100,000.00 in a 2-dp currency
    alert_repeat_threshold: int = 3  # retries / failures / reversals within the window
    alert_balance_floor_warning_minor: int = 500_00  # within 500.00 of a balance floor

    # --- ledger policy ----------------------------------------------------
    max_entries_per_transaction: int = 64
    default_page_size: int = 50
    max_page_size: int = 200

    # --- AI (LEDGR Intelligence) -------------------------------------------
    # Deliberately provider-agnostic: the AI layer is an assistant bolted onto
    # a deterministic ledger, never the other way round, so it is entirely
    # legitimate for it to be unconfigured. When it is, `ai_configured` is
    # False and every AI endpoint answers with a clear "unavailable" error -
    # the core ledger (accounts, transactions, posting) never checks this
    # flag and never depends on it.
    ai_provider: str = Field(default="", description="'' (disabled) | 'anthropic' | 'openai'")
    ai_model: str = Field(default="")
    ai_api_key: str = Field(default="")
    ai_base_url: str = Field(
        default="", description="Override, e.g. for an OpenAI-compatible proxy"
    )
    ai_max_tool_iterations: int = Field(
        default=4, description="Cap on tool-call round trips per Ask LEDGR question"
    )
    ai_request_timeout_seconds: float = 30.0
    # Per authenticated caller; the copilot calls a metered external API.
    ai_rate_limit_per_minute: int = 20

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_provider and self.ai_model and self.ai_api_key)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        """Browser sessions are cookies and CORS is `allow_credentials=True`; a
        wildcard origin would let any website act as the signed-in user."""
        if any(origin.strip() == "*" for origin in value):
            raise ValueError("CORS_ORIGINS must list explicit origins, not '*'")
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_driver_scheme(cls, value: object) -> object:
        """Managed providers (e.g. Render) hand out a bare `postgresql://` URL.

        SQLAlchemy resolves that scheme to psycopg2 by default, which isn't
        installed here - only psycopg3 is. Rewriting to `postgresql+psycopg://`
        is a no-op for URLs that already specify a driver (docker-compose and
        `.env.example` both do), so local and CI behaviour is unchanged.
        """
        if isinstance(value, str) and value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("LEDGR requires PostgreSQL")
        return value

    @model_validator(mode="after")
    def _cookie_settings_are_coherent(self) -> "Settings":
        samesite = self.session_cookie_samesite.lower()
        if samesite not in {"lax", "strict", "none"}:
            raise ValueError("SESSION_COOKIE_SAMESITE must be lax, strict or none")
        # Browsers drop a SameSite=None cookie that is not Secure, so the
        # sign-in would silently never stick; refuse the combination up front.
        if samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
