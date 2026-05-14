"""Phase 9 — Corporate-events calendar foundation tests.

Coverage:

* Migration v9 creates ``corporate_events`` with the required columns
  and indexes; running migrations a second time is a no-op.
* ``ISIN_COUNTRY_MAP`` now resolves ``GR`` → greece.
* ``src.intelligence.listing.detect_listing`` / ``is_athex_listed``
  honour venue, ISIN prefix, ticker suffix (in that priority order)
  and fall back honestly to ``unknown``.
* ATHEX source config is present, ``unsupported: true``, and uses the
  ``corporate_events`` type so the regular news collector skips it.
* ``fetch_athex_events`` returns the typed degraded result by default.
* ``parse_csv`` validates required fields, normalises event types and
  dates, and emits per-row errors without aborting.
* ``import_csv`` matches rows by ISIN first then ticker; unmatched
  rows are kept with ``match_method='unmatched'``; duplicates are
  deduped on second import.
* API filters (month, event_type, exchange, ticker, holding_id, isin,
  date_from/date_to), pagination headers, envelope shape, scrubbed
  URLs, and 404 on missing detail are all honoured.
* Multi-portfolio isolation: a row in pA never bleeds into pB queries.
* Dashboard markup carries the new top-level Events tab, the
  customer label stays "Events", internal id uses ``corporate-events``.
* The News (events) sub-tab + ``/api/v1/events`` did NOT regress.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Module-scoped TestClient + temp DB
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase9_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase9.db")
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
    """Two portfolios with one ATHEX-listed and one US holding each."""
    import asyncio
    from src.database.connection import get_db
    from src.database.models import Holding, Portfolio

    iso = datetime.now(timezone.utc).isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph9_pA", name="Phase 9 A", base_currency="EUR",
                          is_default=0, created_at=iso, updated_at=iso),
                Portfolio(id="ph9_pB", name="Phase 9 B", base_currency="EUR",
                          is_default=0, created_at=iso, updated_at=iso),
            ])
            session.add_all([
                Holding(id="ph9_aapl_a", ticker="AAPL", currency="USD",
                        isin="US0378331005", quantity=5, weight_pct=10.0,
                        portfolio_id="ph9_pA", status="active",
                        created_at=iso, updated_at=iso),
                Holding(id="ph9_opap_a", ticker="OPAP", currency="EUR",
                        isin="GRS419003009", venue="ATHEX",
                        quantity=10, weight_pct=10.0,
                        portfolio_id="ph9_pA", status="active",
                        created_at=iso, updated_at=iso),
                Holding(id="ph9_etea_b", ticker="ETE", currency="EUR",
                        isin="GRS015003007", venue="ATHEX",
                        quantity=20, weight_pct=15.0,
                        portfolio_id="ph9_pB", status="active",
                        created_at=iso, updated_at=iso),
            ])
            await session.commit()

    asyncio.run(_seed())
    yield


# ───────────────────────────────────────────────────────────────────────────
# Migration / model
# ───────────────────────────────────────────────────────────────────────────


class TestMigrationAndModel:
    def test_current_schema_version_bumped(self):
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 9

    def test_corporate_events_table_exists(self, client):
        import asyncio
        from sqlalchemy import inspect
        from src.database.connection import get_engine

        async def _check():
            engine = get_engine()
            async with engine.connect() as conn:
                def _inspect(sync_conn):
                    insp = inspect(sync_conn)
                    cols = {c["name"] for c in insp.get_columns("corporate_events")}
                    idx = {i["name"] for i in insp.get_indexes("corporate_events")}
                    return cols, idx
                cols, idx = await conn.run_sync(_inspect)
                return cols, idx

        cols, idx = asyncio.run(_check())
        required_cols = {
            "id", "portfolio_id", "holding_id", "ticker", "isin",
            "exchange", "source_id", "source_name", "source_url",
            "event_type", "title", "description", "event_date",
            "event_time", "timezone", "status", "confidence",
            "match_method", "dedup_hash", "raw_payload",
            "import_batch_id", "created_at", "updated_at",
        }
        assert required_cols <= cols, f"missing cols: {required_cols - cols}"
        required_indexes = {
            "ix_corporate_events_portfolio_id",
            "ix_corporate_events_holding_id",
            "ix_corporate_events_ticker",
            "ix_corporate_events_isin",
            "ix_corporate_events_event_date",
            "ix_corporate_events_event_type",
            "ix_corporate_events_source_id",
            "ix_corporate_events_exchange",
        }
        assert required_indexes <= idx, f"missing indexes: {required_indexes - idx}"

    def test_migration_v9_is_idempotent(self, client):
        # Run migrations a second time (after the lifespan ran once); must
        # not raise and must not duplicate any structures.
        import asyncio
        from src.database.migrations import run_migrations
        asyncio.run(run_migrations())  # no-op second time
        asyncio.run(run_migrations())  # third time too, for paranoia


# ───────────────────────────────────────────────────────────────────────────
# Listing detection helper
# ───────────────────────────────────────────────────────────────────────────


class TestListingDetector:
    def test_gr_now_resolves_to_greece(self):
        from src.security_master.classifier import ISIN_COUNTRY_MAP
        assert ISIN_COUNTRY_MAP.get("GR") == "greece"

    def test_venue_alias_wins(self):
        from src.intelligence.listing import detect_listing
        listing = detect_listing({"ticker": "OPAP", "venue": "athens stock exchange"})
        assert listing.exchange == "ATHEX"
        assert listing.is_athex
        assert listing.confidence == "venue"

    def test_isin_gr_prefix(self):
        from src.intelligence.listing import detect_listing
        listing = detect_listing({"ticker": "OPAP", "isin": "GRS419003009"})
        assert listing.is_athex
        assert listing.confidence == "isin"

    def test_ticker_suffix_only(self):
        from src.intelligence.listing import detect_listing
        listing = detect_listing({"ticker": "OPAP.AT"})
        assert listing.is_athex
        assert listing.confidence == "ticker_suffix"

    def test_us_isin_is_not_athex(self):
        from src.intelligence.listing import detect_listing
        listing = detect_listing({"ticker": "AAPL", "isin": "US0378331005"})
        assert not listing.is_athex
        assert listing.listing_country == "united states"

    def test_no_signal_returns_unknown(self):
        from src.intelligence.listing import detect_listing, is_athex_listed
        listing = detect_listing({"ticker": "FOO"})
        assert listing.exchange is None
        assert listing.confidence == "unknown"
        assert is_athex_listed({"ticker": "FOO"}) is False

    def test_filter_athex_holdings_preserves_order(self):
        from src.intelligence.listing import filter_athex_holdings
        items = [
            {"ticker": "AAPL", "isin": "US0378331005"},
            {"ticker": "OPAP", "isin": "GRS419003009"},
            {"ticker": "ETE.AT"},
        ]
        out = filter_athex_holdings(items)
        assert [h["ticker"] for h in out] == ["OPAP", "ETE.AT"]


# ───────────────────────────────────────────────────────────────────────────
# Source config contract
# ───────────────────────────────────────────────────────────────────────────


class TestAthexSourceConfig:
    def test_yaml_declares_unsupported_corporate_events_source(self):
        import yaml
        text = (PROJECT_ROOT / "config" / "sources.yaml").read_text("utf-8")
        data = yaml.safe_load(text)
        sources = data.get("sources") or []
        athex = next((s for s in sources if s.get("id") == "athex-corporate-events"), None)
        assert athex is not None, "athex-corporate-events source missing"
        assert athex["type"] == "corporate_events"
        assert athex["unsupported"] is True
        assert athex["enabled"] is False
        # Customer-safe notes should mention the manual-import escape hatch
        assert "manual" in athex.get("notes", "").lower()


# ───────────────────────────────────────────────────────────────────────────
# ATHEX fetcher honest-degraded contract
# ───────────────────────────────────────────────────────────────────────────


class TestAthexFetcher:
    @pytest.mark.asyncio
    async def test_default_returns_unsupported(self):
        from src.corporate_events.athex import fetch_athex_events
        result = await fetch_athex_events()
        assert result.status == "unsupported"
        assert result.events == []
        assert "manual" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_degraded_when_parser_yields_nothing(self):
        # An operator who flips ``unsupported=False`` but whose parser
        # produces zero rows must see ``degraded`` — never a fake row.
        from src.corporate_events.athex import fetch_athex_events
        result = await fetch_athex_events(config={"unsupported": False})
        assert result.status == "degraded"
        assert result.events == []


# ───────────────────────────────────────────────────────────────────────────
# Manual CSV import
# ───────────────────────────────────────────────────────────────────────────


class TestParseCsv:
    def test_minimal_valid_row_parses(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = (
            "ticker,event_type,title,event_date\n"
            "OPAP,earnings,Q4 Results,2026-05-25\n"
        )
        rows, errors = parse_csv(csv_text)
        assert not errors
        assert len(rows) == 1
        assert rows[0]["ticker"] == "OPAP"
        assert rows[0]["event_type"] == "earnings"

    def test_missing_required_field_yields_error(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = "ticker,event_type,title,event_date\nOPAP,,Title,2026-05-25\n"
        rows, errors = parse_csv(csv_text)
        assert not rows
        assert errors and errors[0].field == "event_type"

    def test_unknown_event_type_rejected(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = "ticker,event_type,title,event_date\nOPAP,potluck,T,2026-05-25\n"
        rows, errors = parse_csv(csv_text)
        assert not rows
        assert any("Unknown event_type" in e.message for e in errors)

    def test_alias_event_type_normalises(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = (
            "ticker,event_type,title,event_date\n"
            "OPAP,Annual General Meeting,AGM 2026,2026-06-01\n"
        )
        rows, errors = parse_csv(csv_text)
        assert not errors
        assert rows[0]["event_type"] == "agm"

    def test_european_date_format_accepted(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = "ticker,event_type,title,event_date\nOPAP,earnings,T,25/05/2026\n"
        rows, errors = parse_csv(csv_text)
        assert not errors
        assert rows[0]["event_date"] == "2026-05-25"

    def test_isin_only_row_parses(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = (
            "isin,event_type,title,event_date\n"
            "GRS419003009,dividend,Div Payment,2026-07-01\n"
        )
        rows, errors = parse_csv(csv_text)
        assert not errors and rows[0]["ticker"] is None
        assert rows[0]["isin"] == "GRS419003009"
        # The listing detector should auto-fill exchange=ATHEX from the GR ISIN.
        assert rows[0]["exchange"] == "ATHEX"

    def test_neither_ticker_nor_isin_errors(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = "event_type,title,event_date\nearnings,T,2026-05-25\n"
        rows, errors = parse_csv(csv_text)
        assert not rows and errors and errors[0].field == "ticker/isin"

    def test_url_is_scrubbed(self):
        from src.corporate_events.manual_import import parse_csv
        csv_text = (
            "ticker,event_type,title,event_date,url\n"
            "OPAP,earnings,T,2026-05-25,https://example.com/x?apiKey=SECRET\n"
        )
        rows, _ = parse_csv(csv_text)
        assert "SECRET" not in (rows[0]["source_url"] or "")
        assert "apiKey=***" in rows[0]["source_url"]


# ───────────────────────────────────────────────────────────────────────────
# API — list / filters / envelope / scrubbing
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def imported_events(client, seeded):
    """Import a small batch of corporate events for filter/API tests."""
    csv_text = (
        "ticker,isin,event_type,title,event_date,event_time,url\n"
        "OPAP,GRS419003009,earnings,OPAP Q1,2026-05-15,10:00,https://athex.example/x?apiKey=LEAK\n"
        "OPAP,GRS419003009,dividend,OPAP Dividend,2026-05-20,,\n"
        "OPAP,GRS419003009,agm,OPAP AGM,2026-06-10,,\n"
        ",GRS999999999,announcement,Mystery,2026-05-22,,\n"
        "AAPL,,earnings,AAPL Q1,2026-05-30,,\n"
    )
    r = client.post(
        "/api/v1/corporate-events/import",
        json={"portfolio_id": "ph9_pA", "csv_text": csv_text},
    )
    assert r.status_code == 200, r.text
    # And one event in pB so we can check isolation
    csv_b = (
        "ticker,isin,event_type,title,event_date\n"
        "ETE,GRS015003007,earnings,ETE Results,2026-05-18\n"
    )
    rb = client.post(
        "/api/v1/corporate-events/import",
        json={"portfolio_id": "ph9_pB", "csv_text": csv_b},
    )
    assert rb.status_code == 200
    return r.json()


class TestCorporateEventsApi:
    def test_import_summary_shape(self, imported_events):
        s = imported_events
        assert s["imported"] >= 4
        assert s["matched_by_isin"] >= 1   # OPAP rows match by ISIN
        assert s["matched_by_ticker"] >= 1 # AAPL row matches by ticker
        assert s["unmatched"] >= 1         # Mystery ISIN row
        assert s["batch_id"]

    def test_list_filters_by_month(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "month": "2026-05", "envelope": "true"},
        )
        body = r.json()
        assert r.status_code == 200
        # Five events for pA in May 2026 minus the June one = 4
        types = [it["event_type"] for it in body["items"]]
        assert "agm" not in types  # June filtered out
        assert "earnings" in types

    def test_list_filters_by_event_type(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "event_type": "dividend", "envelope": "true"},
        )
        body = r.json()
        assert all(it["event_type"] == "dividend" for it in body["items"])
        assert body["total"] == len(body["items"])

    def test_list_filters_by_ticker(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "ticker": "AAPL", "envelope": "true"},
        )
        body = r.json()
        assert all(it["ticker"] == "AAPL" for it in body["items"])

    def test_list_filters_by_exchange_athex(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "exchange": "ATHEX", "envelope": "true"},
        )
        body = r.json()
        assert all(it["exchange"] == "ATHEX" for it in body["items"])

    def test_envelope_and_headers(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "envelope": "true", "limit": 2},
        )
        body = r.json()
        assert {"items", "total", "limit", "offset", "has_more"} <= set(body.keys())
        assert r.headers.get("X-Total-Count") == str(body["total"])
        assert r.headers.get("X-Has-More") == ("true" if body["has_more"] else "false")

    def test_default_returns_bare_list(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA"},
        )
        body = r.json()
        assert isinstance(body, list)
        assert "x-total-count" in {k.lower() for k in r.headers.keys()}

    def test_url_is_scrubbed_in_list(self, client, imported_events):
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "ticker": "OPAP", "event_type": "earnings",
                    "envelope": "true"},
        )
        body = r.json()
        url = (body["items"][0]).get("source_url")
        assert url
        assert "LEAK" not in url
        assert "apiKey=***" in url

    def test_portfolio_isolation(self, client, imported_events):
        r_a = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "envelope": "true"},
        )
        r_b = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pB", "envelope": "true"},
        )
        a_titles = {it["title"] for it in r_a.json()["items"]}
        b_titles = {it["title"] for it in r_b.json()["items"]}
        assert a_titles and b_titles
        assert a_titles.isdisjoint(b_titles)
        assert "ETE Results" in b_titles
        assert "ETE Results" not in a_titles

    def test_detail_404_when_missing(self, client, imported_events):
        r = client.get("/api/v1/corporate-events/does-not-exist",
                       params={"portfolio_id": "ph9_pA"})
        assert r.status_code == 404

    def test_detail_includes_scrubbed_url(self, client, imported_events):
        # Find the OPAP earnings event id
        r = client.get(
            "/api/v1/corporate-events",
            params={"portfolio_id": "ph9_pA", "ticker": "OPAP", "event_type": "earnings",
                    "envelope": "true"},
        )
        item = r.json()["items"][0]
        d = client.get(
            f"/api/v1/corporate-events/{item['id']}",
            params={"portfolio_id": "ph9_pA"},
        )
        assert d.status_code == 200
        body = d.json()
        assert "LEAK" not in (body.get("source_url") or "")
        assert "apiKey=***" in body["source_url"]

    def test_dedup_idempotent_second_import(self, client, seeded):
        csv_text = (
            "ticker,isin,event_type,title,event_date\n"
            "OPAP,GRS419003009,earnings,Dedup Probe,2026-09-01\n"
        )
        first = client.post(
            "/api/v1/corporate-events/import",
            json={"portfolio_id": "ph9_pA", "csv_text": csv_text},
        ).json()
        second = client.post(
            "/api/v1/corporate-events/import",
            json={"portfolio_id": "ph9_pA", "csv_text": csv_text},
        ).json()
        assert first["imported"] == 1
        assert second["imported"] == 0
        assert second["skipped_duplicate"] == 1

    def test_refresh_returns_unsupported_today(self, client, seeded):
        r = client.post(
            "/api/v1/corporate-events/refresh",
            params={"portfolio_id": "ph9_pA"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("unsupported", "degraded")
        assert body["imported"] == 0
        assert isinstance(body["reason"], str) and body["reason"]


# ───────────────────────────────────────────────────────────────────────────
# News (events) regression — Phase 8 still works
# ───────────────────────────────────────────────────────────────────────────


class TestNewsRegression:
    def test_events_endpoint_still_returns_bare_list(self, client):
        r = client.get("/api/v1/events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert "x-total-count" in {k.lower() for k in r.headers.keys()}


# ───────────────────────────────────────────────────────────────────────────
# Dashboard markup / JS / CSS contract
# ───────────────────────────────────────────────────────────────────────────


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
    def test_top_level_events_tab_present(self, index_html):
        assert 'data-tab="corporate-events"' in index_html
        # And the customer label is "Events", not "Calendar" / "Corporate"
        assert '>Events ' in index_html or '>Events<' in index_html

    def test_news_subtab_still_present(self, index_html):
        # Phase 5 invariant: News sub-tab still uses data-subtab="events"
        assert 'data-subtab="events"' in index_html
        assert ">News</button>" in index_html

    def test_calendar_grid_and_controls(self, index_html):
        for needle in (
            'id="ce-calendar"',
            'id="ce-month-prev"',
            'id="ce-month-next"',
            'id="ce-event-type"',
            'id="ce-exchange-filter"',
            'id="ce-ticker-filter"',
            'id="ce-refresh-btn"',
            'id="ce-import-btn"',
            'id="ce-import-dialog"',
            'id="ce-detail-dialog"',
            'id="tab-corporate-events"',
        ):
            assert needle in index_html, f"missing {needle}"


class TestDashboardJs:
    def test_corporate_events_loader_registered(self, app_js):
        assert "loadCorporateEvents" in app_js
        # tabLoaders maps the new key
        assert "'corporate-events':" in app_js or '"corporate-events":' in app_js

    def test_corporate_events_api_constants(self, app_js):
        for needle in ("corporateEvents:", "corporateEventsImport:", "corporateEventsRefresh:"):
            assert needle in app_js, f"missing API constant {needle}"

    def test_calendar_renderer_present(self, app_js):
        assert "_ceRenderCalendar" in app_js
        assert "_ceDayCells" in app_js


class TestDashboardCss:
    def test_calendar_classes_present(self, styles_css):
        for cls in (".ce-calendar", ".ce-cal-cell", ".ce-cal-chip",
                    ".ce-status-banner", ".ce-dialog"):
            assert cls in styles_css, f"missing CSS class {cls}"
