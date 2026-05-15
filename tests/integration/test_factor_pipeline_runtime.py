"""Integration tests for Phase 9A — deterministic macro factor reasoning
against the REAL runtime collection path.

These tests exercise ``CollectionAgent._link_event_to_holdings`` end
to end against a temporary SQLite database produced by the actual
``run_migrations`` routine, so they also cover:

* migration v3 (new tables, new event_links columns)
* MacroFactorEvent row creation
* EventLink(link_type="macro_factor") creation with details_json
* sector prior vs. manual override behavior
* portfolio isolation
* coexistence with legacy ticker_match direct linking
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
# Test DB bootstrap — must happen BEFORE importing anything that touches
# the engine, so the singleton picks up the temp path.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    # Snapshot any prior state that other test modules may have set
    # on the singleton settings object (notably, the smoke test suite
    # sets ``api.auth_enabled = False`` on the cached instance at
    # import time).  We restore that exact state at teardown so this
    # test module cannot leak across tests.
    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_phase9a")
    db_path = db_dir / "axion_test.db"
    os.environ["KLEITOS_DB_PATH"] = str(db_path)
    get_settings.cache_clear()  # type: ignore[attr-defined]

    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    yield db_path

    # Teardown: restore the env var to exactly what was there before,
    # rebuild the settings cache, and re-apply the prior auth override.
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
    # Preserve any in-place mutation other test modules had applied
    # (e.g. smoke tests disabling auth on the cached Settings).
    restored.api.auth_enabled = prior_auth_enabled

    connection._engine = None
    connection._session_factory = None


@pytest_asyncio.fixture(scope="module")
async def _migrated_db(_tmp_db):
    from src.database.migrations import run_migrations

    await run_migrations()
    yield _tmp_db


@pytest_asyncio.fixture
async def seeded_session(_migrated_db):
    """Provide a clean seeded database with two portfolios and 5 holdings."""
    from src.database.connection import get_db
    from src.database.models import (
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
    # Wipe previous fixture data so tests can run in any order.
    # Delete order respects FK dependencies — child rows first,
    # parent rows (Portfolio) last.
    async with get_db() as session:
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote, HoldingFactorSensitivity,
            Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        # Two portfolios
        session.add(Portfolio(
            id="pA", name="Portfolio A", description="test",
            base_currency="USD", is_default=1,
            created_at=now, updated_at=now,
        ))
        session.add(Portfolio(
            id="pB", name="Portfolio B", description="test",
            base_currency="USD", is_default=0,
            created_at=now, updated_at=now,
        ))

        # Holdings — mix of sectors and portfolios
        holdings = [
            Holding(
                id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                portfolio_id="pA", status="active",
                created_at=now, updated_at=now,
            ),
            Holding(
                id="h_xom_pA", ticker="XOM", currency="USD", quantity=10,
                portfolio_id="pA", status="active",
                created_at=now, updated_at=now,
            ),
            Holding(
                id="h_jpm_pA", ticker="JPM", currency="USD", quantity=10,
                portfolio_id="pA", status="active",
                created_at=now, updated_at=now,
            ),
            Holding(
                id="h_msft_pB", ticker="MSFT", currency="USD", quantity=10,
                portfolio_id="pB", status="active",
                created_at=now, updated_at=now,
            ),
            Holding(
                id="h_nesn_pB", ticker="NESN", currency="CHF", quantity=10,
                portfolio_id="pB", status="active",
                created_at=now, updated_at=now,
            ),
        ]
        for h in holdings:
            session.add(h)

        # Securities — provides the sector lookup for the factor pipeline
        sectors = {
            "AAPL": ("Information Technology", "united states"),
            "XOM": ("Energy", "united states"),
            "JPM": ("Financials", "united states"),
            "MSFT": ("Information Technology", "united states"),
            "NESN": ("Consumer Staples", "switzerland"),
        }
        for ticker, (sector, geo) in sectors.items():
            session.add(Security(
                id=str(uuid.uuid4()),
                ticker=ticker, name=ticker, currency="USD",
                sector=sector, geography=geo, themes="[]",
                created_at=now, updated_at=now,
            ))

        await session.commit()

    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_link_event(raw: dict) -> tuple[str, int]:
    """Persist an event and run the full link pipeline.

    Returns ``(event_id, link_count)`` where link_count is the raw
    int returned by ``_link_event_to_holdings``.
    """
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
            url=raw.get("url"),
            fetched_at=now,
            created_at=now,
            dedup_hash=str(uuid.uuid4()),  # unique per run
        ))
        await session.commit()

    agent = CollectionAgent()
    link_count = await agent._link_event_to_holdings(event_id, raw)
    return event_id, link_count


# ---------------------------------------------------------------------------
# Actual tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMigrationAndSchema:
    async def test_schema_version_at_least_3(self, seeded_session):
        """Phase 9A introduced schema v3.  Later phases may bump the
        version further (e.g. Phase 9D → v4) — we only require v3+."""
        from sqlalchemy import text
        from src.database.connection import get_db

        async with get_db() as session:
            result = await session.execute(text("SELECT version FROM _schema_version WHERE id = 1"))
            version = result.scalar()
        assert version >= 3

    async def test_new_tables_exist(self, seeded_session):
        from sqlalchemy import text
        from src.database.connection import get_db

        async with get_db() as session:
            rows = (await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )).scalars().all()
        names = set(rows)
        assert "holding_factor_sensitivities" in names
        assert "macro_factor_events" in names

    async def test_event_links_has_channel_and_details_json(self, seeded_session):
        from sqlalchemy import text
        from src.database.connection import get_db

        async with get_db() as session:
            rows = (await session.execute(
                text("PRAGMA table_info(event_links)")
            )).all()
        cols = {row[1] for row in rows}
        assert "channel" in cols
        assert "details_json" in cols


@pytest.mark.asyncio
class TestFactorPipelineRuntime:
    async def test_fed_event_emits_default_prior_factor_links(
        self, seeded_session,
    ):
        """Phase 9A corrective pass: default sector priors MUST now
        emit ``macro_factor`` EventLinks for holdings with meaningful
        exposure (e.g. tech with interest_rate prior -0.6), so the
        deterministic factor pipeline is operational out of the box
        without manual override rows.

        The emitted link carries:
          * an honest ``relevance_score`` from the propagator
            (not inflated)
          * ``link_type == "macro_factor"``, ``link_source ==
            "deterministic_factor"``, and a structured ``details_json``
          * a score below the generic 0.5 AnalysisAgent gate, so it
            is NEVER surfaced via per-event LLM analysis
        """
        from src.database.connection import get_db
        from src.database.models import EventLink, MacroFactorEvent

        event_id, _ = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": (
                "The FOMC voted to raise the federal funds rate by 50 basis "
                "points citing persistent inflation and tight labor markets."
            ),
        })

        async with get_db() as session:
            mfe_rows = (await session.execute(
                select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
            )).scalars().all()
            factor_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                )
            )).scalars().all()

        factors = {r.factor for r in mfe_rows}
        assert "interest_rate" in factors, f"expected interest_rate, got {factors}"

        interest = next(r for r in mfe_rows if r.factor == "interest_rate")
        assert interest.direction == "up"
        assert interest.magnitude in ("major", "extreme")
        assert 0.05 <= interest.confidence <= 0.95
        parsed = json.loads(interest.rationale) if interest.rationale else []
        assert any("50" in item for item in parsed), parsed

        # Corrective-pass invariant: default sector priors now DO emit
        # factor EventLinks at the honest MACRO_FACTOR_LINK_MIN floor.
        assert factor_links, "expected at least one macro_factor link under default priors"

        # AAPL (tech, -0.6 prior) must be in the emitted targets
        targets = {l.link_target for l in factor_links}
        assert "h_aapl_pA" in targets, (
            f"expected tech holding with -0.6 interest_rate prior "
            f"to produce a link, got targets={targets}"
        )

        # Every emitted link must obey the honest contract:
        for l in factor_links:
            assert l.relevance_score is not None
            # Honest floor: >= 0.25 (MACRO_FACTOR_LINK_MIN)
            assert l.relevance_score >= 0.25, (
                f"link relevance {l.relevance_score} below honest floor 0.25"
            )
            # Default-source links must NEVER breach the generic 0.5
            # analysis gate under the brief formula — that's the
            # portfolio-safety tradeoff that protects analysis from
            # factor noise.
            assert l.relevance_score < 0.5, (
                f"default-source factor link unexpectedly crossed analysis "
                f"gate (relevance={l.relevance_score})"
            )
            assert l.link_source == "deterministic_factor"
            assert l.impact_channel in factors
            assert l.channel == l.impact_channel
            assert l.details_json is not None
            details = json.loads(l.details_json)
            assert details["event"]["id"] == event_id
            assert details["factor"]["key"] == l.impact_channel
            assert details["holding"]["id"] == l.link_target
            assert details["sensitivity"]["source"] == "default"

    async def test_manual_override_unlocks_factor_link(self, seeded_session):
        """With a manual HoldingFactorSensitivity override, a strong
        classified factor DOES emit a ``macro_factor`` EventLink — and
        the link carries a well-formed structured ``details_json``."""
        from src.database.connection import get_db
        from src.database.models import (
            EventLink,
            HoldingFactorSensitivity,
            MacroFactorEvent,
        )

        # Seed a manual override: AAPL holding is highly sensitive to
        # interest rates (operator belief).
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(HoldingFactorSensitivity(
                id=str(uuid.uuid4()),
                holding_id="h_aapl_pA",
                factor="interest_rate",
                sensitivity=-1.0,
                source="manual",
                created_at=now,
                updated_at=now,
            ))
            await session.commit()

        event_id, _ = await _run_link_event({
            "title": "Federal Reserve hikes interest rates by 75 basis points",
            "summary": (
                "Powell called the move an unprecedented historic emergency "
                "response to runaway inflation."
            ),
        })

        async with get_db() as session:
            mfe_rows = (await session.execute(
                select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
            )).scalars().all()
            links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                    EventLink.link_target == "h_aapl_pA",
                )
            )).scalars().all()

        mfe_factors = {r.factor for r in mfe_rows}
        assert "interest_rate" in mfe_factors

        assert links, (
            "manual override with strong sensitivity should emit a "
            "macro_factor link for AAPL"
        )
        lnk = links[0]
        assert lnk.impact_channel == "interest_rate"
        assert lnk.channel == "interest_rate"
        assert lnk.link_source == "deterministic_factor"
        assert lnk.relevance_score is not None and lnk.relevance_score >= 0.5
        # link_target MUST remain a holding UUID (invariant)
        assert lnk.link_target == "h_aapl_pA"

        # Structured causal chain
        assert lnk.details_json
        details = json.loads(lnk.details_json)
        assert details["event"]["id"] == event_id
        assert details["factor"]["key"] == "interest_rate"
        assert details["factor"]["direction"] == "up"
        assert details["holding"]["ticker"] == "AAPL"
        assert details["holding"]["portfolio_id"] == "pA"
        assert details["sensitivity"]["source"] == "manual"
        assert details["sensitivity"]["value"] == pytest.approx(-1.0)
        # up rates × -1.0 sensitivity → negative effect
        assert details["expected_effect"]["direction"] == "negative"

    async def test_oil_shock_emits_factor_link_for_energy_holding(self, seeded_session):
        """Pipeline-attack headline classifies oil_energy AND
        geopolitical_risk into MFE rows AND emits a macro_factor
        EventLink for the energy holding under the default +0.8
        oil_energy sector prior.  The emitted link's expected_effect
        must be ``positive`` (oil up × +0.8 sensitivity → positive
        effect on energy exposure).
        """
        from src.database.connection import get_db
        from src.database.models import EventLink, MacroFactorEvent

        event_id, _ = await _run_link_event({
            "title": "Pipeline attack in Strait of Hormuz sends Brent crude oil surging",
            "summary": "A drone strike escalated regional tensions; WTI jumped 12%.",
        })

        async with get_db() as session:
            mfe_rows = (await session.execute(
                select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
            )).scalars().all()
            xom_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                    EventLink.link_target == "h_xom_pA",
                    EventLink.impact_channel == "oil_energy",
                )
            )).scalars().all()

        factors = {r.factor for r in mfe_rows}
        assert "oil_energy" in factors, f"expected oil_energy in {factors}"
        assert "geopolitical_risk" in factors, f"expected geopolitical_risk in {factors}"
        oil = next(r for r in mfe_rows if r.factor == "oil_energy")
        assert oil.direction == "up"

        assert xom_links, (
            "energy holding with +0.8 oil_energy prior should emit "
            "a macro_factor link for an oil-shock event"
        )
        link = xom_links[0]
        assert link.relevance_score is not None
        assert 0.25 <= link.relevance_score < 0.5  # honest default-source range
        details = json.loads(link.details_json)
        assert details["expected_effect"]["direction"] == "positive"
        assert details["sensitivity"]["value"] > 0

    async def test_direct_ticker_matching_still_works(self, seeded_session):
        """Ensure Phase 9A doesn't break existing direct matching."""
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id, _ = await _run_link_event({
            "title": "AAPL reports record quarterly earnings",
            "summary": "Apple delivered strong results driven by iPhone sales.",
        })

        async with get_db() as session:
            direct = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "ticker_match",
                )
            )).scalars().all()

        assert direct, "direct ticker_match links must still be produced"
        targets = {l.link_target for l in direct}
        assert "h_aapl_pA" in targets

    async def test_apple_orchard_no_cross_contamination(self, seeded_session):
        """The canonical false-positive guard: 'Apple orchard' must
        produce no macro factor rows AND no bad AAPL links."""
        from src.database.connection import get_db
        from src.database.models import EventLink, MacroFactorEvent

        event_id, _ = await _run_link_event({
            "title": "Apple orchard destroyed by frost in upstate New York",
            "summary": (
                "Local fruit growers report severe losses after an unseasonably "
                "cold night wiped out a large share of the harvest."
            ),
        })

        async with get_db() as session:
            mfe = (await session.execute(
                select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
            )).scalars().all()
            factor_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                )
            )).scalars().all()
            aapl_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_target == "h_aapl_pA",
                )
            )).scalars().all()

        assert mfe == [], "no macro factor rows expected for orchard story"
        assert factor_links == [], "no macro_factor links expected"
        # No false ticker_match against AAPL either (word "Apple" in the
        # title matches the AAPL security name — this is the existing
        # runtime behavior and we are only asserting the factor pipeline
        # doesn't add NEW bad links beyond what's already there).
        for l in aapl_links:
            assert l.link_type != "macro_factor"

    async def test_portfolio_isolation_across_factor_links(self, seeded_session):
        """Factor links must carry each holding's own portfolio_id in
        details_json, and link_target must be a holding UUID — never a
        ticker, never a factor name.

        Uses default sector priors (corrective-pass behavior) so this
        also proves the out-of-the-box operational path.  Holdings
        live in two portfolios (pA and pB); both must independently
        produce links tagged with their own portfolio_id.
        """
        from src.database.connection import get_db
        from src.database.models import EventLink, Holding

        event_id, _ = await _run_link_event({
            "title": "Federal Reserve hikes interest rates by 75 basis points",
            "summary": "Powell cited sticky inflation and tight labor markets.",
        })

        async with get_db() as session:
            links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                )
            )).scalars().all()
            holdings = {h.id: h for h in (
                await session.execute(select(Holding))
            ).scalars().all()}

        assert links, "expected factor links from the Fed headline under default priors"

        pids_seen: set[str] = set()
        for l in links:
            # Invariant: link_target is a holding UUID present in holdings table
            assert l.link_target in holdings, (
                f"link_target {l.link_target} is not a valid holding UUID"
            )
            details = json.loads(l.details_json)
            assert details["holding"]["id"] == l.link_target
            assert details["holding"]["portfolio_id"] == holdings[l.link_target].portfolio_id
            pids_seen.add(details["holding"]["portfolio_id"])

        # Holdings in BOTH portfolios receive their own factor links.
        # (AAPL/MSFT in pA and pB both have tech sector prior -0.6 on
        # interest_rate; JPM in pA with financials +0.3 is borderline.)
        assert "pA" in pids_seen, f"expected pA in {pids_seen}"
        assert "pB" in pids_seen, f"expected pB in {pids_seen}"

    async def test_idempotent_repeated_call(self, seeded_session):
        """Calling the link pipeline twice for the same event must not
        produce duplicate MacroFactorEvent or duplicate macro_factor
        EventLink rows."""
        from src.agents.collection import CollectionAgent
        from src.database.connection import get_db
        from src.database.models import Event, EventLink, MacroFactorEvent

        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        raw = {
            "title": "CPI rises 0.8% m/m, hotter than expected",
            "summary": "Core CPI accelerated on persistent services inflation.",
        }
        async with get_db() as session:
            session.add(Event(
                id=event_id,
                title=raw["title"],
                summary=raw["summary"],
                fetched_at=now,
                created_at=now,
                dedup_hash=str(uuid.uuid4()),
            ))
            await session.commit()

        agent = CollectionAgent()
        await agent._link_event_to_holdings(event_id, raw)
        await agent._link_event_to_holdings(event_id, raw)

        async with get_db() as session:
            mfe = (await session.execute(
                select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
            )).scalars().all()
            links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "macro_factor",
                )
            )).scalars().all()

        # Uniqueness by (event_id, factor) for MFE
        factor_seen = [r.factor for r in mfe]
        assert len(factor_seen) == len(set(factor_seen))

        # Uniqueness by (event_id, holding_id, factor) for links
        link_keys = [(l.link_target, l.impact_channel) for l in links]
        assert len(link_keys) == len(set(link_keys))


# ---------------------------------------------------------------------------
# Phase 9A CORRECTIVE PASS: type-aware analysis gate + digest touchpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCorrectivePass:
    """These tests prove the corrective-pass invariants:

    1. ``AnalysisAgent._get_linked_holdings`` excludes ``macro_factor``
       links by type, independent of their relevance score — so even a
       hypothetical manual-override factor link with score ≥ 0.5 does
       NOT trigger per-event LLM analysis.
    2. ``AnalysisAgent.generate_digest`` surfaces deterministic factor
       intelligence via a ``macro_factor_touchpoints`` field that is
       populated from the persisted factor links + MacroFactorEvent
       rows, scoped to the caller's portfolio.
    3. A factor-only digest (no analysis notes, only deterministic
       factor signals) is still produced — previously the digest path
       early-returned on empty notes, silently swallowing the signal.
    4. Factor link emission with default sector priors does NOT flood
       the analysis pipeline — tested directly by confirming zero
       analysis notes are created when only macro_factor links exist.
    """

    async def test_analysis_agent_excludes_macro_factor_links_even_if_high_score(
        self, seeded_session,
    ):
        """Type-aware gate: macro_factor links are excluded from
        per-event analysis regardless of relevance_score.  We verify
        this directly by inserting a synthetic high-score macro_factor
        link and confirming ``_get_linked_holdings`` returns an empty
        list while a sibling ticker_match link is still surfaced.
        """
        from src.agents.analysis import AnalysisAgent
        from src.database.connection import get_db
        from src.database.models import Event, EventLink

        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(Event(
                id=event_id,
                title="Synthetic test event",
                fetched_at=now,
                created_at=now,
                dedup_hash=str(uuid.uuid4()),
            ))
            await session.flush()

            # Inject a high-score (0.85) macro_factor link — this is
            # what a hypothetical extreme manual override would look
            # like.  The type-aware gate must still exclude it.
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id=event_id,
                link_type="macro_factor",
                link_target="h_aapl_pA",
                relevance_score=0.85,
                impact_channel="interest_rate",
                link_source="deterministic_factor",
                channel="interest_rate",
                details_json=json.dumps({"synthetic": True}),
                created_at=now,
            ))
            # And a baseline ticker_match link that SHOULD surface.
            session.add(EventLink(
                id=str(uuid.uuid4()),
                event_id=event_id,
                link_type="ticker_match",
                link_target="h_aapl_pA",
                relevance_score=0.9,
                created_at=now,
            ))
            await session.commit()

        agent = AnalysisAgent()
        agent._portfolio_id = "pA"
        linked = await agent._get_linked_holdings(event_id)

        # Exactly one row (the ticker_match); the macro_factor link
        # with score 0.85 must be excluded by type.
        assert len(linked) == 1, f"expected exactly one linked holding, got {linked}"
        assert linked[0]["ticker"] == "AAPL"

    async def test_digest_carries_factor_touchpoints_under_default_priors(
        self, seeded_session,
    ):
        """End-to-end: after running the factor pipeline on a Fed
        event, ``AnalysisAgent.generate_digest`` produces a digest
        whose content contains a ``macro_factor_touchpoints`` field
        with the interest_rate factor summarized across affected
        holdings in the current portfolio."""
        from src.agents.analysis import AnalysisAgent
        from src.database.connection import get_db
        from src.database.models import Digest

        # Emit a classified event through the real runtime path.
        await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": (
                "The FOMC voted to raise the federal funds rate by 50 basis "
                "points citing persistent inflation and tight labor markets."
            ),
        })

        agent = AnalysisAgent()
        result = await agent.run(digest=True, period="daily", portfolio_id="pA")

        # The digest summary is a plain dict, not the full content.
        assert result.get("digest_id") is not None, (
            "expected a factor-only digest to be persisted even without "
            "analysis notes"
        )
        assert result.get("factor_touchpoints", 0) >= 1

        # Pull the persisted digest and inspect its content JSON.
        async with get_db() as session:
            digest = await session.get(Digest, result["digest_id"])

        assert digest is not None
        content = json.loads(digest.content)
        touchpoints = content.get("macro_factor_touchpoints")
        assert touchpoints, f"expected macro_factor_touchpoints in {list(content)}"

        by_factor = {t["factor"]: t for t in touchpoints}
        assert "interest_rate" in by_factor
        ir = by_factor["interest_rate"]
        assert ir["factor_direction"] == "up"
        assert ir["max_magnitude"] in ("major", "extreme")
        assert ir["event_count"] >= 1
        assert ir["holding_count"] >= 1
        assert 0.25 <= ir["max_link_relevance"] < 0.5  # honest default range
        assert 0.0 <= ir["max_factor_confidence"] <= 0.95
        assert ir["label"] == "Interest Rates"
        assert "AAPL" in ir["affected_tickers"]

    async def test_digest_factor_touchpoints_scoped_per_portfolio(
        self, seeded_session,
    ):
        """Factor touchpoints in a portfolio pB digest must NOT include
        holdings from pA, even though the underlying factor event is
        global."""
        from src.agents.analysis import AnalysisAgent
        from src.database.connection import get_db
        from src.database.models import Digest

        await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC moved; persistent inflation cited.",
        })

        agent_b = AnalysisAgent()
        result_b = await agent_b.run(digest=True, period="daily", portfolio_id="pB")
        assert result_b["digest_id"] is not None

        async with get_db() as session:
            digest_b = await session.get(Digest, result_b["digest_id"])
        content_b = json.loads(digest_b.content)
        touchpoints_b = content_b.get("macro_factor_touchpoints", [])

        # Every ticker in pB's touchpoints must belong to pB.
        pB_tickers = {"MSFT", "NESN"}
        for t in touchpoints_b:
            for ticker in t["affected_tickers"]:
                assert ticker in pB_tickers, (
                    f"portfolio pB digest leaked ticker {ticker} from pA"
                )

    async def test_no_analysis_notes_from_macro_factor_links_under_defaults(
        self, seeded_session,
    ):
        """Smoke: running the analysis agent on an event whose ONLY
        links are default-source macro_factor links produces zero
        analysis notes — proving the type-aware gate prevents LLM
        analysis spam."""
        from src.agents.analysis import AnalysisAgent
        from src.database.connection import get_db
        from src.database.models import AnalysisNote, EventLink

        event_id, _ = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC rate decision cited persistent inflation.",
        })

        # Confirm the event has factor links but no ticker_match
        # links (no portfolio ticker in the text).
        async with get_db() as session:
            links = (await session.execute(
                select(EventLink).where(EventLink.event_id == event_id)
            )).scalars().all()
        types = {l.link_type for l in links}
        assert types == {"macro_factor"}, f"expected only macro_factor links, got {types}"

        # Run analysis — should skip this event entirely.
        agent = AnalysisAgent()
        agent._portfolio_id = "pA"
        result = await agent.analyze_events(event_ids=[event_id])

        assert result["analysed"] == 0, (
            f"expected zero analysis notes from factor-only event, got {result}"
        )

        async with get_db() as session:
            notes = (await session.execute(
                select(AnalysisNote).where(AnalysisNote.event_id == event_id)
            )).scalars().all()
        assert notes == []

    async def test_api_post_digests_generate_includes_factor_touchpoints(
        self, seeded_session,
    ):
        """Phase 9A consistency audit: the API background task
        (``POST /api/v1/digests/generate``) previously routed through
        the legacy ``DigestGenerator`` which did NOT include
        ``macro_factor_touchpoints``.  After the consistency fix it
        must call ``AnalysisAgent.generate_digest`` and produce a
        persisted digest whose content carries the touchpoints,
        exactly like the scheduler and OpenClaw paths.
        """
        from src.api.routes.digests import _generate_digest_in_background
        from src.database.connection import get_db
        from src.database.models import Digest

        # Seed a classified event through the real runtime path so the
        # factor pipeline has something to touch.
        await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC rate decision cited persistent inflation.",
        })

        # Invoke the API background task DIRECTLY — this is exactly
        # what BackgroundTasks would call after the HTTP 202 response.
        await _generate_digest_in_background(
            digest_type="ad-hoc", portfolio_id="pA",
        )

        # The most recent digest for pA must now carry touchpoints.
        async with get_db() as session:
            digest = (await session.execute(
                select(Digest)
                .where(Digest.portfolio_id == "pA")
                .order_by(Digest.created_at.desc())
                .limit(1)
            )).scalars().first()

        assert digest is not None, "expected the API path to persist a digest"
        content = json.loads(digest.content)
        assert "macro_factor_touchpoints" in content, (
            f"API POST /digests/generate persisted a digest WITHOUT "
            f"macro_factor_touchpoints — split-brain regression. "
            f"Content keys: {sorted(content)}"
        )
        touchpoints = content["macro_factor_touchpoints"]
        assert touchpoints, "expected at least one factor touchpoint from Fed event"
        factors = {t["factor"] for t in touchpoints}
        assert "interest_rate" in factors

        # Verify the API read path surfaces the touchpoint as a section.
        # This is the path consumed by the dashboard, Telegram /digest,
        # and any external caller of GET /api/v1/digests/latest.
        from src.api.routes.digests import _parse_content_to_sections
        sections = _parse_content_to_sections(digest.content)
        section_titles = {s.title for s in sections}
        assert "Macro Factor Touchpoints" in section_titles, (
            f"GET /digests/latest response transformer did not surface "
            f"the touchpoints as a section; got {section_titles}"
        )
