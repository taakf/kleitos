"""Phase 9N integration tests — action surfacing in real API payloads.

Drives the real intelligence summary, digest fallback, event detail
route, and alerts route against a seeded portfolio and asserts the
Phase 9N additions actually land in the responses.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete


# ---------------------------------------------------------------------------
# Temp DB fixture (mirror of Phase 9D/F/G/H/K/M pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")

    db_dir = tmp_path_factory.mktemp("axion_phase9n")
    db_path = db_dir / "axion_test.db"
    os.environ["KLEITOS_DB_PATH"] = str(db_path)
    os.environ["KLEITOS_DATA_DIR"] = str(db_dir)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    get_settings().api.auth_enabled = False
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
    get_settings().api.auth_enabled = False
    connection._engine = None
    connection._session_factory = None


@pytest_asyncio.fixture(scope="module")
async def _migrated_db(_tmp_db):
    from src.database.migrations import run_migrations
    await run_migrations()
    yield _tmp_db


@pytest_asyncio.fixture
async def stressed_portfolio(_migrated_db):
    """Seed a portfolio with enough signal that the action builder
    produces a rich set of recommendations: a critical alert, an
    attention ticker, a repeated-negative ticker, and a rate factor
    across two holdings."""
    from src.database.connection import get_db
    from src.database.models import (
        Alert, AnalysisNote, AuditLog, Digest, Event, EventLink,
        Holding, HoldingFactorSensitivity, HoldingRelationship,
        MacroFactorEvent, Portfolio, Security,
    )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship,
            AuditLog, Alert, Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Alpha", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=50.0, current_price=180.0, market_value=1800.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_msft", ticker="MSFT", currency="USD", quantity=5,
                    weight_pct=50.0, current_price=400.0, market_value=2000.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
        ])
        for t in ("AAPL", "MSFT", "TSM"):
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector="Information Technology", geography="united states",
                themes="[]", created_at=now, updated_at=now,
            ))
        await session.commit()

    # Seed the event + factor link + alert
    async with get_db() as session:
        session.add(Event(
            id="evt_9n_fed",
            title="Federal Reserve raises rates by 50 bps",
            summary="FOMC vote to raise rates.",
            event_type="rates",
            fetched_at=now, created_at=now,
            dedup_hash=f"9n_fed_{uuid.uuid4().hex[:8]}",
        ))
        session.add(MacroFactorEvent(
            id=str(uuid.uuid4()),
            event_id="evt_9n_fed",
            factor="interest_rate",
            direction="up",
            magnitude="major",
            confidence=0.9,
            rationale=json.dumps(["matched: federal reserve"]),
            created_at=now,
        ))
        for hid, score in (("h_aapl", 0.48), ("h_msft", 0.42)):
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="evt_9n_fed",
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
        # One critical alert on AAPL
        session.add(Alert(
            id="alert_9n_critical",
            portfolio_id="pA",
            alert_type="macro_factor",
            severity="critical",
            title="Rate shock on AAPL",
            body="50 bps hike increases duration risk on AAPL.",
            related_holdings=json.dumps(["h_aapl"]),
            related_events=json.dumps(["evt_9n_fed"]),
            acknowledged=0, delivered=0, agent_id="risk",
            created_at=now,
        ))
        # One high-severity drift alert
        session.add(Alert(
            id="alert_9n_high",
            portfolio_id="pA",
            alert_type="drift",
            severity="high",
            title="Sector concentration",
            body="Portfolio overweight IT.",
            related_holdings=json.dumps([]),
            related_events=json.dumps([]),
            acknowledged=0, delivered=0, agent_id="risk",
            created_at=now,
        ))
        # Two negative important notes on AAPL → triggers attention
        for i, offset in enumerate((1, 3)):
            session.add(AnalysisNote(
                id=f"note_aapl_neg_{i}",
                event_id="evt_9n_fed",
                holding_id="h_aapl",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "AAPL",
                    "impact_direction": "negative",
                    "impact_magnitude": "high",
                    "materiality": "important",
                    "short_term_outlook": "Pressure on AAPL valuation.",
                }),
                materiality="important",
                confidence="high",
                agent_id="analysis",
                created_at=(now_dt - timedelta(hours=offset)).isoformat(),
            ))
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# 1) Intelligence summary carries recommended_actions
# ---------------------------------------------------------------------------


class TestIntelligenceSummaryActions:
    @pytest.mark.asyncio
    async def test_summary_exposes_recommended_actions(self, stressed_portfolio):
        from src.intelligence.summary import build_intelligence_summary
        from src.database.connection import get_db

        async with get_db() as session:
            summary = await build_intelligence_summary(session, portfolio_id="pA")

        assert summary.recommended_actions, (
            "stressed portfolio should produce at least one recommended action"
        )
        keys = {a["key"] for a in summary.recommended_actions}
        # Every grounded rule family we set up should fire
        assert "alerts.critical_present" in keys
        assert "holdings.under_attention" in keys
        assert "factors.strong_rate_pressure" in keys
        # And repeated-negative triggers because of the two AAPL notes
        assert "holdings.repeated_negative" in keys

    @pytest.mark.asyncio
    async def test_summary_dict_is_json_safe_with_actions(self, stressed_portfolio):
        from src.intelligence.summary import build_intelligence_summary
        from src.database.connection import get_db

        async with get_db() as session:
            summary = await build_intelligence_summary(session, portfolio_id="pA")
        d = summary.to_dict()
        assert "recommended_actions" in d
        # Every action round-trips through json
        payload = json.dumps(d["recommended_actions"])
        assert "alerts.critical_present" in payload

    @pytest.mark.asyncio
    async def test_empty_portfolio_produces_empty_actions(self, stressed_portfolio):
        """A portfolio with no holdings / alerts / notes emits zero
        actions — no filler, no noise."""
        from src.database.connection import get_db
        from src.database.models import Portfolio
        from src.intelligence.summary import build_intelligence_summary

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(Portfolio(
                id="empty_pN", name="Empty", base_currency="USD",
                is_default=0, created_at=now, updated_at=now,
            ))
            await session.commit()

        async with get_db() as session:
            summary = await build_intelligence_summary(session, portfolio_id="empty_pN")
        assert summary.recommended_actions == []


# ---------------------------------------------------------------------------
# 2) Digest fallback populates action_items
# ---------------------------------------------------------------------------


class TestDigestActions:
    def test_digest_fallback_populates_action_items(self):
        from src.llm.grounded import GroundedDigestContext, render_deterministic_digest

        ctx = GroundedDigestContext(
            period="daily", portfolio_id="pA",
            notes=[
                {"ticker": "AAPL", "impact_direction": "negative", "materiality": "important"},
                {"ticker": "AAPL", "impact_direction": "negative", "materiality": "important"},
                {"ticker": "MSFT", "impact_direction": "negative", "materiality": "high"},
            ],
            factor_touchpoints=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "factor_direction": "up",
                "affected_tickers": ["AAPL", "MSFT"],
            }],
        )
        d = render_deterministic_digest(ctx)
        assert d["action_items"], "action_items should not be empty with this context"
        # Existing risk flags are preserved at the head of the list
        assert d["risk_flags"]
        # The shared builder's rule families contribute titles
        joined = " ".join(d["action_items"]).lower()
        assert "holdings" in joined or "attention" in joined

    def test_digest_fallback_silent_when_no_signal(self):
        from src.llm.grounded import GroundedDigestContext, render_deterministic_digest

        ctx = GroundedDigestContext(
            period="daily", portfolio_id="pA",
            notes=[],
            factor_touchpoints=[],
        )
        d = render_deterministic_digest(ctx)
        # No negative signals, no factor touchpoints → no risk flags,
        # and no action items.
        assert d["action_items"] == []
        assert d["risk_flags"] == []


# ---------------------------------------------------------------------------
# 3) Event detail API carries why_it_matters + suggested_action
# ---------------------------------------------------------------------------


class TestEventDetailExplanation:
    @pytest.mark.asyncio
    async def test_rate_event_detail_has_grounded_explanation(self, stressed_portfolio):
        from src.api.routes.events import get_event
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            detail = await get_event("evt_9n_fed", session=session)

        # Phase 9N fields must be present
        assert detail.why_it_matters is not None
        assert detail.suggested_action is not None
        assert len(detail.explanation_grounded_in) >= 1

        # why_it_matters should name the Interest Rates factor
        assert "Interest Rates" in detail.why_it_matters or "interest_rate" in detail.why_it_matters.lower()
        # Suggested action should name the affected holdings
        assert "AAPL" in detail.suggested_action or "MSFT" in detail.suggested_action
        # Grounding refs must name the factor key at minimum
        joined = " ".join(detail.explanation_grounded_in)
        assert "factor:interest_rate" in joined


# ---------------------------------------------------------------------------
# 4) Alerts API carries suggested_action per row
# ---------------------------------------------------------------------------


class TestAlertsSuggestedAction:
    @pytest.mark.asyncio
    async def test_active_alerts_include_suggested_action(self, stressed_portfolio):
        from src.api.routes.alerts import active_alerts
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await active_alerts(
                portfolio_id="pA",
                limit=50,
                offset=0,
                priority_ordered=True,
                session=session,
            )
        assert len(rows) >= 2
        # Critical alert has a suggested action
        critical = next(r for r in rows if r.severity == "critical")
        assert critical.suggested_action is not None
        # Drift alert has a concentration/balance next step
        high = next(r for r in rows if r.severity == "high" and r.alert_type == "drift")
        assert high.suggested_action is not None
        assert "concentration" in high.suggested_action.lower() or "balance" in high.suggested_action.lower()
