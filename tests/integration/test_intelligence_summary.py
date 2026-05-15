"""Phase 9G integration tests — portfolio intelligence summary + alert priority.

Covers:

* ``build_intelligence_summary`` end-to-end against a real SQLite DB
  with real holdings, alerts, events, and factor + relationship
  EventLinks.
* Cross-portfolio isolation in the summary output (two portfolios,
  disjoint holdings, their summaries must be disjoint).
* Empty-state handling (no holdings, no alerts, no events).
* Alert severity-first ordering via the new
  ``/api/v1/alerts/active?priority_ordered=true`` option (tested
  against the real route handler, not mocked).
* Top factor / relationship ordering (max_relevance desc).
* Holdings under attention derivation from recent negative analyses.
* Freshness staleness computation.

The tests reuse the Phase 9D/9F temp-SQLite fixture pattern.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select


# ---------------------------------------------------------------------------
# Temp DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_phase9g")
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
    """Two portfolios (pA, pB) with disjoint holdings, alerts, events,
    factor + relationship links, and recent analysis notes."""
    from src.database.connection import get_db
    from src.database.models import (
        Alert,
        AnalysisNote,
        Digest,
        Event,
        EventLink,
        Holding,
        HoldingFactorSensitivity,
        HoldingRelationship,
        MacroFactorEvent,
        Portfolio,
        Security,
        TelegramDelivery,
        TelegramSession,
    )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    fresh_ts = (now_dt - timedelta(minutes=10)).isoformat()
    recent_ts = (now_dt - timedelta(hours=2)).isoformat()

    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote, HoldingFactorSensitivity,
            HoldingRelationship, Alert, Digest, Event, Holding, Security,
            TelegramDelivery, TelegramSession, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Alpha Portfolio", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
            Portfolio(id="pB", name="Beta Portfolio", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
            Portfolio(id="default", name="Main", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
            Portfolio(id="empty", name="Empty Portfolio", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, current_price=180.0, market_value=1800.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_msft_pA", ticker="MSFT", currency="USD", quantity=5,
                    weight_pct=20.0, current_price=400.0, market_value=2000.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                    weight_pct=15.0, current_price=120.0, market_value=6000.0,
                    portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        for t, sector in (("AAPL", "Information Technology"),
                          ("MSFT", "Information Technology"),
                          ("XOM", "Energy")):
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector=sector, geography="united states",
                themes="[]", created_at=now, updated_at=now,
            ))
        await session.commit()

    async with get_db() as session:
        # One event, global (news feed is global — gets fetched_at=now-10m for freshness)
        session.add(Event(
            id="evt_fed_rates",
            title="Federal Reserve raises interest rates by 50 bps",
            summary="FOMC vote.",
            event_type="rates",
            fetched_at=fresh_ts,
            created_at=now,
            dedup_hash="h_fed_rates",
        ))
        session.add(Event(
            id="evt_supplier",
            title="Taiwan Semiconductor announces capacity cut",
            summary="TSMC foundry capacity reduction.",
            event_type="supply_chain",
            fetched_at=recent_ts,
            created_at=now,
            dedup_hash="h_supplier",
        ))
        # Event that is NOT linked to pA or pB holdings (stale, 3 days old)
        session.add(Event(
            id="evt_stale",
            title="Old news",
            summary="Stale.",
            event_type="other",
            fetched_at=(now_dt - timedelta(days=3)).isoformat(),
            created_at=now,
            dedup_hash="h_stale",
        ))
        await session.commit()

    async with get_db() as session:
        # --- Factor links -----------------------------------------
        # pA: AAPL + MSFT both hit by interest_rate (strongest),
        # AAPL also hit by trade_policy (medium).
        # pB: XOM hit by oil_energy (medium).
        session.add(MacroFactorEvent(
            id=str(uuid.uuid4()),
            event_id="evt_fed_rates",
            factor="interest_rate",
            direction="up",
            magnitude="major",
            confidence=0.9,
            rationale=json.dumps(["matched: federal reserve"]),
            created_at=now,
        ))
        # Two rows for interest_rate (AAPL, MSFT) — so aggregator
        # collects both holdings.
        for hid, score in (("h_aapl_pA", 0.44), ("h_msft_pA", 0.38)):
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="evt_fed_rates",
                link_type="macro_factor",
                link_target=hid,
                impact_channel="interest_rate",
                channel="interest_rate",
                relevance_score=score,
                details_json=json.dumps({
                    "factor": {
                        "key": "interest_rate",
                        "direction": "up",
                        "magnitude": "major",
                        "rationale": ["federal reserve"],
                    },
                }),
                created_at=now,
            ))
        # Lower-relevance trade_policy on AAPL
        session.add(EventLink(
            id=str(uuid.uuid4()),
            event_id="evt_fed_rates",
            link_type="macro_factor",
            link_target="h_aapl_pA",
            impact_channel="trade_policy",
            channel="trade_policy",
            relevance_score=0.22,
            details_json=json.dumps({
                "factor": {"key": "trade_policy", "direction": "up"},
            }),
            created_at=now,
        ))
        # pB energy link
        session.add(EventLink(
            id=str(uuid.uuid4()),
            event_id="evt_fed_rates",
            link_type="macro_factor",
            link_target="h_xom_pB",
            impact_channel="oil_energy",
            channel="oil_energy",
            relevance_score=0.31,
            details_json=json.dumps({
                "factor": {"key": "oil_energy", "direction": "up"},
            }),
            created_at=now,
        ))
        # --- Relationship link: AAPL → TSMC supplier (pA only) ----
        session.add(EventLink(
            id=str(uuid.uuid4()),
            event_id="evt_supplier",
            link_type="relationship",
            link_target="h_aapl_pA",
            impact_channel="supplier",
            channel="supplier",
            relevance_score=0.48,
            details_json=json.dumps({
                "related_entity": {"name": "Taiwan Semiconductor", "ticker": "TSM"},
                "rationale": ["TSMC is AAPL's primary foundry"],
            }),
            created_at=now,
        ))
        await session.commit()

    async with get_db() as session:
        # --- Alerts ---------------------------------------------------
        # pA: one critical, one high, one info, one warning
        # pB: one high only
        session.add_all([
            Alert(
                id="a_pA_critical",
                portfolio_id="pA",
                alert_type="macro_factor",
                severity="critical",
                title="Rate shock on AAPL",
                body="50 bps rate shock.",
                related_holdings=json.dumps(["AAPL"]),
                related_events=json.dumps(["evt_fed_rates"]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(hours=4)).isoformat(),
            ),
            Alert(
                id="a_pA_high",
                portfolio_id="pA",
                alert_type="supply_chain",
                severity="high",
                title="Supply chain risk on AAPL",
                body="TSMC capacity cut.",
                related_holdings=json.dumps(["AAPL"]),
                related_events=json.dumps(["evt_supplier"]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(hours=3)).isoformat(),
            ),
            Alert(
                id="a_pA_warning",
                portfolio_id="pA",
                alert_type="drift",
                severity="warning",
                title="Sector concentration",
                body="IT overweight.",
                related_holdings=json.dumps([]),
                related_events=json.dumps([]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(hours=2)).isoformat(),
            ),
            # FRESH info alert — would bump older critical in chronological ordering
            Alert(
                id="a_pA_info_fresh",
                portfolio_id="pA",
                alert_type="info",
                severity="info",
                title="Daily brief ready",
                body="Digest ready.",
                related_holdings=json.dumps([]),
                related_events=json.dumps([]),
                acknowledged=0, delivered=0, agent_id="digest",
                created_at=(now_dt - timedelta(minutes=5)).isoformat(),
            ),
            Alert(
                id="a_pB_high",
                portfolio_id="pB",
                alert_type="oil_risk",
                severity="high",
                title="Oil pressure on XOM",
                body="OPEC cut raises oil.",
                related_holdings=json.dumps(["XOM"]),
                related_events=json.dumps([]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=now,
            ),
        ])
        # --- Analysis notes ------------------------------------------
        # pA: 2 negative (AAPL) + 1 positive (MSFT) — so neg > pos.
        #     AAPL important negative → holdings_under_attention={AAPL}
        session.add_all([
            AnalysisNote(
                id="note_aapl_neg1",
                event_id="evt_fed_rates",
                holding_id="h_aapl_pA",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "AAPL",
                    "impact_direction": "negative",
                    "impact_magnitude": "high",
                    "materiality": "important",
                    "short_term_outlook": "Pressure on AAPL margins",
                }),
                materiality="important",
                confidence="high",
                agent_id="analysis",
                created_at=(now_dt - timedelta(hours=1)).isoformat(),
            ),
            AnalysisNote(
                id="note_aapl_neg2",
                event_id="evt_supplier",
                holding_id="h_aapl_pA",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "AAPL",
                    "impact_direction": "negative",
                    "impact_magnitude": "medium",
                    "materiality": "important",
                    "short_term_outlook": "Supply constraint.",
                }),
                materiality="important",
                confidence="medium",
                agent_id="analysis",
                created_at=(now_dt - timedelta(hours=2)).isoformat(),
            ),
            AnalysisNote(
                id="note_msft_pos",
                event_id="evt_fed_rates",
                holding_id="h_msft_pA",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "MSFT",
                    "impact_direction": "positive",
                    "impact_magnitude": "low",
                    "materiality": "watch",
                    "short_term_outlook": "Marginal benefit.",
                }),
                materiality="watch",
                confidence="medium",
                agent_id="analysis",
                created_at=(now_dt - timedelta(hours=3)).isoformat(),
            ),
        ])
        # --- Digest for pA so has_digest = True ----------------------
        session.add(Digest(
            id="digest_pA",
            portfolio_id="pA",
            digest_type="daily",
            period_start=(now_dt - timedelta(days=1)).isoformat(),
            period_end=now,
            content=json.dumps({
                "headline": "Alpha daily — mildly negative",
                "portfolio_assessment": "Two AAPL negatives pull posture down.",
                "risk_flags": ["Interest rates trending up"],
                "holdings_requiring_attention": ["AAPL"],
                "key_developments": ["AAPL negative impact analysis"],
            }),
            event_count=2,
            alert_count=4,
            holding_count=2,
            delivered=0,
            created_at=now,
        ))
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# 1) Summary builder — full portfolio with alerts, factors, notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_populated_portfolio(seeded):
    from src.database.connection import get_db
    from src.intelligence.summary import build_intelligence_summary

    async with get_db() as session:
        summary = await build_intelligence_summary(session, portfolio_id="pA")

    assert summary.portfolio_id == "pA"
    assert summary.portfolio_name == "Alpha Portfolio"
    assert summary.holding_count == 2

    # Posture: 1 critical alert → strong_negative (overrides everything)
    assert summary.posture == "strong_negative"
    assert "critical" in summary.posture_reason.lower()

    # Alert bucketing
    assert summary.alerts["critical"] == 1
    assert summary.alerts["high"] == 1
    assert summary.alerts["warning"] == 1
    assert summary.alerts["info"] == 1
    assert summary.alerts["total"] == 4

    # Top factors — ordered by max_relevance desc
    assert len(summary.top_factors) >= 2
    factor_keys = [f["factor"] for f in summary.top_factors]
    assert "interest_rate" in factor_keys
    assert "trade_policy" in factor_keys
    # interest_rate (max_relevance 0.44) must sort before trade_policy (0.22)
    assert factor_keys.index("interest_rate") < factor_keys.index("trade_policy")
    # interest_rate should carry both AAPL and MSFT holdings
    interest = next(f for f in summary.top_factors if f["factor"] == "interest_rate")
    assert "AAPL" in interest["holdings"]
    assert "MSFT" in interest["holdings"]
    assert interest["direction"] == "up"

    # Top relationships — the TSMC supplier link for AAPL
    assert len(summary.top_relationships) == 1
    rel = summary.top_relationships[0]
    assert rel["ticker"] == "AAPL"
    assert rel["relationship_type"] == "supplier"
    assert rel["related_entity"] == "Taiwan Semiconductor"

    # Holdings under attention — AAPL (2 important negatives), MSFT is positive/watch
    assert summary.holdings_under_attention == ["AAPL"]

    # Recent events in 24h — both evt_fed_rates and evt_supplier link to
    # pA holdings, so count should be 2 (evt_stale is global and doesn't link).
    assert summary.recent_events_count_24h == 2

    # Freshness — last global event is evt_fed_rates at now-10m, so fresh
    assert summary.freshness["is_fresh"] is True
    assert summary.freshness["stale_minutes"] is not None
    assert summary.freshness["stale_minutes"] < 60

    # Intelligence health counters
    h = summary.intelligence_health
    assert h["factor_links"] >= 3   # 2 interest_rate + 1 trade_policy (pA)
    assert h["relationship_links"] == 1
    assert h["analysis_notes_7d"] == 3
    assert h["has_digest"] is True
    assert h["global_factor_classifications"] >= 1


# ---------------------------------------------------------------------------
# 2) Cross-portfolio isolation — pB summary has no pA data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_pB_isolation(seeded):
    from src.database.connection import get_db
    from src.intelligence.summary import build_intelligence_summary

    async with get_db() as session:
        summary = await build_intelligence_summary(session, portfolio_id="pB")

    assert summary.portfolio_id == "pB"
    assert summary.portfolio_name == "Beta Portfolio"
    assert summary.holding_count == 1

    # pB has only the oil_energy factor + 1 high alert — NO interest_rate, NO trade_policy
    factor_keys = [f["factor"] for f in summary.top_factors]
    assert "interest_rate" not in factor_keys
    assert "trade_policy" not in factor_keys
    assert "oil_energy" in factor_keys

    # pB factor touchpoints must list only XOM
    oil_touch = next(f for f in summary.top_factors if f["factor"] == "oil_energy")
    assert oil_touch["holdings"] == ["XOM"]
    assert "AAPL" not in oil_touch["holdings"]
    assert "MSFT" not in oil_touch["holdings"]

    # Alerts are pB-only
    assert summary.alerts["high"] == 1
    assert summary.alerts["critical"] == 0
    assert summary.alerts["total"] == 1

    # No analyses on pB → no attention
    assert summary.holdings_under_attention == []

    # pB has no TSMC relationship link
    assert summary.top_relationships == []

    # Posture: 1 high alert, no critical, no attention → mildly_negative
    assert summary.posture == "mildly_negative"


# ---------------------------------------------------------------------------
# 3) Empty portfolio — insufficient_data posture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_empty_portfolio(seeded):
    from src.database.connection import get_db
    from src.intelligence.summary import build_intelligence_summary

    async with get_db() as session:
        summary = await build_intelligence_summary(session, portfolio_id="empty")

    assert summary.portfolio_id == "empty"
    assert summary.holding_count == 0
    assert summary.posture == "insufficient_data"
    assert "No active holdings" in summary.posture_reason
    assert summary.top_factors == []
    assert summary.top_relationships == []
    assert summary.alerts["total"] == 0
    assert summary.holdings_under_attention == []


# ---------------------------------------------------------------------------
# 4) Unknown portfolio — never raises, returns insufficient_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_unknown_portfolio_does_not_raise(seeded):
    from src.database.connection import get_db
    from src.intelligence.summary import build_intelligence_summary

    async with get_db() as session:
        summary = await build_intelligence_summary(
            session, portfolio_id="does_not_exist",
        )
    assert summary.portfolio_id == "does_not_exist"
    assert summary.portfolio_name is None
    assert summary.holding_count == 0
    assert summary.posture == "insufficient_data"


# ---------------------------------------------------------------------------
# 5) to_dict shape is stable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_to_dict_shape(seeded):
    from src.database.connection import get_db
    from src.intelligence.summary import build_intelligence_summary

    async with get_db() as session:
        summary = await build_intelligence_summary(session, portfolio_id="pA")

    d = summary.to_dict()
    required = {
        "portfolio_id", "portfolio_name", "holding_count",
        "posture", "posture_reason",
        "top_factors", "top_relationships",
        "alerts", "holdings_under_attention", "recent_events_count_24h",
        "freshness", "intelligence_health", "computed_at",
    }
    assert required.issubset(d.keys())
    assert isinstance(d["top_factors"], list)
    assert isinstance(d["top_relationships"], list)
    assert isinstance(d["alerts"], dict)
    assert isinstance(d["freshness"], dict)
    assert isinstance(d["intelligence_health"], dict)


# ---------------------------------------------------------------------------
# 6) Alert priority ordering — severity-first bumps older critical ahead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_priority_ordering(seeded):
    """With 4 pA alerts in chronological order:
       critical (4h ago) < high (3h) < warning (2h) < info (5m),
    the default (chronological) order returns info first.
    With priority_ordered=true, critical must come FIRST despite being
    the oldest.
    """
    from src.api.routes.alerts import active_alerts
    from src.database.connection import get_session_factory

    factory = get_session_factory()

    # Default ordering (chronological, newest first) — info wins
    async with factory() as session:
        chronological = await active_alerts(
            portfolio_id="pA",
            limit=50,
            offset=0,
            priority_ordered=False,
            session=session,
        )
    assert chronological[0].id == "a_pA_info_fresh"
    assert chronological[-1].id == "a_pA_critical"  # oldest last

    # Priority ordering — critical wins even though it's the oldest
    async with factory() as session:
        priority = await active_alerts(
            portfolio_id="pA",
            limit=50,
            offset=0,
            priority_ordered=True,
            session=session,
        )
    severities_in_priority_order = [a.severity for a in priority]
    assert severities_in_priority_order == ["critical", "high", "warning", "info"]
    assert priority[0].id == "a_pA_critical"


# ---------------------------------------------------------------------------
# 7) Defensive — wrapping build_intelligence_summary always returns a summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_never_raises_on_bad_session():
    """Even if the session blows up mid-build, the wrapper must return
    a valid IntelligenceSummary with an ``insufficient_data`` posture."""
    from src.intelligence.summary import build_intelligence_summary

    class _BrokenSession:
        async def get(self, *a, **k):  # pragma: no cover — raises before get
            raise RuntimeError("boom")

        async def execute(self, *a, **k):
            raise RuntimeError("boom")

    s = await build_intelligence_summary(_BrokenSession(), portfolio_id="pA")
    assert s.portfolio_id == "pA"
    assert s.posture == "insufficient_data"
    assert "summary build failed" in s.posture_reason
