"""
SQLite connection manager for Axion.

Provides an async SQLAlchemy 2.0 engine with WAL mode, busy-timeout
pragmas, and an ``get_db()`` async context manager for session access.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_settings
from src.database.models import Base

logger = logging.getLogger(__name__)

# Module-level singletons — initialised lazily via ``get_engine()``.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ensure_data_dirs(db_path: Path) -> None:
    """Create the parent directories for the database file if missing."""
    db_dir = db_path.parent
    if not db_dir.exists():
        db_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created database directory: %s", db_dir)



def get_engine() -> AsyncEngine:
    """
    Return the singleton async engine, creating it on first call.

    The engine is configured for a single-writer SQLite database with
    WAL mode and appropriate pragmas.
    """
    global _engine

    if _engine is not None:
        return _engine

    settings = get_settings()
    db_path = settings.database.path
    _ensure_data_dirs(db_path)

    url = f"sqlite+aiosqlite:///{db_path}"
    _engine = create_async_engine(
        url,
        echo=settings.system.environment == "development",
        # pool_pre_ping is unnecessary for SQLite (local file, never stale)
    )

    # Capture settings once for the pragma closure (avoid re-loading per connection)
    _cached_settings = settings

    def _set_pragmas_with_cached_settings(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        if _cached_settings.database.wal_mode:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(_cached_settings.database.busy_timeout)}")
        cursor.execute(
            f"PRAGMA journal_size_limit={int(_cached_settings.database.journal_size_limit)}"
        )
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    event.listen(_engine.sync_engine, "connect", _set_pragmas_with_cached_settings)

    logger.info("SQLite async engine created — %s", db_path)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory, creating it on first call."""
    global _session_factory

    if _session_factory is not None:
        return _session_factory

    engine = get_engine()
    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _session_factory


@asynccontextmanager
async def get_db() -> AsyncIterator[AsyncSession]:
    """
    Async context manager that yields a transactional ``AsyncSession``.

    Usage::

        async with get_db() as session:
            result = await session.execute(select(Holding))
            # ... mutate ...
            await session.commit()   # callers commit explicitly

    The session is **not** auto-committed on exit.  Callers that modify
    data must call ``await session.commit()`` themselves so that the
    transaction boundary is explicit.  On exception the session is rolled
    back automatically.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_database() -> None:
    """
    Create all tables defined in the ORM metadata.

    Safe to call multiple times — ``create_all`` is a no-op for tables
    that already exist.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured via metadata.create_all")

    # Quick sanity check: verify WAL mode is active
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        logger.info("SQLite journal_mode = %s", mode)


async def close_database() -> None:
    """Dispose of the engine and release all connections.

    Safe to call repeatedly — if the engine was never created or has
    already been disposed, this is a no-op.  Phase 9K hardened: always
    clears the singletons even if ``dispose`` raises, so a partially-
    failed shutdown doesn't leak a dangling engine reference.
    """
    global _engine, _session_factory

    if _engine is None:
        _session_factory = None
        return

    engine_to_close = _engine
    # Clear BEFORE awaiting dispose so any concurrent call sees the
    # cleared state and doesn't try to reuse the engine being torn down.
    _engine = None
    _session_factory = None
    try:
        await engine_to_close.dispose()
        logger.info("Database engine disposed")
    except Exception as exc:
        logger.warning("Database engine dispose raised: %s", exc)


def reset_connection_state() -> None:
    """Synchronously drop the engine + session-factory singletons.

    Phase 9K hardening helper for tests.  Unlike :func:`close_database`,
    this does NOT await ``engine.dispose()`` — it simply nulls the
    module-level references so the next ``get_engine()`` call rebuilds
    them from the current settings.  Call this after swapping
    ``KLEITOS_DB_PATH`` in a test fixture so the next query hits the
    new DB without needing an event loop.

    The underlying aiosqlite connections (if any) are left to their
    own teardown: SQLite files are append-only WAL mode, and the
    thread-per-connection aiosqlite pool exits gracefully when the
    interpreter shuts down.  Tests that need a truly clean dispose
    should call :func:`close_database` from within an async context
    before calling this helper.
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None
