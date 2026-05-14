"""Phase 8 — News-tab hardening tests.

Coverage:

* ``_scrub_url`` strips ``apiKey``, ``api_key``, ``key``, ``token``,
  ``access_token``, ``auth``, ``Authorization`` and ``secret`` query
  parameters before any URL is returned to a customer surface.
* The list endpoint ``GET /api/v1/events`` honours the new filter
  parameters (q/search, source_id, holding_id, ticker, event_type,
  materiality, materiality_min, confidence_min, factor_key, linked_only,
  date range).
* The list endpoint keeps returning a bare list by default — back-compat
  with Phase 5–7 callers.
* ``envelope=true`` returns the
  ``{items, total, limit, offset, has_more}`` shape.
* ``X-Total-Count`` and ``X-Has-More`` are always set.
* The detail endpoint scrubs ``url`` too.
* ``describe_view`` produces the right human label for the new News
  filters: ``Source: …``, ``Type: …``, ``Factor: …``,
  ``Materiality: …``, ``Linked only`` and ``Search: …``.
* ``validate_filters`` lets the new keys through on the News surface
  and still strips unknown keys.
* The dashboard markup carries the new filter ids, the customer
  label is "News" (never "Events"), and the JS uses a debounced
  backend search and a reset action.
* CSS exposes the chip and pill classes.

The route-level filter tests use FastAPI's ``TestClient`` so we
exercise the full HTTP path (including header injection and Pydantic
response coercion).  Tests are isolated via a fresh temp directory
and SQLite database — no real network, no shared state with other
test files.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Fixtures — module-local temp DB so we don't collide with other suites
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """A FastAPI TestClient backed by a fresh temp DB.

    We do the same env-override dance as ``tests/smoke/test_api_smoke.py``
    — set ``KLEITOS_DB_PATH`` / ``KLEITOS_DATA_DIR`` *before* the FastAPI
    app is imported, clear the settings cache, then enter the lifespan
    so migrations run.
    """
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase8_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase8.db")
    os.environ["KLEITOS_DATA_DIR"] = tmp_dir
    os.environ["KLEITOS_LOG_LEVEL"] = "WARNING"

    from src.config import get_settings  # noqa: WPS433 — late import is intentional
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

    # Restore environment + tear down the engine so other test modules
    # are not poisoned by our temp DB.
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
def seeded_news(client):
    """Seed a deterministic mini-newsroom for filter tests.

    Three events of varied source, type, materiality, confidence and
    date.  Two link to a single holding (AAPL); one carries a
    deterministic ``MacroFactorEvent`` row.  One event URL contains an
    ``apiKey=`` query parameter so we can assert the scrubber runs on
    the response.
    """
    import asyncio
    from src.database.connection import get_db
    from src.database.models import (
        Event, EventLink, Holding, MacroFactorEvent, Portfolio, Source,
    )

    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    iso_old = (now - timedelta(days=10)).isoformat()
    iso_very_old = (now - timedelta(days=40)).isoformat()

    async def _seed():
        async with get_db() as session:
            # Portfolio + one holding (AAPL) — kept tiny on purpose.
            session.add(Portfolio(
                id="phase8_pf", name="Phase 8", base_currency="USD",
                is_default=0, created_at=iso_now, updated_at=iso_now,
            ))
            session.add(Holding(
                id="phase8_aapl", ticker="AAPL", currency="USD",
                quantity=10, weight_pct=10.0, portfolio_id="phase8_pf",
                status="active", created_at=iso_now, updated_at=iso_now,
            ))
            session.add(Source(
                id="phase8_src", name="Phase 8 Wire",
                domain="phase8.example.com",
                url="https://phase8.example.com/feed.xml",
                source_type="rss",
                parser_id="rss_generic",
                trust_level="medium",
                enabled=1, created_at=iso_now,
            ))
            await session.commit()

            session.add_all([
                Event(
                    id="phase8_evt_secret",
                    title="Fed signals rate hike",
                    summary="The Federal Reserve hinted at a 50bps move.",
                    url="https://example.com/article?id=42&apiKey=SECRET_LEAK&token=ZZZZ",
                    event_type="macro",
                    materiality="high",
                    confidence="high",
                    published_at=iso_now,
                    fetched_at=iso_now,
                    created_at=iso_now,
                    dedup_hash=str(uuid.uuid4()),
                    source_id="phase8_src",
                ),
                Event(
                    id="phase8_evt_link",
                    title="Apple unveils new revenue guidance",
                    summary="AAPL CFO laid out a new outlook on guidance.",
                    url="https://example.com/aapl",
                    event_type="earnings",
                    materiality="watch",
                    confidence="medium",
                    published_at=iso_old,
                    fetched_at=iso_old,
                    created_at=iso_old,
                    dedup_hash=str(uuid.uuid4()),
                    source_id="phase8_src",
                ),
                Event(
                    id="phase8_evt_old",
                    title="Old supply chain rumour",
                    summary="A semiconductor shortage rumour from last month.",
                    url="https://example.com/old",
                    event_type="supply_chain",
                    materiality="immaterial",
                    confidence="low",
                    published_at=iso_very_old,
                    fetched_at=iso_very_old,
                    created_at=iso_very_old,
                    dedup_hash=str(uuid.uuid4()),
                    source_id="phase8_src",
                ),
            ])
            await session.commit()

            # Macro factor classification on the Fed event only.
            session.add(MacroFactorEvent(
                id=str(uuid.uuid4()),
                event_id="phase8_evt_secret",
                factor="interest_rate",
                direction="up",
                magnitude="moderate",
                confidence=0.8,
                created_at=iso_now,
            ))
            # Event → holding link on the Apple event only.
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="phase8_evt_link",
                link_type="direct_match",
                link_target="phase8_aapl",
                relevance_score=0.9,
                created_at=iso_old,
            ))
            await session.commit()

    asyncio.run(_seed())
    yield
    # No cleanup — module-scoped client tears down the DB.


# ───────────────────────────────────────────────────────────────────────────
# URL scrubber — direct unit tests
# ───────────────────────────────────────────────────────────────────────────


class TestScrubUrl:
    def test_strips_apikey_query_param(self):
        from src.api.routes.events import _scrub_url
        out = _scrub_url("https://example.com/a?apiKey=BIG_SECRET&id=1")
        assert "BIG_SECRET" not in out
        assert "apiKey=***" in out
        assert "id=1" in out

    def test_strips_api_key_underscore_variant(self):
        from src.api.routes.events import _scrub_url
        out = _scrub_url("https://example.com/a?api_key=abc123&q=test")
        assert "abc123" not in out
        assert "api_key=***" in out
        assert "q=test" in out

    def test_strips_token_and_access_token_and_auth(self):
        from src.api.routes.events import _scrub_url
        out = _scrub_url(
            "https://x.com/?token=AAA&access_token=BBB&auth=CCC&secret=DDD"
        )
        for v in ("AAA", "BBB", "CCC", "DDD"):
            assert v not in out
        assert out.count("***") >= 4

    def test_idempotent_when_no_secrets(self):
        from src.api.routes.events import _scrub_url
        url = "https://example.com/article?id=42&page=2"
        assert _scrub_url(url) == url

    def test_none_returns_none(self):
        from src.api.routes.events import _scrub_url
        assert _scrub_url(None) is None
        assert _scrub_url("") == ""


# ───────────────────────────────────────────────────────────────────────────
# Confidence + materiality ladder helper
# ───────────────────────────────────────────────────────────────────────────


class TestRankLadder:
    def test_at_or_above_confidence(self):
        from src.api.routes.events import _CONFIDENCE_ORDER, _at_or_above
        labels = set(_at_or_above(_CONFIDENCE_ORDER, "medium"))
        assert {"medium", "high", "critical"} <= labels
        assert "low" not in labels
        assert "unscored" not in labels

    def test_at_or_above_materiality_alias(self):
        from src.api.routes.events import _MATERIALITY_ORDER, _at_or_above
        labels = set(_at_or_above(_MATERIALITY_ORDER, "high"))
        # "high" and "important" share rank 3, plus critical above.
        assert {"high", "important", "critical"} <= labels
        assert "watch" not in labels
        assert "immaterial" not in labels

    def test_unknown_rung_returns_empty(self):
        from src.api.routes.events import _CONFIDENCE_ORDER, _at_or_above
        assert _at_or_above(_CONFIDENCE_ORDER, "not_a_rung") == []


# ───────────────────────────────────────────────────────────────────────────
# Route behaviour — filters / envelope / headers / scrubbing
# ───────────────────────────────────────────────────────────────────────────


class TestListEventsFilters:
    def test_default_returns_bare_list(self, client, seeded_news):
        r = client.get("/api/v1/events")
        assert r.status_code == 200
        body = r.json()
        # Back-compat: default shape stays a list of EventResponse.
        assert isinstance(body, list)
        # Headers are still emitted in every response.
        assert "x-total-count" in {k.lower() for k in r.headers.keys()}
        assert "x-has-more" in {k.lower() for k in r.headers.keys()}

    def test_envelope_returns_pagination_shape(self, client, seeded_news):
        r = client.get("/api/v1/events?envelope=true&limit=2")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        for key in ("items", "total", "limit", "offset", "has_more"):
            assert key in body, f"missing envelope key: {key}"
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert isinstance(body["items"], list)
        # Total at least covers our three seeded events (other tests
        # may have inserted more — accept >=).
        assert body["total"] >= 3
        if body["total"] > 2:
            assert body["has_more"] is True

    def test_pagination_headers_match_envelope(self, client, seeded_news):
        r = client.get("/api/v1/events?envelope=true&limit=1")
        envelope = r.json()
        assert r.headers.get("X-Total-Count") == str(envelope["total"])
        assert r.headers.get("X-Has-More") == ("true" if envelope["has_more"] else "false")

    def test_q_filters_by_title_or_summary(self, client, seeded_news):
        r = client.get("/api/v1/events?q=fed&envelope=true")
        body = r.json()
        ids = {it["id"] for it in body["items"]}
        assert "phase8_evt_secret" in ids
        assert "phase8_evt_link" not in ids
        assert "phase8_evt_old" not in ids

    def test_source_id_filter(self, client, seeded_news):
        r = client.get("/api/v1/events?source_id=phase8_src&envelope=true")
        body = r.json()
        ids = {it["id"] for it in body["items"]}
        # All three seeded events belong to phase8_src
        assert {"phase8_evt_secret", "phase8_evt_link", "phase8_evt_old"} <= ids
        r2 = client.get("/api/v1/events?source_id=does_not_exist&envelope=true")
        assert r2.json()["total"] == 0

    def test_event_type_filter(self, client, seeded_news):
        r = client.get("/api/v1/events?event_type=earnings&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_link" in ids
        assert "phase8_evt_secret" not in ids

    def test_ticker_filter(self, client, seeded_news):
        r = client.get("/api/v1/events?ticker=AAPL&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_link" in ids
        assert "phase8_evt_secret" not in ids

    def test_holding_id_filter(self, client, seeded_news):
        r = client.get("/api/v1/events?holding_id=phase8_aapl&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_link" in ids
        assert "phase8_evt_secret" not in ids

    def test_linked_only_filter(self, client, seeded_news):
        r = client.get("/api/v1/events?linked_only=true&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_link" in ids
        assert "phase8_evt_secret" not in ids
        assert "phase8_evt_old" not in ids

    def test_factor_key_filter(self, client, seeded_news):
        r = client.get(
            "/api/v1/events?factor_key=interest_rate&envelope=true"
        )
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_secret" in ids
        assert "phase8_evt_link" not in ids

    def test_materiality_min_filter(self, client, seeded_news):
        # high+ should retain the Fed event (high), drop watch + immaterial
        r = client.get("/api/v1/events?materiality_min=high&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_secret" in ids
        assert "phase8_evt_link" not in ids
        assert "phase8_evt_old" not in ids

    def test_confidence_min_filter(self, client, seeded_news):
        # medium+ should retain the Fed (high) + Apple (medium), drop the
        # immaterial low-confidence supply-chain event.
        r = client.get("/api/v1/events?confidence_min=medium&envelope=true")
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_secret" in ids
        assert "phase8_evt_link" in ids
        assert "phase8_evt_old" not in ids

    def test_date_from_filter_drops_old_event(self, client, seeded_news):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        r = client.get(
            "/api/v1/events",
            params={"date_from": cutoff, "envelope": "true"},
        )
        assert r.status_code == 200, r.text
        ids = {it["id"] for it in r.json()["items"]}
        assert "phase8_evt_old" not in ids
        assert "phase8_evt_secret" in ids


class TestUrlScrubbingInResponses:
    def test_list_response_scrubs_event_url(self, client, seeded_news):
        r = client.get("/api/v1/events?q=fed&envelope=true")
        items = r.json()["items"]
        assert items, "expected the seeded Fed event"
        url = items[0]["url"]
        assert url is not None
        assert "SECRET_LEAK" not in url
        assert "apiKey=***" in url
        assert "token=***" in url

    def test_detail_response_scrubs_event_url(self, client, seeded_news):
        r = client.get("/api/v1/events/phase8_evt_secret")
        assert r.status_code == 200
        body = r.json()
        url = body["url"]
        assert "SECRET_LEAK" not in url
        assert "apiKey=***" in url

    def test_recent_endpoint_also_scrubs(self, client, seeded_news):
        # /events/recent reuses ``_row_to_event`` so the scrubber applies
        # transitively; assert it explicitly so a future helper-split
        # doesn't silently regress.
        r = client.get("/api/v1/events/recent")
        assert r.status_code == 200
        for item in r.json():
            if item.get("id") == "phase8_evt_secret":
                assert "SECRET_LEAK" not in (item.get("url") or "")
                assert "apiKey=***" in item["url"]
                break


# ───────────────────────────────────────────────────────────────────────────
# Navigation / saved-view label tests
# ───────────────────────────────────────────────────────────────────────────


class TestNavigationLabels:
    def test_news_label_with_source_filter(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"source_id": "phase8_src"},
        })
        assert out.startswith("News")
        assert "Source: phase8_src" in out

    def test_news_label_with_ticker_filter(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"ticker": "AAPL"},
        })
        assert "Ticker: AAPL" in out

    def test_news_label_with_factor_filter(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"factor_key": "interest_rate"},
        })
        assert "Factor: interest_rate" in out

    def test_news_label_with_event_type_filter(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"event_type": "earnings"},
        })
        assert "Type: earnings" in out

    def test_news_label_with_materiality_filter(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"materiality_min": "high"},
        })
        assert "Materiality: high" in out

    def test_news_label_with_linked_only_truthy(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"linked_only": "true"},
        })
        assert "Linked only" in out

    def test_news_label_skips_linked_only_when_falsy(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"linked_only": "false"},
        })
        assert "Linked only" not in out
        # The customer label still leads with "News".
        assert "News" in out

    def test_news_label_q_renders_search(self):
        from src.intelligence.navigation import describe_view
        out = describe_view({
            "surface": "events", "subtab": "events",
            "filters": {"q": "fed"},
        })
        assert "Search: fed" in out


class TestValidateFiltersForNews:
    def test_approved_keys_survive(self):
        from src.intelligence.navigation import validate_filters
        out = validate_filters("events", "events", {
            "q": "fed",
            "source_id": "phase8_src",
            "factor_key": "interest_rate",
            "event_type": "earnings",
            "materiality_min": "high",
            "linked_only": "true",
        })
        assert out is not None
        for k in ("q", "source_id", "factor_key", "event_type",
                  "materiality_min", "linked_only"):
            assert k in out

    def test_unknown_key_is_stripped(self):
        from src.intelligence.navigation import validate_filters
        out = validate_filters("events", "events", {
            "q": "fed",
            "rogue_key": "nope",
        })
        assert "rogue_key" not in (out or {})
        assert out and "q" in out


# ───────────────────────────────────────────────────────────────────────────
# Dashboard markup / JS / CSS contract
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (PROJECT_ROOT / "dashboard" / "css" / "styles.css").read_text(encoding="utf-8")


class TestNewsTabMarkup:
    """The News sub-tab markup carries the Phase 8 filter controls."""

    REQUIRED_IDS = [
        "events-search",
        "events-filter-source",
        "events-filter-type",
        "events-filter-factor",
        "events-filter-materiality",
        "events-filter-linked",
        "events-filter-reset",
        "events-filter-status",
    ]

    def test_filter_controls_present(self, index_html):
        for needle in self.REQUIRED_IDS:
            assert f'id="{needle}"' in index_html, f"missing control: {needle}"

    def test_date_range_pills_present(self, index_html):
        for r in ("24h", "7d", "30d"):
            assert f'data-range="{r}"' in index_html, f"missing range pill: {r}"
        # The "All" pill is the default-active one with an empty data-range.
        assert 'data-range=""' in index_html

    def test_news_header_uses_news_label(self, index_html):
        # The sub-tab heading must say "News", not "Events".
        # Match within the News sub-panel only.
        idx = index_html.find('id="subtab-events"')
        assert idx >= 0
        block = index_html[idx:idx + 4000]
        assert "<h2>News</h2>" in block
        assert "<h2>Events</h2>" not in block

    def test_internal_ids_keep_events_prefix(self, index_html):
        # Internal DOM ids must still use the `events-` prefix — Phase
        # 5 promised: customer label changes, internal keys stay.
        assert 'id="subtab-events"' in index_html
        assert 'id="events-table"' in index_html


class TestNewsJsContract:
    def test_debounced_filter_events(self, app_js):
        # Phase 8 — search now debounces before hitting the backend.
        assert "_eventsSearchDebounce" in app_js
        assert "setTimeout" in app_js
        assert "loadEvents()" in app_js

    def test_filter_state_struct_present(self, app_js):
        for key in ("q:", "source_id:", "event_type:", "factor_key:",
                    "materiality_min:", "range:", "linked_only:"):
            assert key in app_js, f"missing filter-state key: {key}"

    def test_reset_helper_present(self, app_js):
        assert "_eventsResetFilters" in app_js
        assert "events-filter-reset" in app_js

    def test_envelope_aware_load(self, app_js):
        # The loader handles the envelope shape from the backend.
        assert "'envelope'" in app_js, "loader must send envelope=true"
        assert "data.items" in app_js, "loader must read envelope items"

    def test_status_chip_renderer(self, app_js):
        assert "_renderEventStatusChips" in app_js
        # The chip CSS classes must match what the stylesheet declares.
        assert "chip-linked" in app_js
        assert "chip-macro" in app_js

    def test_no_legacy_client_side_substring_filter(self, app_js):
        # Phase 8 — the old client-side title.includes(q) path is gone.
        # The bare ``allEvents.filter`` pattern was Phase 7's filterEvents.
        assert "allEvents.filter(" not in app_js


class TestNewsCss:
    def test_filter_bar_classes_present(self, styles_css):
        for cls in (".events-filter-card", ".events-filter-bar",
                    ".events-range-pills", ".events-range-pill",
                    ".events-filter-toggle"):
            assert cls in styles_css, f"missing class: {cls}"

    def test_status_chip_classes_present(self, styles_css):
        for cls in (".events-status-chips", ".events-status-chip",
                    ".chip-linked", ".chip-macro"):
            assert cls in styles_css, f"missing class: {cls}"
