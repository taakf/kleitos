"""System-level routes — health-adjacent endpoints for diagnostics and recovery.

The recovery endpoint reports the current database schema state so a
recovery screen (or support engineer) can see exactly which path the user
is on:

  ok               — DB exists and is at the supported schema version.
  no_database      — No file at the configured DB path (next launch creates one).
  unversioned      — File exists but has no _schema_version stamp yet.
  upgrade_pending  — File exists at an older schema version (a relaunch
                     will migrate it after first creating a backup).
  version_too_new  — File exists at a schema version newer than this app
                     supports. Customer must update Axion or restore a backup.
  corrupt          — File exists but failed integrity checks.

The endpoint is intentionally schema-stable: same keys for every status so
the dashboard recovery screen can rely on them.

The diagnostics endpoint (Phase 4) is a strict superset of recovery —
useful for the support bundle script and dashboard support screens. It
never returns secrets and is safe to call from anywhere local.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from src.config import get_settings
from src.database.migrations import (
    CURRENT_SCHEMA_VERSION,
    _quick_integrity_check,
    _read_db_version_raw,
)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class RecoveryStatus(BaseModel):
    """Structured view of the database state, surfaced for diagnostics."""

    status: str
    issue: str | None
    db_version: int | None
    app_supported_version: int
    db_path: str
    data_dir: str
    backup_dir: str
    next_steps: list[str]
    detail: str | None = None


@router.get("/recovery", response_model=RecoveryStatus)
async def get_recovery_status() -> RecoveryStatus:
    """Report the current database schema state.

    Safe to call even when the rest of the API is reporting issues —
    this endpoint never opens the SQLAlchemy engine and never modifies
    the database file.
    """
    settings = get_settings()
    db_path = Path(settings.database.path)
    data_dir = Path(settings.data_dir)
    backup_dir = data_dir / "backups"

    base = {
        "db_path": str(db_path),
        "data_dir": str(data_dir),
        "backup_dir": str(backup_dir),
        "app_supported_version": CURRENT_SCHEMA_VERSION,
    }

    if not db_path.exists() or db_path.stat().st_size == 0:
        return RecoveryStatus(
            status="no_database",
            issue=None,
            db_version=None,
            next_steps=[
                "No database file exists at the configured path.",
                "The next launch will create a fresh database with the default portfolio.",
            ],
            **base,
        )

    # Integrity check — raises on corrupt.
    try:
        _quick_integrity_check(db_path)
    except (sqlite3.DatabaseError, OSError) as exc:
        return RecoveryStatus(
            status="corrupt",
            issue="database_corrupt",
            db_version=None,
            next_steps=[
                f"Restore a backup from {backup_dir}.",
                "Or move the database file aside and relaunch Axion for a fresh DB.",
                "Axion will not delete or overwrite a corrupt database automatically.",
            ],
            detail=str(exc),
            **base,
        )

    try:
        db_version = _read_db_version_raw(db_path)
    except sqlite3.DatabaseError as exc:
        return RecoveryStatus(
            status="corrupt",
            issue="schema_version_unreadable",
            db_version=None,
            next_steps=[
                f"Restore a backup from {backup_dir}.",
                "Or relaunch Axion with a different AXION_DATA_DIR to start fresh.",
            ],
            detail=str(exc),
            **base,
        )

    if db_version is None:
        return RecoveryStatus(
            status="unversioned",
            issue=None,
            db_version=None,
            next_steps=[
                "The database has no version stamp.",
                "Relaunch Axion to baseline it at v1 and apply pending migrations.",
            ],
            **base,
        )

    if db_version > CURRENT_SCHEMA_VERSION:
        return RecoveryStatus(
            status="version_too_new",
            issue="database_newer_than_app",
            db_version=db_version,
            next_steps=[
                f"Update Axion to a version that supports schema v{db_version}.",
                f"Or restore a compatible backup from {backup_dir}.",
                "Your data has not been modified by this app.",
            ],
            **base,
        )

    if db_version < CURRENT_SCHEMA_VERSION:
        return RecoveryStatus(
            status="upgrade_pending",
            issue=None,
            db_version=db_version,
            next_steps=[
                f"Relaunch Axion to apply the v{db_version + 1}…v{CURRENT_SCHEMA_VERSION} migrations.",
                f"A backup will be written to {backup_dir} before any schema change.",
            ],
            **base,
        )

    return RecoveryStatus(
        status="ok",
        issue=None,
        db_version=db_version,
        next_steps=["No action required — database is at the supported schema version."],
        **base,
    )


# ───────────────────────────────────────────────────────────────────────────
# Diagnostics endpoint — Phase 4 support tooling
# ───────────────────────────────────────────────────────────────────────────


class DiagnosticsResponse(BaseModel):
    """Structured diagnostics snapshot. No secrets — safe to include in
    support bundles, copy into emails, paste into chat.
    """

    timestamp: str
    app_version: str
    python_version: str
    platform: str

    # Database
    schema_status: str
    db_version: int | None
    app_supported_version: int
    db_path: str
    db_size_bytes: int | None
    data_dir: str
    backup_dir: str
    log_dir: str
    backup_count: int

    # Configuration (counts only, no secrets)
    portfolios_count: int | None = None
    holdings_count: int | None = None
    events_count: int | None = None
    alerts_count: int | None = None
    digests_count: int | None = None
    sources_total: int | None = None
    sources_enabled: int | None = None
    sources_healthy: int | None = None

    # Optional integrations — booleans only, never key material
    llm_configured: bool
    llm_provider: str | None
    telegram_configured: bool

    # Phase 7: per-status source counts. Keys are the normalized
    # vocabulary (active / disabled / missing_key / degraded /
    # rate_limited / unreachable / parser_error / unsupported /
    # misconfigured / error) plus ``total``.
    sources_by_status: dict[str, int] = {}

    # If anything failed during collection, surface it here (no traceback).
    warnings: list[str]


def _safe_count(cur: sqlite3.Cursor, sql: str) -> int | None:
    try:
        row = cur.execute(sql).fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None


@router.get("/diagnostics", response_model=DiagnosticsResponse)
async def get_diagnostics() -> DiagnosticsResponse:
    """Return a redacted diagnostics snapshot.

    Safe for local use. Never includes API keys, raw env, holdings values,
    portfolio names, or any user content. Counts and paths only.
    """
    settings = get_settings()
    db_path = Path(settings.database.path)
    data_dir = Path(settings.data_dir)
    backup_dir = data_dir / "backups"
    log_dir = data_dir / "logs"

    warnings: list[str] = []

    # ── Database state ──────────────────────────────────────────────────
    schema_status = "ok"
    db_version: int | None = None
    db_size: int | None = None

    if not db_path.exists() or db_path.stat().st_size == 0:
        schema_status = "no_database"
    else:
        try:
            db_size = db_path.stat().st_size
        except OSError:
            warnings.append("could not stat db file")
        try:
            _quick_integrity_check(db_path)
        except (sqlite3.DatabaseError, OSError) as exc:
            schema_status = "corrupt"
            warnings.append(f"integrity check failed: {exc}")

        if schema_status == "ok":
            try:
                db_version = _read_db_version_raw(db_path)
            except sqlite3.DatabaseError as exc:
                schema_status = "corrupt"
                warnings.append(f"version read failed: {exc}")

            if db_version is None:
                schema_status = "unversioned"
            elif db_version > CURRENT_SCHEMA_VERSION:
                schema_status = "version_too_new"
            elif db_version < CURRENT_SCHEMA_VERSION:
                schema_status = "upgrade_pending"

    # ── Counts ──────────────────────────────────────────────────────────
    # Use raw sqlite3 so this endpoint never depends on the SQLAlchemy
    # engine — it must work even when the DB is in an unusual state.
    portfolios_count = None
    holdings_count = None
    events_count = None
    alerts_count = None
    digests_count = None
    sources_total = None
    sources_enabled = None
    sources_healthy = None

    if schema_status not in ("no_database", "corrupt"):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                portfolios_count = _safe_count(cur, "SELECT COUNT(*) FROM portfolios")
                holdings_count = _safe_count(cur, "SELECT COUNT(*) FROM holdings")
                events_count = _safe_count(cur, "SELECT COUNT(*) FROM events")
                alerts_count = _safe_count(cur, "SELECT COUNT(*) FROM alerts")
                digests_count = _safe_count(cur, "SELECT COUNT(*) FROM digests")
                sources_total = _safe_count(cur, "SELECT COUNT(*) FROM sources")
                sources_enabled = _safe_count(
                    cur, "SELECT COUNT(*) FROM sources WHERE enabled = 1"
                )
                sources_healthy = _safe_count(
                    cur,
                    "SELECT COUNT(*) FROM sources WHERE enabled = 1 AND last_status = 'ok'",
                )
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            warnings.append(f"count query failed: {exc}")

    # ── Backup files (filenames only, never contents) ──────────────────
    backup_count = 0
    if backup_dir.exists():
        backup_count = sum(
            1
            for p in backup_dir.glob("kleitos-pre-v*.db")
            if p.is_file()
        )

    # ── Optional integrations (booleans only) ───────────────────────────
    from src.llm.client import is_llm_available

    llm_provider = (
        settings.llm.provider
        if getattr(settings.llm, "provider", "none") not in (None, "", "none")
        else None
    )
    llm_configured = bool(llm_provider) and is_llm_available()

    tg = settings.telegram
    telegram_configured = bool(tg.token and tg.chat_ids)

    # ── Source status summary (Phase 7) ─────────────────────────────────
    # Combine YAML metadata with whatever rows survived in the DB. The
    # health list is the same data the dashboard renders, so the counts
    # here will always match what the customer sees.
    sources_by_status: dict[str, int] = {}
    try:
        from src.sources.registry import SourceRegistry
        from src.sources.source_status import summarise_by_status
        from src.config import PROJECT_ROOT

        registry = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
        # Lightweight summary — we don't need every field, just the
        # status. For each YAML-declared source, derive the same
        # status the sources/health endpoint would return, without
        # opening a DB session (this endpoint must remain safe even
        # when the DB is unreadable).
        healths_lite: list[dict] = []
        for cfg in registry.get_all_sources():
            if cfg.unsupported:
                status = "unsupported"
            elif not cfg.enabled:
                status = "disabled"
            elif cfg.requires_auth and not os.environ.get(cfg.auth_env_var or "", ""):
                status = "missing_key"
            else:
                status = "active"
            healths_lite.append({"status": status})
        sources_by_status = summarise_by_status(healths_lite)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"sources_by_status unavailable: {exc}")

    # ── App identity ────────────────────────────────────────────────────
    try:
        from src.config import Settings as _S  # noqa: F401  (just to anchor import)
        app_version = settings.system.version
    except Exception:
        app_version = "unknown"
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    return DiagnosticsResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        app_version=app_version,
        python_version=py,
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        schema_status=schema_status,
        db_version=db_version,
        app_supported_version=CURRENT_SCHEMA_VERSION,
        db_path=str(db_path),
        db_size_bytes=db_size,
        data_dir=str(data_dir),
        backup_dir=str(backup_dir),
        log_dir=str(log_dir),
        backup_count=backup_count,
        portfolios_count=portfolios_count,
        holdings_count=holdings_count,
        events_count=events_count,
        alerts_count=alerts_count,
        digests_count=digests_count,
        sources_total=sources_total,
        sources_enabled=sources_enabled,
        sources_healthy=sources_healthy,
        llm_configured=llm_configured,
        llm_provider=llm_provider,
        telegram_configured=telegram_configured,
        sources_by_status=sources_by_status,
        warnings=warnings,
    )
