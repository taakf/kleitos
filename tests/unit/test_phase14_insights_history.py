"""Phase 14 — Insights history deck + saved-view integration tests.

Coverage:

* Navigation: ``intelligence`` is in ``_KNOWN_SURFACES`` and the
  Insights → Overview filter set is registered.
* ``describe_view`` renders the new labels cleanly:
  ``Insights``, ``Insights · Overview``, ``Insights · Overview · Critical``,
  ``Insights · Overview · News impact · Last 7 days``, and
  ``Insights · Overview · AI narration on``.
* ``validate_filters`` accepts the Insights Overview keys and still
  strips unknown keys.
* ``GET /api/v1/intelligence/insights/history``:
  - empty portfolio returns ``{items: [], daily_counts: [...], summary: zero}``;
  - portfolio isolation holds;
  - ``days`` / ``category`` / ``severity`` / ``state`` filters all narrow;
  - ``daily_counts`` always covers the full window;
  - each item carries a typed deep link routed by category;
  - response never contains AI prompt / narration bodies.
* Dashboard markup contract: history deck container, pills, summary
  strip, sparkline element, list, empty copy, JS API constant.
* JS contract: ``_captureCurrentViewPayload`` recognises Overview
  surface; ``_applyTargetFilter`` restores its filters;
  ``_autoSuggestViewName`` emits the same labels as ``describe_view``.
* Existing saved-view tests still pass.
* Support bundle privacy: no insight history rows are inlined.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Pure navigation / describe_view contract
# ─────────────────────────────────────────────────────────────────────


class TestNavigationVocabulary:
    def test_intelligence_in_known_surfaces(self):
        from src.intelligence.navigation import _KNOWN_SURFACES
        assert "intelligence" in _KNOWN_SURFACES

    def test_insights_overview_approved_filters(self):
        from src.intelligence.navigation import _APPROVED_FILTERS
        approved = _APPROVED_FILTERS.get(("intelligence", "overview"))
        assert approved is not None
        for key in ("category", "severity", "time_window_days",
                    "include_ai", "ai", "time_window"):
            assert key in approved, f"missing {key} in approved filters"

    def test_validate_filters_strips_unknown(self):
        from src.intelligence.navigation import validate_filters
        out = validate_filters("intelligence", "overview", {
            "category": "news_impact",
            "severity": "high",
            "time_window_days": "7",
            "include_ai": "true",
            "rogue_key": "should_be_dropped",
        })
        assert out is not None
        assert "category" in out
        assert "severity" in out
        assert "time_window_days" in out
        assert "include_ai" in out
        assert "rogue_key" not in out


class TestDescribeView:
    def test_bare_insights_surface_label(self):
        from src.intelligence.navigation import describe_view
        assert describe_view({"surface": "intelligence"}) == "Insights"

    def test_subtab_overview_appended(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({"surface": "intelligence", "subtab": "overview"})
        assert out == "Insights · Overview"
        # And it never doubles up labels even if surface == subtab.
        assert "Overview · Overview" not in out

    def test_severity_label(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"severity": "critical"},
        })
        assert out == "Insights · Overview · Critical"

    def test_category_and_window(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"category": "news_impact", "time_window_days": 7},
        })
        assert out == "Insights · Overview · News impact · Last 7 days"

    def test_ai_narration_truthy_only(self):
        from src.intelligence.navigation import describe_view
        on = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"include_ai": "true"},
        })
        off = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"include_ai": "false"},
        })
        assert "AI narration on" in on
        assert "AI narration" not in off

    def test_time_window_alias(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"time_window": 30},
        })
        assert "Last 30 days" in out

    def test_combo_label_stable(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {
                "category": "alert", "severity": "high",
                "time_window_days": 14, "include_ai": "true",
            },
        })
        # Each piece in deterministic order keyed off the filter dict.
        for piece in ("Insights", "Overview", "Alert", "High",
                      "Last 14 days", "AI narration on"):
            assert piece in out


# ─────────────────────────────────────────────────────────────────────
# History endpoint via TestClient
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase14_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase14.db")
    os.environ["KLEITOS_DATA_DIR"] = tmp_dir
    os.environ["KLEITOS_LOG_LEVEL"] = "WARNING"

    from src.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    settings.api.auth_enabled = False

    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    if prior_db is None:
        os.environ.pop("KLEITOS_DB_PATH", None)
    else:
        os.environ["KLEITOS_DB_PATH"] = prior_db
    if prior_data is None:
        os.environ.pop("KLEITOS_DATA_DIR", None)
    else:
        os.environ["KLEITOS_DATA_DIR"] = prior_data
    if prior_log is None:
        os.environ.pop("KLEITOS_LOG_LEVEL", None)
    else:
        os.environ["KLEITOS_LOG_LEVEL"] = prior_log
    get_settings.cache_clear()
    connection._engine = None
    connection._session_factory = None


@pytest.fixture(scope="module")
def seeded(client):
    """Two portfolios + a sprinkle of snapshot rows so the history
    deck has something to render.
    """
    import uuid
    from src.database.connection import get_db
    from src.database.models import InsightSnapshot, Portfolio

    iso = datetime.now(timezone.utc).isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph14_pA", name="Phase 14 A",
                          base_currency="USD", is_default=0,
                          created_at=iso, updated_at=iso),
                Portfolio(id="ph14_pB", name="Phase 14 B",
                          base_currency="USD", is_default=0,
                          created_at=iso, updated_at=iso),
            ])
            await session.commit()
        async with get_db() as session:
            # pA: 3 rows across categories/states.
            session.add_all([
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph14_pA",
                    card_key="insight:alert:alert:a1",
                    category="alert", severity="critical",
                    title="Critical AAPL concentration",
                    fingerprint="fp_a1", last_seen_at=iso, first_seen_at=iso,
                    notified_at=iso, notified_severity="critical",
                    status="escalated", created_at=iso, updated_at=iso,
                ),
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph14_pA",
                    card_key="insight:news_impact:event:evt_n1",
                    category="news_impact", severity="high",
                    title="Fed signals rate hike",
                    fingerprint="fp_n1", last_seen_at=iso, first_seen_at=iso,
                    status="new", created_at=iso, updated_at=iso,
                ),
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph14_pA",
                    card_key="insight:listing_country:listing-country:US",
                    category="listing_country", severity="medium",
                    title="Listing concentration: US (87%)",
                    fingerprint="fp_l1", last_seen_at=iso, first_seen_at=iso,
                    status="unchanged", created_at=iso, updated_at=iso,
                ),
            ])
            # pB row that must never leak into pA queries.
            session.add(InsightSnapshot(
                id=str(uuid.uuid4()), portfolio_id="ph14_pB",
                card_key="insight:alert:alert:b1",
                category="alert", severity="high",
                title="pB-only alert",
                fingerprint="fp_b1", last_seen_at=iso, first_seen_at=iso,
                status="new", created_at=iso, updated_at=iso,
            ))
            await session.commit()
    asyncio.run(_seed())
    yield


class TestHistoryEndpoint:
    def test_empty_portfolio_returns_honest_empty(self, client):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "nonexistent_pf"},
        )
        body = r.json()
        assert r.status_code == 200
        assert body["items"] == []
        # Always carries a daily_counts row per day in the window so the
        # sparkline has structure even when totals are zero.
        assert len(body["daily_counts"]) >= 7
        assert body["summary"] == {
            "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
        }

    def test_shape(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "days": 7},
        )
        body = r.json()
        for key in ("portfolio_id", "window_days", "generated_at",
                    "items", "daily_counts", "summary"):
            assert key in body
        # The summary aggregates the seeded statuses.
        assert body["summary"]["total"] >= 3
        assert body["summary"]["new"] >= 1
        assert body["summary"]["escalated"] >= 1
        # Each item carries a deep link (or null) plus all the metadata
        # the dashboard needs to render the row.
        for it in body["items"]:
            for k in ("card_key", "category", "severity", "title",
                      "state", "first_seen_at", "last_seen_at",
                      "deep_link"):
                assert k in it

    def test_state_filter(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "state": "new"},
        )
        body = r.json()
        assert all(it["state"] == "new" for it in body["items"])

    def test_severity_filter(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "severity": "critical"},
        )
        body = r.json()
        assert all(it["severity"] == "critical" for it in body["items"])

    def test_category_filter(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "category": "news_impact"},
        )
        body = r.json()
        assert body["items"]
        assert all(it["category"] == "news_impact" for it in body["items"])

    def test_days_filter_bounds(self, client, seeded):
        # Invalid bounds → 422.
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "days": 0},
        )
        assert r.status_code == 422
        # Upper bound: 365 ok, 366 rejected.
        ok = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "days": 365},
        )
        assert ok.status_code == 200
        bad = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA", "days": 366},
        )
        assert bad.status_code == 422

    def test_portfolio_isolation(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA"},
        )
        titles = {it["title"] for it in r.json()["items"]}
        assert "pB-only alert" not in titles

    def test_deep_link_routing(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA"},
        )
        by_cat = {it["category"]: it for it in r.json()["items"]}
        # Alert card → alerts surface.
        if "alert" in by_cat:
            assert by_cat["alert"]["deep_link"]["surface"] == "alerts"
        # News card → events surface.
        if "news_impact" in by_cat:
            assert by_cat["news_impact"]["deep_link"]["surface"] == "events"
        # Listing card → portfolio exposures.
        if "listing_country" in by_cat:
            dl = by_cat["listing_country"]["deep_link"]
            assert dl["surface"] == "portfolio"
            assert dl["subtab"] == "exposures"

    def test_no_ai_prompt_leakage(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/history",
            params={"portfolio_id": "ph14_pA"},
        )
        text = r.text
        for needle in ("GROUNDING CONTRACT", "STRUCTURED DATA",
                       "Return EXACTLY this JSON"):
            assert needle not in text, f"{needle!r} leaked into history response"


# ─────────────────────────────────────────────────────────────────────
# Saved-view integration via the existing API
# ─────────────────────────────────────────────────────────────────────


class TestSavedViewIntegration:
    def test_existing_saved_views_unaffected(self, client, seeded):
        # Round-trip a News saved view to prove the legacy path still works.
        body = {
            "portfolio_id": "ph14_pA",
            "name": "News · q=fed",
            "surface": "events",
            "payload": {
                "surface": "events", "subtab": "events",
                "filters": {"search": "fed"},
            },
        }
        r = client.post("/api/v1/views", json=body)
        assert r.status_code in (200, 201), r.text
        listing = client.get(
            "/api/v1/views",
            params={"portfolio_id": "ph14_pA"},
        ).json()
        names = [v["name"] for v in listing]
        assert "News · q=fed" in names

    def test_insights_overview_saved_view_roundtrip(self, client, seeded):
        body = {
            "portfolio_id": "ph14_pA",
            "name": "Insights · Critical · 7d",
            "surface": "intelligence",
            "payload": {
                "surface": "intelligence",
                "subtab": "overview",
                "filters": {
                    "category": "news_impact",
                    "severity": "critical",
                    "time_window_days": "7",
                    "include_ai": "true",
                },
            },
        }
        r = client.post("/api/v1/views", json=body)
        assert r.status_code in (200, 201), r.text
        listing = client.get(
            "/api/v1/views", params={"portfolio_id": "ph14_pA"},
        ).json()
        saved = next((v for v in listing if v["name"] == "Insights · Critical · 7d"), None)
        assert saved is not None
        # The route surface label should reflect the new vocabulary
        # — backend describe_view runs over the payload.
        from src.intelligence.navigation import describe_view
        label = describe_view(saved["payload"])
        assert label.startswith("Insights · Overview")
        assert "News impact" in label
        assert "Critical" in label
        assert "Last 7 days" in label
        assert "AI narration on" in label


# ─────────────────────────────────────────────────────────────────────
# Dashboard + JS contract
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text("utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text("utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (PROJECT_ROOT / "dashboard" / "css" / "styles.css").read_text("utf-8")


class TestDashboardMarkup:
    def test_history_deck_present(self, index_html):
        for needle in (
            'id="insights-history"',
            'id="insights-history-summary"',
            'id="insights-history-sparkline"',
            'id="insights-history-list"',
            'id="insights-history-state"',
            'data-history-window="7"',
            'data-history-window="30"',
            'data-history-window="90"',
        ):
            assert needle in index_html, f"missing {needle}"

    def test_what_changed_title(self, index_html):
        assert "What changed" in index_html


class TestDashboardJs:
    def test_history_loader_and_renderer(self, app_js):
        for needle in (
            "intelligenceInsightsHistory",
            "loadInsightsHistory",
            "_renderHistorySummary",
            "_renderHistorySparkline",
            "_renderHistoryList",
        ):
            assert needle in app_js, f"missing JS handle {needle}"

    def test_capture_recognises_overview_surface(self, app_js):
        # _captureCurrentViewPayload must map Overview → surface intelligence.
        assert "subtab === 'overview'" in app_js
        assert "surface = 'intelligence'" in app_js

    def test_restore_applies_overview_filters(self, app_js):
        # _applyTargetFilter must restore insights filters.
        assert "target.surface === 'intelligence'" in app_js
        assert "insights-category-filter" in app_js
        assert "insights-severity-filter" in app_js
        assert "insights-include-ai" in app_js

    def test_run_now_refreshes_history(self, app_js):
        # _runInsightsNow must reload the history deck after a pass.
        assert "loadInsightsHistory" in app_js

    def test_target_surface_map_includes_intelligence(self, app_js):
        assert "intelligence: 'intelligence'" in app_js

    def test_auto_suggest_handles_insights_overview(self, app_js):
        assert "intelligence: 'Insights'" in app_js
        assert "isInsightsOverview" in app_js


class TestDashboardCss:
    def test_history_classes_present(self, styles_css):
        for cls in (
            ".insights-history",
            ".insights-history-pill",
            ".insights-history-chip",
            ".insights-history-sparkline",
            ".insights-history-bar",
            ".insights-history-list",
            ".insights-history-row",
            ".insights-history-empty",
        ):
            assert cls in styles_css, f"missing CSS class {cls}"


# ─────────────────────────────────────────────────────────────────────
# Privacy regression
# ─────────────────────────────────────────────────────────────────────


class TestPrivacy:
    def test_support_bundle_excludes_history_rows(self, tmp_path, client, seeded):
        import importlib.util
        import sqlite3
        import zipfile

        spec = importlib.util.spec_from_file_location(
            "_sb", PROJECT_ROOT / "scripts" / "support_bundle.py",
        )
        sb = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(sb)

        stage = tmp_path / "data_dir"
        (stage / "db").mkdir(parents=True)
        live = Path(os.environ["KLEITOS_DB_PATH"])
        src_conn = sqlite3.connect(str(live))
        try:
            dst_conn = sqlite3.connect(str(stage / "db" / "kleitos.db"))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        out = tmp_path / "bundle.zip"
        sb.build_bundle(stage, out)
        with zipfile.ZipFile(out) as zf:
            for name in zf.namelist():
                blob = zf.read(name).decode("utf-8", errors="replace")
                # Snapshot titles must never end up in the bundle.
                assert "Critical AAPL concentration" not in blob
                assert "pB-only alert" not in blob
                # Narration prompt invariant from Phase 12.
                assert "GROUNDING CONTRACT" not in blob
