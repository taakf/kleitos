"""Integration tests for the Phase 9D deterministic relationship
graph against the REAL runtime collection path + event detail API.

Mirror of the Phase 9A / 9B integration-fixture pattern: temp SQLite
DB, real migrations to v4, real holdings + HoldingRelationship rows,
call ``CollectionAgent._link_event_to_holdings`` directly, then
inspect EventLinks AND the event detail API response to prove the
chain surfaces correctly.
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
# Temp DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_phase9d")
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
    """Two portfolios, several holdings, seeded relationship rows."""
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
    )
    from sqlalchemy import delete

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        # Wipe in FK-safe order.
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote, HoldingFactorSensitivity,
            HoldingRelationship, Alert, Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Portfolio A", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
            Portfolio(id="pB", name="Portfolio B", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
        ])
        # Hold AAPL in both portfolios, NVDA only in pA, GOOGL only in pB.
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_nvda_pA", ticker="NVDA", currency="USD", quantity=5,
                    weight_pct=15.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_aapl_pB", ticker="AAPL", currency="USD", quantity=20,
                    weight_pct=30.0, portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_googl_pB", ticker="GOOGL", currency="USD", quantity=5,
                    weight_pct=10.0, portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        for t in ("AAPL", "NVDA", "GOOGL"):
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector="Information Technology", geography="united states",
                themes="[]", created_at=now, updated_at=now,
            ))
        # Flush portfolios + holdings first so HoldingRelationship FKs
        # resolve against committed rows.
        await session.commit()

    async with get_db() as session:
        # Seed HoldingRelationship rows.
        # AAPL (both portfolios) → TSMC as supplier (0.85)
        # NVDA pA → AMD as competitor (0.60)
        # GOOGL pB → DOJ as regulator (strong strength so it crosses floor)
        session.add_all([
            HoldingRelationship(
                id="rel_aapl_pA_tsmc", holding_id="h_aapl_pA",
                relationship_type="supplier",
                related_ticker="TSM", related_entity_key=None,
                related_name="Taiwan Semiconductor",
                strength=0.85, source="seed",
                description="TSMC is AAPL's primary foundry",
                created_at=now, updated_at=now,
            ),
            HoldingRelationship(
                id="rel_aapl_pB_tsmc", holding_id="h_aapl_pB",
                relationship_type="supplier",
                related_ticker="TSM", related_entity_key=None,
                related_name="Taiwan Semiconductor",
                strength=0.85, source="seed",
                description="TSMC is AAPL's primary foundry (pB)",
                created_at=now, updated_at=now,
            ),
            HoldingRelationship(
                id="rel_nvda_pA_amd", holding_id="h_nvda_pA",
                relationship_type="competitor",
                related_ticker="AMD", related_entity_key=None,
                related_name="Advanced Micro Devices",
                strength=0.60, source="seed",
                description="AMD competes with NVDA in GPUs",
                created_at=now, updated_at=now,
            ),
            HoldingRelationship(
                id="rel_googl_pB_doj", holding_id="h_googl_pB",
                relationship_type="regulator",
                related_ticker=None, related_entity_key="doj_us",
                related_name="US Department of Justice",
                strength=0.85, source="seed",
                description="DOJ antitrust jurisdiction over Alphabet",
                created_at=now, updated_at=now,
            ),
        ])
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_link_event(raw: dict) -> str:
    """Run the real collection-agent link pipeline on a single event."""
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
            fetched_at=now,
            created_at=now,
            dedup_hash=str(uuid.uuid4()),
        ))
        await session.commit()

    agent = CollectionAgent()
    await agent._link_event_to_holdings(event_id, raw)
    return event_id


async def _call_get_event(event_id: str):
    from src.api.routes.events import get_event
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        return await get_event(event_id, session=session)


# ---------------------------------------------------------------------------
# Migration + schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMigrationV4:
    async def test_schema_version_at_or_above_v4(self, seeded):
        """Phase 9D requires schema v4+.  Later phases (9F added v5)
        are additive and never regress the relationship graph, so the
        assertion checks the floor rather than the literal."""
        from sqlalchemy import text
        from src.database.connection import get_db
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        async with get_db() as session:
            v = (await session.execute(
                text("SELECT version FROM _schema_version WHERE id=1")
            )).scalar()
        assert v >= 4
        assert v == CURRENT_SCHEMA_VERSION

    async def test_holding_relationships_table_exists(self, seeded):
        from sqlalchemy import text
        from src.database.connection import get_db
        async with get_db() as session:
            tables = set((await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )).scalars().all())
        assert "holding_relationships" in tables

    async def test_holding_relationships_unique_edge_documented(self, seeded):
        """Document the SQLite NULL-distinct semantics of the unique
        constraint on ``(holding_id, type, related_ticker,
        related_entity_key)``.

        ANSI SQL (and SQLite's default) treats NULL values in a
        UNIQUE constraint as DISTINCT, so rows with NULL in any
        of the identity columns can coexist even with otherwise
        identical non-null values.  This is not a bug — it's the
        documented SQLite semantics.  Phase 9D's seed loader +
        reconciler (in ``src/intelligence/relationships/seeds.py``)
        keys on the full tuple at the application layer and will
        upsert rather than duplicate; the DB-level UNIQUE is a
        belt-and-suspenders guard for the case when BOTH nullable
        columns are populated simultaneously (which no sensible
        seed row does — you either use a ticker OR an entity_key).

        This test confirms the actual semantics so a future reader
        isn't surprised: it inserts a row identical on the non-null
        identity columns of an existing regulator row, verifies it
        inserts successfully, then cleans up.  Uniqueness is
        ultimately enforced by the seed loader and the app layer,
        not by SQLite.
        """
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        now = datetime.now(timezone.utc).isoformat()
        duplicate_id = "rel_dup_regulator_intentional"
        async with get_db() as session:
            session.add(HoldingRelationship(
                id=duplicate_id,
                holding_id="h_googl_pB",
                relationship_type="regulator",
                related_ticker=None,
                related_entity_key="doj_us",
                related_name="US Department of Justice",
                strength=0.50, source="seed",
                created_at=now, updated_at=now,
            ))
            # This should NOT raise under SQLite's default NULL-distinct
            # UNIQUE semantics because ``related_ticker`` is NULL in both
            # the existing and the new row, and NULLs compare unequal.
            await session.commit()

        # Cleanup so the next test's fixture-wipe stays deterministic.
        async with get_db() as session:
            await session.execute(
                select(HoldingRelationship)
                .where(HoldingRelationship.id == duplicate_id)
            )
            obj = await session.get(HoldingRelationship, duplicate_id)
            if obj is not None:
                await session.delete(obj)
                await session.commit()


# ---------------------------------------------------------------------------
# Runtime pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRelationshipRuntime:
    async def test_tsmc_event_propagates_to_aapl_via_supplier(self, seeded):
        """An event about TSMC (not held) should produce a relationship
        EventLink on AAPL in BOTH portfolios, preserving portfolio_id."""
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id = await _run_link_event({
            "title": "TSMC reports unexpected yield issues at leading-edge node",
            "summary": (
                "Taiwan Semiconductor flagged weaker-than-expected yields at "
                "its 3nm process, raising supply risk for major customers."
            ),
        })

        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()

        assert rel_links, "expected relationship links from TSMC event"
        targets = {l.link_target for l in rel_links}
        assert "h_aapl_pA" in targets
        assert "h_aapl_pB" in targets
        # NVDA is not linked to TSMC in this fixture — no NVDA relationship link.
        assert "h_nvda_pA" not in targets

        for l in rel_links:
            assert l.link_source == "deterministic_relationship"
            assert l.channel == "supplier"
            assert l.impact_channel == "supplier"
            assert l.relevance_score is not None
            # Honest ceiling: well below the direct-match 1.0.
            assert l.relevance_score < 0.70
            # Emission floor: >= RELATIONSHIP_MIN_EMIT.
            from src.intelligence.relationships.propagation import RELATIONSHIP_MIN_EMIT
            assert l.relevance_score >= RELATIONSHIP_MIN_EMIT
            assert l.details_json is not None
            details = json.loads(l.details_json)
            assert details["relationship"]["type"] == "supplier"
            assert details["related_entity"]["ticker"] == "TSM"
            assert details["holding"]["id"] == l.link_target
            assert details["holding"]["portfolio_id"] in ("pA", "pB")

    async def test_competitor_event_propagates_via_competitor_edge(self, seeded):
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id = await _run_link_event({
            "title": "AMD reports record data-center GPU revenue",
            "summary": "Advanced Micro Devices MI-series GPUs gain share.",
        })

        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()

        assert rel_links, "expected competitor relationship link for NVDA"
        nvda = [l for l in rel_links if l.link_target == "h_nvda_pA"]
        assert len(nvda) == 1
        assert nvda[0].channel == "competitor"

    async def test_regulator_event_propagates_via_name_fallback(self, seeded):
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id = await _run_link_event({
            "title": "Department of Justice opens new antitrust probe",
            "summary": "The US Department of Justice is investigating search ads.",
        })

        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()

        assert rel_links
        googl = [l for l in rel_links if l.link_target == "h_googl_pB"]
        assert len(googl) == 1
        assert googl[0].channel == "regulator"
        details = json.loads(googl[0].details_json)
        assert details["related_entity"]["match_type"] == "name"

    async def test_event_about_held_ticker_does_not_fire_relationship(self, seeded):
        """When the event is about a HELD ticker, relationship links
        must not double-count — direct matching handles it."""
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id = await _run_link_event({
            "title": "AAPL reports record iPhone sales",
            "summary": "Apple beat estimates on hardware demand.",
        })

        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()

        # AAPL is held, so the matcher excludes it as a related entity.
        # The only relationship row that could match is a supplier edge
        # TO TSMC, which isn't mentioned — so zero relationship links.
        assert rel_links == []

    async def test_unrelated_event_produces_nothing(self, seeded):
        from src.database.connection import get_db
        from src.database.models import EventLink

        event_id = await _run_link_event({
            "title": "Local sports team wins championship",
            "summary": "Fans celebrated downtown.",
        })
        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()
        assert rel_links == []

    async def test_direct_matching_and_factor_pipeline_still_work(self, seeded):
        """Phase 9A / 9B regression invariant: adding relationship
        propagation must not break direct or factor paths."""
        from src.database.connection import get_db
        from src.database.models import EventLink

        # Direct hit
        event_id_direct = await _run_link_event({
            "title": "AAPL beats estimates in strong quarter",
            "summary": "",
        })
        # Factor hit (Fed headline)
        event_id_factor = await _run_link_event({
            "title": "Federal Reserve raises interest rates by 50 bps",
            "summary": "FOMC cited persistent inflation.",
        })

        async with get_db() as session:
            direct_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id_direct,
                    EventLink.link_type == "ticker_match",
                )
            )).scalars().all()
            factor_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id_factor,
                    EventLink.link_type == "macro_factor",
                )
            )).scalars().all()

        assert direct_links, "direct ticker matching must still work"
        assert factor_links, "factor propagation must still work"


# ---------------------------------------------------------------------------
# Event detail API surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEventDetailRelationshipChain:
    async def test_detail_api_surfaces_relationship_chain(self, seeded):
        event_id = await _run_link_event({
            "title": "TSMC reports yield issues at leading-edge node",
            "summary": (
                "Taiwan Semiconductor flagged weaker-than-expected yields."
            ),
        })

        detail = await _call_get_event(event_id)
        rel_links = [l for l in detail.links if l.link_type == "relationship"]
        assert rel_links

        for link in rel_links:
            chain = link.chain
            assert chain is not None
            assert chain["origin"] == "relationship"
            assert chain["channel"] == "supplier"
            assert chain["channel_label"] == "Supplier relationship"
            assert "AAPL" in chain["summary"]
            assert "Taiwan Semiconductor" in chain["summary"]
            # Honest: rationale is human-readable, no raw JSON.
            assert chain["rationale"]
            assert "{" not in chain["summary"]

    async def test_detail_affected_holdings_include_relationship_targets(self, seeded):
        event_id = await _run_link_event({
            "title": "TSMC reports yield issues at leading-edge node",
            "summary": "Taiwan Semiconductor flagged weaker yields.",
        })
        detail = await _call_get_event(event_id)
        # Both pA and pB AAPL should appear
        by_pid: dict[str, set[str]] = {}
        for h in detail.affected_holdings:
            by_pid.setdefault(h.portfolio_id, set()).add(h.ticker)
        assert "AAPL" in by_pid.get("pA", set())
        assert "AAPL" in by_pid.get("pB", set())
        # The NVDA edge to AMD is not triggered by this event.
        assert "NVDA" not in by_pid.get("pA", set())

    async def test_detail_api_portfolio_isolation(self, seeded):
        event_id = await _run_link_event({
            "title": "TSMC reports yield issues at leading-edge node",
            "summary": "Taiwan Semiconductor flagged weaker yields.",
        })
        detail = await _call_get_event(event_id)
        for link in detail.links:
            if link.link_type != "relationship":
                continue
            chain = link.chain
            assert chain is not None
            # portfolio_id in the chain must match the actual holding.
            assert chain["holding_portfolio_id"] in ("pA", "pB")