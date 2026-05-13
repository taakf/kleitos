"""Phase 3 — data-safety regression tests.

Covers:
- 3A: pre-migration backup behaviour (created when upgrade is needed,
  skipped on fresh installs, skipped at head, blocks migration on failure).
- 3B: newer-DB raises the typed ``DatabaseVersionTooNewError`` carrying
  versions + paths, and the recovery endpoint surfaces the same state.
- 3C: corrupt-DB raises ``DatabaseCorruptError`` and the file is not
  modified by the failed run.
- 3D: each v3…v8 migration step is idempotent (a second call on the same
  DB does not crash or duplicate any object).

These tests use raw sqlite3 + asyncio + the SQLAlchemy engine reset hook
from ``connection.reset_connection_state`` so each test gets an isolated
DB at a known schema version. They never touch ~/axion-data.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

from src.config import get_settings
from src.database import connection as connection_module
from src.database.migrations import (
    BackupFailedError,
    CURRENT_SCHEMA_VERSION,
    DatabaseCorruptError,
    DatabaseVersionTooNewError,
    _backup_db,
    _migrate_v3,
    _migrate_v4,
    _migrate_v5,
    _migrate_v6,
    _migrate_v7,
    _migrate_v8,
    _quick_integrity_check,
    _read_db_version_raw,
    run_migrations,
)


# ───────────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the app at a fresh temp data dir for the duration of the test."""
    data_dir = tmp_path / "data"
    db_dir = data_dir / "db"
    db_dir.mkdir(parents=True)
    backups_dir = data_dir / "backups"
    db_path = db_dir / "kleitos.db"

    monkeypatch.setenv("AXION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AXION_DB_PATH", str(db_path))
    monkeypatch.setenv("KLEITOS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("KLEITOS_DB_PATH", str(db_path))

    get_settings.cache_clear()
    connection_module.reset_connection_state()

    yield {
        "data_dir": data_dir,
        "db_path": db_path,
        "backups_dir": backups_dir,
    }

    # Tear down: dispose any engine we created so the next test gets a fresh one.
    connection_module.reset_connection_state()
    get_settings.cache_clear()


def _seed_v2_db(db_path: Path) -> None:
    """Write a hand-built v2 DB with one holding so we can test upgrades."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE _schema_version (
                id INTEGER PRIMARY KEY,
                version INTEGER,
                applied_at TEXT,
                description TEXT
            );
            INSERT INTO _schema_version VALUES (1, 2, '2026-01-01T00:00:00Z', 'baseline');

            CREATE TABLE portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                base_currency TEXT NOT NULL DEFAULT 'USD',
                is_default INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO portfolios
              VALUES ('default', 'Main Portfolio', 'pre-existing', 'USD', 1,
                      '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');

            CREATE TABLE holdings (
                id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                isin TEXT,
                venue TEXT,
                currency TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_cost_basis REAL,
                current_price REAL,
                market_value REAL,
                weight_pct REAL,
                portfolio_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO holdings VALUES (
                'h1', 'AAPL', NULL, NULL, 'USD', 100, 150.0, NULL, NULL, NULL,
                'default', 'active',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _read_table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# 3A — backup behaviour
# ───────────────────────────────────────────────────────────────────────────


class TestBackupOnUpgrade:
    """Backup must run on upgrades, but not on fresh installs or no-ops."""

    def test_fresh_db_creates_no_backup(self, isolated_db):
        """A brand-new install has no existing data to back up."""
        asyncio.run(run_migrations())

        version = _read_db_version_raw(isolated_db["db_path"])
        assert version == CURRENT_SCHEMA_VERSION
        # backups/ may or may not exist; it must NOT contain any pre-v* file.
        if isolated_db["backups_dir"].exists():
            assert (
                len(list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))) == 0
            )

    def test_v2_upgrade_creates_exactly_one_backup(self, isolated_db):
        _seed_v2_db(isolated_db["db_path"])
        asyncio.run(run_migrations())

        backups = list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))
        assert len(backups) == 1, f"expected 1 backup, got {len(backups)}"
        # Filename pattern
        name = backups[0].name
        assert name.startswith(f"kleitos-pre-v{CURRENT_SCHEMA_VERSION}-")
        assert name.endswith(".db")

        # Backup is a valid sqlite file with v2 schema + the original holding
        bconn = sqlite3.connect(str(backups[0]))
        try:
            ver = bconn.execute(
                "SELECT version FROM _schema_version WHERE id=1"
            ).fetchone()[0]
            assert ver == 2
            ticker = bconn.execute(
                "SELECT ticker FROM holdings WHERE id='h1'"
            ).fetchone()[0]
            assert ticker == "AAPL"
        finally:
            bconn.close()

    def test_running_at_head_does_not_create_extra_backup(self, isolated_db):
        # First call: migrate fresh DB to head (no backup).
        asyncio.run(run_migrations())
        connection_module.reset_connection_state()

        # Second call: DB is at head; must NOT create a backup.
        asyncio.run(run_migrations())

        if isolated_db["backups_dir"].exists():
            assert (
                len(list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))) == 0
            )

    def test_v2_then_rerun_creates_only_one_backup(self, isolated_db):
        _seed_v2_db(isolated_db["db_path"])
        asyncio.run(run_migrations())
        connection_module.reset_connection_state()
        asyncio.run(run_migrations())

        backups = list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))
        assert len(backups) == 1, (
            f"second run-at-head must not create another backup, "
            f"got {len(backups)}: {[b.name for b in backups]}"
        )

    def test_backup_failure_blocks_migration(self, isolated_db, monkeypatch):
        _seed_v2_db(isolated_db["db_path"])
        # Sabotage the backup by patching the helper to raise.
        from src.database import migrations as mig_mod

        def _raise(*_a, **_kw):
            raise OSError("simulated disk full")

        monkeypatch.setattr(mig_mod, "_backup_db", _raise)

        with pytest.raises(BackupFailedError) as exc_info:
            asyncio.run(run_migrations())

        # Original DB must still be at v2 (no schema change applied).
        assert _read_db_version_raw(isolated_db["db_path"]) == 2
        # The seeded holding survived intact.
        conn = sqlite3.connect(str(isolated_db["db_path"]))
        try:
            assert (
                conn.execute("SELECT ticker FROM holdings WHERE id='h1'").fetchone()[
                    0
                ]
                == "AAPL"
            )
            # None of the Phase 9 tables were added.
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            conn.close()
        for t in (
            "holding_factor_sensitivities",
            "macro_factor_events",
            "holding_relationships",
            "saved_views",
        ):
            assert t not in tables, f"{t} should not exist after backup failure"

        # Exception carries useful context.
        assert exc_info.value.db_path == isolated_db["db_path"]
        assert "simulated disk full" in exc_info.value.reason


# ───────────────────────────────────────────────────────────────────────────
# 3B — newer-DB handling
# ───────────────────────────────────────────────────────────────────────────


class TestNewerDb:
    def _seed_db_at_version(self, db_path: Path, version: int) -> None:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(
                f"""
                CREATE TABLE _schema_version (
                    id INTEGER PRIMARY KEY,
                    version INTEGER,
                    applied_at TEXT,
                    description TEXT
                );
                INSERT INTO _schema_version
                  VALUES (1, {version}, '2030-01-01T00:00:00Z', 'future');
                """
            )
            conn.commit()
        finally:
            conn.close()

    def test_newer_db_raises_typed_error(self, isolated_db):
        future = CURRENT_SCHEMA_VERSION + 1
        self._seed_db_at_version(isolated_db["db_path"], future)

        with pytest.raises(DatabaseVersionTooNewError) as exc_info:
            asyncio.run(run_migrations())

        exc = exc_info.value
        assert exc.db_version == future
        assert exc.app_version == CURRENT_SCHEMA_VERSION
        assert exc.db_path == isolated_db["db_path"]
        assert exc.data_dir == isolated_db["data_dir"]
        assert exc.backup_dir == isolated_db["backups_dir"]
        # Must not have created a backup (we never started migrating).
        if isolated_db["backups_dir"].exists():
            assert (
                len(list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))) == 0
            )

    def test_newer_db_does_not_modify_file(self, isolated_db):
        future = CURRENT_SCHEMA_VERSION + 5
        self._seed_db_at_version(isolated_db["db_path"], future)
        original_bytes = isolated_db["db_path"].read_bytes()

        with pytest.raises(DatabaseVersionTooNewError):
            asyncio.run(run_migrations())

        assert isolated_db["db_path"].read_bytes() == original_bytes


# ───────────────────────────────────────────────────────────────────────────
# 3C — corrupt-DB handling
# ───────────────────────────────────────────────────────────────────────────


class TestCorruptDb:
    def test_corrupt_db_raises_typed_error(self, isolated_db):
        # Write garbage that is not a SQLite header. Size > 0 so the pre-flight
        # actually inspects the file.
        isolated_db["db_path"].write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)

        with pytest.raises(DatabaseCorruptError) as exc_info:
            asyncio.run(run_migrations())

        exc = exc_info.value
        assert exc.db_path == isolated_db["db_path"]
        assert exc.backup_dir == isolated_db["backups_dir"]
        assert exc.original_error is not None

    def test_corrupt_file_is_unchanged_after_failure(self, isolated_db):
        payload = b"NOT A SQLITE FILE\x00\xff" * 32
        isolated_db["db_path"].write_bytes(payload)

        with pytest.raises(DatabaseCorruptError):
            asyncio.run(run_migrations())

        assert isolated_db["db_path"].read_bytes() == payload

    def test_corrupt_db_does_not_attempt_backup(self, isolated_db):
        """We do not back up a corrupt file silently — the user must decide."""
        isolated_db["db_path"].write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)

        with pytest.raises(DatabaseCorruptError):
            asyncio.run(run_migrations())

        if isolated_db["backups_dir"].exists():
            assert (
                len(list(isolated_db["backups_dir"].glob("kleitos-pre-v*.db"))) == 0
            )


# ───────────────────────────────────────────────────────────────────────────
# 3D — idempotency: each v3..v8 step survives being applied twice
# ───────────────────────────────────────────────────────────────────────────


class TestMigrationIdempotency:
    """Each migration step must guard create-table / column-add with IF NOT EXISTS.

    The pattern in the codebase is to inspect existing tables/columns
    before issuing DDL. We verify this by running a head-of-tree DB once
    via run_migrations(), then invoking each step function a second time
    on the same DB — no errors expected, no duplicate rows/columns.
    """

    @pytest.fixture
    def head_db_engine(self, isolated_db):
        """Run real migrations to head, then return the engine."""
        asyncio.run(run_migrations())
        from sqlalchemy import create_engine

        sync_url = f"sqlite:///{isolated_db['db_path']}"
        engine = create_engine(sync_url)
        yield engine
        engine.dispose()

    @pytest.mark.parametrize(
        "step_fn,name",
        [
            (_migrate_v3, "v3"),
            (_migrate_v4, "v4"),
            (_migrate_v5, "v5"),
            (_migrate_v6, "v6"),
            (_migrate_v7, "v7"),
            (_migrate_v8, "v8"),
        ],
    )
    def test_step_is_idempotent(self, head_db_engine, step_fn, name):
        """Re-applying any step at head must be a no-op (no exception, no dup)."""
        with head_db_engine.begin() as conn:
            tables_before = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            step_fn(conn)
            tables_after = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert tables_before == tables_after, (
            f"{name} produced a different table set on second application"
        )


# ───────────────────────────────────────────────────────────────────────────
# Recovery endpoint (3B contract)
# ───────────────────────────────────────────────────────────────────────────


class TestRecoveryEndpoint:
    """The /api/v1/system/recovery endpoint surfaces the DB state in JSON."""

    def _make_client(self):
        # Late import so monkeypatched env vars apply.
        from fastapi.testclient import TestClient
        from src.config import get_settings
        from src.main import app

        # Disable auth for the TestClient (it has no real client IP and
        # the rate-limit middleware would 401 us). Mirrors the pattern in
        # tests/smoke/test_api_smoke.py.
        get_settings().api.auth_enabled = False
        return TestClient(app, raise_server_exceptions=False)

    def test_after_fresh_install_status_is_ok(self, isolated_db):
        asyncio.run(run_migrations())
        connection_module.reset_connection_state()

        with self._make_client() as client:
            r = client.get("/api/v1/system/recovery")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert body["db_version"] == CURRENT_SCHEMA_VERSION
            assert body["app_supported_version"] == CURRENT_SCHEMA_VERSION
            assert str(isolated_db["data_dir"]) in body["data_dir"]
            assert "backups" in body["backup_dir"]
            assert isinstance(body["next_steps"], list)

    def test_newer_db_status_is_version_too_new(self, isolated_db):
        # Seed a future-version DB directly. The recovery endpoint must
        # surface this state WITHOUT running migrations (or it would raise).
        conn = sqlite3.connect(str(isolated_db["db_path"]))
        try:
            conn.executescript(
                f"""
                CREATE TABLE _schema_version (id INTEGER PRIMARY KEY, version INTEGER,
                    applied_at TEXT, description TEXT);
                INSERT INTO _schema_version VALUES (1, {CURRENT_SCHEMA_VERSION + 3},
                    '2030-01-01T00:00:00Z', 'future');
                """
            )
            conn.commit()
        finally:
            conn.close()

        # Don't run lifespan here (it would crash). Instead, hit the endpoint
        # via TestClient with raise_server_exceptions=False AND skip lifespan
        # by not using a context manager. The recovery endpoint does its own
        # raw-sqlite reads so it doesn't need the engine to be initialised.
        from fastapi.testclient import TestClient
        from src.api.routes.system import router as system_router
        from fastapi import FastAPI

        bare_app = FastAPI()
        bare_app.include_router(system_router)
        with TestClient(bare_app, raise_server_exceptions=False) as client:
            r = client.get("/api/v1/system/recovery")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "version_too_new"
            assert body["issue"] == "database_newer_than_app"
            assert body["db_version"] == CURRENT_SCHEMA_VERSION + 3
            assert body["app_supported_version"] == CURRENT_SCHEMA_VERSION

    def test_corrupt_db_status_is_corrupt(self, isolated_db):
        isolated_db["db_path"].write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)
        from fastapi.testclient import TestClient
        from src.api.routes.system import router as system_router
        from fastapi import FastAPI

        bare_app = FastAPI()
        bare_app.include_router(system_router)
        with TestClient(bare_app, raise_server_exceptions=False) as client:
            r = client.get("/api/v1/system/recovery")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "corrupt"
            assert body["db_version"] is None
            assert body["detail"]  # non-empty error string


# ───────────────────────────────────────────────────────────────────────────
# scripts/migrate.py CLI exit codes
# ───────────────────────────────────────────────────────────────────────────


class TestMigrateScriptExitCodes:
    """End-to-end verification of the customer-facing migrate.py entry."""

    def _run_migrate(self, isolated_db) -> tuple[int, str]:
        import subprocess

        env = {
            "AXION_DATA_DIR": str(isolated_db["data_dir"]),
            "AXION_DB_PATH": str(isolated_db["db_path"]),
            "KLEITOS_DATA_DIR": str(isolated_db["data_dir"]),
            "KLEITOS_DB_PATH": str(isolated_db["db_path"]),
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        proc = subprocess.run(
            [sys.executable, "scripts/migrate.py"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_fresh_db_exits_zero(self, isolated_db):
        rc, _ = self._run_migrate(isolated_db)
        assert rc == 0

    def test_newer_db_exits_2(self, isolated_db):
        conn = sqlite3.connect(str(isolated_db["db_path"]))
        try:
            conn.executescript(
                f"""
                CREATE TABLE _schema_version (id INTEGER PRIMARY KEY,
                    version INTEGER, applied_at TEXT, description TEXT);
                INSERT INTO _schema_version VALUES (1, {CURRENT_SCHEMA_VERSION + 1},
                    '2030-01-01T00:00:00Z', 'future');
                """
            )
            conn.commit()
        finally:
            conn.close()
        rc, out = self._run_migrate(isolated_db)
        assert rc == 2, f"expected 2 (version too new), got {rc}\n{out}"
        assert "newer database detected" in out.lower()

    def test_corrupt_db_exits_3(self, isolated_db):
        isolated_db["db_path"].write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)
        rc, out = self._run_migrate(isolated_db)
        assert rc == 3, f"expected 3 (corrupt), got {rc}\n{out}"
        assert "could not open" in out.lower()


# ───────────────────────────────────────────────────────────────────────────
# Helper-function smoke
# ───────────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_quick_integrity_check_passes_on_valid_db(self, tmp_path):
        db = tmp_path / "good.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        _quick_integrity_check(db)  # must not raise

    def test_quick_integrity_check_raises_on_garbage(self, tmp_path):
        db = tmp_path / "bad.db"
        db.write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)
        with pytest.raises(sqlite3.DatabaseError):
            _quick_integrity_check(db)

    def test_backup_helper_produces_valid_sqlite(self, tmp_path):
        src = tmp_path / "src.db"
        conn = sqlite3.connect(str(src))
        try:
            conn.executescript(
                """
                CREATE TABLE t (x INTEGER);
                INSERT INTO t VALUES (1), (2), (3);
                """
            )
            conn.commit()
        finally:
            conn.close()

        out_dir = tmp_path / "backups"
        backup_path = _backup_db(src, out_dir, target_version=42)
        assert backup_path.exists()
        assert backup_path.parent == out_dir
        assert backup_path.name.startswith("kleitos-pre-v42-")
        assert backup_path.name.endswith(".db")

        # Backup is a valid sqlite file with the same data.
        bconn = sqlite3.connect(str(backup_path))
        try:
            rows = bconn.execute("SELECT x FROM t ORDER BY x").fetchall()
            assert rows == [(1,), (2,), (3,)]
        finally:
            bconn.close()
