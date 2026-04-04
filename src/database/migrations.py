"""
Schema migration system for Axion.

Manages database schema evolution through numbered migration steps.
Each step is a function that receives a synchronous connection and
performs DDL operations.  The current schema version is tracked in
the ``_schema_version`` table.

Design principles:
  - Safe to call on every startup (idempotent).
  - Existing installs are baselined automatically.
  - Fresh installs get the full schema via ``create_all`` + version stamp.
  - Future migrations (e.g. multi-portfolio) are registered as numbered steps.
  - The app refuses to start if the DB is from a newer version than the code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from src.database.connection import get_engine
from src.database.models import Base

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Current schema version — increment this when adding a new migration step.
# ---------------------------------------------------------------------------
CURRENT_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Migration registry — ordered list of (version, description, function).
# Each function receives a synchronous SQLAlchemy connection.
#
# Convention:
#   - Version 1 is the baseline (tables created by create_all).
#   - Version 2+ are incremental migrations added as the schema evolves.
# ---------------------------------------------------------------------------
_MIGRATIONS: list[tuple[int, str, callable]] = [
    # (1, "baseline", None)  — version 1 is implicit (create_all)
    # Future example:
    # (2, "add portfolio table and portfolio_id columns", _migrate_v2),
]


async def _get_existing_tables(conn) -> set[str]:
    """Return the set of table names that already exist in the database."""

    def _inspect_tables(sync_conn):
        inspector = inspect(sync_conn)
        return set(inspector.get_table_names())

    return await conn.run_sync(_inspect_tables)


async def _ensure_columns(conn) -> int:
    """Add any columns defined in the ORM but missing from the live schema.

    SQLAlchemy's ``create_all`` only creates *tables* — it does **not**
    alter existing ones.  This helper bridges that gap for simple
    ``ALTER TABLE ADD COLUMN`` operations.

    Returns the number of columns added.
    """

    def _sync_ensure_columns(sync_conn) -> int:
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
                elif col.server_default is not None:
                    default = f" DEFAULT {col.server_default.arg.text}"

                ddl = (
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {col.name} {col_type} {nullable}{default}"
                )
                sync_conn.execute(text(ddl))
                logger.info("Added column %s.%s (%s)", table_name, col.name, col_type)
                added += 1

        return added

    return await conn.run_sync(_sync_ensure_columns)


def _get_db_version(sync_conn) -> int | None:
    """Read the current schema version from _schema_version table.

    Returns None if the table doesn't exist yet.
    """
    inspector = inspect(sync_conn)
    if "_schema_version" not in inspector.get_table_names():
        return None
    result = sync_conn.execute(text("SELECT version FROM _schema_version WHERE id = 1"))
    row = result.fetchone()
    return row[0] if row else None


def _set_db_version(sync_conn, version: int, description: str) -> None:
    """Write the schema version to the _schema_version table."""
    now = datetime.now(timezone.utc).isoformat()
    # Upsert: try UPDATE first, then INSERT if no rows affected
    result = sync_conn.execute(
        text("UPDATE _schema_version SET version = :v, applied_at = :at, description = :desc WHERE id = 1"),
        {"v": version, "at": now, "desc": description},
    )
    if result.rowcount == 0:
        sync_conn.execute(
            text("INSERT INTO _schema_version (id, version, applied_at, description) VALUES (1, :v, :at, :desc)"),
            {"v": version, "at": now, "desc": description},
        )


async def run_migrations() -> None:
    """
    Ensure the database schema is up to date.

    Strategy:
      1. If the DB is brand new (no tables), run ``create_all`` and stamp
         with the current version.
      2. If the DB has tables but no version table, this is an existing
         install from before versioning — baseline it at version 1.
      3. If the DB version matches the code, do nothing (fast path).
      4. If the DB version is higher than the code, refuse to start
         (prevents running old code against a newer schema).
      5. If the DB version is lower, apply incremental migrations.

    Always runs ``_ensure_columns`` to handle simple additive schema
    changes that don't need a full migration step.
    """
    engine = get_engine()

    async with engine.begin() as conn:
        existing_tables = await _get_existing_tables(conn)

        # --- Fresh install: no tables at all ---
        if not existing_tables:
            logger.info("Fresh database — creating all tables")
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(
                lambda sc: _set_db_version(sc, CURRENT_SCHEMA_VERSION, "initial schema")
            )
            logger.info("Database created at schema version %d", CURRENT_SCHEMA_VERSION)
            return

        # --- Ensure all tables exist (handles models added since last run) ---
        await conn.run_sync(Base.metadata.create_all)

        # --- Ensure all columns exist (handles columns added to existing tables) ---
        cols_added = await _ensure_columns(conn)
        if cols_added:
            logger.info("Added %d new column(s) to existing tables", cols_added)

        # --- Check/set schema version ---
        db_version = await conn.run_sync(_get_db_version)

        if db_version is None:
            # Existing install from before versioning — baseline at v1
            logger.info("Existing database without version tracking — baselining at v1")
            await conn.run_sync(
                lambda sc: _set_db_version(sc, 1, "baseline from pre-versioned install")
            )
            db_version = 1

        # --- Version compatibility check ---
        if db_version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version ({db_version}) is newer than this "
                f"application supports ({CURRENT_SCHEMA_VERSION}). "
                f"Please upgrade Axion or restore from a compatible backup."
            )

        if db_version == CURRENT_SCHEMA_VERSION:
            logger.info("Schema version %d — up to date", db_version)
        else:
            # Apply incremental migrations
            for version, description, migrate_fn in _MIGRATIONS:
                if version <= db_version:
                    continue  # already applied
                if version > CURRENT_SCHEMA_VERSION:
                    break  # shouldn't happen, but be safe

                logger.info("Applying migration v%d: %s", version, description)
                if migrate_fn is not None:
                    await conn.run_sync(migrate_fn)
                await conn.run_sync(
                    lambda sc, v=version, d=description: _set_db_version(sc, v, d)
                )
                logger.info("Migration v%d complete", version)

    # Final verification
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA journal_mode"))
        mode = result.scalar()
        final_tables = await _get_existing_tables(conn)
        logger.info(
            "Database ready — schema=v%d, journal=%s, tables=%d",
            CURRENT_SCHEMA_VERSION,
            mode,
            len(final_tables),
        )
