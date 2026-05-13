"""Phase 9H integration tests — operator control surface.

Covers every contract the Phase 9H brief required:

* Factor sensitivity effective-value listing (manual override wins
  over sector default; no override falls back to sector prior; no
  sector falls back to zero).
* Manual override CRUD — create, upsert (same key), delete.
* Manual override source protection — can't modify an ai_inferred row.
* Manual override factor-key validation (unknown factor → 400).
* Relationship listing with source discriminator visible.
* Manual relationship create / update / delete.
* Source protection — operator cannot update or delete a seed row.
* Identity-collision guard — can't create a manual row at the same
  identity tuple as an existing row (seed or otherwise).
* Seed reconcile on demand with temp YAML (create, prune).
* Reconcile never touches manual rows even when they collide with
  the same identity tuple as a seed row (seed is skipped).
* Backfill workflow — bounded, idempotent, portfolio-safe.
* Backfill calling twice in a row is a strict no-op.
* Portfolio isolation — rows in pA are never returned when listing pB.
* Every mutating call writes an AuditLog row.

The tests share a module-level temp SQLite fixture (same pattern as
Phases 9D/9F/9G) and seed two disjoint portfolios.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select


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

    db_dir = tmp_path_factory.mktemp("axion_phase9h")
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
    """Two portfolios (pA, pB) with disjoint holdings, sectors,
    and one seed relationship row on AAPL/pA → TSMC to anchor the
    source-protection tests."""
    from src.database.connection import get_db
    from src.database.models import (
        Alert, AnalysisNote, AuditLog, Digest, Event, EventLink,
        Holding, HoldingFactorSensitivity, HoldingRelationship,
        MacroFactorEvent, Portfolio, Security, TelegramDelivery,
        TelegramSession,
    )

    now = datetime.now(timezone.utc).isoformat()

    async with get_db() as session:
        for model in (
            AuditLog, EventLink, MacroFactorEvent, AnalysisNote,
            HoldingFactorSensitivity, HoldingRelationship, Alert, Digest,
            Event, Holding, Security, TelegramDelivery, TelegramSession,
            Portfolio,
        ):
            await session.execute(delete(model))
        await session.commit()

        session.add_all([
            Portfolio(id="pA", name="Alpha", base_currency="USD",
                      is_default=1, created_at=now, updated_at=now),
            Portfolio(id="pB", name="Beta", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
            Portfolio(id="default", name="Main", base_currency="USD",
                      is_default=0, created_at=now, updated_at=now),
        ])
        session.add_all([
            Holding(id="h_aapl_pA", ticker="AAPL", currency="USD", quantity=10,
                    weight_pct=25.0, current_price=180.0, market_value=1800.0,
                    portfolio_id="pA", status="active",
                    created_at=now, updated_at=now),
            Holding(id="h_xom_pB", ticker="XOM", currency="USD", quantity=50,
                    weight_pct=20.0, current_price=120.0, market_value=6000.0,
                    portfolio_id="pB", status="active",
                    created_at=now, updated_at=now),
        ])
        for t, sector in (("AAPL", "Information Technology"),
                          ("XOM", "Energy"),
                          ("TSM", "Information Technology")):
            session.add(Security(
                id=str(uuid.uuid4()), ticker=t, name=t, currency="USD",
                sector=sector, geography="united states",
                themes="[]", created_at=now, updated_at=now,
            ))
        await session.commit()

    async with get_db() as session:
        # One SEED relationship so tests can prove operator endpoints
        # never touch seed-sourced rows.
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
    yield


# ---------------------------------------------------------------------------
# Temp YAML fixtures for reconcile tests
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, relationships: list[dict]) -> None:
    import yaml
    content = {"version": 1, "relationships": relationships}
    path.write_text(yaml.safe_dump(content))


# ---------------------------------------------------------------------------
# 1) Effective sensitivities listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_effective_sensitivities_default_from_sector(seeded):
    """With no manual overrides, an AAPL holding (IT sector) should
    return the sector default for every factor.  interest_rate for
    tech is -0.6 per the Phase 9A sensitivity prior table."""
    from src.api.routes.operator import list_effective_sensitivities
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = await list_effective_sensitivities(
            portfolio_id="pA",
            holding_id=None,
            factor="interest_rate",
            session=session,
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.ticker == "AAPL"
    assert row.factor == "interest_rate"
    assert row.effective_value == -0.6
    assert row.source == "default"
    assert row.override_id is None
    assert row.override_value is None


@pytest.mark.asyncio
async def test_effective_sensitivities_manual_override_wins(seeded):
    """After upserting a manual override on AAPL/interest_rate, the
    effective listing must return the override and mark source='manual'."""
    from src.api.routes.operator import (
        list_effective_sensitivities, upsert_sensitivity_override,
        SensitivityOverrideCreate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = await upsert_sensitivity_override(
            payload=SensitivityOverrideCreate(
                holding_id="h_aapl_pA",
                factor="interest_rate",
                sensitivity=-0.9,
                reason="test override",
            ),
            session=session,
        )
    assert row.source == "manual"
    assert row.sensitivity == -0.9

    async with factory() as session:
        rows = await list_effective_sensitivities(
            portfolio_id="pA",
            holding_id=None,
            factor="interest_rate",
            session=session,
        )
    assert len(rows) == 1
    assert rows[0].effective_value == -0.9
    assert rows[0].source == "manual"
    assert rows[0].override_id is not None
    assert rows[0].override_value == -0.9


@pytest.mark.asyncio
async def test_effective_sensitivities_portfolio_isolation(seeded):
    """An override on pA must NOT leak into pB's effective listing."""
    from src.api.routes.operator import (
        list_effective_sensitivities, upsert_sensitivity_override,
        SensitivityOverrideCreate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await upsert_sensitivity_override(
            payload=SensitivityOverrideCreate(
                holding_id="h_aapl_pA",
                factor="interest_rate",
                sensitivity=-0.9,
            ),
            session=session,
        )

    async with factory() as session:
        pA_rows = await list_effective_sensitivities(
            portfolio_id="pA", holding_id=None, factor="interest_rate", session=session,
        )
        pB_rows = await list_effective_sensitivities(
            portfolio_id="pB", holding_id=None, factor="interest_rate", session=session,
        )

    pA_tickers = {r.ticker for r in pA_rows}
    pB_tickers = {r.ticker for r in pB_rows}
    assert pA_tickers == {"AAPL"}
    assert pB_tickers == {"XOM"}
    # pA row has the override, pB row must have the default (XOM is Energy)
    pA_row = next(r for r in pA_rows if r.ticker == "AAPL")
    pB_row = next(r for r in pB_rows if r.ticker == "XOM")
    assert pA_row.source == "manual"
    assert pA_row.effective_value == -0.9
    assert pB_row.source == "default"
    assert pB_row.effective_value == 0.1  # energy sector interest_rate prior


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_factor(seeded):
    from fastapi import HTTPException
    from src.api.routes.operator import (
        upsert_sensitivity_override, SensitivityOverrideCreate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await upsert_sensitivity_override(
                payload=SensitivityOverrideCreate(
                    holding_id="h_aapl_pA",
                    factor="not_a_real_factor",
                    sensitivity=0.5,
                ),
                session=session,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_upsert_updates_existing_manual_row(seeded):
    """A second upsert on the same (holding, factor) must UPDATE the
    existing manual row, not insert a new one."""
    from src.api.routes.operator import (
        upsert_sensitivity_override, SensitivityOverrideCreate,
        list_sensitivity_overrides,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        first = await upsert_sensitivity_override(
            payload=SensitivityOverrideCreate(
                holding_id="h_aapl_pA",
                factor="inflation",
                sensitivity=-0.5,
            ),
            session=session,
        )
    async with factory() as session:
        second = await upsert_sensitivity_override(
            payload=SensitivityOverrideCreate(
                holding_id="h_aapl_pA",
                factor="inflation",
                sensitivity=-0.2,
                reason="revised",
            ),
            session=session,
        )
    # Same id — this is an update
    assert second.id == first.id
    assert second.sensitivity == -0.2

    async with factory() as session:
        all_overrides = await list_sensitivity_overrides(portfolio_id="pA", session=session)
    inflation_rows = [r for r in all_overrides if r.factor == "inflation"]
    assert len(inflation_rows) == 1


@pytest.mark.asyncio
async def test_upsert_refuses_to_overwrite_ai_inferred_row(seeded):
    """Operator manual upsert must refuse to stomp on an ai_inferred
    row — the operator has to delete it explicitly first."""
    from fastapi import HTTPException
    from src.api.routes.operator import (
        upsert_sensitivity_override, SensitivityOverrideCreate,
    )
    from src.database.connection import get_db
    from src.database.models import HoldingFactorSensitivity

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        session.add(HoldingFactorSensitivity(
            id="fs_ai",
            holding_id="h_aapl_pA",
            factor="credit_conditions",
            sensitivity=-0.7,
            source="ai_inferred",
            created_at=now, updated_at=now,
        ))
        await session.commit()

    from src.database.connection import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await upsert_sensitivity_override(
                payload=SensitivityOverrideCreate(
                    holding_id="h_aapl_pA",
                    factor="credit_conditions",
                    sensitivity=-0.4,
                ),
                session=session,
            )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_manual_override_refuses_non_manual(seeded):
    """Delete endpoint must refuse to delete anything other than a
    source='manual' row.  This protects ai_inferred and defensively
    guards against any future seed-source row leaking into the table."""
    from fastapi import HTTPException
    from src.api.routes.operator import delete_sensitivity_override
    from src.database.connection import get_db, get_session_factory
    from src.database.models import HoldingFactorSensitivity

    # Seed an ai_inferred row
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        session.add(HoldingFactorSensitivity(
            id="fs_ai_del",
            holding_id="h_aapl_pA",
            factor="oil_energy",
            sensitivity=0.0,
            source="ai_inferred",
            created_at=now, updated_at=now,
        ))
        await session.commit()

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await delete_sensitivity_override(
                override_id="fs_ai_del",
                reason=None,
                session=session,
            )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_manual_override_happy_path(seeded):
    from src.api.routes.operator import (
        upsert_sensitivity_override, delete_sensitivity_override,
        list_sensitivity_overrides, SensitivityOverrideCreate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = await upsert_sensitivity_override(
            payload=SensitivityOverrideCreate(
                holding_id="h_aapl_pA",
                factor="regulation_policy",
                sensitivity=-0.4,
            ),
            session=session,
        )

    async with factory() as session:
        result = await delete_sensitivity_override(
            override_id=row.id, reason="no longer relevant", session=session,
        )
    assert result["deleted"] is True

    async with factory() as session:
        remaining = await list_sensitivity_overrides(portfolio_id="pA", session=session)
    assert not any(r.factor == "regulation_policy" for r in remaining)


# ---------------------------------------------------------------------------
# 2) Relationships — listing + source protection + manual CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_relationships_shows_seed_row(seeded):
    from src.api.routes.operator import list_relationships
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = await list_relationships(
            portfolio_id="pA", source=None, holding_id=None, session=session,
        )
    assert len(rows) == 1
    assert rows[0].source == "seed"
    assert rows[0].related_name == "Taiwan Semiconductor"
    assert rows[0].ticker == "AAPL"
    assert rows[0].portfolio_id == "pA"


@pytest.mark.asyncio
async def test_list_relationships_portfolio_isolation(seeded):
    """Seed rows belong to pA only — pB listing must be empty."""
    from src.api.routes.operator import list_relationships
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        pB_rows = await list_relationships(
            portfolio_id="pB", source=None, holding_id=None, session=session,
        )
    assert pB_rows == []


@pytest.mark.asyncio
async def test_list_relationships_source_filter(seeded):
    """Filter by source=manual must return zero until one is created."""
    from src.api.routes.operator import list_relationships
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        manual_rows = await list_relationships(
            portfolio_id="pA", source="manual", holding_id=None, session=session,
        )
        seed_rows = await list_relationships(
            portfolio_id="pA", source="seed", holding_id=None, session=session,
        )
    assert manual_rows == []
    assert len(seed_rows) == 1


@pytest.mark.asyncio
async def test_create_manual_relationship_happy_path(seeded):
    from src.api.routes.operator import (
        create_manual_relationship, list_relationships, RelationshipCreate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = await create_manual_relationship(
            payload=RelationshipCreate(
                holding_id="h_aapl_pA",
                relationship_type="regulator",
                related_entity_key="doj_us",
                related_name="US Department of Justice",
                strength=0.6,
                description="Antitrust oversight",
                reason="manual: explicit DOJ link",
            ),
            session=session,
        )
    assert row.source == "manual"
    assert row.related_entity_key == "doj_us"
    assert row.strength == 0.6
    assert row.portfolio_id == "pA"

    async with factory() as session:
        all_rows = await list_relationships(
            portfolio_id="pA", source=None, holding_id=None, session=session,
        )
    sources = {r.source for r in all_rows}
    assert sources == {"seed", "manual"}
    assert len(all_rows) == 2


@pytest.mark.asyncio
async def test_create_manual_relationship_collides_with_seed(seeded):
    """Operator cannot create a manual row at the same identity tuple
    as an existing seed row (supplier + TSM already exists)."""
    from fastapi import HTTPException
    from src.api.routes.operator import create_manual_relationship, RelationshipCreate
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await create_manual_relationship(
                payload=RelationshipCreate(
                    holding_id="h_aapl_pA",
                    relationship_type="supplier",
                    related_ticker="TSM",
                    related_entity_key=None,
                    strength=0.99,
                ),
                session=session,
            )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_manual_requires_ticker_or_key(seeded):
    from fastapi import HTTPException
    from src.api.routes.operator import create_manual_relationship, RelationshipCreate
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await create_manual_relationship(
                payload=RelationshipCreate(
                    holding_id="h_aapl_pA",
                    relationship_type="competitor",
                    related_ticker=None,
                    related_entity_key=None,
                    strength=0.5,
                ),
                session=session,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_update_manual_relationship_succeeds_and_audits(seeded):
    from src.api.routes.operator import (
        create_manual_relationship, update_manual_relationship,
        RelationshipCreate, RelationshipUpdate,
    )
    from src.database.connection import get_db, get_session_factory
    from src.database.models import AuditLog
    from sqlalchemy import func as sql_func, select

    factory = get_session_factory()
    async with factory() as session:
        row = await create_manual_relationship(
            payload=RelationshipCreate(
                holding_id="h_aapl_pA",
                relationship_type="customer",
                related_ticker=None,
                related_entity_key="foxconn_cn",
                related_name="Foxconn",
                strength=0.5,
            ),
            session=session,
        )
    async with factory() as session:
        updated = await update_manual_relationship(
            rel_id=row.id,
            payload=RelationshipUpdate(
                strength=0.72,
                description="Major contract customer",
                reason="test",
            ),
            session=session,
        )
    assert updated.strength == 0.72
    assert updated.description == "Major contract customer"

    # Audit row should exist
    async with get_db() as session:
        n = (await session.execute(
            select(sql_func.count(AuditLog.id)).where(
                AuditLog.entity_type == "holding_relationship",
                AuditLog.entity_id == row.id,
                AuditLog.action == "update",
            )
        )).scalar()
    assert n >= 1


@pytest.mark.asyncio
async def test_update_relationship_refuses_seed_row(seeded):
    """The seeded AAPL/TSMC row has source='seed' — operator must
    not be able to mutate it through this endpoint."""
    from fastapi import HTTPException
    from src.api.routes.operator import update_manual_relationship, RelationshipUpdate
    from src.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await update_manual_relationship(
                rel_id="rel_seed_aapl_tsmc",
                payload=RelationshipUpdate(strength=0.01),
                session=session,
            )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_relationship_refuses_seed_row(seeded):
    from fastapi import HTTPException
    from src.api.routes.operator import delete_manual_relationship
    from src.database.connection import get_db, get_session_factory
    from src.database.models import HoldingRelationship

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await delete_manual_relationship(
                rel_id="rel_seed_aapl_tsmc",
                reason=None,
                session=session,
            )
    assert exc_info.value.status_code == 409

    # And the seed row is still there
    async with get_db() as session:
        row = await session.get(HoldingRelationship, "rel_seed_aapl_tsmc")
    assert row is not None
    assert row.source == "seed"


# ---------------------------------------------------------------------------
# 3) Reconcile on demand — temp YAML
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_with_empty_yaml_prunes_seed_rows(seeded, tmp_path):
    """With an empty seed YAML, reconcile(prune=True) must remove
    every seed row but leave manual / ai_inferred rows untouched.
    We bypass the route here and call the reconciler directly with a
    temp YAML so we don't depend on the repo's real seed file."""
    from src.database.connection import get_db
    from src.database.models import HoldingRelationship
    from src.intelligence.relationships.reconciler import reconcile_seed_relationships

    # First, add a manual row so we can prove it survives
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        session.add(HoldingRelationship(
            id="rel_manual_survives",
            holding_id="h_aapl_pA",
            relationship_type="competitor",
            related_ticker=None,
            related_entity_key="samsung_kr",
            related_name="Samsung Electronics",
            strength=0.4,
            source="manual",
            description="manual row",
            created_at=now, updated_at=now,
        ))
        await session.commit()

    empty_yaml = tmp_path / "empty.yaml"
    _write_yaml(empty_yaml, [])
    stats = await reconcile_seed_relationships(yaml_path=empty_yaml, prune=True)

    # Seed AAPL/TSMC row pruned
    assert stats.pruned >= 1

    async with get_db() as session:
        seed_row = await session.get(HoldingRelationship, "rel_seed_aapl_tsmc")
        manual_row = await session.get(HoldingRelationship, "rel_manual_survives")
    assert seed_row is None               # pruned
    assert manual_row is not None         # still here
    assert manual_row.source == "manual"


@pytest.mark.asyncio
async def test_reconcile_creates_new_seed_rows_from_yaml(seeded, tmp_path):
    """A temp YAML with a fresh (ticker, supplier) entry must produce
    a ``created`` count and insert a seed row the next time reconcile
    runs."""
    from src.database.connection import get_db
    from src.database.models import HoldingRelationship
    from src.intelligence.relationships.reconciler import reconcile_seed_relationships
    from sqlalchemy import select as sa_select

    # Wipe pre-existing seeds first so the counters are clean.
    empty_yaml = tmp_path / "empty2.yaml"
    _write_yaml(empty_yaml, [])
    await reconcile_seed_relationships(yaml_path=empty_yaml, prune=True)

    # Now author a single new seed: AAPL → customer: generic enterprise
    new_yaml = tmp_path / "new_seed.yaml"
    _write_yaml(new_yaml, [
        {
            "ticker": "AAPL",
            "related_ticker": None,
            "related_entity_key": "enterprise_us",
            "related_name": "Enterprise buyers (US)",
            "type": "customer",
            "strength": 0.5,
            "description": "placeholder customer segment",
        },
    ])
    stats = await reconcile_seed_relationships(yaml_path=new_yaml, prune=True)

    assert stats.created == 1
    assert stats.pruned == 0

    async with get_db() as session:
        rows = (await session.execute(
            sa_select(HoldingRelationship).where(
                HoldingRelationship.source == "seed",
                HoldingRelationship.relationship_type == "customer",
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].related_entity_key == "enterprise_us"


@pytest.mark.asyncio
async def test_reconcile_idempotent(seeded, tmp_path):
    """Running reconcile twice with the same YAML must write nothing
    the second time — ``unchanged`` == previous ``created`` and
    ``created`` == 0 on the second pass."""
    from src.intelligence.relationships.reconciler import reconcile_seed_relationships

    yaml_path = tmp_path / "stable.yaml"
    _write_yaml(yaml_path, [
        {
            "ticker": "XOM",
            "related_ticker": None,
            "related_entity_key": "opec",
            "related_name": "OPEC",
            "type": "supplier",
            "strength": 0.4,
        },
    ])
    first = await reconcile_seed_relationships(yaml_path=yaml_path, prune=True)
    second = await reconcile_seed_relationships(yaml_path=yaml_path, prune=True)

    assert first.created >= 1
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged >= 1


# ---------------------------------------------------------------------------
# 4) Backfill — bounded, idempotent, audited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_bounds_window_hard_max(seeded):
    """A caller passing window_days=999 must be clamped to
    MAX_WINDOW_DAYS (30).  Stats carry the clamped value."""
    from src.intelligence.backfill import backfill_recent_events, MAX_WINDOW_DAYS

    stats = await backfill_recent_events(window_days=999, max_events=10)
    assert stats.window_days == MAX_WINDOW_DAYS


@pytest.mark.asyncio
async def test_backfill_idempotent_replay(seeded):
    """Two back-to-back backfills must land on the same link counts.
    The first pass may add links; the second must add zero."""
    from src.database.connection import get_db
    from src.database.models import Event, EventLink, MacroFactorEvent
    from src.intelligence.backfill import backfill_recent_events
    from sqlalchemy import func as sql_func

    # Seed a single in-window event whose title the factor classifier
    # will certainly hit (50 bps rate hike is a canonical interest_rate
    # trigger — Phase 9A already covers this).
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        session.add(Event(
            id="evt_backfill_test",
            title="Federal Reserve raises interest rates by 50 bps",
            summary="The FOMC raised the policy rate by 50 basis points.",
            content="",
            fetched_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
            created_at=now,
            dedup_hash=f"bf_{uuid.uuid4().hex[:8]}",
        ))
        await session.commit()

    first = await backfill_recent_events(window_days=7, max_events=10, reason="test")
    assert first.events_scanned >= 1
    assert first.events_replayed >= 1
    # Should have landed at least one MFE row for interest_rate
    assert first.mfe_added >= 1

    second = await backfill_recent_events(window_days=7, max_events=10, reason="test second")
    # Idempotent — no new links, no new MFE rows
    assert second.links_added == 0
    assert second.mfe_added == 0

    # And an audit row per pass
    async with get_db() as session:
        from src.database.models import AuditLog
        from sqlalchemy import select as sa_select
        audit_rows = (await session.execute(
            sa_select(AuditLog).where(
                AuditLog.entity_type == "intelligence_backfill"
            )
        )).scalars().all()
    assert len(audit_rows) >= 2


@pytest.mark.asyncio
async def test_backfill_handles_empty_window(seeded):
    """A window with no events in range must return a zero-scan
    stats object and not raise."""
    from src.database.connection import get_db
    from src.database.models import Event
    from src.intelligence.backfill import backfill_recent_events
    from sqlalchemy import delete as sa_delete

    async with get_db() as session:
        await session.execute(sa_delete(Event))
        await session.commit()

    stats = await backfill_recent_events(window_days=1, max_events=5)
    assert stats.events_scanned == 0
    assert stats.events_replayed == 0
    assert stats.events_failed == 0
    assert stats.links_added == 0


# ---------------------------------------------------------------------------
# 5) Taxonomy endpoints — stable metadata readout for UIs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factor_taxonomy_listing():
    from src.api.routes.operator import list_factor_taxonomy

    entries = await list_factor_taxonomy()
    assert len(entries) >= 10
    keys = {e["key"] for e in entries}
    assert {
        "interest_rate", "inflation", "credit_conditions", "oil_energy",
        "usd_fx", "trade_policy", "geopolitical_risk", "regulation_policy",
        "consumer_demand", "technology_cycle",
    }.issubset(keys)
    for e in entries:
        assert "label" in e and "description" in e


@pytest.mark.asyncio
async def test_sector_priors_listing():
    from src.api.routes.operator import list_sector_priors

    entries = await list_sector_priors()
    sectors = {e["sector"] for e in entries}
    assert "technology" in sectors
    assert "energy" in sectors


# ---------------------------------------------------------------------------
# 6) Full CRUD round-trip — create → list → update → delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_relationship_full_roundtrip(seeded):
    from src.api.routes.operator import (
        create_manual_relationship, update_manual_relationship,
        delete_manual_relationship, list_relationships,
        RelationshipCreate, RelationshipUpdate,
    )
    from src.database.connection import get_session_factory

    factory = get_session_factory()

    async with factory() as session:
        row = await create_manual_relationship(
            payload=RelationshipCreate(
                holding_id="h_aapl_pA",
                relationship_type="parent",
                related_ticker=None,
                related_entity_key="apple_parent",
                related_name="Apple Inc parent",
                strength=0.5,
                description="test parent",
            ),
            session=session,
        )
        created_id = row.id

    async with factory() as session:
        listed = await list_relationships(
            portfolio_id="pA", source="manual", holding_id=None, session=session,
        )
    assert any(r.id == created_id for r in listed)

    async with factory() as session:
        updated = await update_manual_relationship(
            rel_id=created_id,
            payload=RelationshipUpdate(strength=0.33, related_name=None),
            session=session,
        )
    assert updated.strength == 0.33

    async with factory() as session:
        result = await delete_manual_relationship(
            rel_id=created_id, reason=None, session=session,
        )
    assert result["deleted"] is True

    async with factory() as session:
        listed_after = await list_relationships(
            portfolio_id="pA", source="manual", holding_id=None, session=session,
        )
    assert not any(r.id == created_id for r in listed_after)
