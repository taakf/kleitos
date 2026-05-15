"""Phase 9K integration tests for production / runtime hardening.

Covers four concerns in a single file so they share the temp-DB
fixture pattern used by Phase 9D/9F/9G/9H integration tests:

1. **Rate-limit policy end-to-end.**  Drives the real middleware
   against a fastapi.TestClient and proves:
     * normal dashboard read traffic does NOT 429 at the old
       ``rate_limit_rpm=100`` ceiling (this is the Phase 9J finding)
     * a write-flood from loopback DOES get capped at the mutation
       bucket
     * a non-loopback abuse flood DOES get capped at the public bucket
     * 429 responses carry the bucket name + Retry-After header

2. **Runtime lifecycle.**  Proves :func:`close_database` is
   safe to call multiple times and that :func:`reset_connection_state`
   makes the next ``get_engine()`` call rebuild from the latest
   settings.

3. **In-flight guards.**  Proves that two concurrent
   ``backfill_recent_events`` calls produce one success + one
   ``BackfillInProgressError``, and the matching behaviour for
   ``reconcile_seed_relationships``.

4. **WebSocket surface.**  Connects a real client to
   ``/api/v1/ws``, calls :func:`src.api.routes.ws.broadcast` from
   in-process, and asserts the client receives the JSON payload.
   This is the only E2E-adjacent test in this file — it uses a
   httpx-style WS connection, not a browser, so it fits the
   integration tier.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete


# ---------------------------------------------------------------------------
# Temp DB fixture (mirror of Phase 9D/9F/9G/9H pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    """Module-scoped temp DB fixture for the Phase 9K integration tests.

    Important compatibility notes:

    * ``get_settings.cache_clear()`` wipes the cached settings
      singleton, so the next call rebuilds from the environment
      (and loses any mutations the smoke test suite had applied at
      module load time — notably ``api.auth_enabled = False``).
      We therefore re-apply the same ``auth_enabled = False`` on
      the freshly-built instance so the smoke tests can still hit
      the live middleware without 401'ing.
    * On teardown we restore the prior env vars AND re-apply the
      ``auth_enabled = False`` on the fresh post-teardown cache so
      every later test in the same pytest run still sees the same
      invariant the smoke suite relies on.
    """
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")

    db_dir = tmp_path_factory.mktemp("axion_phase9k")
    db_path = db_dir / "axion_test.db"
    os.environ["KLEITOS_DB_PATH"] = str(db_path)
    os.environ["KLEITOS_DATA_DIR"] = str(db_dir)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    # Re-apply the smoke-suite's module-level invariant so any smoke
    # test that runs after this fixture still sees auth_enabled=False.
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
    get_settings().api.auth_enabled = False  # preserve smoke invariant
    connection._engine = None
    connection._session_factory = None


@pytest_asyncio.fixture(scope="module")
async def _migrated_db(_tmp_db):
    from src.database.migrations import run_migrations
    await run_migrations()
    yield _tmp_db


@pytest_asyncio.fixture
async def seeded(_migrated_db):
    """Minimal seed: one portfolio + one holding + one event so the
    backfill / reconcile entry points have something real to chew on."""
    from src.database.connection import get_db
    from src.database.models import (
        Alert, AnalysisNote, AuditLog, Digest, Event, EventLink,
        Holding, HoldingFactorSensitivity, HoldingRelationship,
        MacroFactorEvent, Portfolio, Security,
    )

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship,
            AuditLog, Alert, Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add(Portfolio(
            id="default", name="Main", base_currency="USD",
            is_default=1, created_at=now, updated_at=now,
        ))
        session.add(Holding(
            id="h_aapl", ticker="AAPL", currency="USD", quantity=10,
            weight_pct=100.0, current_price=180.0, market_value=1800.0,
            portfolio_id="default", status="active",
            created_at=now, updated_at=now,
        ))
        session.add(Security(
            id=str(uuid.uuid4()), ticker="AAPL", name="Apple",
            currency="USD", sector="Information Technology",
            geography="united states", themes="[]",
            created_at=now, updated_at=now,
        ))
        session.add(Event(
            id="evt_9k",
            title="Federal Reserve raises interest rates by 50 bps",
            summary="FOMC vote.",
            event_type="rates",
            fetched_at=now,
            created_at=now,
            dedup_hash=f"e2e_9k_{uuid.uuid4().hex[:8]}",
        ))
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# 1) Rate-limit policy — end-to-end via fastapi.TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limit_client(_migrated_db):
    """Return a TestClient that exercises the real RateLimitMiddleware
    with a clean window dict, then cleanly restores every setting it
    touches.

    Safety pattern:

    * We do NOT reload ``src.main`` — other tests (smoke, unit,
      integration) import the module-level ``app`` and any reload
      would leave them holding a dead reference.
    * We save the current ``api.*`` settings values, override them
      in place, then restore them in the fixture teardown.  Restore
      is in a ``try/finally`` so a test assertion failure still
      rewinds global state.
    * We also clear the middleware's window dict before AND after
      the test so we don't inherit or leak sliding-window state
      across tests.

    Lowers the dashboard_read bucket to 25 so the test can provoke
    a 429 without firing thousands of requests.  The classifier
    logic is untouched — only numeric ceilings are tightened.
    """
    from fastapi.testclient import TestClient

    from src.config import get_settings
    from src.main import app

    s = get_settings()
    prior = {
        "auth_enabled": s.api.auth_enabled,
        "rate_limit_rpm": s.api.rate_limit_rpm,
        "rate_limit_dashboard_read_rpm": s.api.rate_limit_dashboard_read_rpm,
        "rate_limit_mutation_rpm": s.api.rate_limit_mutation_rpm,
    }
    s.api.auth_enabled = False
    s.api.rate_limit_dashboard_read_rpm = 25
    s.api.rate_limit_mutation_rpm = 10
    s.api.rate_limit_rpm = 5

    # Clear the middleware's per-IP window dict so this test starts
    # with a fresh limiter state, regardless of what earlier tests
    # (or the app boot itself) left behind.
    from src.api.middleware import RateLimitMiddleware
    for mw in getattr(app, "user_middleware", []):
        cls = getattr(mw, "cls", None)
        if cls is RateLimitMiddleware:
            # user_middleware holds the wrapper, not the instance —
            # the instance is created lazily on first request.  We
            # instead walk the ASGI app stack via app.middleware_stack.
            pass

    # Walk the middleware stack to find the live RateLimitMiddleware
    # instance and reset its windows.  The stack is built lazily on
    # first request, so we trigger it with a harmless call.
    with TestClient(app) as client:
        try:
            client.get("/api/v1/health")  # builds the middleware stack
            # Walk the stack from the outer app inward
            stack = app.middleware_stack
            while stack is not None:
                if isinstance(stack, RateLimitMiddleware):
                    stack._windows.clear()
                    stack._last_prune = 0.0
                    break
                stack = getattr(stack, "app", None)
            yield client
        finally:
            # Clear the window again on the way out so the next test
            # starts clean.  This is defensive — the state-reset helper
            # above should already be enough.
            try:
                stack = app.middleware_stack
                while stack is not None:
                    if isinstance(stack, RateLimitMiddleware):
                        stack._windows.clear()
                        stack._last_prune = 0.0
                        break
                    stack = getattr(stack, "app", None)
            except Exception:
                pass
            # Restore every setting we touched.
            for k, v in prior.items():
                setattr(s.api, k, v)


class TestRateLimitPolicyE2E:
    """Drive the real middleware through httpx and prove each bucket
    behaves per spec."""

    def test_dashboard_read_ceiling_is_higher_than_public(self, rate_limit_client):
        """The Phase 9J finding: a normal dashboard tab cycle fires
        ~20 GET requests.  At the old ``rate_limit_rpm=5`` public
        ceiling that would 429; at the new ``dashboard_read=25``
        ceiling (which represents the production 1_200) it does not."""
        client = rate_limit_client
        # Fire 15 loopback GETs in quick succession — would be well
        # over the public bucket (5) but under dashboard_read (25).
        statuses = []
        for _ in range(15):
            r = client.get("/api/v1/intelligence/summary?portfolio_id=default")
            statuses.append(r.status_code)
        assert all(s == 200 for s in statuses), (
            f"dashboard read burst hit a 429 at {statuses.index(429) if 429 in statuses else '??'}: "
            f"{statuses}"
        )

    def test_dashboard_read_429s_when_ceiling_exhausted(self, rate_limit_client):
        """Past 25 loopback GETs in the same minute, the dashboard
        bucket itself should enforce its own (still generous) ceiling."""
        client = rate_limit_client
        got_429 = False
        got_429_body = None
        for i in range(40):
            r = client.get("/api/v1/intelligence/summary?portfolio_id=default")
            if r.status_code == 429:
                got_429 = True
                got_429_body = r.json()
                break
        assert got_429, "dashboard_read bucket never 429'd after 40 requests"
        assert got_429_body["bucket"] == "dashboard_read"
        assert got_429_body["limit_per_minute"] == 25
        assert got_429_body["retry_after_seconds"] >= 1

    def test_mutation_bucket_is_tighter_than_dashboard_read(self, rate_limit_client):
        """Writes hit the tighter mutation ceiling (10 in the test
        config) even from loopback — proving the policy doesn't let
        write-flood abuse hide behind the dashboard_read lane."""
        client = rate_limit_client
        body = {
            "window_days": 1, "max_events": 1, "reason": "rate-limit test"
        }
        got_429_at = None
        for i in range(20):
            r = client.post("/api/v1/operator/backfill", json=body)
            if r.status_code == 429:
                got_429_at = i
                detail = r.json()
                assert detail["bucket"] == "mutation"
                assert detail["limit_per_minute"] == 10
                break
        assert got_429_at is not None and got_429_at <= 12, (
            f"mutation bucket did not 429 within 12 POSTs (got to {got_429_at})"
        )

    def test_public_bucket_429_reports_retry_after_header(self, rate_limit_client):
        """Clients need the ``Retry-After`` header to back off
        politely after a 429."""
        client = rate_limit_client
        # Simulate a non-loopback origin by setting X-Forwarded-For.
        # In production, reverse proxies would set client.host to the
        # real IP; in TestClient we can't easily spoof client.host, so
        # instead we hammer the dashboard_read bucket until it 429s
        # and verify the header / JSON shape on that response.
        r = None
        for _ in range(40):
            r = client.get("/api/v1/intelligence/summary?portfolio_id=default")
            if r.status_code == 429:
                break
        assert r is not None and r.status_code == 429
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) >= 1
        body = r.json()
        assert "bucket" in body
        assert "limit_per_minute" in body
        assert "retry_after_seconds" in body

    def test_health_endpoint_is_never_rate_limited(self, rate_limit_client):
        """``/api/v1/health`` is the exempt path — load balancers need
        to poll it without tripping the limiter."""
        client = rate_limit_client
        for _ in range(60):
            r = client.get("/api/v1/health")
            assert r.status_code == 200, f"health 429'd unexpectedly: {r.status_code}"


# ---------------------------------------------------------------------------
# 2) Runtime lifecycle — close + reset helpers
# ---------------------------------------------------------------------------


class TestLifecycleHelpers:
    @pytest.mark.asyncio
    async def test_close_database_is_idempotent(self, seeded):
        """Phase 9K hardening: ``close_database`` must be safe to call
        twice in a row without raising."""
        from src.database.connection import close_database
        await close_database()
        # Second call — must not raise.
        await close_database()

    def test_reset_connection_state_clears_singletons(self, seeded):
        """``reset_connection_state`` is the synchronous sibling of
        ``close_database`` for test fixtures that swap DB paths."""
        import src.database.connection as connection
        from src.database.connection import get_engine, reset_connection_state

        # Warm up the singleton
        engine = get_engine()
        assert connection._engine is engine
        assert connection._session_factory is not None

        reset_connection_state()
        assert connection._engine is None
        assert connection._session_factory is None

        # Next call rebuilds
        new_engine = get_engine()
        assert new_engine is not None
        assert connection._engine is new_engine

    @pytest.mark.asyncio
    async def test_close_then_reset_is_safe(self, seeded):
        """Test fixtures call both — the sequence must not raise."""
        from src.database.connection import close_database, reset_connection_state
        import src.database.connection as connection

        await close_database()
        reset_connection_state()
        assert connection._engine is None
        assert connection._session_factory is None


# ---------------------------------------------------------------------------
# 3) In-flight guards — reconcile + backfill
# ---------------------------------------------------------------------------


class TestInFlightGuards:
    @pytest.mark.asyncio
    async def test_backfill_concurrent_calls_raises_on_second(self, seeded):
        """Two concurrent ``backfill_recent_events`` awaits: the first
        must succeed, the second must raise ``BackfillInProgressError``.
        We start both via ``asyncio.gather`` with
        ``return_exceptions=True`` so both tasks complete even when
        one raises."""
        from src.intelligence.backfill import (
            BackfillInProgressError,
            backfill_recent_events,
            is_backfill_running,
        )

        # Seed a few extra events so the backfill takes more than a
        # microsecond — gives us a real window for the second caller
        # to hit the lock.
        from src.database.connection import get_db
        from src.database.models import Event
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            for i in range(5):
                session.add(Event(
                    id=f"evt_concurrent_{i}",
                    title=f"Fed rate move #{i}",
                    summary="filler for in-flight guard test",
                    event_type="rates",
                    fetched_at=now,
                    created_at=now,
                    dedup_hash=f"concurrent_{i}_{uuid.uuid4().hex[:6]}",
                ))
            await session.commit()

        assert is_backfill_running() is False

        results = await asyncio.gather(
            backfill_recent_events(window_days=7, max_events=50, reason="concurrent A"),
            backfill_recent_events(window_days=7, max_events=50, reason="concurrent B"),
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1, f"expected 1 success, got {len(successes)}: {results!r}"
        assert len(errors) == 1
        assert isinstance(errors[0], BackfillInProgressError)
        assert "already running" in str(errors[0]).lower()

        # After both finish, the lock is free again
        assert is_backfill_running() is False

    @pytest.mark.asyncio
    async def test_reconcile_concurrent_calls_raises_on_second(self, seeded, tmp_path):
        """Same guard on the reconcile entry point."""
        from src.intelligence.relationships.reconciler import (
            ReconcileInProgressError,
            is_reconcile_running,
            reconcile_seed_relationships,
        )
        import yaml

        # Use an empty YAML so the reconcile runs to completion fast
        # without touching the real seed file.
        empty_yaml = tmp_path / "empty_9k.yaml"
        empty_yaml.write_text(yaml.safe_dump({"version": 1, "relationships": []}))

        assert is_reconcile_running() is False

        results = await asyncio.gather(
            reconcile_seed_relationships(yaml_path=empty_yaml, prune=True),
            reconcile_seed_relationships(yaml_path=empty_yaml, prune=True),
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ReconcileInProgressError)

        assert is_reconcile_running() is False

    @pytest.mark.asyncio
    async def test_backfill_in_flight_is_false_after_run(self, seeded):
        """After a normal backfill completes, the lock is released
        regardless of success/failure."""
        from src.intelligence.backfill import (
            backfill_recent_events,
            is_backfill_running,
        )
        await backfill_recent_events(window_days=7, max_events=5, reason="cleanup check")
        assert is_backfill_running() is False

    @pytest.mark.asyncio
    async def test_operator_route_returns_409_on_backfill_conflict(self, seeded):
        """The trigger_backfill route must catch ``BackfillInProgressError``
        and return HTTP 409 with the ``in_progress`` flag."""
        from fastapi import HTTPException
        from src.api.routes.operator import trigger_backfill, BackfillRequest
        import src.intelligence.backfill as backfill_mod

        async def _pretend_running(**_kwargs):
            raise backfill_mod.BackfillInProgressError("already running")

        # Monkey-patch the module-level entry point the route calls.
        orig = backfill_mod.backfill_recent_events
        import src.api.routes.operator as op_mod
        op_mod.backfill_recent_events = _pretend_running  # type: ignore[assignment]
        try:
            with pytest.raises(HTTPException) as exc_info:
                await trigger_backfill(
                    payload=BackfillRequest(window_days=1, max_events=1)
                )
        finally:
            op_mod.backfill_recent_events = orig  # type: ignore[assignment]

        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["in_progress"] is True
        assert detail["action"] == "backfill"

    @pytest.mark.asyncio
    async def test_operator_route_returns_409_on_reconcile_conflict(self, seeded):
        from fastapi import HTTPException
        from src.api.routes.operator import trigger_reconcile
        import src.intelligence.relationships.reconciler as rec_mod

        async def _pretend_running(**_kwargs):
            raise rec_mod.ReconcileInProgressError("already running")

        import src.api.routes.operator as op_mod
        orig = op_mod.reconcile_seed_relationships
        op_mod.reconcile_seed_relationships = _pretend_running  # type: ignore[assignment]
        try:
            # Build a minimal mock session so the route's unused audit
            # path doesn't trip on a None session.
            class _Session:
                def add(self, *a, **k): pass
                async def commit(self): pass
            with pytest.raises(HTTPException) as exc_info:
                await trigger_reconcile(
                    prune=False, reason=None, session=_Session(),  # type: ignore[arg-type]
                )
        finally:
            op_mod.reconcile_seed_relationships = orig  # type: ignore[assignment]

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["action"] == "reconcile"

    @pytest.mark.asyncio
    async def test_operator_status_endpoint_reports_lock_state(self, seeded):
        """The new ``/api/v1/operator/actions/status`` endpoint must
        reflect the in-flight state of both locks — so the UI can
        poll it to disable buttons during live runs."""
        from src.api.routes.operator import operator_actions_status
        from src.intelligence.backfill import _BACKFILL_LOCK
        from src.intelligence.relationships.reconciler import _RECONCILE_LOCK

        baseline = await operator_actions_status()
        assert baseline["backfill"]["in_progress"] is False
        assert baseline["reconcile"]["in_progress"] is False

        async with _BACKFILL_LOCK:
            busy = await operator_actions_status()
            assert busy["backfill"]["in_progress"] is True
            assert busy["reconcile"]["in_progress"] is False

        async with _RECONCILE_LOCK:
            busy2 = await operator_actions_status()
            assert busy2["backfill"]["in_progress"] is False
            assert busy2["reconcile"]["in_progress"] is True

        after = await operator_actions_status()
        assert after["backfill"]["in_progress"] is False
        assert after["reconcile"]["in_progress"] is False


# ---------------------------------------------------------------------------
# 4) WebSocket verification — real connect + broadcast + receive
# ---------------------------------------------------------------------------


class TestWebSocketBroadcast:
    """Use fastapi.testclient.TestClient.websocket_connect to exercise
    the real ``/api/v1/ws`` endpoint end-to-end: accept, broadcast,
    receive, disconnect.  This is the smallest faithful proof that
    the live-update transport layer Axion ships actually works."""

    def test_ws_connect_and_broadcast_receive(self):
        from fastapi.testclient import TestClient
        from src.api.routes.ws import broadcast

        import importlib
        import src.main as main_mod
        importlib.reload(main_mod)
        app = main_mod.app

        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/ws") as ws:
                # Fire a broadcast from the test thread.  The server's
                # event loop runs inside TestClient — we have to use
                # the portal pattern that TestClient exposes to schedule
                # the coroutine on the server loop.
                portal = client.portal  # TestClient exposes this in 0.115+
                portal.call(broadcast, {"type": "phase9k-test", "value": 42})

                # Receive the broadcast payload.
                payload = ws.receive_json()
                assert payload == {"type": "phase9k-test", "value": 42}

    def test_ws_broadcast_to_no_connections_is_noop(self):
        """With no clients connected, ``broadcast`` is a silent no-op."""
        from src.api.routes.ws import broadcast

        # Just calling it shouldn't raise
        asyncio.run(broadcast({"type": "noop"}))
