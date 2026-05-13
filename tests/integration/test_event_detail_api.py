"""Integration tests for the Phase 9B event detail API.

These tests exercise the event detail endpoint directly (without
starting a TestClient) so the same temp-DB + runtime-collection
fixture pattern used by Phase 9A works unchanged.  Calling the
route function directly with a session dependency is the
standard FastAPI pattern for this kind of focused test.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Temp DB fixture — same snapshot/restore pattern as Phase 9A
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_phase9b")
    db_path = db_dir / "axion_test.db"
    os.environ["KLEITOS_DB_PATH"] = str(db_path)
    get_settings.cache_clear()  # type: ignore[attr-defined]

    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    yield db_path

    if prior_env_db is None:
        os.environ.pop("KLEITOS_DB_PATH", None)
    else:
        os.environ["KLEITOS_DB_PATH"] = prior_env_db
    if prior_env_data is None:
        os.environ.pop("KLEITOS_DATA_DIR", None)
    else:
        os.environ["KLEITOS_DATA_DIR"] = prior_env_data
    get_settings.cache_clear()  # type: ignore[attr-defined]
    restored = get_settings()
    restored.api.auth_enabled = prior_auth_enabled
    connection._engine = None
    connection._session_factory = None


@pytest_asyncio.fixture(scope="module")
async def _migrated_db(_tmp_db):
    from src.database.migrations import run_migrations
    await run_migrations()
    yield _tmp_db


@pytest_asyncio.fixture
async def seeded(_migrated_db):
    """Fresh DB per test: two portfolios, five holdings, securities."""
    from src.database.connection import get_db
    from src.database.models import (
        Alert,
        AnalysisNote,
        Digest,
        Event,
        EventLink,
        Holding,
        HoldingFactorSensitivity,
        MacroFactorEvent,
        Portfolio,
        Security,
    )
    from sqlalchemy import delete

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote, HoldingFactorSensitivity,
            Alert, Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Portfolio A", base_currency="USD", is_default=1,
                      created_at=now, updated_at=now),
            Portfolio(id="pB", name="Portfolio B", base_currency="USD", is_default=0,
                      created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_xom_pA", ticker="XOM", currency="USD", quantity=10,
                    weight_pct=15.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_msft_pB", ticker="MSFT", currency="USD", quantity=10,
                    weight_pct=30.0, portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        sectors = {
            "AAPL": "Information Technology",
            "XOM": "Energy",
            "MSFT": "Information Technology",
        }
        for t, sec in sectors.items():
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector=sec, geography="united states", themes="[]",
                created_at=now, updated_at=now,
            ))
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_link_event(raw: dict) -> str:
    """Persist an event, run the real link pipeline, return event_id."""
    from src.agents.collection import CollectionAgent
    from src.database.connection import get_db
    from src.database.models import Event

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        session.add(Event(
            id=event_id,
            title=raw["title"],
            summary=raw.get("summary", ""),
            fetched_at=now,
            created_at=now,
            dedup_hash=str(uuid.uuid4()),
        ))
        await session.commit()
    agent = CollectionAgent()
    await agent._link_event_to_holdings(event_id, raw)
    return event_id


async def _call_get_event(event_id: str):
    """Call the real FastAPI route function with a real DB session."""
    from src.api.routes.events import get_event
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        return await get_event(event_id, session=session)


async def _call_list_events():
    from src.api.routes.events import list_events
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Pass explicit defaults — the route signature uses Query()
        # sentinels which only resolve during real HTTP dispatch.
        return await list_events(
            ticker=None, event_type=None, materiality=None,
            date_from=None, date_to=None, limit=50, offset=0,
            session=session,
        )


# ---------------------------------------------------------------------------
# Event detail tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEventDetailAPI:
    async def test_factor_event_returns_full_detail_shape(self, seeded):
        """A Fed rate-hike event returns factor tags, affected holdings
        (across portfolios), and normalized chains for every link."""
        event_id = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": (
                "The FOMC voted to raise the federal funds rate by 50 basis "
                "points citing persistent inflation."
            ),
        })

        detail = await _call_get_event(event_id)

        # Core fields
        assert detail.id == event_id
        assert "Federal Reserve" in detail.title

        # Factor tags — interest_rate must be present
        tag_keys = {t.key for t in detail.factor_tags}
        assert "interest_rate" in tag_keys
        ir_tag = next(t for t in detail.factor_tags if t.key == "interest_rate")
        assert ir_tag.direction == "up"
        assert ir_tag.label == "Interest Rates"
        assert 0.0 <= ir_tag.confidence <= 1.0

        # linked_ticker_count is populated
        assert detail.linked_ticker_count >= 2  # AAPL + MSFT at minimum

        # Affected holdings cross portfolios (Phase 9A emits default-prior
        # factor links; these will show up here with honest 0.25–0.5 range)
        pids = {h.portfolio_id for h in detail.affected_holdings}
        assert "pA" in pids
        assert "pB" in pids
        tickers_in_pA = {
            h.ticker for h in detail.affected_holdings
            if h.portfolio_id == "pA"
        }
        tickers_in_pB = {
            h.ticker for h in detail.affected_holdings
            if h.portfolio_id == "pB"
        }
        assert "AAPL" in tickers_in_pA
        assert "MSFT" in tickers_in_pB
        # MSFT is in pB, not pA — no cross-portfolio collapse
        assert "MSFT" not in tickers_in_pA

        # Every affected holding carries its own portfolio_id + weight
        for h in detail.affected_holdings:
            assert h.portfolio_id in ("pA", "pB")
            assert h.ticker
            assert isinstance(h.link_types, list) and h.link_types

    async def test_factor_link_has_normalized_chain(self, seeded):
        """Every macro_factor link must come back with a deterministic
        factor chain payload."""
        event_id = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })
        detail = await _call_get_event(event_id)

        factor_links = [l for l in detail.links if l.link_type == "macro_factor"]
        assert factor_links, "expected at least one macro_factor link"

        for link in factor_links:
            assert link.chain is not None, "chain must be normalized for macro_factor"
            chain = link.chain
            assert chain["origin"] == "deterministic_factor"
            assert chain["factor_key"] == "interest_rate"
            assert chain["factor_label"] == "Interest Rates"
            assert chain["factor_direction"] == "up"
            assert chain["holding_ticker"] in ("AAPL", "MSFT", "XOM")
            # Rationale list is present and non-empty
            assert isinstance(chain["rationale"], list)
            assert chain["rationale"]
            # Summary is human-readable, not JSON
            assert chain["summary"]
            assert "{" not in chain["summary"]  # not a raw JSON dump
            # Effect confidence mirrors the honest p_holding
            assert chain["effect_confidence"] is not None
            assert 0.05 <= chain["effect_confidence"] <= 0.85

    async def test_direct_ticker_link_has_synthesized_chain(self, seeded):
        """Ticker_match links have no stored chain but must still
        receive a normalized chain at render time."""
        event_id = await _run_link_event({
            "title": "AAPL reports record earnings",
            "summary": "Apple beat analyst estimates.",
        })
        detail = await _call_get_event(event_id)

        direct = [l for l in detail.links if l.link_type == "ticker_match"]
        assert direct, "expected ticker_match link for AAPL"
        for link in direct:
            assert link.chain is not None
            chain = link.chain
            assert chain["origin"] == "direct_match"
            assert chain["factor_key"] is None
            assert chain["channel_label"] == "Ticker match"
            assert chain["holding_ticker"] == "AAPL"
            assert chain["rationale"]

    async def test_event_with_no_links_has_empty_lists(self, seeded):
        """An event whose text triggers nothing returns empty lists
        (not null) so the UI doesn't need null checks."""
        event_id = await _run_link_event({
            "title": "Apple orchard destroyed by frost in upstate New York",
            "summary": "Local growers report severe crop losses.",
        })
        detail = await _call_get_event(event_id)

        assert detail.links == []
        assert detail.affected_holdings == []
        assert detail.factor_tags == []
        assert detail.related_analyses == []
        assert detail.related_alerts == []
        assert detail.linked_ticker_count == 0

    async def test_related_analyses_surface(self, seeded):
        """An analysis note referencing an event must appear in the detail."""
        from src.database.connection import get_db
        from src.database.models import AnalysisNote

        event_id = await _run_link_event({
            "title": "AAPL reports record earnings",
            "summary": "Apple beat analyst estimates.",
        })

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(AnalysisNote(
                id=str(uuid.uuid4()),
                event_id=event_id,
                holding_id="h_aapl_pA",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "AAPL",
                    "impact_direction": "positive",
                    "impact_magnitude": "medium",
                    "short_term_outlook": "Strong iPhone demand supports the thesis.",
                }),
                materiality="important",
                confidence="0.7",
                agent_id="analysis",
                created_at=now,
            ))
            await session.commit()

        detail = await _call_get_event(event_id)
        assert len(detail.related_analyses) == 1
        rn = detail.related_analyses[0]
        assert rn.ticker == "AAPL"
        assert rn.materiality == "important"
        assert "iPhone" in rn.summary or rn.summary  # summary extracted

    async def test_related_alerts_surface_by_related_events_json(self, seeded):
        """An Alert whose ``related_events`` JSON array contains the
        event id must appear in the detail payload with its own
        portfolio_id preserved."""
        from src.database.connection import get_db
        from src.database.models import Alert

        event_id = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            # Two alerts — one references the event, one doesn't.
            session.add(Alert(
                id="a1",
                portfolio_id="pA",
                alert_type="macro_risk",
                severity="warning",
                title="Rates rising — tech exposure",
                body="Watch tech weights.",
                related_events=json.dumps([event_id]),
                related_holdings=json.dumps(["h_aapl_pA"]),
                agent_id="risk",
                created_at=now,
            ))
            session.add(Alert(
                id="a2",
                portfolio_id="pA",
                alert_type="concentration",
                severity="info",
                title="Unrelated alert",
                body="No link.",
                related_events=json.dumps(["some-other-uuid"]),
                related_holdings=json.dumps([]),
                agent_id="risk",
                created_at=now,
            ))
            await session.commit()

        detail = await _call_get_event(event_id)
        assert len(detail.related_alerts) == 1
        alert = detail.related_alerts[0]
        assert alert.id == "a1"
        assert alert.portfolio_id == "pA"
        assert alert.severity == "warning"
        assert alert.acknowledged is False

    async def test_portfolio_safety_affected_holdings_dont_collapse(self, seeded):
        """Multi-portfolio holdings keep their portfolio_id — no
        cross-portfolio collapse, no ghost holdings."""
        event_id = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })
        detail = await _call_get_event(event_id)

        # Every affected holding row carries its real portfolio_id
        for h in detail.affected_holdings:
            assert h.portfolio_id in ("pA", "pB")
        # AAPL appears ONLY in pA and MSFT ONLY in pB
        by_ticker = {}
        for h in detail.affected_holdings:
            by_ticker.setdefault(h.ticker, set()).add(h.portfolio_id)
        assert by_ticker.get("AAPL", set()) == {"pA"}
        assert by_ticker.get("MSFT", set()) == {"pB"}

    async def test_event_not_found_raises(self, seeded):
        """Unknown event ids return 404."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await _call_get_event("nonexistent-event-id")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Event list tests (Phase 9B factor_tags on rows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEventListAPI:
    async def test_list_surfaces_factor_tags(self, seeded):
        """The event list response must carry factor_tags per row."""
        await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })
        rows = await _call_list_events()
        assert rows, "expected at least one event row"

        # Find the Fed event
        fed_rows = [r for r in rows if "Federal Reserve" in r.title]
        assert fed_rows
        fed = fed_rows[0]

        tag_keys = {t.key for t in fed.factor_tags}
        assert "interest_rate" in tag_keys, f"missing interest_rate tag: {tag_keys}"
        assert fed.linked_ticker_count >= 2

    async def test_list_shows_empty_tags_for_non_factor_event(self, seeded):
        """Events that classify no factors return an empty tag list,
        not null."""
        await _run_link_event({
            "title": "Apple orchard destroyed by frost",
            "summary": "Local fruit growers report severe losses.",
        })
        rows = await _call_list_events()
        orchard = next((r for r in rows if "orchard" in r.title), None)
        assert orchard is not None
        assert orchard.factor_tags == []


# ---------------------------------------------------------------------------
# Regression: Phase 9A invariants still hold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPhase9ARegressions:
    async def test_direct_matching_still_emits_ticker_links(self, seeded):
        event_id = await _run_link_event({
            "title": "AAPL reports record earnings",
            "summary": "Apple beat estimates.",
        })
        detail = await _call_get_event(event_id)
        assert any(l.link_type == "ticker_match" for l in detail.links)

    async def test_factor_links_still_emit_under_default_priors(self, seeded):
        event_id = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })
        detail = await _call_get_event(event_id)
        factor_links = [l for l in detail.links if l.link_type == "macro_factor"]
        assert factor_links
        for link in factor_links:
            assert 0.25 <= (link.relevance_score or 0) < 0.5
