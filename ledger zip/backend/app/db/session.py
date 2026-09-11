"""Engine, session factory and unit-of-work helpers.

Statement and lock timeouts are set on every connection: a ledger write that
blocks forever on a row lock is a production incident, so the database is told
to give up first and surface a retryable error.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine: Engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    future=True,
)


@event.listens_for(engine, "connect")
def _set_connection_timeouts(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET statement_timeout = {settings.db_statement_timeout_ms}")
        cursor.execute(f"SET lock_timeout = {settings.db_lock_timeout_ms}")
        cursor.execute("SET idle_in_transaction_session_timeout = 30000")
    dbapi_connection.commit()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone unit of work for scripts and out-of-request work.

    Used by the idempotency service, which deliberately needs a *second*
    connection so its claim can commit independently of the ledger write.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency.

    Services own commits: a request handler that never reaches an explicit
    commit leaves nothing behind.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
