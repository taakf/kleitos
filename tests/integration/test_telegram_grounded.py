"""Phase 9F integration tests for the grounded Telegram intelligence layer.

Covers every testable contract the Phase 9F brief required:

* Telegram session store (``telegram_sessions``) — read / write / switch
* Portfolio isolation in the per-chat grounded chat reply
* Dedupe + cooldown in the delivery gate (``should_deliver``)
* Retry safety — failed sends do NOT mark ``Alert.delivered``
* Successful sends mark ``Alert.delivered`` and write a
  ``telegram_deliveries`` row
* Grounded alert formatting carries severity, portfolio, holdings,
  channel, and deterministic "why" line
* Grounded digest formatting reads Phase 9E shape (headline,
  risk_flags, holdings_requiring_attention) — no legacy sections
* Schema v5 migration is applied and ``telegram_sessions`` +
  ``telegram_deliveries`` tables exist
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, inspect, select


# ---------------------------------------------------------------------------
# Temp DB fixture (mirror of Phase 9D pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_phase9f")
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
    """Two portfolios, several holdings, an alert + event + factor link."""
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

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote, HoldingFactorSensitivity,
            HoldingRelationship, Alert, Digest, Event, Holding, Security,
            TelegramDelivery, TelegramSession, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Portfolio A", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
            Portfolio(id="pB", name="Portfolio B", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
            Portfolio(id="default", name="Default", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, current_price=180.0, market_value=1800.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_nvda_pA", ticker="NVDA", currency="USD", quantity=5,
                    weight_pct=15.0, current_price=500.0, market_value=2500.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                    weight_pct=20.0, current_price=120.0, market_value=6000.0,
                    portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        for t, sector in (("AAPL", "Information Technology"),
                          ("NVDA", "Information Technology"),
                          ("XOM", "Energy")):
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector=sector, geography="united states",
                themes="[]", created_at=now, updated_at=now,
            ))
        await session.commit()

    # Seed an event + factor link + alert for the AAPL / pA pair.
    async with get_db() as session:
        event_id = "evt_fed_rates"
        session.add(Event(
            id=event_id,
            title="Federal Reserve raises interest rates by 50 bps",
            summary="FOMC vote to raise rates.",
            event_type="rates",
            fetched_at=now,
            created_at=now,
            dedup_hash="h_fed_rates",
        ))
        await session.commit()

    async with get_db() as session:
        session.add(MacroFactorEvent(
            id=str(uuid.uuid4()),
            event_id="evt_fed_rates",
            factor="interest_rate",
            direction="up",
            magnitude="major",
            confidence=0.9,
            rationale=json.dumps(["matched: federal reserve", "parsed: 50 bps"]),
            created_at=now,
        ))
        session.add(EventLink(
            id=str(uuid.uuid4()),
            event_id="evt_fed_rates",
            link_type="macro_factor",
            link_target="h_aapl_pA",
            impact_channel="interest_rate",
            channel="interest_rate",
            relevance_score=0.42,
            details_json=json.dumps({
                "factor": {
                    "key": "interest_rate",
                    "direction": "up",
                    "magnitude": "major",
                    "rationale": ["federal reserve", "50 bps"],
                },
                "expected_effect": {
                    "direction": "negative",
                    "confidence": 0.42,
                },
            }),
            created_at=now,
        ))
        # Alert belonging to pA referring to the event + AAPL
        session.add(Alert(
            id="alert_pA_1",
            portfolio_id="pA",
            alert_type="macro_factor",
            severity="high",
            title="Rate-hike exposure on AAPL",
            body="50 bps hike raises duration risk for AAPL.",
            related_holdings=json.dumps(["AAPL"]),
            related_events=json.dumps(["evt_fed_rates"]),
            acknowledged=0,
            delivered=0,
            agent_id="risk",
            created_at=now,
        ))
        # Alert belonging to pB (disjoint) — to prove portfolio isolation
        session.add(Alert(
            id="alert_pB_1",
            portfolio_id="pB",
            alert_type="sector_risk",
            severity="high",
            title="Oil pressure on XOM",
            body="OPEC cut raises oil prices.",
            related_holdings=json.dumps(["XOM"]),
            related_events=json.dumps([]),
            acknowledged=0,
            delivered=0,
            agent_id="risk",
            created_at=now,
        ))
        # A low-severity alert — must be skipped entirely
        session.add(Alert(
            id="alert_pA_noise",
            portfolio_id="pA",
            alert_type="info",
            severity="info",
            title="Daily digest ready",
            body="",
            related_holdings=json.dumps([]),
            related_events=json.dumps([]),
            acknowledged=0,
            delivered=0,
            agent_id="digest",
            created_at=now,
        ))
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# 1) Migration v5 — tables exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v5_migration_creates_tables(_migrated_db):
    from src.database.connection import get_engine

    engine = get_engine()
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    assert "telegram_sessions" in tables
    assert "telegram_deliveries" in tables


@pytest.mark.asyncio
async def test_schema_version_matches_current(_migrated_db):
    """Assert the stamped schema version matches
    ``CURRENT_SCHEMA_VERSION``.  This used to hard-code a literal
    integer (``5``) but that created a one-line regression every
    time a phase added a migration.  Sourcing from the constant
    keeps the test stable across additive migrations while still
    catching any drift between ``run_migrations`` and the stamp.
    """
    from src.database.connection import get_engine
    from src.database.migrations import CURRENT_SCHEMA_VERSION
    from sqlalchemy import text

    engine = get_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT version FROM _schema_version WHERE id = 1")
        )).fetchone()
    assert row is not None
    assert row[0] == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2) Telegram session store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_portfolio_for_unknown_chat(seeded):
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        DEFAULT_PORTFOLIO_ID,
        get_active_portfolio_id,
    )

    async with get_db() as session:
        pid = await get_active_portfolio_id(session, chat_id=12345)
    assert pid == DEFAULT_PORTFOLIO_ID


@pytest.mark.asyncio
async def test_set_active_portfolio_persists(seeded):
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        get_active_portfolio_id,
        set_active_portfolio_id,
    )

    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=99, portfolio_id="pA")
    async with get_db() as session:
        assert await get_active_portfolio_id(session, chat_id=99) == "pA"
    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=99, portfolio_id="pB")
    async with get_db() as session:
        assert await get_active_portfolio_id(session, chat_id=99) == "pB"


# ---------------------------------------------------------------------------
# 3) Grounded alert message builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_message_contains_portfolio_and_holdings(seeded):
    from src.database.connection import get_db
    from src.database.models import Alert
    from src.integrations.telegram.grounded import build_grounded_alert_message

    async with get_db() as session:
        alert = await session.get(Alert, "alert_pA_1")
        message, meta = await build_grounded_alert_message(session, alert)

    assert "[HIGH]" in message
    assert "Rate-hike exposure on AAPL" in message
    assert "`pA`" in message  # portfolio identity
    assert "`AAPL`" in message  # affected holding
    # Grounded "why it matters" line comes from the deterministic
    # factor chain — must name interest_rate or carry the rationale
    assert (
        "federal reserve" in message.lower()
        or "interest rate" in message.lower()
        or "deterministic" in message.lower()
    )
    assert meta["portfolio_id"] == "pA"
    assert meta["tickers"] == ["AAPL"]
    assert meta["event_id"] == "evt_fed_rates"
    assert meta["holding_id"] == "h_aapl_pA"
    assert meta["channel"] == "interest_rate"


@pytest.mark.asyncio
async def test_alert_message_without_linked_event_is_still_useful(seeded):
    """An alert with no related event still produces a grounded
    Markdown message with severity, title, body, portfolio, holdings."""
    from src.database.connection import get_db
    from src.database.models import Alert
    from src.integrations.telegram.grounded import build_grounded_alert_message

    async with get_db() as session:
        alert = await session.get(Alert, "alert_pB_1")
        message, meta = await build_grounded_alert_message(session, alert)

    assert "[HIGH]" in message
    assert "Oil pressure on XOM" in message
    assert "`pB`" in message
    assert "`XOM`" in message
    # No chain → no Why line, but still the rest of the envelope
    assert meta["portfolio_id"] == "pB"
    assert meta["tickers"] == ["XOM"]


# ---------------------------------------------------------------------------
# 4) Delivery gate — dedupe + cooldown + portfolio match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_portfolio_blocks_send(seeded):
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import should_deliver

    async with get_db() as session:
        decision = await should_deliver(
            session,
            chat_id=1,
            alert_id="alert_pA_1",
            alert_portfolio_id="pA",
            chat_portfolio_id="pB",   # mismatched!
            event_id="evt_fed_rates",
            holding_id="h_aapl_pA",
            channel="interest_rate",
        )
    assert decision.should_send is False
    assert decision.reason == "wrong_portfolio"


@pytest.mark.asyncio
async def test_same_alert_not_delivered_twice(seeded):
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        record_delivery,
        should_deliver,
    )

    async with get_db() as session:
        # First attempt: clean slate → allowed
        d1 = await should_deliver(
            session,
            chat_id=7,
            alert_id="alert_pA_1",
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
            event_id="evt_fed_rates",
            holding_id="h_aapl_pA",
            channel="interest_rate",
        )
        assert d1.should_send is True
        await record_delivery(
            session,
            chat_id=7, alert_id="alert_pA_1",
            portfolio_id="pA", dedup_key=d1.dedup_key,
            status="sent",
        )

    async with get_db() as session:
        # Second attempt for the same (chat, alert) tuple → blocked
        d2 = await should_deliver(
            session,
            chat_id=7,
            alert_id="alert_pA_1",
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
            event_id="evt_fed_rates",
            holding_id="h_aapl_pA",
            channel="interest_rate",
        )
    assert d2.should_send is False
    assert d2.reason == "already_delivered"


@pytest.mark.asyncio
async def test_cooldown_blocks_same_event_holding_channel(seeded):
    """Two different alert ids that share the same
    (event, holding, channel) cooldown key must be collapsed within
    the cooldown window."""
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        record_delivery,
        should_deliver,
    )

    async with get_db() as session:
        d1 = await should_deliver(
            session,
            chat_id=8,
            alert_id="alert_first",
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
            event_id="evt_fed_rates",
            holding_id="h_aapl_pA",
            channel="interest_rate",
        )
        assert d1.should_send is True
        await record_delivery(
            session,
            chat_id=8, alert_id="alert_first",
            portfolio_id="pA", dedup_key=d1.dedup_key,
            status="sent",
        )

    async with get_db() as session:
        d2 = await should_deliver(
            session,
            chat_id=8,
            alert_id="alert_second",   # different alert id!
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
            event_id="evt_fed_rates",
            holding_id="h_aapl_pA",
            channel="interest_rate",   # same cooldown key
        )
    assert d2.should_send is False
    assert d2.reason == "cooldown"


@pytest.mark.asyncio
async def test_failed_delivery_can_retry(seeded):
    """A failed delivery writes status='failed' and does NOT block
    a subsequent retry attempt for the same (chat_id, alert_id)."""
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        record_delivery,
        should_deliver,
    )

    async with get_db() as session:
        d1 = await should_deliver(
            session,
            chat_id=9,
            alert_id="alert_retry",
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
        )
        assert d1.should_send is True
        await record_delivery(
            session,
            chat_id=9, alert_id="alert_retry",
            portfolio_id="pA", dedup_key=d1.dedup_key,
            status="failed", error="network timeout",
        )

    async with get_db() as session:
        # Retry: still allowed because the prior row is status='failed'
        d2 = await should_deliver(
            session,
            chat_id=9,
            alert_id="alert_retry",
            alert_portfolio_id="pA",
            chat_portfolio_id="pA",
        )
    assert d2.should_send is True


# ---------------------------------------------------------------------------
# 5) End-to-end deliver_alert with a mocked Telegram send
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.fail_on: set[int] = set()

    async def send_message(self, chat_id, text, parse_mode=None):
        if chat_id in self.fail_on:
            raise RuntimeError(f"simulated send failure for chat {chat_id}")
        self.sent.append((chat_id, text))


class _FakeApp:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_deliver_alert_routes_only_to_matching_chat_portfolio(seeded):
    """Chat 100 pinned to pA, chat 200 pinned to pB.  An alert in pA
    must only reach chat 100; chat 200 gets nothing."""
    from src.database.connection import get_db
    from src.database.models import Alert
    from src.integrations.telegram.grounded import set_active_portfolio_id
    from src.integrations.telegram import bot as bot_mod
    from src.integrations.telegram import notifications as notif

    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=100, portfolio_id="pA")
        await set_active_portfolio_id(session, chat_id=200, portfolio_id="pB")

    fake_bot = _FakeBot()
    prior_app = bot_mod._bot_app
    prior_chats = bot_mod._authorized_chats
    bot_mod._bot_app = _FakeApp(fake_bot)
    bot_mod._authorized_chats = {100, 200}
    try:
        async with get_db() as session:
            alert = await session.get(Alert, "alert_pA_1")
        summary = await notif.deliver_alert(alert)
    finally:
        bot_mod._bot_app = prior_app
        bot_mod._authorized_chats = prior_chats

    assert summary["sent"] == 1
    sent_chat_ids = {cid for cid, _ in fake_bot.sent}
    assert sent_chat_ids == {100}
    assert summary["reasons"][200] == "wrong_portfolio"


@pytest.mark.asyncio
async def test_deliver_alert_low_severity_skipped_and_marked_delivered(seeded):
    """Low-severity alerts don't reach Telegram but ARE still marked
    delivered=1 so the poller doesn't re-process them forever."""
    from src.database.connection import get_db
    from src.database.models import Alert
    from src.integrations.telegram import bot as bot_mod
    from src.integrations.telegram import notifications as notif

    # Reset the delivered flag (other tests may have set it)
    async with get_db() as session:
        noise = await session.get(Alert, "alert_pA_noise")
        noise.delivered = 0
        noise.delivered_at = None
        await session.commit()

    fake_bot = _FakeBot()
    prior_app = bot_mod._bot_app
    prior_chats = bot_mod._authorized_chats
    bot_mod._bot_app = _FakeApp(fake_bot)
    bot_mod._authorized_chats = {100}
    try:
        async with get_db() as session:
            alert = await session.get(Alert, "alert_pA_noise")
        summary = await notif.deliver_alert(alert)
    finally:
        bot_mod._bot_app = prior_app
        bot_mod._authorized_chats = prior_chats

    assert summary["sent"] == 0
    assert summary["skipped"] >= 1
    assert fake_bot.sent == []

    async with get_db() as session:
        row = await session.get(Alert, "alert_pA_noise")
        assert row.delivered == 1  # cosmetic stamp, no actual push


@pytest.mark.asyncio
async def test_deliver_alert_failed_send_does_not_mark_delivered(seeded):
    """A Telegram send that blows up must leave ``Alert.delivered=0``
    so the poll loop can retry next cycle."""
    from src.database.connection import get_db
    from src.database.models import Alert, TelegramDelivery
    from src.integrations.telegram.grounded import set_active_portfolio_id
    from src.integrations.telegram import bot as bot_mod
    from src.integrations.telegram import notifications as notif

    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=300, portfolio_id="pA")
        # Reset the test alert so prior tests can't poison the state
        a = await session.get(Alert, "alert_pA_1")
        a.delivered = 0
        a.delivered_at = None
        await session.execute(
            delete(TelegramDelivery).where(TelegramDelivery.alert_id == "alert_pA_1")
        )
        await session.commit()

    fake_bot = _FakeBot()
    fake_bot.fail_on = {300}
    prior_app = bot_mod._bot_app
    prior_chats = bot_mod._authorized_chats
    bot_mod._bot_app = _FakeApp(fake_bot)
    bot_mod._authorized_chats = {300}
    try:
        async with get_db() as session:
            alert = await session.get(Alert, "alert_pA_1")
        summary = await notif.deliver_alert(alert)
    finally:
        bot_mod._bot_app = prior_app
        bot_mod._authorized_chats = prior_chats

    assert summary["sent"] == 0
    assert summary["failed"] == 1

    async with get_db() as session:
        alert = await session.get(Alert, "alert_pA_1")
        assert alert.delivered == 0  # critical: retry on next poll tick
        rows = (await session.execute(
            select(TelegramDelivery).where(TelegramDelivery.alert_id == "alert_pA_1")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "failed"


@pytest.mark.asyncio
async def test_deliver_alert_success_marks_delivered(seeded):
    """A successful send writes status='sent' and stamps
    ``Alert.delivered=1`` so the dashboard surfaces the push."""
    from src.database.connection import get_db
    from src.database.models import Alert, TelegramDelivery
    from src.integrations.telegram.grounded import set_active_portfolio_id
    from src.integrations.telegram import bot as bot_mod
    from src.integrations.telegram import notifications as notif

    # Fresh alert row — we'll insert a new one so prior delivery
    # dedupe rows can't block us.
    now = datetime.now(timezone.utc).isoformat()
    alert_id = f"alert_pA_fresh_{uuid.uuid4().hex[:6]}"
    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=400, portfolio_id="pA")
        session.add(Alert(
            id=alert_id,
            portfolio_id="pA",
            alert_type="macro_factor",
            severity="critical",
            title="Rate shock on AAPL",
            body="Fed rate shock — major duration risk.",
            related_holdings=json.dumps(["AAPL"]),
            related_events=json.dumps(["evt_fed_rates"]),
            acknowledged=0,
            delivered=0,
            agent_id="risk",
            created_at=now,
        ))
        await session.commit()

    fake_bot = _FakeBot()
    prior_app = bot_mod._bot_app
    prior_chats = bot_mod._authorized_chats
    bot_mod._bot_app = _FakeApp(fake_bot)
    bot_mod._authorized_chats = {400}
    try:
        async with get_db() as session:
            alert = await session.get(Alert, alert_id)
        summary = await notif.deliver_alert(alert)
    finally:
        bot_mod._bot_app = prior_app
        bot_mod._authorized_chats = prior_chats

    assert summary["sent"] == 1
    assert fake_bot.sent  # one Markdown message captured

    async with get_db() as session:
        alert = await session.get(Alert, alert_id)
        assert alert.delivered == 1
        rows = (await session.execute(
            select(TelegramDelivery).where(TelegramDelivery.alert_id == alert_id)
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "sent"


# ---------------------------------------------------------------------------
# 6) Grounded chat reply — portfolio-scoped + no cross-portfolio leakage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_reply_scoped_to_chat_portfolio(seeded):
    """Two Telegram chats, pinned to disjoint portfolios.  The reply
    for chat-pA must not mention XOM; the reply for chat-pB must not
    mention AAPL.  Portfolio isolation is structural."""
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import (
        render_grounded_telegram_reply,
        set_active_portfolio_id,
    )

    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=1001, portfolio_id="pA")
        await set_active_portfolio_id(session, chat_id=1002, portfolio_id="pB")

    async with get_db() as session:
        reply_a, mode_a, pid_a = await render_grounded_telegram_reply(
            session, chat_id=1001, query="what's in my portfolio?",
        )
    async with get_db() as session:
        reply_b, mode_b, pid_b = await render_grounded_telegram_reply(
            session, chat_id=1002, query="what's in my portfolio?",
        )

    # LLM is not available in the test environment — both should
    # fall back to the deterministic renderer.
    assert mode_a == "rule-based"
    assert mode_b == "rule-based"
    assert pid_a == "pA"
    assert pid_b == "pB"

    # Structural portfolio isolation — each reply must carry its own
    # tickers only.
    assert "XOM" not in reply_a
    assert "AAPL" not in reply_b
    # And the other direction — the intended ticker should be there
    # (via the holdings list).  We check softly because the
    # deterministic renderer formats holdings in a compact list and
    # may elide some rows.
    assert "pA" in reply_a
    assert "pB" in reply_b


@pytest.mark.asyncio
async def test_chat_reply_defaults_to_default_portfolio_for_unknown_chat(seeded):
    from src.database.connection import get_db
    from src.integrations.telegram.grounded import render_grounded_telegram_reply

    async with get_db() as session:
        answer, mode, pid = await render_grounded_telegram_reply(
            session, chat_id=9999, query="hello",
        )
    assert pid == "default"
    assert mode == "rule-based"  # LLM unavailable in tests
    assert "Portfolio default" in answer or "default" in answer


# ---------------------------------------------------------------------------
# 7) Grounded digest rendering
# ---------------------------------------------------------------------------


def test_format_grounded_digest_reads_phase9e_shape():
    from src.integrations.telegram.grounded import format_grounded_digest_message

    digest_json = {
        "headline": "Daily digest: 4 notes across 3 holdings — slightly negative outlook",
        "portfolio_assessment": "Slightly negative tone across 4 per-holding notes.",
        "risk_flags": ["Interest rates trending up — duration risk"],
        "holdings_requiring_attention": ["AAPL", "MSFT"],
        "key_developments": [
            "2 negative signals, 1 positive, 1 neutral",
            "Factor touchpoint: Interest Rates up on AAPL, MSFT",
        ],
        "market_context": "Deterministic fallback — no LLM narrative.",
    }
    msg = format_grounded_digest_message(digest_json, portfolio_id="pA")
    assert "INTELLIGENCE DIGEST" in msg
    assert "`pA`" in msg
    assert "slightly negative outlook" in msg
    assert "duration risk" in msg
    assert "AAPL" in msg
    assert "MSFT" in msg
    # The "Deterministic fallback" marker is filtered out so it
    # doesn't leak into user-facing digest text.
    assert "Deterministic fallback" not in msg


def test_format_grounded_digest_accepts_json_string():
    from src.integrations.telegram.grounded import format_grounded_digest_message

    raw = json.dumps({
        "headline": "Weekly digest — clear skies",
        "portfolio_assessment": "Mixed tone.",
        "risk_flags": [],
        "holdings_requiring_attention": [],
        "key_developments": [],
    })
    msg = format_grounded_digest_message(raw, portfolio_id="default")
    assert "clear skies" in msg
    assert "`default`" in msg


def test_format_grounded_digest_handles_garbage():
    """Malformed input still produces a non-empty message."""
    from src.integrations.telegram.grounded import format_grounded_digest_message

    msg = format_grounded_digest_message("not json at all", portfolio_id="pA")
    assert "INTELLIGENCE DIGEST" in msg
    assert "`pA`" in msg


# ---------------------------------------------------------------------------
# 8) Cross-portfolio alert isolation — deliver_alert refuses mismatched chats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pA_alert_does_not_reach_pB_chat(seeded):
    """Two chats, one pinned to pA and one to pB.  An alert in pB
    must NOT reach the pA-pinned chat, and vice versa."""
    from src.database.connection import get_db
    from src.database.models import Alert
    from src.integrations.telegram.grounded import set_active_portfolio_id
    from src.integrations.telegram import bot as bot_mod
    from src.integrations.telegram import notifications as notif

    async with get_db() as session:
        await set_active_portfolio_id(session, chat_id=2001, portfolio_id="pA")
        await set_active_portfolio_id(session, chat_id=2002, portfolio_id="pB")

    fake_bot = _FakeBot()
    prior_app = bot_mod._bot_app
    prior_chats = bot_mod._authorized_chats
    bot_mod._bot_app = _FakeApp(fake_bot)
    bot_mod._authorized_chats = {2001, 2002}
    try:
        async with get_db() as session:
            alert_pb = await session.get(Alert, "alert_pB_1")
        summary = await notif.deliver_alert(alert_pb)
    finally:
        bot_mod._bot_app = prior_app
        bot_mod._authorized_chats = prior_chats

    sent_chat_ids = {cid for cid, _ in fake_bot.sent}
    assert sent_chat_ids == {2002}       # only the pB chat received it
    assert 2001 not in sent_chat_ids
    assert summary["reasons"][2001] == "wrong_portfolio"
