"""Phase 4 — support-tooling and diagnostics regression tests.

Covers:
- 4C: scripts/rotate_logs.py rotates oversized files and leaves small ones alone.
- 4D: scripts/support_bundle.py produces a redacted zip with no .db/.env/secrets
  and includes the expected metadata + log tails.
- 4E: GET /api/v1/system/diagnostics returns a redacted structured snapshot
  even when the DB is absent or corrupt.
- 4F: dashboard renderWelcomeCard markup contract — the first-run panel
  exists, names the offline CSV path, and labels AI as optional.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from src.config import get_settings
from src.database import connection as connection_module
from src.database.migrations import CURRENT_SCHEMA_VERSION, run_migrations


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the app at a fresh temp data dir + reset SQLAlchemy caches."""
    data_dir = tmp_path / "data"
    db_dir = data_dir / "db"
    db_dir.mkdir(parents=True)
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
        "log_dir": data_dir / "logs",
        "backup_dir": data_dir / "backups",
        "support_dir": data_dir / "support",
    }
    connection_module.reset_connection_state()
    get_settings.cache_clear()


# ───────────────────────────────────────────────────────────────────────────
# 4C — rotate_logs
# ───────────────────────────────────────────────────────────────────────────


class TestRotateLogs:
    def _import_rotate(self):
        # Load by file path because scripts/ is not a package.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "rotate_logs", PROJECT_ROOT / "scripts" / "rotate_logs.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_small_files_are_left_alone(self, tmp_path):
        rotate_logs = self._import_rotate()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        small = log_dir / "axion-launcher.log"
        small.write_text("hello\n" * 10)
        before = small.read_text()
        counts = rotate_logs.rotate(log_dir)
        assert counts["axion-launcher.log"] == 0
        assert small.read_text() == before

    def test_oversized_file_is_rotated(self, tmp_path):
        rotate_logs = self._import_rotate()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        big = log_dir / "axion-server.log"
        # 6 MiB > 5 MiB threshold
        big.write_bytes(b"X" * (6 * 1024 * 1024))
        counts = rotate_logs.rotate(log_dir)
        assert counts["axion-server.log"] == 1
        # Live file recreated empty; rotated to .1
        assert big.exists() and big.stat().st_size == 0
        assert (log_dir / "axion-server.log.1").exists()
        assert (log_dir / "axion-server.log.1").stat().st_size == 6 * 1024 * 1024

    def test_unknown_files_are_ignored(self, tmp_path):
        rotate_logs = self._import_rotate()
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        rogue = log_dir / "random.log"
        rogue.write_bytes(b"X" * (10 * 1024 * 1024))
        rotate_logs.rotate(log_dir)
        # Unknown name is left alone (not in KNOWN_LOG_NAMES).
        assert rogue.exists()
        assert rogue.stat().st_size == 10 * 1024 * 1024


# ───────────────────────────────────────────────────────────────────────────
# 4D — support bundle
# ───────────────────────────────────────────────────────────────────────────


class TestSupportBundle:
    def _run_bundle(self, data_dir: Path, extra_env: dict | None = None) -> Path:
        env = {
            **os.environ,
            "AXION_DATA_DIR": str(data_dir),
            "AXION_DB_PATH": str(data_dir / "db" / "kleitos.db"),
            "KLEITOS_DATA_DIR": str(data_dir),
            "KLEITOS_DB_PATH": str(data_dir / "db" / "kleitos.db"),
        }
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [sys.executable, "scripts/support_bundle.py"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"support_bundle exited {proc.returncode}\n{proc.stderr}\n{proc.stdout}"
        bundles = sorted((data_dir / "support").glob("axion-support-*.zip"))
        assert bundles, "no support bundle produced"
        return bundles[-1]

    def test_bundle_has_required_files(self, isolated_db):
        # Make a migrated DB plus some log files.
        asyncio.run(run_migrations())
        (isolated_db["log_dir"]).mkdir(parents=True, exist_ok=True)
        (isolated_db["log_dir"] / "axion-launcher.log").write_text("launcher line\n")
        (isolated_db["log_dir"] / "axion-server.log").write_text("server line\n")

        zip_path = self._run_bundle(isolated_db["data_dir"])
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for required in (
                "metadata.json",
                "environment.redacted.json",
                "db_diagnostics.json",
                "backups.json",
                "logs_index.json",
                "settings.redacted.json",
                "README.txt",
                "requirements.txt",
            ):
                assert required in names, f"bundle missing {required}: {names}"
            # log tail files
            assert "logs/axion-launcher.log" in names
            assert "logs/axion-server.log" in names

    def test_bundle_excludes_db_and_env(self, isolated_db):
        asyncio.run(run_migrations())
        zip_path = self._run_bundle(isolated_db["data_dir"])
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert not name.endswith(".db"), f".db leaked: {name}"
                assert not name.endswith(".env"), f"raw .env leaked: {name}"
                assert "kleitos.db" not in name, f"DB filename leaked: {name}"

    def test_bundle_redacts_known_secret_env_vars(self, isolated_db):
        fake_anthropic = "sk-ant-FAKEPHASE4TEST" + ("a" * 24)
        fake_openai = "sk-projFAKETESTTOKEN" + ("b" * 24)
        fake_telegram = "1234567890:FAKETELEGRAMTOKEN" + ("c" * 24)
        plain = "this-is-not-a-secret"
        zip_path = self._run_bundle(
            isolated_db["data_dir"],
            extra_env={
                "ANTHROPIC_API_KEY": fake_anthropic,
                "OPENAI_API_KEY": fake_openai,
                "AXION_TELEGRAM_TOKEN": fake_telegram,
                "PLAIN_PUBLIC_INFO": plain,
            },
        )
        with zipfile.ZipFile(zip_path) as zf:
            env_data = json.loads(zf.read("environment.redacted.json"))
        # Secrets redacted
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AXION_TELEGRAM_TOKEN"):
            assert k in env_data, f"{k} missing from snapshot"
            assert "redacted" in env_data[k], (
                f"{k} not redacted: {env_data[k]!r}"
            )
        # Plain values preserved
        assert env_data.get("PLAIN_PUBLIC_INFO") == plain
        # Full secret value must not appear anywhere in the zip text
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                data = zf.read(name)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                for secret in (fake_anthropic, fake_openai, fake_telegram):
                    assert secret not in text, (
                        f"secret leaked into {name}: {secret[:12]}…"
                    )

    def test_bundle_db_diagnostics_reports_schema_version(self, isolated_db):
        asyncio.run(run_migrations())
        zip_path = self._run_bundle(isolated_db["data_dir"])
        with zipfile.ZipFile(zip_path) as zf:
            diag = json.loads(zf.read("db_diagnostics.json"))
        assert diag["schema_version"] == CURRENT_SCHEMA_VERSION
        assert diag["tables"]["portfolios"] >= 1
        assert diag["tables"]["holdings"] == 0

    def test_bundle_runs_even_without_db(self, isolated_db):
        # Don't run migrations; DB file never created.
        zip_path = self._run_bundle(isolated_db["data_dir"])
        with zipfile.ZipFile(zip_path) as zf:
            diag = json.loads(zf.read("db_diagnostics.json"))
        assert diag["db_exists"] is False
        assert diag["schema_version"] is None


# ───────────────────────────────────────────────────────────────────────────
# 4E — diagnostics endpoint
# ───────────────────────────────────────────────────────────────────────────


class TestDiagnosticsEndpoint:
    def _make_bare_client(self):
        """Mount system router on a bare FastAPI app — no auth, no lifespan."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes.system import router as system_router

        app = FastAPI()
        app.include_router(system_router)
        return TestClient(app, raise_server_exceptions=False)

    def test_returns_200_after_migration(self, isolated_db):
        asyncio.run(run_migrations())
        with self._make_bare_client() as client:
            r = client.get("/api/v1/system/diagnostics")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["schema_status"] == "ok"
            assert body["db_version"] == CURRENT_SCHEMA_VERSION
            assert body["app_supported_version"] == CURRENT_SCHEMA_VERSION
            assert body["portfolios_count"] >= 1
            assert body["holdings_count"] == 0
            assert body["sources_total"] is not None
            assert isinstance(body["warnings"], list)
            assert isinstance(body["llm_configured"], bool)
            assert isinstance(body["telegram_configured"], bool)

    def test_handles_missing_db_gracefully(self, isolated_db):
        # Don't run migrations; the file never exists.
        with self._make_bare_client() as client:
            r = client.get("/api/v1/system/diagnostics")
            assert r.status_code == 200
            body = r.json()
            assert body["schema_status"] == "no_database"
            assert body["db_version"] is None
            assert body["portfolios_count"] is None

    def test_handles_corrupt_db_gracefully(self, isolated_db):
        isolated_db["db_path"].write_bytes(b"NOT A SQLITE FILE\x00\xff" * 32)
        with self._make_bare_client() as client:
            r = client.get("/api/v1/system/diagnostics")
            assert r.status_code == 200
            body = r.json()
            assert body["schema_status"] == "corrupt"
            assert body["db_version"] is None
            assert body["warnings"]  # non-empty

    def test_never_returns_secrets(self, isolated_db, monkeypatch):
        # Set a fake secret and confirm it doesn't appear in the JSON.
        fake = "sk-ant-" + ("Z" * 48)
        monkeypatch.setenv("ANTHROPIC_API_KEY", fake)
        # Force config reload to pick up env.
        get_settings.cache_clear()
        asyncio.run(run_migrations())
        with self._make_bare_client() as client:
            r = client.get("/api/v1/system/diagnostics")
            text = r.text
        assert fake not in text


# ───────────────────────────────────────────────────────────────────────────
# 4F — dashboard first-run contract
# ───────────────────────────────────────────────────────────────────────────


class TestFirstRunDashboardContract:
    """Source-level checks on the welcome-card markup.

    These guard against regressions to the customer-facing copy. They run
    without a browser, so they're cheap to keep in the unit suite.
    """

    @pytest.fixture(scope="class")
    def app_js(self) -> str:
        return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(
            encoding="utf-8"
        )

    def test_welcome_card_has_first_run_marker(self, app_js):
        assert 'data-first-run="empty"' in app_js, (
            "welcome card must carry a stable marker for first-run E2E hooks"
        )

    def test_welcome_card_mentions_offline_csv(self, app_js):
        assert "CSV import works offline" in app_js

    def test_welcome_card_labels_ai_as_optional(self, app_js):
        # The "(optional)" label must appear near the AI step.
        assert "Connect an AI provider" in app_js
        assert "(optional)" in app_js

    def test_welcome_card_points_at_sample_csv(self, app_js):
        assert "sample_portfolio.csv" in app_js

    def test_welcome_card_does_not_promise_live_prices(self, app_js):
        # We deliberately do NOT promise live prices on the welcome card.
        # Scan only the returned HTML template, not the surrounding comments
        # (which legitimately mention these phrases as things we omit).
        idx = app_js.find("function renderWelcomeCard(")
        assert idx != -1
        end = app_js.find("\n    }\n", idx)
        body = app_js[idx:end]
        # Strip JS line/block comments before searching.
        no_block = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
        no_line = re.sub(r"//[^\n]*", "", no_block)
        haystack = no_line.lower()
        forbidden = ("live prices", "real-time prices", "real-time quotes")
        for needle in forbidden:
            assert needle not in haystack, (
                f"welcome card HTML must not imply {needle!r}"
            )

    def test_welcome_card_has_import_and_settings_actions(self, app_js):
        idx = app_js.find("function renderWelcomeCard(")
        end = app_js.find("\n    }\n", idx)
        body = app_js[idx:end]
        assert "Import portfolio" in body
        assert "Configure AI / sources" in body
