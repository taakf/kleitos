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
CURRENT_SCHEMA_VERSION = 8

# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

_DEFAULT_PORTFOLIO_ID = "default"


def _migrate_v2(sync_conn) -> None:
    """V2: Add multi-portfolio support.

    1. Create portfolios table.
    2. Insert default "Main Portfolio" with id='default'.
    3. Add portfolio_id FK to holdings (update existing rows).
    4. Add portfolio_id to alerts and digests (nullable, backfill).

    Note: SQLite cannot add FK constraints via ALTER TABLE, so we add
    the column without the FK and rely on the ORM for referential integrity.
    The FK is defined in the model for fresh installs (create_all).
    """
    now = datetime.now(timezone.utc).isoformat()
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Create portfolios table if not exists
    if "portfolios" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                is_default INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_portfolios_is_default ON portfolios (is_default)"
        ))
        logger.info("Created portfolios table")

    # 2. Insert default portfolio if not exists
    existing = sync_conn.execute(
        text("SELECT id FROM portfolios WHERE id = :id"),
        {"id": _DEFAULT_PORTFOLIO_ID},
    ).fetchone()
    if not existing:
        sync_conn.execute(text("""
            INSERT INTO portfolios (id, name, description, base_currency, is_default, created_at, updated_at)
            VALUES (:id, :name, :desc, :ccy, 1, :now, :now)
        """), {
            "id": _DEFAULT_PORTFOLIO_ID,
            "name": "Main Portfolio",
            "desc": "Default portfolio created during upgrade",
            "ccy": "USD",
            "now": now,
        })
        logger.info("Created default portfolio '%s'", _DEFAULT_PORTFOLIO_ID)

    # 3. Update holdings: change portfolio_id from 'main' to 'default'
    #    (the old hardcoded value was 'main')
    if "holdings" in existing_tables:
        sync_conn.execute(text("""
            UPDATE holdings SET portfolio_id = :new_id
            WHERE portfolio_id = 'main' OR portfolio_id IS NULL
        """), {"new_id": _DEFAULT_PORTFOLIO_ID})
        logger.info("Migrated holdings to default portfolio")

    # 4. Add portfolio_id to alerts if missing
    if "alerts" in existing_tables:
        alert_cols = {c["name"] for c in inspector.get_columns("alerts")}
        if "portfolio_id" not in alert_cols:
            sync_conn.execute(text(
                "ALTER TABLE alerts ADD COLUMN portfolio_id TEXT"
            ))
            sync_conn.execute(text(
                "UPDATE alerts SET portfolio_id = :pid"
            ), {"pid": _DEFAULT_PORTFOLIO_ID})
            logger.info("Added portfolio_id to alerts")

    # 5. Add portfolio_id to digests if missing
    if "digests" in existing_tables:
        digest_cols = {c["name"] for c in inspector.get_columns("digests")}
        if "portfolio_id" not in digest_cols:
            sync_conn.execute(text(
                "ALTER TABLE digests ADD COLUMN portfolio_id TEXT"
            ))
            sync_conn.execute(text(
                "UPDATE digests SET portfolio_id = :pid"
            ), {"pid": _DEFAULT_PORTFOLIO_ID})
            logger.info("Added portfolio_id to digests")


def _migrate_v3(sync_conn) -> None:
    """V3: Deterministic macro factor reasoning (Phase 9A).

    1. Add ``channel`` and ``details_json`` nullable columns to
       ``event_links`` so factor-driven links can carry a factor key
       and a structured causal chain without repurposing
       ``link_target`` (which runtime consumers treat as a holding
       UUID).
    2. Create ``holding_factor_sensitivities`` (per-holding factor
       weights) if missing.
    3. Create ``macro_factor_events`` (one row per classified
       event-factor pair) if missing.

    All operations are additive and idempotent. This migration never
    touches existing event_link rows or direct matching behavior.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    # 1. Add event_links.channel and event_links.details_json if missing
    if "event_links" in existing_tables:
        el_cols = {c["name"] for c in inspector.get_columns("event_links")}
        if "channel" not in el_cols:
            sync_conn.execute(text(
                "ALTER TABLE event_links ADD COLUMN channel TEXT"
            ))
            logger.info("Added event_links.channel column")
        if "details_json" not in el_cols:
            sync_conn.execute(text(
                "ALTER TABLE event_links ADD COLUMN details_json TEXT"
            ))
            logger.info("Added event_links.details_json column")

    # 2. Create holding_factor_sensitivities table if missing
    if "holding_factor_sensitivities" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE holding_factor_sensitivities (
                id TEXT PRIMARY KEY,
                holding_id TEXT NOT NULL,
                factor TEXT NOT NULL,
                sensitivity REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (holding_id) REFERENCES holdings(id),
                CONSTRAINT uq_holding_factor_sensitivities_holding_factor
                    UNIQUE (holding_id, factor)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_factor_sensitivities_holding_id "
            "ON holding_factor_sensitivities (holding_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_factor_sensitivities_factor "
            "ON holding_factor_sensitivities (factor)"
        ))
        logger.info("Created holding_factor_sensitivities table")

    # 3. Create macro_factor_events table if missing
    if "macro_factor_events" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE macro_factor_events (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                factor TEXT NOT NULL,
                direction TEXT NOT NULL,
                magnitude TEXT NOT NULL DEFAULT 'unknown',
                confidence REAL NOT NULL,
                rationale TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events(id),
                CONSTRAINT uq_macro_factor_events_event_factor
                    UNIQUE (event_id, factor)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_macro_factor_events_event_id "
            "ON macro_factor_events (event_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_macro_factor_events_factor "
            "ON macro_factor_events (factor)"
        ))
        logger.info("Created macro_factor_events table")


def _migrate_v4(sync_conn) -> None:
    """V4: Deterministic relationship graph (Phase 9D).

    1. Create ``holding_relationships`` table if missing.  Rows are
       anchored to ``holding_id`` so portfolio correctness flows
       naturally through the FK — there is no separate portfolio
       column and no risk of cross-portfolio leakage.
    2. Add supporting indexes on the join keys used by the runtime
       matcher: holding_id (for bulk load), related_ticker (for
       ticker hit lookups), related_entity_key, and relationship_type.

    All operations are additive and idempotent.  Nothing existing is
    touched; direct matching, factor links, and chain data continue
    to work unchanged.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    if "holding_relationships" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE holding_relationships (
                id TEXT PRIMARY KEY,
                holding_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                related_ticker TEXT,
                related_entity_key TEXT,
                related_name TEXT,
                strength REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT 'seed',
                description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (holding_id) REFERENCES holdings(id),
                CONSTRAINT uq_holding_relationships_unique_edge
                    UNIQUE (holding_id, relationship_type, related_ticker, related_entity_key)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_relationships_holding_id "
            "ON holding_relationships (holding_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_relationships_related_ticker "
            "ON holding_relationships (related_ticker)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_relationships_related_entity_key "
            "ON holding_relationships (related_entity_key)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_holding_relationships_type "
            "ON holding_relationships (relationship_type)"
        ))
        logger.info("Created holding_relationships table (Phase 9D)")


def _migrate_v5(sync_conn) -> None:
    """V5: Telegram session + delivery bookkeeping (Phase 9F).

    1. Create ``telegram_sessions`` (per-chat active portfolio pin).
    2. Create ``telegram_deliveries`` (per-chat per-alert audit trail
       with dedupe + cooldown bookkeeping).

    Both tables are additive.  A fresh install gets them via
    ``create_all`` on the model metadata; this migration step only
    runs when we're upgrading a pre-9F database in place.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    if "telegram_sessions" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE telegram_sessions (
                chat_id INTEGER PRIMARY KEY,
                active_portfolio_id TEXT NOT NULL DEFAULT 'default',
                updated_at TEXT NOT NULL
            )
        """))
        logger.info("Created telegram_sessions table (Phase 9F)")

    if "telegram_deliveries" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE telegram_deliveries (
                id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                alert_id TEXT NOT NULL,
                portfolio_id TEXT,
                dedup_key TEXT,
                status TEXT NOT NULL,
                error TEXT,
                sent_at TEXT NOT NULL,
                CONSTRAINT uq_telegram_deliveries_chat_alert
                    UNIQUE (chat_id, alert_id)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_telegram_deliveries_alert_id "
            "ON telegram_deliveries (alert_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_telegram_deliveries_dedup_key "
            "ON telegram_deliveries (dedup_key)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_telegram_deliveries_sent_at "
            "ON telegram_deliveries (sent_at)"
        ))
        logger.info("Created telegram_deliveries table (Phase 9F)")


def _migrate_v6(sync_conn) -> None:
    """V6: Phase 9P notification inbox read state.

    1. Create ``notification_reads`` table if missing.  Tracks the
       operator's per-portfolio read state for inbox items composed
       from existing alert / digest / operator / recommendation rows.

    All operations are additive and idempotent.  No existing tables
    are touched.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    if "notification_reads" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE notification_reads (
                id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                notification_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                read_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CONSTRAINT uq_notification_reads_portfolio_key
                    UNIQUE (portfolio_id, notification_key)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_notification_reads_portfolio_id "
            "ON notification_reads (portfolio_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_notification_reads_source_type "
            "ON notification_reads (source_type)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_notification_reads_read_at "
            "ON notification_reads (read_at)"
        ))
        logger.info("Created notification_reads table (Phase 9P)")


def _migrate_v7(sync_conn) -> None:
    """V7: Phase 9T recommended action dismiss/read state.

    1. Create ``action_states`` table if missing.  Tracks per-portfolio
       lifecycle state (read / dismissed) and a fingerprint for the
       reappearance rule.

    All operations are additive and idempotent.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    if "action_states" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE action_states (
                id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                action_key TEXT NOT NULL,
                state TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CONSTRAINT uq_action_states_portfolio_key
                    UNIQUE (portfolio_id, action_key)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_action_states_portfolio_id "
            "ON action_states (portfolio_id)"
        ))
        sync_conn.execute(text(
            "CREATE INDEX ix_action_states_state "
            "ON action_states (state)"
        ))
        logger.info("Created action_states table (Phase 9T)")


def _migrate_v8(sync_conn) -> None:
    """V8: Phase 9U saved analytical views.

    1. Create ``saved_views`` table if missing.

    All operations are additive and idempotent.
    """
    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    if "saved_views" not in existing_tables:
        sync_conn.execute(text("""
            CREATE TABLE saved_views (
                id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                name TEXT NOT NULL,
                surface TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CONSTRAINT uq_saved_views_portfolio_name
                    UNIQUE (portfolio_id, name)
            )
        """))
        sync_conn.execute(text(
            "CREATE INDEX ix_saved_views_portfolio_id "
            "ON saved_views (portfolio_id)"
        ))
        logger.info("Created saved_views table (Phase 9U)")


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------
_MIGRATIONS: list[tuple[int, str, callable]] = [
    # Version 1 is implicit (create_all baseline).
    (2, "add multi-portfolio support", _migrate_v2),
    (3, "add deterministic macro factor reasoning (Phase 9A)", _migrate_v3),
    (4, "add deterministic relationship graph (Phase 9D)", _migrate_v4),
    (5, "add telegram session + delivery bookkeeping (Phase 9F)", _migrate_v5),
    (6, "add notification inbox read state (Phase 9P)", _migrate_v6),
    (7, "add recommended action dismiss/read state (Phase 9T)", _migrate_v7),
    (8, "add saved analytical views (Phase 9U)", _migrate_v8),
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
            # Insert default portfolio for fresh installs
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(text("""
                INSERT INTO portfolios (id, name, description, base_currency, is_default, created_at, updated_at)
                VALUES (:id, :name, :desc, :ccy, 1, :now, :now)
            """), {
                "id": _DEFAULT_PORTFOLIO_ID,
                "name": "Main Portfolio",
                "desc": "Default portfolio",
                "ccy": "USD",
                "now": now,
            })
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
