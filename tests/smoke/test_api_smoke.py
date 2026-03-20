"""API smoke tests — verify all core endpoints respond correctly.

Uses FastAPI's TestClient for synchronous testing without starting a server.
Tests run against a temporary SQLite database to avoid touching production data.
Auth is disabled for testing (tests verify endpoint behavior, not auth middleware).
"""

import os
import tempfile

# Override config BEFORE any imports that trigger config loading
_tmp_dir = tempfile.mkdtemp()
os.environ["KLEITOS_DB_PATH"] = os.path.join(_tmp_dir, "test_smoke.db")
os.environ["KLEITOS_DATA_DIR"] = _tmp_dir
os.environ["KLEITOS_LOG_LEVEL"] = "WARNING"

from src.config import get_settings

# Force config reload with test overrides, then disable auth for API tests
get_settings.cache_clear()
_settings = get_settings()
# Directly override auth for test context (TestClient has no real client IP)
_settings.api.auth_enabled = False

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

# Use context manager to trigger lifespan (startup/shutdown including migrations)
client = TestClient(app, raise_server_exceptions=False)


def setup_module(module):
    """Ensure the test client enters the lifespan context."""
    client.__enter__()


def teardown_module(module):
    """Clean up the test client lifespan."""
    client.__exit__(None, None, None)


class TestHealthEndpoint:
    """Health endpoint should always respond."""

    def test_health_returns_200(self):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("ok", "degraded")
        assert "database" in data
        assert "version" in data

    def test_health_includes_version(self):
        r = client.get("/api/v1/health")
        assert r.json()["version"] == "1.0.0"


class TestPortfolioEndpoints:
    """Portfolio CRUD endpoints."""

    def test_list_holdings_empty(self):
        r = client.get("/api/v1/portfolio/holdings")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_portfolio_summary(self):
        r = client.get("/api/v1/portfolio/summary")
        assert r.status_code == 200
        data = r.json()
        assert "total_market_value" in data
        assert "holding_count" in data

    def test_create_holding(self):
        r = client.post("/api/v1/portfolio/holdings", json={
            "ticker": "TEST",
            "quantity": 100,
            "avg_cost_basis": 50.0,
            "current_price": 55.0,
            "currency": "USD",
        })
        assert r.status_code in (200, 201)
        data = r.json()
        assert data.get("ticker") == "TEST" or "id" in data

    def test_exposure_endpoint(self):
        r = client.get("/api/v1/portfolio/exposure")
        assert r.status_code == 200


class TestEventEndpoints:
    """Event listing endpoints."""

    def test_list_events(self):
        r = client.get("/api/v1/events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_recent_events(self):
        r = client.get("/api/v1/events/recent")
        assert r.status_code == 200


class TestAlertEndpoints:
    """Alert endpoints."""

    def test_list_alerts(self):
        r = client.get("/api/v1/alerts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_active_alerts(self):
        r = client.get("/api/v1/alerts/active")
        assert r.status_code == 200


class TestDigestEndpoints:
    """Digest endpoints."""

    def test_list_digests(self):
        r = client.get("/api/v1/digests")
        assert r.status_code == 200

    def test_latest_digest(self):
        r = client.get("/api/v1/digests/latest")
        # 200 with data or 404 if no digests — both acceptable
        assert r.status_code in (200, 404)


class TestSourceEndpoints:
    """Source management endpoints."""

    def test_list_sources(self):
        r = client.get("/api/v1/sources")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestAgentEndpoints:
    """Agent status endpoints."""

    def test_agents_status(self):
        r = client.get("/api/v1/agents/status")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_agent_runs(self):
        r = client.get("/api/v1/agents/runs")
        assert r.status_code == 200


class TestAuditEndpoint:
    """Audit trail endpoint."""

    def test_audit_log(self):
        r = client.get("/api/v1/audit")
        assert r.status_code == 200


class TestExportEndpoints:
    """Export endpoints."""

    def test_export_portfolio_csv(self):
        r = client.get("/api/v1/export/portfolio?format=csv")
        assert r.status_code == 200


class TestDashboard:
    """Dashboard static files."""

    def test_root_redirects_to_dashboard(self):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302, 307)
        assert "/dashboard" in r.headers.get("location", "")

    def test_dashboard_loads(self):
        r = client.get("/dashboard/")
        assert r.status_code == 200
        assert "Axion" in r.text


class TestOpenClawBridge:
    """OpenClaw bridge endpoints."""

    def test_openclaw_tools(self):
        r = client.get("/api/v1/openclaw/tools")
        assert r.status_code == 200

    def test_openclaw_status(self):
        r = client.get("/api/v1/openclaw/status")
        assert r.status_code == 200
