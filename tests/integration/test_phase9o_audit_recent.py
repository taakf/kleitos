"""Phase 9O integration tests — /api/v1/audit/recent route.

Drives the real audit shaping route against a seeded portfolio and
asserts that:

  * operator factor override / relationship CRUD rows are returned
    in shaped form
  * reconcile + backfill maintenance rows are always returned
    regardless of the portfolio_id filter
  * non-operator rows (events, alerts, etc.) are filtered out
  * portfolio_id filter correctly scopes rows that reference
    holdings (via HoldingFactorSensitivity / HoldingRelationship)
  * the /categories + /group-refs helper endpoints return the
    documented category labels / bucketing

Uses the same temp-DB + session-factory pattern as the Phase 9D/F/
G/H/K/M/N integration tests.
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
# Temp DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")

    db_dir = tmp_path_factory.mktemp("axion_phase9o")
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
async def seeded_audit(_migrated_db):
    """Seed a set of portfolios + holdings + audit rows that exercises
    every shape branch (factor upsert/update/delete, relationship
    create/update/delete, reconcile with/without changes, backfill)."""
    from src.database.connection import get_db
    from src.database.models import (
        AuditLog,
        Alert,
        Event,
        EventLink,
        MacroFactorEvent,
        AnalysisNote,
        Holding,
        HoldingFactorSensitivity,
        HoldingRelationship,
        Portfolio,
        Security,
    )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    # Step 1: wipe any prior state
    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship,
            AuditLog, Alert, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

    # Step 2: insert portfolios + holdings (must commit before the
    # FK-dependent sensitivity / relationship rows land)
    async with get_db() as session:
        session.add_all([
            Portfolio(id="pA", name="Alpha", base_currency="USD", is_default=1,
                      created_at=now, updated_at=now),
            Portfolio(id="pB", name="Beta", base_currency="USD", is_default=0,
                      created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(
                id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                weight_pct=100.0, current_price=180.0, market_value=1800.0,
                portfolio_id="pA", status="active",
                created_at=now, updated_at=now,
            ),
            Holding(
                id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                weight_pct=100.0, current_price=120.0, market_value=6000.0,
                portfolio_id="pB", status="active",
                created_at=now, updated_at=now,
            ),
        ])
        await session.commit()

    # Step 3: insert FK-dependent factor override + relationship rows
    async with get_db() as session:
        session.add(HoldingFactorSensitivity(
            id="ovr_aapl_rate",
            holding_id="h_aapl_pA",
            factor="interest_rate",
            sensitivity=-0.9,
            source="manual",
            created_at=now,
            updated_at=now,
        ))
        session.add(HoldingRelationship(
            id="rel_aapl_tsmc",
            holding_id="h_aapl_pA",
            relationship_type="supplier",
            related_ticker="TSM",
            related_name="Taiwan Semiconductor",
            strength=0.85,
            source="manual",
            created_at=now,
            updated_at=now,
        ))
        await session.commit()

    # --- AuditLog rows spanning every operator shape branch ---------
    async with get_db() as session:
        def iso_offset(minutes: int) -> str:
            return (now_dt - timedelta(minutes=minutes)).isoformat()

        # A non-operator row that MUST be filtered out
        session.add(AuditLog(
            id="nonop_evt_1",
            entity_type="event",
            entity_id="evt_abc",
            action="ingest",
            old_value=None,
            new_value=json.dumps({"source": "sec"}),
            agent_id="ingest",
            user_id="system",
            reason=None,
            created_at=iso_offset(1),
        ))

        # Factor override update — should resolve to pA
        session.add(AuditLog(
            id="op_factor_update",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_aapl_rate",
            action="update",
            old_value=json.dumps({
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.60, "holding_id": "h_aapl_pA",
            }),
            new_value=json.dumps({
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.90, "holding_id": "h_aapl_pA",
            }),
            agent_id="operator",
            user_id="operator",
            reason="phase 9o test",
            created_at=iso_offset(10),
        ))

        # Manual relationship create — should resolve to pA
        session.add(AuditLog(
            id="op_rel_create",
            entity_type="holding_relationship",
            entity_id="rel_aapl_tsmc",
            action="create",
            old_value=None,
            new_value=json.dumps({
                "holding_id": "h_aapl_pA",
                "relationship_type": "supplier",
                "related_ticker": "TSM",
                "strength": 0.85,
                "source": "manual",
            }),
            agent_id="operator",
            user_id="operator",
            reason="TSMC is primary foundry",
            created_at=iso_offset(20),
        ))

        # Reconcile row (global — always visible)
        session.add(AuditLog(
            id="op_reconcile",
            entity_type="holding_relationships",
            entity_id="seed_reconcile",
            action="reconcile",
            old_value=None,
            new_value=json.dumps({
                "created": 2, "updated": 1, "unchanged": 12,
                "pruned": 0, "skipped_no_holding": 0,
            }),
            agent_id="operator",
            user_id="operator",
            reason=None,
            created_at=iso_offset(30),
        ))

        # Two consecutive no-op reconciles to exercise the dedupe rule
        session.add(AuditLog(
            id="op_reconcile_noop_1",
            entity_type="holding_relationships",
            entity_id="seed_reconcile",
            action="reconcile",
            old_value=None,
            new_value=json.dumps({
                "created": 0, "updated": 0, "unchanged": 12,
                "pruned": 0, "skipped_no_holding": 0,
            }),
            agent_id="operator",
            user_id="operator",
            reason=None,
            created_at=iso_offset(40),
        ))
        session.add(AuditLog(
            id="op_reconcile_noop_2",
            entity_type="holding_relationships",
            entity_id="seed_reconcile",
            action="reconcile",
            old_value=None,
            new_value=json.dumps({
                "created": 0, "updated": 0, "unchanged": 12,
                "pruned": 0, "skipped_no_holding": 0,
            }),
            agent_id="operator",
            user_id="operator",
            reason=None,
            created_at=iso_offset(50),
        ))

        # Backfill row (global — always visible)
        session.add(AuditLog(
            id="op_backfill",
            entity_type="intelligence_backfill",
            entity_id="window_7d",
            action="backfill",
            old_value=None,
            new_value=json.dumps({
                "window_days": 7,
                "events_scanned": 47,
                "events_replayed": 47,
                "links_added": 12,
                "mfe_added": 3,
                "events_failed": 0,
            }),
            agent_id="operator_backfill",
            user_id="operator",
            reason="ui backfill",
            created_at=iso_offset(60),
        ))

        await session.commit()

    yield


# ---------------------------------------------------------------------------
# 1) /api/v1/audit/recent — unfiltered
# ---------------------------------------------------------------------------


class TestRecentUnfiltered:
    @pytest.mark.asyncio
    async def test_returns_all_operator_rows_newest_first(self, seeded_audit):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type=None,
                limit=10, session=session,
            )

        # Non-operator event row must be filtered out
        ids = [r.id for r in rows]
        assert "nonop_evt_1" not in ids

        # All four shape branches must be present
        entity_types = {r.entity_type for r in rows}
        assert "holding_factor_sensitivity" in entity_types
        assert "holding_relationship" in entity_types
        assert "holding_relationships" in entity_types
        assert "intelligence_backfill" in entity_types

        # Consecutive no-op reconciles should collapse — only one
        # of the two must survive.  The real reconcile (op_reconcile)
        # must also be present.
        reconcile_rows = [r for r in rows if r.entity_type == "holding_relationships"]
        assert any(r.id == "op_reconcile" for r in reconcile_rows)
        # At most one of the two no-ops
        noop_survivors = [r.id for r in reconcile_rows
                          if r.id in ("op_reconcile_noop_1", "op_reconcile_noop_2")]
        assert len(noop_survivors) <= 1

        # Ordering — newest first (factor update at t-10 is the
        # newest operator row; backfill at t-60 is the oldest)
        iso_by_id = {r.id: r.timestamp for r in rows}
        assert iso_by_id["op_factor_update"] > iso_by_id["op_backfill"]

    @pytest.mark.asyncio
    async def test_shaped_factor_update_carries_highlights(self, seeded_audit):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type=None,
                limit=10, session=session,
            )
        factor = next(r for r in rows if r.id == "op_factor_update")
        assert "AAPL" in factor.title
        assert "interest_rate" in factor.title
        assert "-0.60" in factor.summary
        assert "-0.90" in factor.summary
        assert factor.old_highlights == {"sensitivity": -0.60}
        assert factor.new_highlights == {"sensitivity": -0.90}
        assert factor.reason == "phase 9o test"
        # Phase 9O also resolves the portfolio_id for factor rows
        assert factor.portfolio_id == "pA"

    @pytest.mark.asyncio
    async def test_relationship_create_entry_is_shaped(self, seeded_audit):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type=None,
                limit=10, session=session,
            )
        rel = next(r for r in rows if r.id == "op_rel_create")
        assert "Created relationship" in rel.title
        assert "TSM" in rel.title
        assert "supplier" in rel.title
        assert rel.new_highlights == {"strength": 0.85}
        assert rel.portfolio_id == "pA"

    @pytest.mark.asyncio
    async def test_backfill_entry_has_stats_highlights(self, seeded_audit):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type=None,
                limit=10, session=session,
            )
        bf = next(r for r in rows if r.id == "op_backfill")
        assert "7d window" in bf.title
        assert bf.new_highlights["links_added"] == 12
        assert bf.new_highlights["events_failed"] == 0
        # Global — no portfolio resolution
        assert bf.portfolio_id is None


# ---------------------------------------------------------------------------
# 2) /api/v1/audit/recent — portfolio_id filter
# ---------------------------------------------------------------------------


class TestRecentPortfolioFilter:
    @pytest.mark.asyncio
    async def test_portfolio_filter_keeps_scoped_and_global(self, seeded_audit):
        """When pA is requested:
          * factor update (resolves to pA) → kept
          * rel create (resolves to pA) → kept
          * reconcile + backfill (global) → kept
        """
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id="pA", entity_type=None,
                limit=10, session=session,
            )
        ids = {r.id for r in rows}
        assert "op_factor_update" in ids  # resolved pA
        assert "op_rel_create" in ids     # resolved pA
        assert "op_reconcile" in ids       # global maintenance
        assert "op_backfill" in ids        # global maintenance

    @pytest.mark.asyncio
    async def test_portfolio_filter_excludes_other_portfolio(self, seeded_audit):
        """When pB is requested the factor update + rel create (both
        belong to pA) must be dropped, but global reconcile + backfill
        rows must still come through."""
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id="pB", entity_type=None,
                limit=10, session=session,
            )
        ids = {r.id for r in rows}
        assert "op_factor_update" not in ids
        assert "op_rel_create" not in ids
        assert "op_reconcile" in ids
        assert "op_backfill" in ids


# ---------------------------------------------------------------------------
# 3) /api/v1/audit/recent — explicit entity_type filter
# ---------------------------------------------------------------------------


class TestRecentEntityTypeFilter:
    @pytest.mark.asyncio
    async def test_filter_to_backfill_only(self, seeded_audit):
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type="intelligence_backfill",
                limit=10, session=session,
            )
        assert len(rows) == 1
        assert rows[0].entity_type == "intelligence_backfill"
        assert rows[0].id == "op_backfill"

    @pytest.mark.asyncio
    async def test_non_operator_entity_type_returns_empty(self, seeded_audit):
        """An explicit filter for a non-operator entity type must
        return an empty list — no leakage from the event / alert rows."""
        from src.api.routes.audit import recent_operator_actions
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            rows = await recent_operator_actions(
                portfolio_id=None, entity_type="event",
                limit=10, session=session,
            )
        assert rows == []


# ---------------------------------------------------------------------------
# 4) /api/v1/audit/categories + /group-refs helpers
# ---------------------------------------------------------------------------


class TestCategoriesEndpoint:
    @pytest.mark.asyncio
    async def test_categories_returns_labels_and_order(self):
        from src.api.routes.audit import evidence_ref_categories

        resp = await evidence_ref_categories()
        assert "categories" in resp
        assert "order" in resp
        # Key invariants that the frontend depends on
        assert resp["categories"]["factors"] == "Factors"
        assert resp["categories"]["alerts"] == "Alerts"
        assert resp["categories"]["holdings"] == "Holdings"
        # Order is a list of the same keys
        assert set(resp["order"]) == set(resp["categories"].keys())

    @pytest.mark.asyncio
    async def test_group_refs_endpoint_returns_buckets(self):
        from src.api.routes.audit import group_refs_endpoint

        resp = await group_refs_endpoint(
            ref=["factor:interest_rate", "alert:a1", "holding:h1"],
        )
        assert resp["groups"]["factors"] == ["factor:interest_rate"]
        assert resp["groups"]["alerts"] == ["alert:a1"]
        assert resp["groups"]["holdings"] == ["holding:h1"]
