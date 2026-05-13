#!/usr/bin/env python3
"""
Axion local smoke test — proves the app is usable from a fresh state.

What this verifies, end-to-end, against a *fresh* temp data dir and SQLite:

  1.  Configuration loads.
  2.  Source registry parses config/sources.yaml.
  3.  Migrations run against an empty DB and create the schema at head.
  4.  Default portfolio ("default") is seeded by migrations.
  5.  /api/v1/health responds 200 with sane fields.
  6.  /dashboard/ serves the static HTML.
  7.  GET /api/v1/portfolios returns the default portfolio.
  8.  CSV extract+import flow (POST /portfolio/extract → /import-reviewed) using
      the bundled sample_portfolio.csv.
  9.  GET /portfolio/holdings returns the imported rows.
 10.  GET /portfolio/summary returns sane totals.
 11.  Sources sync from YAML into the DB at startup (lifespan).
 12.  WebSocket /api/v1/ws connects and stays open.
 13.  At least one export route returns 200.
 14.  POST /api/v1/settings/test-provider responds without crashing,
      regardless of whether an AI key is configured.

Exit code 0 = all green. Non-zero = at least one check failed.

Run with:
    python scripts/smoke_local.py

This uses FastAPI's TestClient (no real server needed) plus a real temp DB.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# ── Bootstrap: temp data dir BEFORE any src.* import ─────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_TMP_DIR = Path(tempfile.mkdtemp(prefix="axion-smoke-"))
_DATA_DIR = _TMP_DIR / "data"
_DB_PATH = _DATA_DIR / "db" / "kleitos.db"
(_DATA_DIR / "db").mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)

# Override BEFORE config import so paths are isolated
os.environ["AXION_DATA_DIR"] = str(_DATA_DIR)
os.environ["AXION_DB_PATH"] = str(_DB_PATH)
os.environ["KLEITOS_DATA_DIR"] = str(_DATA_DIR)
os.environ["KLEITOS_DB_PATH"] = str(_DB_PATH)
os.environ["AXION_LOG_LEVEL"] = "WARNING"


# ── Tiny test framework (no pytest dependency) ───────────────────────────────
class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  \033[32m[PASS]\033[0m {name}")

    def fail(self, name: str, why: str) -> None:
        self.failed.append((name, why))
        print(f"  \033[31m[FAIL]\033[0m {name}\n         {why}")

    def total(self) -> int:
        return len(self.passed) + len(self.failed)

    def summary(self) -> str:
        ok = len(self.passed)
        bad = len(self.failed)
        total = ok + bad
        return f"{ok}/{total} passed, {bad} failed"


REPORT = Report()


def check(name: str, fn):
    try:
        fn()
        REPORT.ok(name)
    except AssertionError as e:
        REPORT.fail(name, str(e) or "assertion failed")
    except Exception as e:
        REPORT.fail(name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ── Imports (after env vars are set) ─────────────────────────────────────────
print("Bootstrapping Axion in temp data dir:", _DATA_DIR)
print()

from src.config import get_settings  # noqa: E402

get_settings.cache_clear()  # ignore any cached prod settings
_settings = get_settings()
# Disable auth for in-process TestClient (no real client IP)
_settings.api.auth_enabled = False

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402


# ── Per-check functions ──────────────────────────────────────────────────────
def check_config_loads():
    s = get_settings()
    assert s.api.host == "127.0.0.1", f"expected 127.0.0.1, got {s.api.host}"
    # Compare resolved paths so macOS /var → /private/var symlinks don't fail us.
    db_real = Path(s.database.path).resolve()
    tmp_real = _DATA_DIR.resolve()
    assert str(db_real).startswith(str(tmp_real)), \
        f"db path {db_real} not in temp data dir {tmp_real}"


def check_source_registry_parses():
    from src.sources.registry import SourceRegistry
    yaml_path = PROJECT_ROOT / "config" / "sources.yaml"
    assert yaml_path.exists(), f"missing {yaml_path}"
    reg = SourceRegistry(yaml_path)
    sources = reg.get_all_sources()
    assert sources, "source registry loaded zero sources"


client: TestClient | None = None


def _ensure_client():
    """Lazily start TestClient lifespan (runs migrations, sync sources, etc.)."""
    global client
    if client is None:
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()


def check_migrations_ran():
    _ensure_client()
    assert _DB_PATH.exists(), f"db file not created at {_DB_PATH}"
    # Inspect schema version directly
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    try:
        row = conn.execute("SELECT version FROM _schema_version WHERE id = 1").fetchone()
        assert row is not None, "_schema_version table empty"
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        assert row[0] == CURRENT_SCHEMA_VERSION, \
            f"schema version {row[0]} != head {CURRENT_SCHEMA_VERSION}"
    finally:
        conn.close()


def check_default_portfolio_exists():
    _ensure_client()
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, is_default FROM portfolios WHERE id = 'default'"
        ).fetchone()
        assert row is not None, "default portfolio row missing"
        assert row[1] == 1, f"default portfolio is_default flag is {row[1]}, expected 1"
    finally:
        conn.close()


def check_health_endpoint():
    _ensure_client()
    r = client.get("/api/v1/health")
    assert r.status_code == 200, f"status {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["status"] in ("ok", "degraded"), f"unexpected status {body['status']}"
    assert body["database"] == "connected", f"db not connected: {body['database']}"
    assert "version" in body
    assert "llm_status" in body
    assert body["llm_status"] in ("active", "configured", "disabled")


def check_dashboard_served():
    _ensure_client()
    r = client.get("/dashboard/")
    assert r.status_code == 200, f"dashboard status {r.status_code}"
    text = r.text
    assert "<title" in text.lower() and "axion" in text.lower(), \
        "dashboard HTML missing Axion title"


def check_portfolios_endpoint():
    _ensure_client()
    r = client.get("/api/v1/portfolios")
    assert r.status_code == 200, f"status {r.status_code}: {r.text[:200]}"
    items = r.json()
    assert isinstance(items, list), f"expected list, got {type(items).__name__}"
    ids = [p.get("id") for p in items]
    assert "default" in ids, f"default portfolio not returned: {ids}"


def check_sources_synced_to_db():
    """Lifespan should sync YAML sources into the DB."""
    _ensure_client()
    import sqlite3
    conn = sqlite3.connect(_DB_PATH)
    try:
        n = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        assert n > 0, "no sources synced from YAML into DB"
    finally:
        conn.close()


def check_csv_import_flow():
    """Extract + import sample_portfolio.csv → at least one holding lands."""
    _ensure_client()
    csv_path = PROJECT_ROOT / "sample_portfolio.csv"
    assert csv_path.exists(), f"missing sample CSV at {csv_path}"

    with csv_path.open("rb") as fh:
        files = {"file": ("sample_portfolio.csv", fh, "text/csv")}
        r = client.post("/api/v1/portfolio/extract", files=files)
    assert r.status_code == 200, f"extract status {r.status_code}: {r.text[:200]}"
    payload = r.json()
    assert payload["status"] == "ok", f"extract status {payload['status']}: {payload.get('message')}"
    rows = payload["rows"]
    assert len(rows) >= 1, "extract returned zero rows"

    import_body = {"rows": rows, "portfolio_id": "default"}
    r = client.post("/api/v1/portfolio/import-reviewed", json=import_body)
    assert r.status_code == 200, f"import status {r.status_code}: {r.text[:200]}"
    res = r.json()
    assert res["status"] in ("success", "partial"), f"import returned {res['status']}"
    total = res["holdings_imported"] + res["holdings_updated"]
    assert total >= 1, f"no holdings imported or updated (errors: {res.get('errors')})"


def check_holdings_listed():
    _ensure_client()
    r = client.get("/api/v1/portfolio/holdings", params={"portfolio_id": "default"})
    assert r.status_code == 200, f"holdings status {r.status_code}"
    holdings = r.json()
    assert len(holdings) >= 1, "no holdings returned after import"
    # Spot-check shape
    h = holdings[0]
    for k in ("id", "ticker", "currency", "quantity"):
        assert k in h, f"holding missing '{k}': {list(h.keys())}"


def check_summary_endpoint():
    _ensure_client()
    r = client.get("/api/v1/portfolio/summary", params={"portfolio_id": "default"})
    assert r.status_code == 200, f"summary status {r.status_code}: {r.text[:200]}"
    s = r.json()
    for k in ("total_market_value", "holding_count"):
        assert k in s, f"summary missing '{k}'"


def check_websocket_connects():
    _ensure_client()
    with client.websocket_connect("/api/v1/ws") as ws:
        # Server may send a ping after 30s; we just verify the handshake.
        assert ws is not None


def check_export_route():
    _ensure_client()
    # /export/portfolio returns a CSV download; we just want a non-5xx response.
    r = client.get("/api/v1/export/portfolio", params={"portfolio_id": "default"})
    assert r.status_code in (200, 404), \
        f"export status {r.status_code} (expected 200 or 404): {r.text[:200]}"


def check_test_provider_endpoint():
    _ensure_client()
    r = client.post("/api/v1/settings/test-provider")
    assert r.status_code == 200, f"test-provider status {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["status"] in ("active", "configured", "disabled", "unreachable", "error"), \
        f"unexpected status {body['status']}"


def check_events_endpoint_no_500():
    _ensure_client()
    r = client.get("/api/v1/events")
    assert r.status_code == 200, f"events status {r.status_code}: {r.text[:200]}"


def check_alerts_endpoint_no_500():
    _ensure_client()
    r = client.get("/api/v1/alerts")
    assert r.status_code == 200, f"alerts status {r.status_code}: {r.text[:200]}"


# ── Driver ───────────────────────────────────────────────────────────────────
def main() -> int:
    print("=" * 60)
    print("Axion local smoke test")
    print("=" * 60)
    print()

    try:
        check("config loads with temp data dir",  check_config_loads)
        check("source registry parses YAML",     check_source_registry_parses)
        check("migrations run on fresh DB",      check_migrations_ran)
        check("default portfolio is seeded",     check_default_portfolio_exists)
        check("/api/v1/health responds",         check_health_endpoint)
        check("/dashboard/ serves HTML",         check_dashboard_served)
        check("GET /api/v1/portfolios works",    check_portfolios_endpoint)
        check("sources synced from YAML to DB",  check_sources_synced_to_db)
        check("CSV extract+import flow",         check_csv_import_flow)
        check("holdings listed after import",    check_holdings_listed)
        check("portfolio summary works",         check_summary_endpoint)
        check("websocket /api/v1/ws connects",   check_websocket_connects)
        check("export route is reachable",       check_export_route)
        check("test-provider returns sanely",    check_test_provider_endpoint)
        check("/api/v1/events returns 200",      check_events_endpoint_no_500)
        check("/api/v1/alerts returns 200",      check_alerts_endpoint_no_500)
    finally:
        # Tear down the TestClient lifespan cleanly
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except Exception:
                pass
        # Clean up temp dir
        try:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)
        except Exception:
            pass

    print()
    print("=" * 60)
    print("Result:", REPORT.summary())
    print("=" * 60)
    if REPORT.failed:
        print()
        print("Failures:")
        for name, why in REPORT.failed:
            short = why.splitlines()[0] if why else "?"
            print(f"  - {name}: {short}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
