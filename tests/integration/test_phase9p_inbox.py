"""Phase 9P integration tests — /api/v1/notifications routes.

Drives the real inbox / mark-read / mark-all-read endpoints against
a seeded DB and asserts:

  * the inbox composes alerts + digests + operator entries +
    high-priority recommended actions for the active portfolio
  * portfolio isolation is preserved (pA rows never leak into pB)
  * marking one item read persists and shrinks the unread count
  * marking all read is idempotent and bounded to the current view
  * the unique constraint on (portfolio_id, notification_key) is
    enforced (double-mark is a no-op)
  * NotificationRead rows survive a fresh inbox build
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
# Temp DB fixtures (mirror of Phase 9D/F/G/H/K/M/N/O pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")

    db_dir = tmp_path_factory.mktemp("axion_phase9p")
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
async def seeded_inbox(_migrated_db):
    """Seed two portfolios with alerts, a digest, an operator audit row
    and a high-priority Phase 9N action source so the inbox has at
    least one item of every kind."""
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
            Holding(id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                    weight_pct=100, current_price=120, market_value=6000,
                    portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        await session.commit()

    # Alerts — pA has critical + info; pB has only a high (no leakage)
    async with get_db() as session:
        session.add_all([
            Alert(
                id="alert_pA_critical", portfolio_id="pA",
                alert_type="macro_factor", severity="critical",
                title="Rate shock on AAPL",
                body="Fed 50bps shock — duration risk.",
                related_holdings=json.dumps(["h_aapl_pA"]),
                related_events=json.dumps(["evt_fed"]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(hours=1)).isoformat(),
            ),
            Alert(
                id="alert_pA_info", portfolio_id="pA",
                alert_type="info", severity="info",
                title="Daily brief ready", body="Morning digest is available.",
                related_holdings=json.dumps([]),
                related_events=json.dumps([]),
                acknowledged=0, delivered=0, agent_id="digest",
                created_at=(now_dt - timedelta(minutes=5)).isoformat(),
            ),
            Alert(
                id="alert_pB_high", portfolio_id="pB",
                alert_type="oil", severity="high",
                title="Oil pressure on XOM", body="OPEC cut.",
                related_holdings=json.dumps(["h_xom_pB"]),
                related_events=json.dumps([]),
                acknowledged=0, delivered=0, agent_id="risk",
                created_at=(now_dt - timedelta(minutes=30)).isoformat(),
            ),
        ])
        # Digest — pA only
        session.add(Digest(
            id="digest_pA", portfolio_id="pA", digest_type="daily",
            period_start=(now_dt - timedelta(days=1)).isoformat(),
            period_end=now,
            content=json.dumps({
                "headline": "Alpha daily — mildly negative on rate shock",
                "portfolio_assessment": "AAPL negatives dominate.",
                "risk_flags": ["Interest rates trending up"],
                "holdings_requiring_attention": ["AAPL"],
                "key_developments": [],
                "action_items": [],
            }),
            event_count=3, alert_count=2, holding_count=2,
            delivered=0,
            created_at=(now_dt - timedelta(hours=2)).isoformat(),
        ))
        # Operator audit — backfill row (global, but the route
        # includes it for every portfolio)
        session.add(AuditLog(
            id="audit_backfill", entity_type="intelligence_backfill",
            entity_id="window_7d", action="backfill",
            old_value=None,
            new_value=json.dumps({
                "window_days": 7, "events_scanned": 47, "events_replayed": 47,
                "links_added": 12, "mfe_added": 3, "events_failed": 0,
            }),
            agent_id="operator_backfill", user_id="operator",
            reason="e2e seed", created_at=(now_dt - timedelta(minutes=20)).isoformat(),
        ))
        # Operator audit — a factor override on AAPL (resolves to pA)
        session.add(AuditLog(
            id="audit_factor", entity_type="holding_factor_sensitivity",
            entity_id="ovr_aapl", action="update",
            old_value=json.dumps({"ticker": "AAPL", "factor": "interest_rate",
                                  "sensitivity": -0.6, "holding_id": "h_aapl_pA"}),
            new_value=json.dumps({"ticker": "AAPL", "factor": "interest_rate",
                                  "sensitivity": -0.9, "holding_id": "h_aapl_pA"}),
            agent_id="operator", user_id="operator",
            reason="9p test", created_at=(now_dt - timedelta(minutes=15)).isoformat(),
        ))
        await session.commit()

    yield


# ---------------------------------------------------------------------------
# 1) GET /api/v1/notifications — composes all sources for pA
# ---------------------------------------------------------------------------


class TestInboxCompose:
    @pytest.mark.asyncio
    async def test_inbox_pA_contains_alerts_digest_and_operator(
        self, seeded_inbox,
    ):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        source_types = {i.source_type for i in resp.items}
        assert "alert" in source_types
        assert "digest" in source_types
        assert "operator" in source_types
        # Plus at least one high-priority recommended action from the
        # Phase 9N summary (the critical alert triggers one)
        assert "action" in source_types

    @pytest.mark.asyncio
    async def test_inbox_respects_portfolio_isolation(self, seeded_inbox):
        """pB's inbox must NOT contain pA's critical alert, even though
        the two portfolios share the same DB."""
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp_a = await get_inbox(portfolio_id="pA", session=session)
        async with factory() as session:
            resp_b = await get_inbox(portfolio_id="pB", session=session)

        a_alert_ids = {i.source_id for i in resp_a.items if i.source_type == "alert"}
        b_alert_ids = {i.source_id for i in resp_b.items if i.source_type == "alert"}
        assert "alert_pA_critical" in a_alert_ids
        assert "alert_pA_critical" not in b_alert_ids
        assert "alert_pB_high" in b_alert_ids
        assert "alert_pB_high" not in a_alert_ids

        # pB does NOT have a digest — so its inbox should be smaller
        assert "digest_pA" not in {
            i.source_id for i in resp_b.items if i.source_type == "digest"
        }

    @pytest.mark.asyncio
    async def test_inbox_priority_ordering_is_correct(self, seeded_inbox):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        # Unread items first (all are unread on first load),
        # then high → medium → low
        priorities = [i.priority for i in resp.items]
        # Find first low and make sure no high comes after it
        if "low" in priorities:
            first_low = priorities.index("low")
            assert "high" not in priorities[first_low:]
        # High-priority item (critical alert) must be first
        assert resp.items[0].priority == "high"

    @pytest.mark.asyncio
    async def test_inbox_summary_counts_match_items(self, seeded_inbox):
        from src.api.routes.notifications import get_inbox
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)

        assert resp.summary["total"] == len(resp.items)
        assert resp.summary["unread"] == sum(1 for i in resp.items if i.unread)
        assert "by_source" in resp.summary


# ---------------------------------------------------------------------------
# 2) POST /api/v1/notifications/mark-read — per-item read state
# ---------------------------------------------------------------------------


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_mark_single_item_read_persists(self, seeded_inbox):
        from src.api.routes.notifications import (
            get_inbox, mark_read, MarkReadRequest,
        )
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            before = await get_inbox(portfolio_id="pA", session=session)
        unread_before = before.summary["unread"]
        assert unread_before >= 1

        async with factory() as session:
            resp = await mark_read(
                MarkReadRequest(key="alert:alert_pA_critical", portfolio_id="pA"),
                session=session,
            )
        assert resp.read is True

        async with factory() as session:
            after = await get_inbox(portfolio_id="pA", session=session)
        assert after.summary["unread"] == unread_before - 1

        # The specific item should now be marked read
        critical_items = [i for i in after.items if i.key == "alert:alert_pA_critical"]
        assert len(critical_items) == 1
        assert critical_items[0].unread is False

    @pytest.mark.asyncio
    async def test_mark_read_is_idempotent(self, seeded_inbox):
        from src.api.routes.notifications import mark_read, MarkReadRequest
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        # First call
        async with factory() as session:
            r1 = await mark_read(
                MarkReadRequest(key="alert:alert_pA_info", portfolio_id="pA"),
                session=session,
            )
        # Second call — must not raise or create a duplicate row
        async with factory() as session:
            r2 = await mark_read(
                MarkReadRequest(key="alert:alert_pA_info", portfolio_id="pA"),
                session=session,
            )
        assert r1.read is True and r2.read is True
        assert r1.read_at == r2.read_at  # idempotent — same row returned

    @pytest.mark.asyncio
    async def test_mark_read_in_pA_does_not_affect_pB(self, seeded_inbox):
        from src.api.routes.notifications import (
            get_inbox, mark_read, MarkReadRequest,
        )
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await mark_read(
                MarkReadRequest(key="alert:alert_pB_high", portfolio_id="pA"),
                session=session,
            )
        # Now pB's copy of that alert (same id, different portfolio)
        # should still be unread
        async with factory() as session:
            resp_b = await get_inbox(portfolio_id="pB", session=session)
        b_item = next(
            (i for i in resp_b.items if i.key == "alert:alert_pB_high"),
            None,
        )
        # The alert belongs to pB anyway, so we just assert the
        # key we marked in pA didn't leak.  pB's own item is
        # still unread because the NotificationRead row is scoped
        # to portfolio_id='pA'.
        assert b_item is not None
        assert b_item.unread is True

    @pytest.mark.asyncio
    async def test_mark_read_rejects_malformed_key(self, seeded_inbox):
        from fastapi import HTTPException
        from src.api.routes.notifications import mark_read, MarkReadRequest
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await mark_read(
                    MarkReadRequest(key="no_colon", portfolio_id="pA"),
                    session=session,
                )
            assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 3) POST /api/v1/notifications/mark-all-read
# ---------------------------------------------------------------------------


class TestMarkAllRead:
    @pytest.mark.asyncio
    async def test_mark_all_zeroes_the_unread_count(self, seeded_inbox):
        from src.api.routes.notifications import (
            get_inbox, mark_all_read, MarkAllReadRequest,
        )
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await mark_all_read(
                MarkAllReadRequest(portfolio_id="pA"),
                session=session,
            )
        async with factory() as session:
            resp = await get_inbox(portfolio_id="pA", session=session)
        assert resp.summary["unread"] == 0
        # Items themselves should all be marked read now
        assert all(not i.unread for i in resp.items)

    @pytest.mark.asyncio
    async def test_mark_all_second_call_marks_zero(self, seeded_inbox):
        from src.api.routes.notifications import (
            mark_all_read, MarkAllReadRequest,
        )
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            r1 = await mark_all_read(
                MarkAllReadRequest(portfolio_id="pA"),
                session=session,
            )
        async with factory() as session:
            r2 = await mark_all_read(
                MarkAllReadRequest(portfolio_id="pA"),
                session=session,
            )
        # The second call should mark nothing new
        assert r2.marked == 0

    @pytest.mark.asyncio
    async def test_mark_all_in_pA_leaves_pB_unread(self, seeded_inbox):
        from src.api.routes.notifications import (
            get_inbox, mark_all_read, MarkAllReadRequest,
        )
        from src.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await mark_all_read(
                MarkAllReadRequest(portfolio_id="pA"),
                session=session,
            )
        async with factory() as session:
            resp_b = await get_inbox(portfolio_id="pB", session=session)
        # pB's critical alert should STILL be unread
        assert resp_b.summary["unread"] >= 1
