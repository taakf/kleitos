"""
Simple migration system for Axion.

Uses SQLAlchemy ``metadata.create_all`` to ensure all tables exist.
Logs which tables were created vs. already present.  Designed to be
called at application startup.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from src.database.connection import get_engine
from src.database.models import Base

logger = logging.getLogger(__name__)


async def _get_existing_tables(conn) -> set[str]:  # noqa: ANN001
    """Return the set of table names that already exist in the database."""

    def _inspect_tables(sync_conn):  # noqa: ANN001, ANN202
        inspector = inspect(sync_conn)
        return set(inspector.get_table_names())

    return await conn.run_sync(_inspect_tables)


async def _ensure_columns(conn) -> int:
    """Add any columns defined in the ORM but missing from the live schema.

    SQLAlchemy's ``create_all`` only creates *tables* -- it does **not**
    alter existing ones.  This helper bridges that gap for simple
    ``ALTER TABLE ADD COLUMN`` operations.

    Returns the number of columns added.
    """

    def _sync_ensure_columns(sync_conn) -> int:  # noqa: ANN001
        inspector = inspect(sync_conn)
        added = 0

        for table_name, table in Base.metadata.tables.items():
            if table_name not in inspector.get_table_names():
                continue  # table will be created by create_all

            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}

            for col in table.columns:
                if col.name in existing_cols:
                    continue

                # Build ALTER TABLE statement
                col_type = col.type.compile(dialect=sync_conn.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default = ""
                if col.default is not None and col.default.is_scalar:
                    default = f" DEFAULT {col.default.arg!r}"

                ddl = (
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {col.name} {col_type} {nullable}{default}"
                )
                sync_conn.execute(text(ddl))
                logger.info("Added column %s.%s (%s)", table_name, col.name, col_type)
                added += 1

        return added

    return await conn.run_sync(_sync_ensure_columns)


async def run_migrations() -> None:
    """
    Ensure every ORM-defined table exists in the database and all
    model-defined columns are present.

    Steps:
      1. ``create_all`` -- creates any missing tables.
      2. ``_ensure_columns`` -- adds any missing columns to existing tables.

    Safe to call on every startup.
    """
    engine = get_engine()

    async with engine.begin() as conn:
        before = await _get_existing_tables(conn)

        await conn.run_sync(Base.metadata.create_all)

        after = await _get_existing_tables(conn)

        # --- Column-level migrations -----------------------------------------
        cols_added = await _ensure_columns(conn)
        if cols_added:
            logger.info("Column migration complete: %d column(s) added", cols_added)

    expected = set(Base.metadata.tables.keys())
    created = after - before
    already_existed = before & expected

    if created:
        logger.info(
            "Created %d table(s): %s",
            len(created),
            ", ".join(sorted(created)),
        )
    if already_existed:
        logger.info(
            "Verified %d existing table(s): %s",
            len(already_existed),
            ", ".join(sorted(already_existed)),
        )

    missing = expected - after
    if missing:
        logger.error(
            "MIGRATION FAILURE — %d expected table(s) missing after create_all: %s",
            len(missing),
            ", ".join(sorted(missing)),
        )
        raise RuntimeError(
            f"Failed to create tables: {', '.join(sorted(missing))}"
        )

    # Log WAL mode confirmation
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        logger.info("Database ready — journal_mode=%s, tables=%d", mode, len(after))
