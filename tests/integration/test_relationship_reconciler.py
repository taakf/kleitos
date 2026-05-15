"""Integration tests for the Phase 9D corrective-pass seed reconciler.

Covers every contract listed in the corrective-pass brief:

* fresh reconcile creates expected source=seed rows
* repeated reconcile is idempotent (no-ops on unchanged YAML)
* changing the YAML updates existing seed rows safely
* manual rows (source != 'seed') are preserved across reconciles
* multiple portfolios holding the same ticker each receive their
  own seed-anchored relationship row
* seeds referencing a ticker that no portfolio holds are skipped
  without crashing
* runtime relationship propagation works after automatic reconcile
* pruning deletes seed-only rows that disappear from the YAML,
  and NEVER touches manual / ai_inferred rows
* no cross-portfolio leakage in any of the above
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.intelligence.relationships.reconciler import (
    ReconcileStats,
    _seed_row_matches_db,
    reconcile_seed_relationships,
)
from src.intelligence.relationships.seeds import SeedRelationship


# ---------------------------------------------------------------------------
# Temp DB fixture — same shape as the Phase 9D integration suite
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tmp_db(tmp_path_factory):
    from src.config import get_settings

    prior_env_db = os.environ.get("KLEITOS_DB_PATH")
    prior_env_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_settings = get_settings()
    prior_auth_enabled = prior_settings.api.auth_enabled

    db_dir = tmp_path_factory.mktemp("axion_reconciler")
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
    """Clean DB per test with two portfolios and shared-ticker holdings."""
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
        for model in (
            EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship,
            Alert, Digest, Event, Holding, Security, Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Portfolio A", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
            Portfolio(id="pB", name="Portfolio B", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
        ])
        # AAPL is held in BOTH portfolios on purpose — tests assert
        # the reconciler creates one seed row per matching holding.
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_aapl_pB", ticker="AAPL", currency="USD", quantity=20,
                    weight_pct=30.0, portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_nvda_pA", ticker="NVDA", currency="USD", quantity=5,
                    weight_pct=15.0, portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
        ])
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# YAML fixture factory
# ---------------------------------------------------------------------------


def _write_yaml(
    tmp_path, entries: list[dict],
) -> str:
    """Write a minimal seed YAML file and return its path."""
    import yaml
    path = tmp_path / "relationships.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "relationships": entries}),
        encoding="utf-8",
    )
    return str(path)


# ---------------------------------------------------------------------------
# Pure helper test (no DB)
# ---------------------------------------------------------------------------


class TestSeedRowMatchesDb:
    """Pure comparison helper — no DB involvement."""

    def _make_row(self, strength=0.85, name="Taiwan Semi", desc="foo"):
        # Simple stand-in for a HoldingRelationship row with just the
        # attributes the comparator reads.
        class _R:
            pass
        r = _R()
        r.strength = strength
        r.related_name = name
        r.description = desc
        return r

    def test_equal_rows_match(self):
        seed = SeedRelationship(
            "AAPL", "supplier", "TSM", None, "Taiwan Semi", 0.85, "foo",
        )
        row = self._make_row()
        assert _seed_row_matches_db(seed, row) is True

    def test_strength_change_detected(self):
        seed = SeedRelationship(
            "AAPL", "supplier", "TSM", None, "Taiwan Semi", 0.75, "foo",
        )
        row = self._make_row(strength=0.85)
        assert _seed_row_matches_db(seed, row) is False

    def test_name_change_detected(self):
        seed = SeedRelationship(
            "AAPL", "supplier", "TSM", None, "Taiwan Semiconductor", 0.85, "foo",
        )
        row = self._make_row(name="Taiwan Semi")
        assert _seed_row_matches_db(seed, row) is False

    def test_description_change_detected(self):
        seed = SeedRelationship(
            "AAPL", "supplier", "TSM", None, "Taiwan Semi", 0.85, "new desc",
        )
        row = self._make_row(desc="foo")
        assert _seed_row_matches_db(seed, row) is False

    def test_tiny_float_difference_tolerated(self):
        """1e-12 drift must not trigger a spurious update."""
        seed = SeedRelationship(
            "AAPL", "supplier", "TSM", None, "Taiwan Semi", 0.85, "foo",
        )
        row = self._make_row(strength=0.85 + 1e-12)
        assert _seed_row_matches_db(seed, row) is True


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFreshReconcile:
    async def test_creates_source_seed_rows(self, seeded, tmp_path):
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",
                "type": "supplier",
                "strength": 0.85,
                "description": "TSMC is AAPL's primary foundry",
            },
        ])

        stats = await reconcile_seed_relationships(yaml_path=yaml_path)

        # Both AAPL holdings (pA and pB) should get their own seed row.
        assert stats.seed_rows_loaded == 1
        assert stats.created == 2
        assert stats.updated == 0
        assert stats.unchanged == 0
        assert stats.pruned == 0

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship)
                .where(HoldingRelationship.relationship_type == "supplier")
                .where(HoldingRelationship.related_ticker == "TSM")
            )).scalars().all()

        assert len(rows) == 2
        holding_ids = {r.holding_id for r in rows}
        assert holding_ids == {"h_aapl_pA", "h_aapl_pB"}
        for r in rows:
            assert r.source == "seed"
            assert r.strength == pytest.approx(0.85)
            assert r.related_name == "Taiwan Semiconductor"
            assert r.description == "TSMC is AAPL's primary foundry"
            assert r.created_at == r.updated_at

    async def test_missing_holding_is_skipped(self, seeded, tmp_path):
        """Seed references MSFT, but no portfolio holds MSFT."""
        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "MSFT",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semi",
                "type": "supplier",
                "strength": 0.80,
            },
        ])

        stats = await reconcile_seed_relationships(yaml_path=yaml_path)

        assert stats.seed_rows_loaded == 1
        assert stats.created == 0
        assert stats.skipped_no_holding == 1

    async def test_empty_yaml_is_noop(self, seeded, tmp_path):
        yaml_path = _write_yaml(tmp_path, [])
        stats = await reconcile_seed_relationships(yaml_path=yaml_path)
        assert stats == ReconcileStats()

    async def test_nonexistent_yaml_is_noop(self, seeded, tmp_path):
        # Pointing at a path that doesn't exist must not crash.
        stats = await reconcile_seed_relationships(
            yaml_path=tmp_path / "does_not_exist.yaml",
        )
        assert stats.seed_rows_loaded == 0
        assert stats.created == 0


@pytest.mark.asyncio
class TestIdempotency:
    async def test_repeated_reconcile_produces_no_writes(self, seeded, tmp_path):
        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",
                "type": "supplier",
                "strength": 0.85,
            },
        ])
        first = await reconcile_seed_relationships(yaml_path=yaml_path)
        assert first.created == 2

        second = await reconcile_seed_relationships(yaml_path=yaml_path)
        assert second.created == 0
        assert second.updated == 0
        assert second.unchanged == 2
        assert second.pruned == 0

        third = await reconcile_seed_relationships(yaml_path=yaml_path)
        assert third == second


@pytest.mark.asyncio
class TestUpdates:
    async def test_strength_change_updates_row(self, seeded, tmp_path):
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        yaml_initial = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semi",
                "type": "supplier",
                "strength": 0.70,
            },
        ])
        await reconcile_seed_relationships(yaml_path=yaml_initial)

        # Change the strength and re-reconcile.
        yaml_updated = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",  # name also changed
                "type": "supplier",
                "strength": 0.90,
                "description": "Primary foundry",
            },
        ])
        stats = await reconcile_seed_relationships(yaml_path=yaml_updated)

        assert stats.created == 0
        assert stats.updated == 2
        assert stats.unchanged == 0

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship).where(
                    HoldingRelationship.related_ticker == "TSM"
                )
            )).scalars().all()

        assert len(rows) == 2
        for r in rows:
            assert r.strength == pytest.approx(0.90)
            assert r.related_name == "Taiwan Semiconductor"
            assert r.description == "Primary foundry"
            # updated_at should differ from created_at after an update
            assert r.updated_at >= r.created_at


@pytest.mark.asyncio
class TestPreserveManualRows:
    async def test_manual_row_at_same_identity_not_touched(self, seeded, tmp_path):
        """An operator-authored manual row at the same identity must
        survive a reconcile pass untouched.  The reconciler logs the
        skip in stats.skipped_manual_row and never writes."""
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(HoldingRelationship(
                id="manual_1",
                holding_id="h_aapl_pA",
                relationship_type="supplier",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semiconductor (operator-authored)",
                strength=0.99,
                source="manual",
                description="operator note",
                created_at=now,
                updated_at=now,
            ))
            await session.commit()

        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",
                "type": "supplier",
                "strength": 0.70,   # different from the manual row
            },
        ])
        stats = await reconcile_seed_relationships(yaml_path=yaml_path)

        # pA holding has a manual row → skipped.
        # pB holding has nothing → created.
        assert stats.created == 1
        assert stats.skipped_manual_row == 1

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship).where(
                    HoldingRelationship.holding_id == "h_aapl_pA"
                )
            )).scalars().all()

        assert len(rows) == 1
        manual = rows[0]
        assert manual.id == "manual_1"
        assert manual.source == "manual"
        assert manual.strength == pytest.approx(0.99)
        assert manual.related_name.endswith("(operator-authored)")

    async def test_manual_row_survives_prune(self, seeded, tmp_path):
        """When a YAML seed is removed, the reconciler prunes seed
        rows but NEVER touches manual rows."""
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        # Start with a seed row on pA AAPL and a DIFFERENT manual row
        # on pB AAPL (different relationship_type so the seed and
        # manual rows have different identities — manual is the only
        # row at its identity).
        yaml_initial = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semi",
                "type": "supplier",
                "strength": 0.85,
            },
        ])
        await reconcile_seed_relationships(yaml_path=yaml_initial)

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(HoldingRelationship(
                id="manual_pB_customer",
                holding_id="h_aapl_pB",
                relationship_type="customer",
                related_ticker="TSM",
                related_entity_key=None,
                related_name="Taiwan Semi (manual)",
                strength=0.50,
                source="manual",
                description=None,
                created_at=now,
                updated_at=now,
            ))
            await session.commit()

        # Now write an empty YAML → every seed row should be pruned,
        # manual row should survive.
        yaml_empty = _write_yaml(tmp_path, [])
        stats = await reconcile_seed_relationships(yaml_path=yaml_empty)

        assert stats.pruned == 2   # the two seed rows created earlier

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship)
            )).scalars().all()

        assert len(rows) == 1
        assert rows[0].id == "manual_pB_customer"
        assert rows[0].source == "manual"


@pytest.mark.asyncio
class TestPortfolioIsolation:
    async def test_each_portfolio_gets_its_own_row(self, seeded, tmp_path):
        """A single YAML seed row naming AAPL as the held ticker
        must expand to one DB row per AAPL position, each with its
        own holding_id and (via the FK) its own portfolio_id.  This
        is how multi-portfolio correctness flows structurally."""
        from src.database.connection import get_db
        from src.database.models import Holding, HoldingRelationship

        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semi",
                "type": "supplier",
                "strength": 0.85,
            },
        ])
        await reconcile_seed_relationships(yaml_path=yaml_path)

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship, Holding)
                .join(Holding, HoldingRelationship.holding_id == Holding.id)
                .where(HoldingRelationship.related_ticker == "TSM")
            )).all()

        pairs = [(rel.holding_id, h.portfolio_id) for rel, h in rows]
        assert len(pairs) == 2
        assert ("h_aapl_pA", "pA") in pairs
        assert ("h_aapl_pB", "pB") in pairs

    async def test_pruning_does_not_touch_other_portfolios(
        self, seeded, tmp_path,
    ):
        """Removing AAPL from the YAML must prune BOTH AAPL seed rows
        (pA and pB), but must NOT touch any other relationship row."""
        from src.database.connection import get_db
        from src.database.models import HoldingRelationship

        # Seed AAPL→TSM + NVDA→AMD competitor.
        yaml_both = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semi",
                "type": "supplier",
                "strength": 0.85,
            },
            {
                "ticker": "NVDA",
                "related_ticker": "AMD",
                "related_name": "Advanced Micro Devices",
                "type": "competitor",
                "strength": 0.60,
            },
        ])
        await reconcile_seed_relationships(yaml_path=yaml_both)

        # Drop the AAPL row.
        yaml_only_nvda = _write_yaml(tmp_path, [
            {
                "ticker": "NVDA",
                "related_ticker": "AMD",
                "related_name": "Advanced Micro Devices",
                "type": "competitor",
                "strength": 0.60,
            },
        ])
        stats = await reconcile_seed_relationships(yaml_path=yaml_only_nvda)

        assert stats.pruned == 2
        assert stats.unchanged == 1

        async with get_db() as session:
            rows = (await session.execute(
                select(HoldingRelationship)
            )).scalars().all()

        assert len(rows) == 1
        assert rows[0].holding_id == "h_nvda_pA"
        assert rows[0].relationship_type == "competitor"


# ---------------------------------------------------------------------------
# End-to-end: runtime propagation after automatic reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRuntimeAfterReconcile:
    async def test_collection_path_uses_seeded_rows(self, seeded, tmp_path):
        """After reconcile, the CollectionAgent relationship pipeline
        must see the seeded rows and emit ``link_type='relationship'``
        EventLinks for a qualifying event.  Zero manual DB edits."""
        from src.agents.collection import CollectionAgent
        from src.database.connection import get_db
        from src.database.models import Event, EventLink

        # Reconcile a supplier edge: AAPL → TSM.
        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",
                "type": "supplier",
                "strength": 0.85,
            },
        ])
        stats = await reconcile_seed_relationships(yaml_path=yaml_path)
        assert stats.created == 2

        # Persist an event about TSMC (the related entity), then run
        # the real collection link pipeline on it.
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(Event(
                id=event_id,
                title="TSMC reports yield issues at leading-edge node",
                summary=(
                    "Taiwan Semiconductor flagged weaker-than-expected yields "
                    "at its advanced node."
                ),
                fetched_at=now,
                created_at=now,
                dedup_hash=str(uuid.uuid4()),
            ))
            await session.commit()

        agent = CollectionAgent()
        await agent._link_event_to_holdings(event_id, {
            "title": "TSMC reports yield issues at leading-edge node",
            "summary": (
                "Taiwan Semiconductor flagged weaker-than-expected yields "
                "at its advanced node."
            ),
        })

        async with get_db() as session:
            rel_links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()

        # Both AAPL holdings emit their own relationship link, each
        # carrying its own holding_id → portfolio_id.
        assert len(rel_links) == 2
        targets = {l.link_target for l in rel_links}
        assert targets == {"h_aapl_pA", "h_aapl_pB"}
        for l in rel_links:
            assert l.channel == "supplier"
            assert l.link_source == "deterministic_relationship"
            assert l.relevance_score is not None
            # Honest ceiling: relationship confidence < direct match.
            assert l.relevance_score < 0.70

    async def test_no_cross_portfolio_leakage_end_to_end(
        self, seeded, tmp_path,
    ):
        """After reconcile + event linking, every emitted relationship
        EventLink points at a holding that actually exists in the DB,
        and the details_json carries the real portfolio_id."""
        import json
        from src.agents.collection import CollectionAgent
        from src.database.connection import get_db
        from src.database.models import EventLink, Event, Holding

        yaml_path = _write_yaml(tmp_path, [
            {
                "ticker": "AAPL",
                "related_ticker": "TSM",
                "related_name": "Taiwan Semiconductor",
                "type": "supplier",
                "strength": 0.85,
            },
        ])
        await reconcile_seed_relationships(yaml_path=yaml_path)

        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            session.add(Event(
                id=event_id,
                title="TSMC reports yield issues",
                summary="Taiwan Semiconductor flagged yield problems.",
                fetched_at=now,
                created_at=now,
                dedup_hash=str(uuid.uuid4()),
            ))
            await session.commit()

        agent = CollectionAgent()
        await agent._link_event_to_holdings(event_id, {
            "title": "TSMC reports yield issues",
            "summary": "Taiwan Semiconductor flagged yield problems.",
        })

        async with get_db() as session:
            links = (await session.execute(
                select(EventLink).where(
                    EventLink.event_id == event_id,
                    EventLink.link_type == "relationship",
                )
            )).scalars().all()
            holdings_by_id = {
                h.id: h for h in (
                    await session.execute(select(Holding))
                ).scalars().all()
            }

        pids_seen: set[str] = set()
        for l in links:
            assert l.link_target in holdings_by_id
            details = json.loads(l.details_json)
            real_pid = holdings_by_id[l.link_target].portfolio_id
            assert details["holding"]["portfolio_id"] == real_pid
            pids_seen.add(real_pid)

        assert pids_seen == {"pA", "pB"}
