"""Phase 9Q integration tests — API payloads carry structured
navigation targets.

Drives the real route handlers against a seeded DB and asserts:

  * ``GET /api/v1/notifications`` returns a structured
    ``action_target`` dict on every item with a known surface
  * ``GET /api/v1/events/{id}?portfolio_id=...`` returns a parallel
    ``explanation_grounded_in_targets`` list with navigable refs
    made clickable (nav_target != null) and unknown refs marked
    non-navigable (nav_target == null)
  * ``GET /api/v1/alerts/active`` returns an ``evidence_targets``
    list parallel to the alert's related_events + related_holdings
  * ``GET /api/v1/audit/recent`` returns a ``nav_target`` on every
    operator entry, routed by entity_type
  * ``GET /api/v1/intelligence/summary`` attaches ``nav_target`` to
    every recommended_action dict (null when the action family has
    no mapped target)

All routes remain portfolio-safe — the target's ``portfolio_id``
must match the caller's active portfolio.
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
# Temp DB fixtures — same pattern as Phase 9P integration tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")

    db_dir = tmp_path_factory.mktemp("axion_phase9q")
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
async def seeded(_migrated_db):
    from src.database.connection import get_db
    from src.database.models import (
        Alert,
        AnalysisNote,
        AuditLog,
        Digest,
        Event,
        EventLink,
        Holding,
        HoldingFactorSensitivity,
        HoldingRelationship,
        MacroFactorEvent,
        NotificationRead,
        Portfolio,
        Security,
    )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship,
            AuditLog, Alert, Digest, Event, Holding, Security,
            NotificationRead, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

    async with get_db() as session:
        session.add_all([
            Portfolio(id="pA", name="Alpha", base_currency="USD", is_default=1,
                      created_at=now, updated_at=now),
            Portfolio(id="pB", name="Beta", base_currency="USD", is_default=0,
                      created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=60, current_price=180, market_value=1800,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_msft_pA", ticker="MSFT", currency="USD", quantity=5,
                    weight_pct=40, current_price=400, market_value=2000,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
        ])
        # Event + factor classification for the grounded explanation
        session.add(Event(
            id="evt_9q_fed",
            title="Federal Reserve raises rates by 50 bps",
            summary="FOMC votes to raise rates.",
            event_type="rates",
            fetched_at=now,
            created_at=now,
            dedup_hash="9q_fed",
        ))
        await session.commit()

    async with get_db() as session:
        session.add(MacroFactorEvent(
            id=str(uuid.uuid4()),
            event_id="evt_9q_fed",
            factor="interest_rate",
            direction="up",
            magnitude="major",
            confidence=0.9,
            rationale=json.dumps(["50 bps"]),
            created_at=now,
        ))
        for hid, score in (("h_aapl_pA", 0.48), ("h_msft_pA", 0.42)):
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="evt_9q_fed",
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
                        "rationale": ["fed 50bps"],
                    },
                    "expected_effect": {"direction": "negative", "confidence": score},
                }),
                created_at=now,
            ))
        # Alerts
        session.add_all([
            Alert(
                id="alert_9q_crit", portfolio_id="pA",
                alert_type="macro_factor", severity="critical",
                title="Rate shock on AAPL", body="Fed 50 bps",
                related_holdings=json.dumps(["h_aapl_pA"]),
                related_events=json.dumps(["evt_9q_fed"]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(hours=1)).isoformat(),
            ),
        ])
        # Digest
        session.add(Digest(
            id="digest_9q", portfolio_id="pA", digest_type="daily",
            period_start=(now_dt - timedelta(days=1)).isoformat(),
            period_end=now,
            content=json.dumps({
                "headline": "Mildly negative on rate shock",
                "portfolio_assessment": "AAPL rate pressure",
                "risk_flags": ["rates"],
                "key_developments": [],
                "action_items": [],
            }),
            event_count=3, alert_count=1, holding_count=2, delivered=0,
            created_at=(now_dt - timedelta(hours=2)).isoformat(),
        ))
        # Operator factor override audit row (resolves to pA via join)
        session.add(HoldingFactorSensitivity(
            id="ovr_9q", holding_id="h_aapl_pA", factor="interest_rate",
            sensitivity=-0.9, source="manual",
            created_at=now, updated_at=now,
        ))
        await session.commit()

    async with get_db() as session:
        session.add(AuditLog(
            id="audit_factor_9q",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_9q", action="update",
            old_value=json.dumps({
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.6, "holding_id": "h_aapl_pA",
            }),
            new_value=json.dumps({
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.9, "holding_id": "h_aapl_pA",
            }),
            agent_id="operator", user_id="operator",
            reason="9q test",
            created_at=(now_dt - timedelta(minutes=15)).isoformat(),
        ))
        session.add(AuditLog(
            id="audit_backfill_9q",
            entity_type="intelligence_backfill",
            entity_id="window_7d", action="backfill",
            old_value=None,
            new_value=json.dumps({
                "window_days": 7, "events_scanned": 10, "events_replayed": 10,
                "links_added": 2, "mfe_added": 0, "events_failed": 0,
            }),
            agent_id="operator_backfill", user_id="operator",
            reason="9q test",
            created_at=(now_dt - timedelta(minutes=5)).isoformat(),
        ))
        await session.commit()

    yield


# ---------------------------------------------------------------------------
# 1) /api/v1/notifications — structured action_target
# ---------------------------------------------------------------------------


class TestNotificationsTarget:
    @pytest.mark.asyncio
    async def test_alert_item_has_structured_alert_target(self, seeded):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        alert_items = [i for i in resp.items if i.source_type == "alert"]
        assert alert_items, "expected at least one alert item in inbox"
        crit = next(i for i in alert_items if i.source_id == "alert_9q_crit")
        # Phase 9Q — structured target dict (not legacy string)
        assert isinstance(crit.action_target, dict)
        assert crit.action_target["surface"] == "alerts"
        assert crit.action_target["portfolio_id"] == "pA"
        assert crit.action_target["entity_type"] == "alert"
        assert crit.action_target["entity_id"] == "alert_9q_crit"
        assert crit.action_target["highlight_key"] == "alert:alert_9q_crit"

    @pytest.mark.asyncio
    async def test_digest_item_has_digest_subtab_target(self, seeded):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        digest = next(i for i in resp.items if i.source_type == "digest")
        assert isinstance(digest.action_target, dict)
        assert digest.action_target["surface"] == "digest"
        assert digest.action_target["subtab"] == "digest"
        assert digest.action_target["portfolio_id"] == "pA"

    @pytest.mark.asyncio
    async def test_operator_item_routes_by_entity_type(self, seeded):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        op_items = [i for i in resp.items if i.source_type == "operator"]
        assert op_items
        # Find the factor override item (entity_type in metadata)
        factor = next(
            (i for i in op_items
             if i.metadata.get("entity_type") == "holding_factor_sensitivity"),
            None,
        )
        assert factor is not None
        assert isinstance(factor.action_target, dict)
        assert factor.action_target["surface"] == "operator"
        assert factor.action_target["subtab"] == "factors"
        # The factor filter is extracted from evidence_refs
        assert factor.action_target["filter"] == "interest_rate"


# ---------------------------------------------------------------------------
# 2) /api/v1/events/{id} — explanation_grounded_in_targets
# ---------------------------------------------------------------------------


class TestEventDetailTargets:
    @pytest.mark.asyncio
    async def test_event_detail_includes_parallel_targets(self, seeded):
        from src.api.routes.events import get_event
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_event(
                event_id="evt_9q_fed",
                portfolio_id="pA",
                session=session,
            )

        assert resp.explanation_grounded_in_targets, (
            "expected parallel navigation targets list"
        )
        # Every entry has the parallel shape {ref, nav_target}
        for entry in resp.explanation_grounded_in_targets:
            assert "ref" in entry
            assert "nav_target" in entry
        # The factor:interest_rate ref must map to an operator factor target
        factor_entry = next(
            (e for e in resp.explanation_grounded_in_targets
             if e["ref"] == "factor:interest_rate"),
            None,
        )
        assert factor_entry is not None
        assert factor_entry["nav_target"] is not None
        assert factor_entry["nav_target"]["surface"] == "operator"
        assert factor_entry["nav_target"]["subtab"] == "factors"
        assert factor_entry["nav_target"]["filter"] == "interest_rate"
        assert factor_entry["nav_target"]["portfolio_id"] == "pA"

    @pytest.mark.asyncio
    async def test_event_detail_default_portfolio_when_not_passed(self, seeded):
        """When no portfolio_id query is passed, the nav targets fall
        back to 'default' — the frontend is responsible for always
        supplying the active portfolio, but the route never crashes."""
        from src.api.routes.events import get_event
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_event(
                event_id="evt_9q_fed",
                portfolio_id=None,
                session=session,
            )
        # Should still return the targets list, just with the fallback
        assert isinstance(resp.explanation_grounded_in_targets, list)
        for entry in resp.explanation_grounded_in_targets:
            nav = entry.get("nav_target")
            if nav is not None:
                assert nav["portfolio_id"] == "default"


# ---------------------------------------------------------------------------
# 3) /api/v1/alerts/active — evidence_targets
# ---------------------------------------------------------------------------


class TestAlertsEvidenceTargets:
    @pytest.mark.asyncio
    async def test_active_alerts_include_evidence_targets(self, seeded):
        from src.api.routes.alerts import active_alerts
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await active_alerts(
                portfolio_id="pA",
                limit=10,
                offset=0,
                priority_ordered=True,
                session=session,
            )
        crit = next(r for r in rows if r.id == "alert_9q_crit")
        assert crit.evidence_targets, "expected evidence_targets list"
        # Should include an event ref and a holding ref
        refs = {et["ref"] for et in crit.evidence_targets}
        assert "event:evt_9q_fed" in refs
        assert "holding:h_aapl_pA" in refs
        # The event ref should carry a navigable target
        event_entry = next(
            et for et in crit.evidence_targets if et["ref"] == "event:evt_9q_fed"
        )
        assert event_entry["nav_target"] is not None
        assert event_entry["nav_target"]["surface"] == "events"
        assert event_entry["nav_target"]["entity_id"] == "evt_9q_fed"
        # Portfolio safety — target is scoped to the alert's own portfolio
        assert event_entry["nav_target"]["portfolio_id"] == "pA"


# ---------------------------------------------------------------------------
# 4) /api/v1/audit/recent — nav_target per operator row
# ---------------------------------------------------------------------------


class TestAuditRecentNavTarget:
    @pytest.mark.asyncio
    async def test_factor_override_row_has_factor_target(self, seeded):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id="pA", entity_type=None,
                limit=10, session=session,
            )
        factor = next(r for r in rows if r.id == "audit_factor_9q")
        assert factor.nav_target is not None
        assert factor.nav_target["surface"] == "operator"
        assert factor.nav_target["subtab"] == "factors"
        assert factor.nav_target["portfolio_id"] == "pA"
        assert factor.nav_target["filter"] == "interest_rate"

    @pytest.mark.asyncio
    async def test_backfill_row_has_maintenance_target(self, seeded):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id="pA", entity_type=None,
                limit=10, session=session,
            )
        bf = next(r for r in rows if r.id == "audit_backfill_9q")
        assert bf.nav_target is not None
        assert bf.nav_target["surface"] == "operator"
        assert bf.nav_target["subtab"] == "maintenance"


# ---------------------------------------------------------------------------
# 5) /api/v1/intelligence/summary — recommended_actions carry nav_target
# ---------------------------------------------------------------------------


class TestSummaryActionTargets:
    @pytest.mark.asyncio
    async def test_summary_actions_carry_nav_target(self, seeded):
        from src.database.connection import get_session_factory
        from src.intelligence.summary import build_intelligence_summary

        factory = get_session_factory()
        async with factory() as session:
            summary = await build_intelligence_summary(
                session, portfolio_id="pA",
            )
        actions = summary.recommended_actions or []
        assert actions, "expected at least one recommended action for pA"
        # At least one should carry a structured nav_target
        with_targets = [a for a in actions if a.get("nav_target")]
        assert with_targets, "no recommended action carried a nav_target"
        # Each nav_target is portfolio-safe
        for a in with_targets:
            assert a["nav_target"]["portfolio_id"] == "pA"
            assert a["nav_target"]["surface"] in (
                "alerts", "portfolio", "operator", "digest", "events",
            )

    @pytest.mark.asyncio
    async def test_factors_family_carries_factor_filter(self, seeded):
        """If the seeded data produces a factors.* action, its
        nav_target should carry the interest_rate filter."""
        from src.database.connection import get_session_factory
        from src.intelligence.summary import build_intelligence_summary

        factory = get_session_factory()
        async with factory() as session:
            summary = await build_intelligence_summary(
                session, portfolio_id="pA",
            )
        factors_actions = [
            a for a in (summary.recommended_actions or [])
            if str(a.get("key", "")).startswith("factors.")
        ]
        if factors_actions:
            nav = factors_actions[0].get("nav_target")
            assert nav is not None
            assert nav["surface"] == "operator"
            assert nav["subtab"] == "factors"
