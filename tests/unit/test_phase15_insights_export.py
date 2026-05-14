"""Phase 15 — Insights export + shareable Overview state tests.

Coverage:

* Navigation:
  - ``history_state`` (with alias ``state``) is in the Insights Overview
    approved filter set.
  - ``validate_filters`` accepts it; unknown keys still get stripped.
  - ``describe_view`` renders the state label cleanly.
  - ``encode_nav_hash`` / ``decode_nav_hash`` round-trip the new
    history_state + time_window_days filters without dropping them.

* Export endpoints:
  - ``POST /api/v1/intelligence/insights/export`` returns a CSV with a
    fixed header row and a ``section`` column distinguishing current
    cards from history transitions.
  - ``GET /api/v1/intelligence/insights/export.json`` returns the
    stable JSON shape (portfolio_id, generated_at, window_days,
    filters, summary, current_cards, history, daily_counts,
    grounding_status, warnings, coverage, last_generated_at).
  - Filters (category, severity, history_state, days) are honoured.
  - Portfolio isolation holds — exporting portfolio A never includes
    portfolio B rows.
  - Privacy: response bodies never contain API keys, AI prompt
    bodies, uploaded document content, or .env strings.
  - The CSV filename header matches
    ``axion-insights-overview-YYYYMMDD-HHMMSS.csv``.

* Dashboard markup contract:
  - Three new toolbar buttons exist on the Insights Overview surface
    (Export CSV / Export JSON / Copy share link) with the expected ids.
  - app.js carries the matching event wiring + API constants.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Pure navigation / describe_view contract
# ─────────────────────────────────────────────────────────────────────


class TestPhase15NavigationVocabulary:
    def test_history_state_in_approved_filters(self):
        from src.intelligence.navigation import _APPROVED_FILTERS
        approved = _APPROVED_FILTERS[("intelligence", "overview")]
        assert "history_state" in approved
        assert "state" in approved  # alias

    def test_validate_filters_keeps_history_state(self):
        from src.intelligence.navigation import validate_filters
        out = validate_filters("intelligence", "overview", {
            "category": "news_impact",
            "history_state": "new",
            "rogue": "drop_me",
        })
        assert out is not None
        assert out.get("history_state") == "new"
        assert "rogue" not in out

    def test_state_alias_also_passes(self):
        from src.intelligence.navigation import validate_filters
        out = validate_filters("intelligence", "overview", {
            "state": "escalated",
        })
        assert out is not None
        assert out.get("state") == "escalated"


class TestPhase15DescribeView:
    def test_history_state_new_label(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"history_state": "new"},
        })
        assert "New only" in out

    def test_history_state_escalated_label(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"history_state": "escalated"},
        })
        assert "Escalated only" in out

    def test_history_state_all_is_silent(self):
        # An empty/all state filter must NOT add a noisy suffix.
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"history_state": ""},
        })
        assert out == "Insights · Overview"

    def test_state_alias_renders_same_label(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {"state": "new"},
        })
        assert "New only" in out

    def test_combo_label_with_history_state(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "intelligence", "subtab": "overview",
            "filters": {
                "category": "news_impact",
                "severity": "high",
                "time_window_days": 7,
                "include_ai": "true",
                "history_state": "escalated",
            },
        })
        for piece in ("Insights", "Overview", "News impact", "High",
                      "Last 7 days", "AI narration on", "Escalated only"):
            assert piece in out


class TestPhase15HashRoundTrip:
    def test_encode_decode_carries_history_state(self):
        from src.intelligence.navigation import (
            NavigationTarget, decode_nav_hash, encode_nav_hash,
        )
        t = NavigationTarget(
            surface="intelligence",
            portfolio_id="default",
            subtab="overview",
            filters={
                "category": "news_impact",
                "severity": "high",
                "time_window_days": "7",
                "history_state": "escalated",
                "include_ai": "true",
            },
        )
        h = encode_nav_hash(t)
        assert h.startswith("#nav=")
        d = decode_nav_hash(h)
        assert d is not None
        assert d.get("surface") == "intelligence"
        assert d.get("subtab") == "overview"
        filters = d.get("filters") or {}
        assert filters.get("history_state") == "escalated"
        assert filters.get("time_window_days") == "7"
        assert filters.get("category") == "news_impact"
        assert filters.get("severity") == "high"
        assert filters.get("include_ai") == "true"

    def test_encode_strips_label_but_keeps_filters(self):
        from src.intelligence.navigation import (
            NavigationTarget, decode_nav_hash, encode_nav_hash,
        )
        t = NavigationTarget(
            surface="intelligence",
            portfolio_id="default",
            subtab="overview",
            label="UI label that must be stripped",
            filters={"history_state": "new"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert "label" not in d
        assert (d.get("filters") or {}).get("history_state") == "new"


# ─────────────────────────────────────────────────────────────────────
# Export endpoints via TestClient
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase15_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase15.db")
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
    """Two portfolios + insight-snapshot rows for the history portion."""
    import uuid
    from src.database.connection import get_db
    from src.database.models import InsightSnapshot, Portfolio

    iso = datetime.now(timezone.utc).isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph15_pA", name="Phase 15 A",
                          base_currency="USD", is_default=0,
                          created_at=iso, updated_at=iso),
                Portfolio(id="ph15_pB", name="Phase 15 B",
                          base_currency="USD", is_default=0,
                          created_at=iso, updated_at=iso),
            ])
            await session.commit()
        async with get_db() as session:
            session.add_all([
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph15_pA",
                    card_key="insight:alert:alert:a1",
                    category="alert", severity="critical",
                    title="Critical concentration on PH15_TICKER_A",
                    fingerprint="fp_a1", last_seen_at=iso, first_seen_at=iso,
                    notified_at=iso, notified_severity="critical",
                    status="escalated", created_at=iso, updated_at=iso,
                ),
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph15_pA",
                    card_key="insight:news_impact:event:evt_n1",
                    category="news_impact", severity="high",
                    title="Fed signals rate hike (PH15)",
                    fingerprint="fp_n1", last_seen_at=iso, first_seen_at=iso,
                    status="new", created_at=iso, updated_at=iso,
                ),
                InsightSnapshot(
                    id=str(uuid.uuid4()), portfolio_id="ph15_pA",
                    card_key="insight:listing_country:listing-country:US",
                    category="listing_country", severity="medium",
                    title="Listing concentration: US (PH15)",
                    fingerprint="fp_l1", last_seen_at=iso, first_seen_at=iso,
                    status="unchanged", created_at=iso, updated_at=iso,
                ),
            ])
            # pB row that must never leak into pA exports.
            session.add(InsightSnapshot(
                id=str(uuid.uuid4()), portfolio_id="ph15_pB",
                card_key="insight:alert:alert:b1",
                category="alert", severity="high",
                title="PH15_B_TENANT_ALERT — must not leak",
                fingerprint="fp_b1", last_seen_at=iso, first_seen_at=iso,
                status="new", created_at=iso, updated_at=iso,
            ))
            await session.commit()
    asyncio.run(_seed())
    yield


class TestCsvExport:
    def test_csv_header_row(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200, r.text
        text = r.text
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        assert rows, "CSV must have at least the header row"
        header = rows[0]
        # The Phase 15 spec pins the exact column ordering.
        expected_cols = [
            "section", "category", "severity", "state",
            "title", "summary", "why_it_matters", "recommended_action",
            "affected_holdings", "confidence",
            "first_seen_at", "last_seen_at", "notified_at",
            "deep_link_label", "deep_link_surface", "deep_link_subtab",
            "source_type",
        ]
        assert header == expected_cols

    def test_csv_history_section_present(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA", "days": "30"},
        )
        assert r.status_code == 200
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        # All three seeded snapshot rows should appear with section=history.
        history_rows = [r for r in rows if r.get("section") == "history"]
        titles = [r.get("title") for r in history_rows]
        assert any("PH15" in (t or "") for t in titles), (
            f"expected at least one seeded PH15 history row in CSV: {titles!r}"
        )

    def test_csv_filename_header(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200
        cd = r.headers.get("content-disposition") or ""
        m = re.search(r'filename="?(axion-insights-overview-\d{8}-\d{6}\.csv)"?', cd)
        assert m, f"Content-Disposition did not match expected pattern: {cd}"

    def test_csv_portfolio_isolation(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA"},
        )
        text = r.text
        assert "PH15_B_TENANT_ALERT" not in text, (
            "portfolio ph15_pA export leaked a ph15_pB row"
        )

    def test_csv_filter_state_new_only(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA", "history_state": "new"},
        )
        assert r.status_code == 200
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        history_rows = [r for r in rows if r.get("section") == "history"]
        # The escalated + unchanged seeded rows must be gone.
        assert all(r.get("state") == "new" for r in history_rows), (
            f"history rows leaked non-new states: {[r.get('state') for r in history_rows]}"
        )

    def test_csv_filter_category_narrows(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA", "category": "alert"},
        )
        assert r.status_code == 200
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        history_rows = [r for r in rows if r.get("section") == "history"]
        # Only the seeded alert row should remain in history.
        for row in history_rows:
            assert row.get("category") == "alert", (
                f"category=alert filter let through {row.get('category')}"
            )


class TestJsonExport:
    def test_json_shape(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("portfolio_id", "generated_at", "window_days",
                    "filters", "summary", "current_cards", "history",
                    "daily_counts", "grounding_status", "warnings",
                    "coverage", "last_generated_at"):
            assert key in body, f"JSON export missing key: {key}"

    def test_json_summary_zero_keys(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "nonexistent_pf"},
        )
        assert r.status_code == 200
        body = r.json()
        for k in ("new", "escalated", "unchanged", "total"):
            assert body["summary"].get(k) == 0, body["summary"]

    def test_json_portfolio_isolation(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA"},
        )
        body_text = r.text
        assert "PH15_B_TENANT_ALERT" not in body_text

    def test_json_filters_pass_through(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={
                "portfolio_id": "ph15_pA",
                "category": "alert",
                "severity": "critical",
                "history_state": "escalated",
                "days": "30",
            },
        )
        assert r.status_code == 200
        body = r.json()
        f = body.get("filters") or {}
        assert f.get("category") == "alert"
        assert f.get("severity") == "critical"
        assert f.get("history_state") == "escalated"
        assert body.get("window_days") == 30

    def test_json_history_state_filter_narrows(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA", "history_state": "new"},
        )
        assert r.status_code == 200
        items = r.json().get("history") or []
        for it in items:
            assert it.get("state") == "new", f"leaked state: {it.get('state')}"


# ─────────────────────────────────────────────────────────────────────
# Privacy invariants
# ─────────────────────────────────────────────────────────────────────


FORBIDDEN_SUBSTRINGS = [
    "GROUNDING CONTRACT",
    "STRUCTURED DATA",
    "ANTI-HALLUCINATION",
    "BEGIN PDF",
    "END PDF",
    "-----BEGIN",
    "api_key=",
    "apikey=",
    "Bearer ",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "FINNHUB_KEY",
    "NEWSAPI_KEY",
    "TELEGRAM_BOT_TOKEN",
]


class TestPrivacy:
    def test_csv_has_no_forbidden_substrings(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200
        text = r.text
        for needle in FORBIDDEN_SUBSTRINGS:
            assert needle not in text, (
                f"CSV export leaked forbidden substring: {needle!r}"
            )

    def test_json_has_no_forbidden_substrings(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200
        text = r.text
        for needle in FORBIDDEN_SUBSTRINGS:
            assert needle not in text, (
                f"JSON export leaked forbidden substring: {needle!r}"
            )

    def test_scrubber_redacts_leaked_strings(self):
        """Defensive: even if a future generator regression were to
        inline a prompt body or secret, the ``_safe_str`` scrubber
        replaces it with ``[redacted]`` before the row is emitted."""
        from src.api.routes.intelligence import _safe_str
        assert _safe_str("GROUNDING CONTRACT please") == "[redacted]"
        assert _safe_str("api_key=sk_test_123") == "[redacted]"
        assert _safe_str("Bearer abc123") == "[redacted]"
        # Plain content is untouched.
        assert _safe_str("Fed signals rate hike") == "Fed signals rate hike"
        assert _safe_str(None) == ""

    def test_no_env_path_in_export(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA"},
        )
        # ``.axion.env`` is where Axion stores AI keys; it must never
        # appear in an export.
        assert ".axion.env" not in r.text
        # Generic ``.env`` substring is a softer check — the export
        # has no business referencing dotfile config either.
        assert "/.env" not in r.text


# ─────────────────────────────────────────────────────────────────────
# Dashboard markup + JS contract
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text(
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(
        encoding="utf-8",
    )


class TestDashboardMarkup:
    def test_export_csv_button(self, index_html):
        assert 'id="insights-export-csv-btn"' in index_html

    def test_export_json_button(self, index_html):
        assert 'id="insights-export-json-btn"' in index_html

    def test_copy_share_link_button(self, index_html):
        assert 'id="insights-copy-link-btn"' in index_html

    def test_buttons_inside_overview_subtab(self, index_html):
        # Sanity: the three buttons live under the Insights → Overview
        # subtab markup (not somewhere stray).
        m = re.search(
            r'id="subtab-overview".*?</div>\s*<!-- Inbox sub-tab',
            index_html, re.DOTALL,
        )
        assert m, "could not locate subtab-overview block"
        block = m.group(0)
        for needle in (
            "insights-export-csv-btn",
            "insights-export-json-btn",
            "insights-copy-link-btn",
        ):
            assert needle in block, f"{needle} not under subtab-overview"


class TestDashboardJs:
    def test_api_constants_present(self, app_js):
        assert "intelligenceInsightsExportCsv" in app_js
        assert "intelligenceInsightsExportJson" in app_js
        assert "/api/v1/intelligence/insights/export.json" in app_js

    def test_button_wiring_present(self, app_js):
        # The wire-once block hooks each of the three new buttons.
        for needle in (
            "insights-export-csv-btn",
            "insights-export-json-btn",
            "insights-copy-link-btn",
        ):
            assert needle in app_js, f"app.js missing wire for {needle}"

    def test_capture_payload_includes_history_state(self, app_js):
        # Phase 15 — the captured saved-view payload carries
        # history_state so a shared link round-trips it.
        assert "history_state" in app_js

    def test_apply_filter_restores_history_state(self, app_js):
        # The restore branch reads history_state (with alias state) and
        # sets the dashboard select.
        assert "insights-history-state" in app_js
        # The restore branch must reference the same set of values
        # the select offers (new/escalated/unchanged/all).
        assert "'new'" in app_js
        assert "'escalated'" in app_js

    def test_copy_helper_routes_through_existing_copyDeepLink(self, app_js):
        # Phase 9R `_copyDeepLink` is the single source of truth for
        # writing nav hashes to the clipboard.  The new copy-share
        # button must route through it instead of rolling its own.
        assert "_copyInsightsShareLink" in app_js
        assert "window._copyDeepLink" in app_js


# ─────────────────────────────────────────────────────────────────────
# Stable surface vocabulary lock-in (Phase 9Q)
# ─────────────────────────────────────────────────────────────────────


class TestSurfaceLockIn:
    def test_known_surfaces_unchanged_by_phase15(self):
        # Phase 15 must not silently add a new surface — the export
        # endpoints live under the existing ``intelligence`` surface.
        from src.intelligence.navigation import _KNOWN_SURFACES
        assert _KNOWN_SURFACES == frozenset({
            "alerts", "digest", "events", "operator", "portfolio",
            "corporate-events", "settings",
            "intelligence",
        })


# ─────────────────────────────────────────────────────────────────────
# Smoke: export endpoint returns valid CSV that parses cleanly
# ─────────────────────────────────────────────────────────────────────


class TestCsvSmoke:
    def test_csv_parses_as_valid_csv(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/export",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200
        # Round-trip through csv.DictReader to confirm the response
        # parses cleanly (no embedded newlines that break naive readers,
        # no quoting mismatch).
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        assert reader.fieldnames is not None
        # Every parsed row has a matching section value from {current, history}.
        for row in rows:
            assert row.get("section") in ("current", "history"), row

    def test_json_export_is_valid_json(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/export.json",
            params={"portfolio_id": "ph15_pA"},
        )
        assert r.status_code == 200
        # Will raise if the body isn't valid JSON.
        body = json.loads(r.text)
        assert isinstance(body, dict)
