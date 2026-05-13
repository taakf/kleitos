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
"""

from __future__ import annotations

import sqlite3
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
