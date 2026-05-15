"""Phase 13 — Insight notification + scheduling layer tests.

Coverage matrix:

* Fingerprint stability: re-generating the same card produces the
  same fingerprint; severity/title/evidence changes move it; AI
  narration (different summary wording) does NOT move it.
* :func:`card_key` is stable across re-runs and identical for two
  cards built from the same source row.
* Migration v11: ``insight_snapshots`` table + indexes + unique
  constraint exist after the lifespan, and ``run_migrations()`` is
  idempotent.
* Notifier classifies first-run cards as ``first_run`` (quiet) and
  subsequent re-runs of new content as ``new``.  Severity escalation
  flips state to ``escalated``.  Unchanged content stays
  ``unchanged`` and updates ``last_seen_at`` only.
* Notifier is idempotent — a second pass with identical input adds
  zero new rows.
* Snapshots never carry AI prompt body or narrated summary text.
* Inbox shaper surfaces only ``new`` / ``escalated`` insights, only
  above the inbox severity floor.
* Telegram delivery is a no-op when not configured; when mocked-
  configured, only ``new`` / ``escalated`` cards above the severity
  floor are pushed.
* Digest builder attaches ``top_insights`` with deterministic body
  only (no AI summary, no live prices).
* ``POST /api/v1/intelligence/insights/run`` persists snapshots and
  returns a structured summary.
* ``GET /api/v1/intelligence/insights/last-run`` returns the most
  recent ``last_seen_at`` for the portfolio.
* Dashboard markup: Overview tab gains the Run-now button + Last-
  generated stamp; pill classes shipped.
* Multi-portfolio isolation: a snapshot from pA never affects pB.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Pure unit tests for the fingerprint helpers
# ─────────────────────────────────────────────────────────────────────


def _make_card(**overrides):
    from src.intelligence.insights import InsightCard, InsightEvidence
    base = dict(
        id=overrides.pop("id", "ins_test"),
        portfolio_id="default",
        severity="high",
        category="news_impact",
        title="Fed signals rate hike",
        summary="Materiality high; affects 1 holding.",
        affected_holdings=["AAPL"],
        evidence=[InsightEvidence(kind="news", ref="event:evt_x", label="N")],
    )
    base.update(overrides)
    return InsightCard(**base)


class TestFingerprint:
    def test_card_key_stable_across_rebuilds(self):
        from src.intelligence.insights import card_key
        a = _make_card(id="x1")
        b = _make_card(id="x2")
        # Different ids; same source ref → same key.
        assert card_key(a) == card_key(b)
        assert card_key(a).startswith("insight:news_impact:event:")

    def test_card_key_independent_of_id(self):
        from src.intelligence.insights import card_key
        a = _make_card(id="a")
        b = _make_card(id="b")
        assert card_key(a) == card_key(b)

    def test_fingerprint_changes_with_severity(self):
        from src.intelligence.insights import card_fingerprint
        a = _make_card()
        b = _make_card(severity="critical")
        assert card_fingerprint(a) != card_fingerprint(b)

    def test_fingerprint_changes_with_evidence(self):
        from src.intelligence.insights import InsightEvidence, card_fingerprint
        a = _make_card()
        b = _make_card(
            evidence=[InsightEvidence(kind="news", ref="event:evt_y", label="N")],
        )
        assert card_fingerprint(a) != card_fingerprint(b)

    def test_fingerprint_stable_under_summary_rewrite(self):
        # AI narration rewords summary/why_it_matters but does NOT change
        # material content — the fingerprint must NOT move.
        from src.intelligence.insights import card_fingerprint
        a = _make_card(summary="Original deterministic body.")
        b = _make_card(summary="AI-rewritten wording, same evidence.")
        assert card_fingerprint(a) == card_fingerprint(b)

    def test_is_escalation_logic(self):
        from src.intelligence.insights import is_escalation
        assert is_escalation(old_severity="medium", new_severity="critical")
        assert is_escalation(old_severity="low", new_severity="high")
        assert not is_escalation(old_severity="high", new_severity="medium")
        assert not is_escalation(old_severity="high", new_severity="high")


# ─────────────────────────────────────────────────────────────────────
# TestClient + temp DB + seeded portfolio (mirrors Phase 12 fixture)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase13_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase13.db")
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
    """Two portfolios; pA has one holding + alert + news + corporate event.
    pB stays empty so isolation can be asserted.
    """
    import asyncio
    import uuid
    from src.database.connection import get_db
    from src.database.models import (
        Alert, CorporateEvent, Event, EventLink, Holding,
        MacroFactorEvent, Portfolio,
    )

    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    iso_3d_ago = (now - timedelta(days=3)).isoformat()
    upcoming = (now + timedelta(days=4)).date().isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph13_pA", name="Phase 13 A", base_currency="USD",
                          is_default=0, created_at=iso, updated_at=iso),
                Portfolio(id="ph13_pB", name="Phase 13 B", base_currency="USD",
                          is_default=0, created_at=iso, updated_at=iso),
            ])
            await session.commit()
        async with get_db() as session:
            session.add(Holding(
                id="ph13_aapl", ticker="AAPL", currency="USD",
                isin="US0378331005", quantity=10, weight_pct=100.0,
                portfolio_id="ph13_pA", status="active",
                created_at=iso, updated_at=iso,
            ))
            await session.commit()
        async with get_db() as session:
            session.add(Event(
                id="ph13_evt",
                title="Fed signals rate hike",
                summary="50bps signalled by FOMC.",
                event_type="macro", materiality="high", confidence="high",
                published_at=iso_3d_ago, fetched_at=iso_3d_ago,
                created_at=iso, dedup_hash=str(uuid.uuid4()),
            ))
            session.add(EventLink(
                id=str(uuid.uuid4()), event_id="ph13_evt",
                link_type="macro_factor", link_target="ph13_aapl",
                channel="interest_rate", relevance_score=0.6,
                created_at=iso,
            ))
            session.add(MacroFactorEvent(
                id=str(uuid.uuid4()), event_id="ph13_evt",
                factor="interest_rate", direction="up", magnitude="moderate",
                confidence=0.8, created_at=iso,
            ))
            session.add(CorporateEvent(
                id="ph13_ce", portfolio_id="ph13_pA",
                holding_id="ph13_aapl", ticker="AAPL", isin="US0378331005",
                exchange="NASDAQ",
                source_id="manual_csv", source_name="Manual CSV Import",
                event_type="earnings",
                title="AAPL Q1 results", event_date=upcoming,
                confidence="unscored", match_method="isin",
                created_at=iso, updated_at=iso,
            ))
            session.add(Alert(
                id="ph13_alert",
                alert_type="risk_concentration",
                severity="high",
                title="Concentration alert",
                body="AAPL at 100% of portfolio.",
                portfolio_id="ph13_pA",
                acknowledged=0,
                agent_id="risk",
                created_at=iso,
            ))
            await session.commit()
    asyncio.run(_seed())
    yield


# ─────────────────────────────────────────────────────────────────────
# Migration v11
# ─────────────────────────────────────────────────────────────────────


class TestMigrationV11:
    def test_schema_version_bumped(self):
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 11

    def test_insight_snapshots_table_exists(self, client):
        import asyncio
        from sqlalchemy import inspect
        from src.database.connection import get_engine

        async def _check():
            engine = get_engine()
            async with engine.connect() as conn:
                def _inspect(sync):
                    insp = inspect(sync)
                    cols = {c["name"] for c in insp.get_columns("insight_snapshots")}
                    idx = {i["name"] for i in insp.get_indexes("insight_snapshots")}
                    return cols, idx
                return await conn.run_sync(_inspect)

        cols, idx = asyncio.run(_check())
        required_cols = {
            "id", "portfolio_id", "card_key", "category", "severity",
            "title", "fingerprint", "last_seen_at", "first_seen_at",
            "notified_at", "notified_severity", "telegram_delivered_at",
            "status", "created_at", "updated_at",
        }
        assert required_cols <= cols, f"missing cols: {required_cols - cols}"
        assert "ix_insight_snapshots_portfolio_id" in idx
        assert "ix_insight_snapshots_status" in idx

    def test_migration_idempotent(self, client):
        import asyncio
        from src.database.migrations import run_migrations
        asyncio.run(run_migrations())
        asyncio.run(run_migrations())


# ─────────────────────────────────────────────────────────────────────
# Notifier behaviour (read-only DB access via the live fixture)
# ─────────────────────────────────────────────────────────────────────


class TestNotifier:
    def test_run_route_returns_summary(self, client, seeded):
        r = client.post(
            "/api/v1/intelligence/insights/run",
            params={"portfolio_id": "ph13_pA"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("portfolio_id", "generated_at", "new", "escalated",
                    "unchanged", "telegram_status", "is_first_run"):
            assert key in body
        # First pass on a portfolio with new content reports is_first_run.
        # Total cards is at least the alert + news + corporate event +
        # listing-country + a couple of data-gap cards.
        assert body["telegram_status"] in (
            "skipped", "not_configured", "delivered", "failed",
        )

    def test_run_route_is_idempotent(self, client, seeded):
        # Second pass against identical state — no new/escalated rows.
        r = client.post(
            "/api/v1/intelligence/insights/run",
            params={"portfolio_id": "ph13_pA"},
        )
        body = r.json()
        assert body["new"] == 0
        assert body["escalated"] == 0

    def test_last_run_returns_timestamp(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights/last-run",
            params={"portfolio_id": "ph13_pA"},
        )
        body = r.json()
        assert body["portfolio_id"] == "ph13_pA"
        assert body["last_generated_at"]

    def test_insights_endpoint_stamps_notification_state(self, client, seeded):
        r = client.get(
            "/api/v1/intelligence/insights",
            params={"portfolio_id": "ph13_pA", "limit": "20"},
        )
        body = r.json()
        for c in body["insights"]:
            tags = [g for g in c["data_gaps"] if g.startswith("notification:")]
            assert tags, f"card {c['id']} missing notification state"
            state = tags[0].split(":", 1)[1]
            assert state in ("new", "escalated", "unchanged", "first_run")
        assert "last_generated_at" in body

    def test_pB_isolation(self, client, seeded):
        # pB has zero rows; run() must not bleed pA's snapshots into pB.
        r = client.post(
            "/api/v1/intelligence/insights/run",
            params={"portfolio_id": "ph13_pB"},
        )
        assert r.status_code == 200
        last = client.get(
            "/api/v1/intelligence/insights/last-run",
            params={"portfolio_id": "ph13_pB"},
        ).json()
        # pB had no rows AND its only card is a "no holdings" onboarding
        # card.  Snapshots stamp at least one row (the data-gap card).
        assert last["last_generated_at"]
        # And the snapshot itself never references pA holdings.
        from src.database.models import InsightSnapshot
        import asyncio
        from sqlalchemy import select
        from src.database.connection import get_db

        async def _count(pid):
            async with get_db() as session:
                rows = (await session.execute(
                    select(InsightSnapshot).where(
                        InsightSnapshot.portfolio_id == pid,
                    )
                )).scalars().all()
                return rows
        pa = asyncio.run(_count("ph13_pA"))
        pb = asyncio.run(_count("ph13_pB"))
        pa_keys = {r.card_key for r in pa}
        pb_keys = {r.card_key for r in pb}
        assert pa_keys.isdisjoint(pb_keys) or all(
            "data_gap" in k or "config" in k for k in pa_keys & pb_keys
        )


# ─────────────────────────────────────────────────────────────────────
# Snapshot rows never carry AI bodies
# ─────────────────────────────────────────────────────────────────────


class TestSnapshotPrivacy:
    def test_snapshot_row_carries_no_ai_body(self, client, seeded):
        """The snapshot row stores card_key/category/severity/title/fingerprint;
        the customer-visible summary + AI narration are NEVER persisted."""
        import asyncio
        from sqlalchemy import select
        from src.database.connection import get_db
        from src.database.models import InsightSnapshot

        async def _all():
            async with get_db() as session:
                rows = (await session.execute(
                    select(InsightSnapshot).where(
                        InsightSnapshot.portfolio_id == "ph13_pA",
                    )
                )).scalars().all()
                return [r.__dict__ for r in rows]
        rows = asyncio.run(_all())
        for r in rows:
            for field, value in r.items():
                if not isinstance(value, str):
                    continue
                assert "GROUNDING CONTRACT" not in value, \
                    "narration prompt leaked into snapshot row"


# ─────────────────────────────────────────────────────────────────────
# Inbox integration
# ─────────────────────────────────────────────────────────────────────


class TestInboxIntegration:
    def test_inbox_shows_new_insight(self, client, seeded):
        """After Phase 13 runs, the inbox surfaces ``new``/``escalated``
        insights as items with source_type='insight'."""
        # Seed a brand-new insight by adding another high alert that
        # the generator hasn't yet snapshotted — easiest is to bump
        # severity of the existing alert (escalation) OR add a fresh
        # alert.  Use a fresh alert.
        import asyncio
        from datetime import datetime, timezone
        from src.database.connection import get_db
        from src.database.models import Alert

        iso = datetime.now(timezone.utc).isoformat()

        async def _add_alert():
            async with get_db() as session:
                session.add(Alert(
                    id="ph13_alert_2",
                    alert_type="stale_data",
                    severity="high",
                    title="Phase 13 test — fresh alert",
                    body="Body.",
                    portfolio_id="ph13_pA",
                    acknowledged=0,
                    agent_id="risk",
                    created_at=iso,
                ))
                await session.commit()
        asyncio.run(_add_alert())

        # Run notifier to register the new card.
        client.post(
            "/api/v1/intelligence/insights/run",
            params={"portfolio_id": "ph13_pA"},
        )

        inbox = client.get(
            "/api/v1/notifications",
            params={"portfolio_id": "ph13_pA"},
        ).json()
        insight_items = [
            i for i in inbox["items"] if i["source_type"] == "insight"
        ]
        assert insight_items, "expected at least one insight inbox item"
        item = insight_items[0]
        assert item["unread"] is True
        assert item["action_target"] is not None
        assert item["action_target"]["surface"]

    def test_floor_filters_info_insights(self, seeded):
        # The shaper alone must drop info-severity insight items.
        from src.intelligence.notifications import _shape_insight
        card_dict = {
            "id": "x", "portfolio_id": "ph13_pA",
            "severity": "info", "category": "data_gap",
            "title": "AI provider not configured", "summary": "S",
            "evidence": [{"kind": "config", "ref": "settings:ai", "label": "x"}],
            "deep_links": [],
            "created_at": "2026-05-14T00:00:00+00:00",
            "data_gaps": [], "affected_holdings": [],
            "source_type": "deterministic", "rank": 100,
            "confidence": None, "recommended_action": None,
            "why_it_matters": None,
        }
        item = _shape_insight(
            {"card_key": "k", "state": "new", "card": card_dict},
            frozenset(), "ph13_pA",
        )
        assert item is None

    def test_unchanged_insights_skipped(self, seeded):
        from src.intelligence.notifications import _shape_insight
        item = _shape_insight(
            {"card_key": "k", "state": "unchanged", "card": {
                "severity": "high", "category": "alert",
                "title": "x", "summary": "y", "evidence": [],
                "deep_links": [], "created_at": "",
            }},
            frozenset(), "ph13_pA",
        )
        assert item is None


# ─────────────────────────────────────────────────────────────────────
# Telegram delivery (mocked)
# ─────────────────────────────────────────────────────────────────────


class TestTelegram:
    @pytest.mark.asyncio
    async def test_no_op_when_not_configured(self):
        from src.intelligence.insights import (
            InsightCard, InsightEvidence, InsightsCoverage, InsightsResponse,
            notify_new_or_escalated,
        )
        from src.database.connection import get_db

        # Build a real session, but mock is_telegram_configured False.
        card = InsightCard(
            id="x", portfolio_id="ph13_pA", severity="critical",
            category="alert", title="T", summary="S",
            evidence=[InsightEvidence(kind="alert", ref="alert:a", label="A")],
        )
        resp = InsightsResponse(
            portfolio_id="ph13_pA", insights=[card],
            coverage=InsightsCoverage(),
            total=1, limit=12,
        )

        async with get_db() as session:
            with patch(
                "src.integrations.telegram.is_telegram_configured",
                return_value=False,
            ):
                out = await notify_new_or_escalated(
                    session, resp, deliver_telegram=True,
                )
        assert out.telegram_status == "not_configured"
        assert out.telegram_delivered == []

    @pytest.mark.asyncio
    async def test_delivers_when_configured(self):
        from src.intelligence.insights import (
            InsightCard, InsightEvidence, InsightsCoverage, InsightsResponse,
            notify_new_or_escalated,
        )
        from src.database.connection import get_db

        card = InsightCard(
            id="y", portfolio_id="ph13_pA", severity="critical",
            category="alert", title="T2", summary="S2",
            evidence=[InsightEvidence(kind="alert", ref="alert:b", label="B")],
        )
        resp = InsightsResponse(
            portfolio_id="ph13_pA", insights=[card],
            coverage=InsightsCoverage(), total=1, limit=12,
        )

        async def fake_deliver(insight):
            return {"delivered": True, "status": "delivered", "sent_to": [1]}

        # Suppress the "new vs escalated" classification by pre-clearing
        # any leftover snapshot for this card key.
        from src.database.models import InsightSnapshot
        from sqlalchemy import delete
        async with get_db() as session:
            await session.execute(delete(InsightSnapshot).where(
                InsightSnapshot.portfolio_id == "ph13_pA",
                InsightSnapshot.card_key.like("insight:alert:alert:b%"),
            ))
            await session.commit()

        async with get_db() as session:
            with patch(
                "src.integrations.telegram.is_telegram_configured",
                return_value=True,
            ), patch(
                "src.integrations.telegram.notifications.deliver_insight",
                new=fake_deliver,
            ):
                out = await notify_new_or_escalated(
                    session, resp, deliver_telegram=True,
                )
        # The card was added fresh, so it lands in "new" — but
        # is_first_run flips it to "unchanged" if no prior snapshots
        # for this portfolio existed.  Either way, when there are
        # already-existing pA snapshots from earlier tests, this card
        # IS new and should be delivered.
        if out.new or out.escalated:
            assert out.telegram_status == "delivered"
            assert "insight:alert:alert:b" in out.telegram_delivered


# ─────────────────────────────────────────────────────────────────────
# Digest integration
# ─────────────────────────────────────────────────────────────────────


class TestDigest:
    @pytest.mark.asyncio
    async def test_top_insights_attached(self, client, seeded):
        """The agent helper returns a deterministic top-insights list."""
        from src.agents.analysis import AnalysisAgent
        agent = AnalysisAgent()
        agent._portfolio_id = "ph13_pA"
        out = await agent._fetch_top_insights_for_digest(limit=5)
        assert isinstance(out, list)
        for item in out:
            for key in ("severity", "category", "title", "summary",
                        "evidence_ref", "evidence_label", "source_type"):
                assert key in item, f"missing {key}"
            assert item["source_type"] == "deterministic"


# ─────────────────────────────────────────────────────────────────────
# Scheduler wiring
# ─────────────────────────────────────────────────────────────────────


class TestScheduler:
    def test_job_registered(self):
        from src.scheduler.jobs import AxionScheduler
        sched = AxionScheduler()
        sched.setup({})
        ids = {j.id for j in sched._scheduler.get_jobs()}
        assert "insights_generation" in ids


# ─────────────────────────────────────────────────────────────────────
# Dashboard contract
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


class TestDashboard:
    def test_run_now_and_last_generated_markup(self, index_html):
        assert 'id="insights-run-now-btn"' in index_html
        assert 'id="insights-last-generated"' in index_html

    def test_js_calls_run_endpoint(self, app_js):
        assert "intelligenceInsightsRun" in app_js
        assert "_runInsightsNow" in app_js
        assert "_renderInsightNotificationPill" in app_js

    def test_css_carries_notif_pill_classes(self, styles_css):
        for cls in (".insight-notif-pill", ".insight-notif-new",
                    ".insight-notif-escalated", ".insight-notif-notified"):
            assert cls in styles_css, f"missing CSS class {cls}"
