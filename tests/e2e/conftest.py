"""Phase 9J — Real-browser E2E harness.

This conftest boots a real uvicorn server against a temp SQLite
database, seeds a realistic set of portfolios / holdings / events /
alerts / digests / operator rows, and yields the base URL to the
tests.  Playwright tests in this directory then drive a headless
Chromium browser against the live server.

Design constraints
------------------
1. **Deterministic**: every test sees the same seeded DB.  Tests that
   mutate data must clean up after themselves or use module-scoped
   fixtures that the next test knows about.
2. **Fast**: one uvicorn process per module; one DB per session.  The
   server starts once and tests share it.
3. **Network-isolated**: we override the DB path and LLM availability
   so the server never tries to reach the real internet.  The
   scheduler is still registered (it's a boot-path dependency) but
   its interval triggers don't fire during the test window.
4. **Safe**: the API auth middleware is disabled via
   ``auth_enabled = False`` in the settings instance after the cache
   is cleared.  CORS is already localhost-only in defaults.
5. **Portable**: we avoid any host-specific paths.  A free TCP port
   is picked at fixture time and passed through ``AXION_PORT``.

The fixture pattern mirrors the pattern used by
``tests/integration/test_relationship_runtime.py`` and
``tests/integration/test_operator_surface.py`` for the temp DB; the
only new piece is the live uvicorn thread + browser client.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a currently-free TCP port on localhost.

    The port is released immediately after we read it — there's a
    tiny race window before uvicorn grabs it, but for local test
    runs that's negligible.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout_s: float = 15.0) -> None:
    """Poll /api/v1/health until the server is ready or we time out."""
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # pragma: no cover — retried
            last_exc = exc
        time.sleep(0.2)
    raise RuntimeError(
        f"uvicorn did not become ready on {base_url} within {timeout_s}s "
        f"(last exception: {last_exc!r})"
    )


# ---------------------------------------------------------------------------
# DB + settings fixture (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _e2e_env(tmp_path_factory):
    """Set up env vars and clear singleton caches so the server
    picks up our temp DB instead of the real one.

    Session-scoped because we only want one boot cycle for every
    E2E test in the run.
    """
    from src.config import get_settings

    prior_env: dict[str, str | None] = {
        "KLEITOS_DB_PATH": os.environ.get("KLEITOS_DB_PATH"),
        "KLEITOS_DATA_DIR": os.environ.get("KLEITOS_DATA_DIR"),
        "AXION_PORT": os.environ.get("AXION_PORT"),
        "KLEITOS_PORT": os.environ.get("KLEITOS_PORT"),
        "AXION_TELEGRAM_TOKEN": os.environ.get("AXION_TELEGRAM_TOKEN"),
        "KLEITOS_TELEGRAM_TOKEN": os.environ.get("KLEITOS_TELEGRAM_TOKEN"),
    }

    db_dir = tmp_path_factory.mktemp("axion_e2e")
    db_path = db_dir / "axion_e2e.db"
    port = _free_port()

    os.environ["KLEITOS_DB_PATH"] = str(db_path)
    os.environ["KLEITOS_DATA_DIR"] = str(db_dir)
    os.environ["AXION_PORT"] = str(port)
    # Make sure the Telegram bot does NOT start during E2E.
    os.environ.pop("AXION_TELEGRAM_TOKEN", None)
    os.environ.pop("KLEITOS_TELEGRAM_TOKEN", None)

    # Clear the config cache and null the connection singletons so
    # every downstream import sees the temp DB.
    get_settings.cache_clear()  # type: ignore[attr-defined]
    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    # Force auth_enabled=False on the freshly-loaded settings so the
    # Playwright browser + our httpx calls don't need an API key.
    # Also lift the rate limit so a single Playwright process firing
    # many requests back-to-back doesn't trip the sliding-window
    # throttle (100 rpm default is too low for an E2E run).
    s = get_settings()
    s.api.auth_enabled = False
    s.api.rate_limit_rpm = 100_000
    # Confirm the port stuck.
    assert s.api.port == port, f"expected port {port}, got {s.api.port}"

    yield {"db_path": db_path, "port": port, "base_url": f"http://127.0.0.1:{port}"}

    # --- Restore env on teardown -----------------------------------
    for k, v in prior_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()  # type: ignore[attr-defined]
    connection._engine = None
    connection._session_factory = None


# ---------------------------------------------------------------------------
# Seed the DB with a realistic set of rows for the E2E flows
# ---------------------------------------------------------------------------


def _seed_e2e_database() -> None:
    """Run migrations + insert a realistic Phase 9I-compatible fixture.

    Synchronous wrapper so it can be called from the session fixture
    before uvicorn boots.  Opens its own event loop.
    """
    import asyncio

    async def _seed() -> None:
        from src.database.connection import get_db
        from src.database.migrations import run_migrations
        from src.database.models import (
            Alert, AnalysisNote, Digest, Event, EventLink, Holding,
            HoldingFactorSensitivity, HoldingRelationship, MacroFactorEvent,
            Portfolio, Security,
        )

        await run_migrations()

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        fresh = (now_dt - timedelta(minutes=10)).isoformat()
        recent = (now_dt - timedelta(hours=2)).isoformat()

        async with get_db() as session:
            from sqlalchemy import delete
            # Wipe in FK-safe order.  Leave the rows from lifespan
            # (e.g. seed relationship reconcile) alone — we rebuild
            # them below.
            for model in (
                EventLink, MacroFactorEvent, AnalysisNote,
                HoldingFactorSensitivity, HoldingRelationship,
                Alert, Digest, Event, Holding, Security,
            ):
                await session.execute(delete(model))
            await session.execute(delete(Portfolio))
            await session.commit()

            # --- Portfolios -----------------------------------------
            session.add_all([
                Portfolio(
                    id="default", name="Main Portfolio", base_currency="USD",
                    is_default=1, created_at=now, updated_at=now,
                ),
                Portfolio(
                    id="pA", name="Alpha Portfolio", base_currency="USD",
                    is_default=0, created_at=now, updated_at=now,
                ),
                Portfolio(
                    id="pB", name="Beta Portfolio", base_currency="USD",
                    is_default=0, created_at=now, updated_at=now,
                ),
            ])

            # --- Holdings -------------------------------------------
            session.add_all([
                Holding(
                    id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=60.0, current_price=180.0, market_value=1800.0,
                    avg_cost_basis=150.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now,
                ),
                Holding(
                    id="h_msft_pA", ticker="MSFT", currency="USD", quantity=5,
                    weight_pct=40.0, current_price=400.0, market_value=2000.0,
                    avg_cost_basis=300.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now,
                ),
                Holding(
                    id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                    weight_pct=100.0, current_price=120.0, market_value=6000.0,
                    avg_cost_basis=100.0, portfolio_id="pB", status="active",
                    created_at=now, updated_at=now,
                ),
                # A default-portfolio holding so the /default path is
                # also non-empty — the dashboard opens on default.
                Holding(
                    id="h_googl_default", ticker="GOOGL", currency="USD", quantity=4,
                    weight_pct=100.0, current_price=140.0, market_value=560.0,
                    avg_cost_basis=130.0, portfolio_id="default", status="active",
                    created_at=now, updated_at=now,
                ),
            ])

            # --- Securities -----------------------------------------
            for t, sector in (
                ("AAPL", "Information Technology"),
                ("MSFT", "Information Technology"),
                ("XOM", "Energy"),
                ("GOOGL", "Communication Services"),
                ("TSM", "Information Technology"),
            ):
                session.add(Security(
                    id=str(uuid.uuid4()), ticker=t, name=f"{t} Inc.",
                    currency="USD", sector=sector, geography="united states",
                    themes="[]", created_at=now, updated_at=now,
                ))
            await session.commit()

        # --- Events ------------------------------------------------------
        async with get_db() as session:
            session.add_all([
                Event(
                    id="evt_fed_rates",
                    title="Federal Reserve raises interest rates by 50 bps",
                    summary="FOMC voted 9-1 to raise the federal funds rate by 50 basis points.",
                    event_type="rates",
                    fetched_at=fresh,
                    created_at=now,
                    dedup_hash="e2e_fed_rates",
                ),
                Event(
                    id="evt_supplier",
                    title="Taiwan Semiconductor announces capacity cut",
                    summary="TSMC will reduce foundry capacity in Q2 due to weaker demand.",
                    event_type="supply_chain",
                    fetched_at=recent,
                    created_at=now,
                    dedup_hash="e2e_supplier",
                ),
                Event(
                    id="evt_opec",
                    title="OPEC+ extends oil production cuts",
                    summary="OPEC+ agreed to extend voluntary production cuts into the next quarter.",
                    event_type="commodities",
                    fetched_at=recent,
                    created_at=now,
                    dedup_hash="e2e_opec",
                ),
            ])
            await session.commit()

        # --- Macro factor + relationship links -------------------------
        async with get_db() as session:
            # Factor: interest_rate classification of Fed event
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
            # oil_energy classification of OPEC event (global fact)
            session.add(MacroFactorEvent(
                id=str(uuid.uuid4()),
                event_id="evt_opec",
                factor="oil_energy",
                direction="up",
                magnitude="moderate",
                confidence=0.7,
                rationale=json.dumps(["matched: opec", "production cut"]),
                created_at=now,
            ))
            # Factor EventLinks for AAPL + MSFT (pA)
            for hid, score in (("h_aapl_pA", 0.48), ("h_msft_pA", 0.42)):
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
                            "rationale": ["federal reserve", "50 bps"],
                        },
                        "expected_effect": {
                            "direction": "negative",
                            "confidence": score,
                        },
                    }),
                    created_at=now,
                ))
            # Oil link for XOM (pB)
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="evt_opec",
                link_type="macro_factor",
                link_target="h_xom_pB",
                impact_channel="oil_energy",
                channel="oil_energy",
                relevance_score=0.55,
                details_json=json.dumps({
                    "factor": {
                        "key": "oil_energy",
                        "direction": "up",
                        "magnitude": "moderate",
                        "rationale": ["opec", "production cut"],
                    },
                    "expected_effect": {
                        "direction": "positive",
                        "confidence": 0.55,
                    },
                }),
                created_at=now,
            ))

            # Relationship link: AAPL → TSMC supplier (pA) on TSMC event
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id="evt_supplier",
                link_type="relationship",
                link_target="h_aapl_pA",
                impact_channel="supplier",
                channel="supplier",
                relevance_score=0.52,
                details_json=json.dumps({
                    "related_entity": {"name": "Taiwan Semiconductor", "ticker": "TSM"},
                    "rationale": [
                        "TSMC is AAPL's primary foundry",
                        "capacity cut reduces AAPL's supply headroom",
                    ],
                    "expected_effect": {"direction": "negative", "confidence": 0.52},
                }),
                created_at=now,
            ))

            # Seed relationship row (proves seed-row protection in UI)
            session.add(HoldingRelationship(
                id="rel_seed_aapl_tsmc",
                holding_id="h_aapl_pA",
                relationship_type="supplier",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semiconductor",
                strength=0.85,
                source="seed",
                description="Seed: TSMC is AAPL's primary foundry",
                created_at=now, updated_at=now,
            ))
            await session.commit()

        # --- Alerts (various severities) + analysis note --------------
        async with get_db() as session:
            session.add_all([
                Alert(
                    id="alert_pA_critical",
                    portfolio_id="pA",
                    alert_type="macro_factor",
                    severity="critical",
                    title="Rate shock on AAPL",
                    body="Fed 50bps shock — major duration risk for AAPL.",
                    related_holdings=json.dumps(["AAPL"]),
                    related_events=json.dumps(["evt_fed_rates"]),
                    acknowledged=0, delivered=0, agent_id="risk",
                    created_at=(now_dt - timedelta(hours=6)).isoformat(),
                ),
                Alert(
                    id="alert_pA_info_fresh",
                    portfolio_id="pA",
                    alert_type="info",
                    severity="info",
                    title="Daily brief ready",
                    body="Morning digest is now available.",
                    related_holdings=json.dumps([]),
                    related_events=json.dumps([]),
                    acknowledged=0, delivered=0, agent_id="digest",
                    created_at=(now_dt - timedelta(minutes=5)).isoformat(),
                ),
                Alert(
                    id="alert_pA_high",
                    portfolio_id="pA",
                    alert_type="supply_chain",
                    severity="high",
                    title="Supply chain pressure on AAPL",
                    body="TSMC capacity cut increases single-foundry risk.",
                    related_holdings=json.dumps(["AAPL"]),
                    related_events=json.dumps(["evt_supplier"]),
                    acknowledged=0, delivered=0, agent_id="risk",
                    created_at=(now_dt - timedelta(hours=2)).isoformat(),
                ),
                # pB-only alert so portfolio isolation is visible
                Alert(
                    id="alert_pB_only",
                    portfolio_id="pB",
                    alert_type="oil",
                    severity="high",
                    title="Oil pressure on XOM",
                    body="OPEC+ cut supports oil; XOM positive exposure.",
                    related_holdings=json.dumps(["XOM"]),
                    related_events=json.dumps(["evt_opec"]),
                    acknowledged=0, delivered=0, agent_id="risk",
                    created_at=now,
                ),
            ])

            # One negative impact_analysis note for AAPL so the Phase 9G
            # intelligence summary posture logic has something to work with.
            session.add(AnalysisNote(
                id="note_aapl_neg",
                event_id="evt_fed_rates",
                holding_id="h_aapl_pA",
                note_type="impact_analysis",
                content=json.dumps({
                    "ticker": "AAPL",
                    "impact_direction": "negative",
                    "impact_magnitude": "high",
                    "materiality": "important",
                    "short_term_outlook": "Rate pressure on AAPL multiples.",
                }),
                materiality="important",
                confidence="high",
                agent_id="analysis",
                created_at=(now_dt - timedelta(hours=1)).isoformat(),
            ))
            await session.commit()

        # --- Digest (Phase 9E grounded shape) --------------------------
        async with get_db() as session:
            session.add(Digest(
                id="digest_pA_e2e",
                portfolio_id="pA",
                digest_type="daily",
                period_start=(now_dt - timedelta(days=1)).isoformat(),
                period_end=now,
                content=json.dumps({
                    "headline": "Alpha daily — mildly negative on rate shock",
                    "portfolio_assessment": (
                        "Two AAPL negatives pull posture down; MSFT neutral. "
                        "Rate shock is the dominant factor touchpoint."
                    ),
                    "risk_flags": [
                        "Interest rates trending up — duration risk",
                        "Single-foundry dependency on TSMC",
                    ],
                    "holdings_requiring_attention": ["AAPL"],
                    "key_developments": [
                        "Fed raised rates by 50 bps",
                        "TSMC capacity cut announced",
                    ],
                    "action_items": [],
                    "market_context": (
                        "Rate regime remains restrictive; energy complex firm."
                    ),
                }),
                event_count=3,
                alert_count=3,
                holding_count=2,
                delivered=0,
                created_at=now,
            ))
            await session.commit()

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# Live uvicorn server fixture (session-scoped)
# ---------------------------------------------------------------------------


class _UvicornThread(threading.Thread):
    """Run uvicorn.Server.run() in a background daemon thread.

    Uvicorn drives its own asyncio loop inside run(); we just start
    the thread and poll /health from the test thread.  Shutdown goes
    through server.should_exit.
    """

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(daemon=True, name="axion-e2e-uvicorn")
        self._config = config
        self.server = uvicorn.Server(config)
        self.exc: Exception | None = None

    def run(self) -> None:  # pragma: no cover — driven by tests
        try:
            self.server.run()
        except Exception as exc:
            self.exc = exc


@pytest.fixture(scope="session")
def axion_server(_e2e_env):
    """Boot uvicorn against the seeded temp DB and yield the base URL."""
    # Seed BEFORE importing src.main so the startup hook sees a
    # populated DB and its relationship reconcile / source sync pass
    # runs against the temp file.
    _seed_e2e_database()

    # Import the FastAPI app LAZILY — after env vars are set and caches
    # are cleared — so it picks up the temp DB.
    from src.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=_e2e_env["port"],
        log_level="warning",
        access_log=False,
        loop="asyncio",
        lifespan="on",
    )
    thread = _UvicornThread(config)
    thread.start()

    try:
        _wait_for_health(_e2e_env["base_url"])
        yield _e2e_env["base_url"]
    finally:
        thread.server.should_exit = True
        thread.join(timeout=15)
        if thread.is_alive():  # pragma: no cover — defensive
            thread.server.force_exit = True
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Playwright page fixture — wraps the pytest-playwright sync api so
# tests can just use `page` with the E2E base URL already configured.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, axion_server):
    """pytest-playwright recognises this fixture and uses it to build
    the browser context.  We pipe our E2E base URL through as the
    default so tests can call ``page.goto("/dashboard")``.
    """
    return {
        **browser_context_args,
        "base_url": axion_server,
        "ignore_https_errors": True,
        "viewport": {"width": 1280, "height": 900},
    }


# ---------------------------------------------------------------------------
# Phase 9K — failure-artifact defaults
# ---------------------------------------------------------------------------
#
# The ``--screenshot=only-on-failure``, ``--video=retain-on-failure``,
# ``--tracing=retain-on-failure`` and ``--output=test-results/e2e``
# defaults are applied globally via ``addopts`` in ``pyproject.toml``
# so every ``pytest tests/e2e --browser chromium`` invocation captures
# debugging artifacts on failure without any extra flags.
#
# Explicit CLI flags still win because pytest lets command-line args
# override addopts.  Green runs keep the disk clean because
# ``only-on-failure`` / ``retain-on-failure`` only save artifacts for
# tests that actually fail.
#
# Open a saved trace with::
#
#     .venv/bin/python -m playwright show-trace \
#         test-results/e2e/<node-slug>/trace.zip


# ---------------------------------------------------------------------------
# Auto-captured browser console output for every test
# ---------------------------------------------------------------------------


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Standard pytest recipe: stash the call outcome on the node so
    ``_capture_page_errors`` can inspect it during teardown."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(autouse=True)
def _capture_page_errors(request, page):
    """Attach console + pageerror listeners to every test's page and,
    on failure, dump the last ~20 messages to stdout.  Lets us debug
    E2E flakes without re-running with custom instrumentation."""
    errors: list[str] = []

    def _on_console(msg):
        if msg.type in ("error", "warning"):
            errors.append(f"{msg.type}: {msg.text[:400]}")

    page.on("console", _on_console)
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    yield

    call_rep = getattr(request.node, "rep_call", None)
    if call_rep is not None and call_rep.failed and errors:
        print(f"\n--- browser console for {request.node.nodeid} ---")
        for e in errors[-20:]:
            print(f"  {e}")
