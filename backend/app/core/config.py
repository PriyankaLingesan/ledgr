"""Application configuration.

All configuration is environment-driven so the same image runs locally, in CI
and on ECS without code changes.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
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

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_provider and self.ai_model and self.ai_api_key)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("LEDGR requires PostgreSQL")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
