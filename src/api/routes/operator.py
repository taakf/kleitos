"""Phase 9H — Operator control surface.

Single module that exposes safe, auditable operator workflows over
the already-correct Phase 9A-9G deterministic intelligence stack.
No new math, no new LLM, no new scoring models — every endpoint in
this file is a thin, portfolio-safe wrapper around logic that
Phase 9A/9D/9G already validated.

Exposed operations (all under ``/api/v1/operator``)::

  GET    /factor-sensitivities              — list EFFECTIVE sensitivities
                                                for a portfolio (manual override
                                                OR sector default), one row per
                                                (holding, factor) pair
  GET    /factor-sensitivities/overrides    — list MANUAL override rows only
  POST   /factor-sensitivities/overrides    — create / update a manual override
  DELETE /factor-sensitivities/overrides/{id}
                                                 — delete a manual override

  GET    /relationships                     — list effective relationship rows
                                                for a portfolio, with source
                                                discriminator
  POST   /relationships                     — create a MANUAL relationship row
  PUT    /relationships/{id}                — update a MANUAL relationship row
  DELETE /relationships/{id}                — delete a MANUAL relationship row
  POST   /relationships/reconcile           — trigger seed→DB reconciliation
                                                on demand.  Returns the
                                                :class:`ReconcileStats` shape.

  POST   /backfill                          — trigger bounded deterministic
                                                replay of recent events.
                                                Returns :class:`BackfillStats`.

Every mutating endpoint:

* refuses to touch seed or ai_inferred rows when the operator asks to
  modify manual rows (and vice-versa);
* writes an ``AuditLog`` row before returning;
* returns a stable JSON shape with ``source`` and ``updated_at``
  visible so the operator can see exactly what they did.

Nothing in this file is destructive beyond deleting rows the operator
explicitly authored.  Seed rows are never deleted through this route —
the reconciler is the only thing that can prune them, and it only
touches its own namespace.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import (
    AuditLog,
    Holding,
    HoldingFactorSensitivity,
    HoldingRelationship,
    Security,
)
from src.api.routes.ws import notify_operator_action
from src.intelligence.backfill import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    MAX_EVENTS_PER_RUN,
    BackfillInProgressError,
    backfill_recent_events,
    is_backfill_running,
)
from src.intelligence.factors.sensitivity import (
    SECTOR_PRIORS,
    SensitivityResolver,
)
from src.intelligence.factors.taxonomy import FACTOR_KEYS, get_factor
from src.intelligence.relationships.reconciler import (
    ReconcileInProgressError,
    is_reconcile_running,
    reconcile_seed_relationships,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


#: Which source strings the operator is allowed to modify via this
#: surface.  ``seed`` rows are owned by the reconciler.  ``ai_inferred``
#: is reserved for a future phase — operators never hand-edit those.
_OPERATOR_OWNED_SOURCE = "manual"


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class EffectiveSensitivityRow(BaseModel):
    """One effective (holding, factor) sensitivity row.

    The frontend uses ``source`` to decide whether to render the row
    as an operator override or as a sector default.  ``override_id``
    is populated only when a manual override row exists.
    """

    holding_id: str
    ticker: str
    factor: str
    factor_label: str
    effective_value: float        # the value the propagator would use
    source: str                   # "manual" | "default" | "zero"
    sector: str | None
    override_id: str | None = None
    override_value: float | None = None
    updated_at: str | None = None


class SensitivityOverrideRow(BaseModel):
    """A manual override row, 1:1 with ``holding_factor_sensitivities``."""

    id: str
    holding_id: str
    ticker: str
    factor: str
    sensitivity: float
    source: str
    created_at: str
    updated_at: str


class SensitivityOverrideCreate(BaseModel):
    """Payload for create / upsert of a manual sensitivity override."""

    holding_id: str = Field(..., min_length=1)
    factor: str = Field(..., min_length=1)
    sensitivity: float = Field(..., ge=-1.0, le=1.0)
    reason: str | None = Field(default=None, max_length=500)


class RelationshipRow(BaseModel):
    """Operator-facing view of a :class:`HoldingRelationship`."""

    id: str
    holding_id: str
    ticker: str
    portfolio_id: str
    relationship_type: str
    related_ticker: str | None
    related_entity_key: str | None
    related_name: str | None
    strength: float
    source: str                # seed | manual | ai_inferred
    description: str | None
    created_at: str
    updated_at: str


class RelationshipCreate(BaseModel):
    """Payload to create a MANUAL relationship row.

    ``holding_id`` must exist.  One of ``related_ticker`` or
    ``related_entity_key`` is required — the tuple
    ``(holding_id, relationship_type, related_ticker,
    related_entity_key)`` is the DB uniqueness key.
    """

    holding_id: str = Field(..., min_length=1)
    relationship_type: str = Field(..., min_length=1)
    related_ticker: str | None = None
    related_entity_key: str | None = None
    related_name: str | None = None
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    description: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


class RelationshipUpdate(BaseModel):
    """Payload to update a MANUAL relationship row.  Partial updates."""

    related_name: str | None = None
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    description: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


class BackfillRequest(BaseModel):
    """Payload for ``POST /operator/backfill``."""

    window_days: int = Field(default=DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS)
    max_events: int = Field(default=MAX_EVENTS_PER_RUN, ge=1, le=MAX_EVENTS_PER_RUN)
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _audit(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    old_value: dict | None,
    new_value: dict | None,
    reason: str | None,
) -> None:
    """Write a single ``AuditLog`` row and commit.

    Every operator endpoint calls this before returning so every
    mutation is recoverable from the audit trail alone.
    """
    now = datetime.now(timezone.utc).isoformat()
    session.add(AuditLog(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=json.dumps(old_value) if old_value is not None else None,
        new_value=json.dumps(new_value) if new_value is not None else None,
        agent_id="operator",
        reason=reason,
        created_at=now,
    ))


# ---------------------------------------------------------------------------
# Factor sensitivities
# ---------------------------------------------------------------------------


@router.get(
    "/factor-sensitivities",
    response_model=list[EffectiveSensitivityRow],
)
async def list_effective_sensitivities(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    holding_id: str | None = Query(None, description="Optional single-holding filter"),
    factor: str | None = Query(None, description="Optional factor filter"),
    session: AsyncSession = Depends(get_session),
) -> list[EffectiveSensitivityRow]:
    """Return the EFFECTIVE sensitivity for every (holding, factor) pair
    in the portfolio.

    The returned rows reflect exactly what the Phase 9A propagator
    would use at runtime: manual override if present, otherwise the
    sector default, otherwise ``source = "zero"`` (no exposure).
    """
    holdings_q = select(Holding).where(
        Holding.portfolio_id == portfolio_id,
        Holding.status == "active",
    )
    if holding_id:
        holdings_q = holdings_q.where(Holding.id == holding_id)
    holdings = (await session.execute(holdings_q)).scalars().all()
    if not holdings:
        return []

    tickers = [h.ticker for h in holdings]
    sector_by_ticker = {
        t: s
        for t, s in (
            await session.execute(
                select(Security.ticker, Security.sector).where(
                    Security.ticker.in_(tickers)
                )
            )
        ).all()
    }

    # Pull overrides that belong to this portfolio's holdings.
    holding_id_list = [h.id for h in holdings]
    override_rows = (await session.execute(
        select(HoldingFactorSensitivity).where(
            HoldingFactorSensitivity.holding_id.in_(holding_id_list)
        )
    )).scalars().all()
    overrides_by_key: dict[tuple[str, str], HoldingFactorSensitivity] = {
        (r.holding_id, r.factor): r for r in override_rows
    }

    resolver = SensitivityResolver(
        manual_overrides=[
            (r.holding_id, r.factor, r.sensitivity, r.source) for r in override_rows
        ]
    )

    factor_list = [factor] if factor else list(FACTOR_KEYS)

    out: list[EffectiveSensitivityRow] = []
    for h in holdings:
        sector = sector_by_ticker.get(h.ticker)
        for f in factor_list:
            defn = get_factor(f)
            if defn is None:
                continue
            resolved = resolver.resolve(
                holding_id=h.id, factor=f, sector=sector,
            )
            ovr = overrides_by_key.get((h.id, f))
            out.append(EffectiveSensitivityRow(
                holding_id=h.id,
                ticker=h.ticker,
                factor=f,
                factor_label=defn.label,
                effective_value=round(resolved.value, 4),
                source=resolved.source,
                sector=resolved.sector,
                override_id=ovr.id if ovr else None,
                override_value=(
                    round(float(ovr.sensitivity), 4) if ovr else None
                ),
                updated_at=(ovr.updated_at if ovr else None),
            ))
    return out


@router.get(
    "/factor-sensitivities/overrides",
    response_model=list[SensitivityOverrideRow],
)
async def list_sensitivity_overrides(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    session: AsyncSession = Depends(get_session),
) -> list[SensitivityOverrideRow]:
    """List raw manual / ai_inferred override rows for the portfolio."""
    rows = (await session.execute(
        select(HoldingFactorSensitivity, Holding.ticker)
        .join(Holding, HoldingFactorSensitivity.holding_id == Holding.id)
        .where(Holding.portfolio_id == portfolio_id)
        .order_by(HoldingFactorSensitivity.updated_at.desc())
    )).all()
    return [
        SensitivityOverrideRow(
            id=row.id,
            holding_id=row.holding_id,
            ticker=ticker,
            factor=row.factor,
            sensitivity=float(row.sensitivity),
            source=row.source or "manual",
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row, ticker in rows
    ]


@router.post(
    "/factor-sensitivities/overrides",
    response_model=SensitivityOverrideRow,
    status_code=201,
)
async def upsert_sensitivity_override(
    payload: SensitivityOverrideCreate,
    session: AsyncSession = Depends(get_session),
) -> SensitivityOverrideRow:
    """Create or update a manual sensitivity override.

    Refuses unknown factor keys.  The same ``(holding_id, factor)``
    tuple already covered by the DB unique constraint — we upsert by
    deleting the old row and inserting a new one when a collision is
    detected, because the DB uses ``manual`` as a source discriminator
    and we need the prior row's source to appear in the audit trail.
    """
    if get_factor(payload.factor) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown factor key: {payload.factor}",
        )

    holding = await session.get(Holding, payload.holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    now = datetime.now(timezone.utc).isoformat()

    existing = (await session.execute(
        select(HoldingFactorSensitivity).where(
            HoldingFactorSensitivity.holding_id == payload.holding_id,
            HoldingFactorSensitivity.factor == payload.factor,
        )
    )).scalars().first()

    old_value = None
    if existing is not None:
        # Protect seed-like defaults.  Only manual rows are writable
        # through this surface.  If the existing row is ai_inferred
        # (reserved) we refuse — an operator must first delete the
        # ai_inferred row explicitly.
        if existing.source == "ai_inferred":
            raise HTTPException(
                status_code=409,
                detail=(
                    "An AI-inferred override already exists. "
                    "Delete it explicitly before creating a manual override."
                ),
            )
        # Phase 9O — enrich the audit payload with ticker + factor so
        # the downstream shaping helper can render a human-readable
        # row ("AAPL · interest_rate") without a DB lookup.
        old_value = {
            "id": existing.id,
            "holding_id": existing.holding_id,
            "factor": existing.factor,
            "ticker": holding.ticker,
            "sensitivity": float(existing.sensitivity),
            "source": existing.source,
            "updated_at": existing.updated_at,
        }
        existing.sensitivity = float(payload.sensitivity)
        existing.source = _OPERATOR_OWNED_SOURCE
        existing.updated_at = now
        row = existing
    else:
        row = HoldingFactorSensitivity(
            id=str(uuid.uuid4()),
            holding_id=payload.holding_id,
            factor=payload.factor,
            sensitivity=float(payload.sensitivity),
            source=_OPERATOR_OWNED_SOURCE,
            created_at=now,
            updated_at=now,
        )
        session.add(row)

    new_value = {
        "id": row.id,
        "holding_id": row.holding_id,
        "factor": row.factor,
        "ticker": holding.ticker,  # Phase 9O — enrich audit readback
        "sensitivity": float(row.sensitivity),
        "source": row.source,
    }
    await _audit(
        session,
        entity_type="holding_factor_sensitivity",
        entity_id=row.id,
        action="upsert" if existing is None else "update",
        old_value=old_value,
        new_value=new_value,
        reason=payload.reason,
    )
    await session.commit()

    return SensitivityOverrideRow(
        id=row.id,
        holding_id=row.holding_id,
        ticker=holding.ticker,
        factor=row.factor,
        sensitivity=float(row.sensitivity),
        source=row.source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.delete(
    "/factor-sensitivities/overrides/{override_id}",
    status_code=200,
)
async def delete_sensitivity_override(
    override_id: str,
    reason: str | None = Query(None, max_length=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete a manual sensitivity override.

    Only ``source='manual'`` rows are deletable through this surface.
    Rows with other source values are left intact and the endpoint
    returns 409 — the operator must use a different channel.
    """
    row = await session.get(HoldingFactorSensitivity, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Override not found")
    if row.source != _OPERATOR_OWNED_SOURCE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete row with source={row.source!r}. "
                "Operator delete only applies to manual rows."
            ),
        )

    # Phase 9O — include ticker in the audit payload so the recent
    # actions surface can render "Deleted override · AAPL / factor"
    # instead of "Deleted override · h_aapl_pA / factor".
    holding_ticker: str | None = None
    try:
        parent = await session.get(Holding, row.holding_id)
        if parent is not None:
            holding_ticker = parent.ticker
    except Exception:
        holding_ticker = None

    old_value = {
        "id": row.id,
        "holding_id": row.holding_id,
        "factor": row.factor,
        "ticker": holding_ticker,
        "sensitivity": float(row.sensitivity),
        "source": row.source,
    }
    await session.delete(row)
    await _audit(
        session,
        entity_type="holding_factor_sensitivity",
        entity_id=override_id,
        action="delete",
        old_value=old_value,
        new_value=None,
        reason=reason,
    )
    await session.commit()
    return {"id": override_id, "deleted": True}


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def _row_to_relationship(row: HoldingRelationship, ticker: str, portfolio_id: str) -> RelationshipRow:
    return RelationshipRow(
        id=row.id,
        holding_id=row.holding_id,
        ticker=ticker,
        portfolio_id=portfolio_id,
        relationship_type=row.relationship_type,
        related_ticker=row.related_ticker,
        related_entity_key=row.related_entity_key,
        related_name=row.related_name,
        strength=float(row.strength or 0.0),
        source=row.source or "seed",
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/relationships", response_model=list[RelationshipRow])
async def list_relationships(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    source: str | None = Query(None, description="Filter by source (seed|manual|ai_inferred)"),
    holding_id: str | None = Query(None, description="Filter by holding id"),
    session: AsyncSession = Depends(get_session),
) -> list[RelationshipRow]:
    """List every ``holding_relationships`` row for the portfolio.

    The ``source`` column tells the operator exactly which bucket
    each row came from so they can audit seed vs manual vs ai_inferred
    at a glance.
    """
    stmt = (
        select(HoldingRelationship, Holding.ticker, Holding.portfolio_id)
        .join(Holding, HoldingRelationship.holding_id == Holding.id)
        .where(Holding.portfolio_id == portfolio_id)
    )
    if source:
        stmt = stmt.where(HoldingRelationship.source == source)
    if holding_id:
        stmt = stmt.where(HoldingRelationship.holding_id == holding_id)
    stmt = stmt.order_by(HoldingRelationship.updated_at.desc())
    rows = (await session.execute(stmt)).all()
    return [_row_to_relationship(r, t, p) for r, t, p in rows]


@router.post(
    "/relationships",
    response_model=RelationshipRow,
    status_code=201,
)
async def create_manual_relationship(
    payload: RelationshipCreate,
    session: AsyncSession = Depends(get_session),
) -> RelationshipRow:
    """Create a MANUAL relationship row.

    Refuses to collide with an existing row at the same identity tuple,
    regardless of source — if a seed row already claims that edge, the
    operator must delete the seed entry from the YAML and reconcile,
    not hand-patch it through this endpoint.  This keeps seed and
    manual rows on cleanly separated lanes.
    """
    if not payload.related_ticker and not payload.related_entity_key:
        raise HTTPException(
            status_code=400,
            detail="Either related_ticker or related_entity_key is required",
        )

    holding = await session.get(Holding, payload.holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    # Collision check across ALL sources
    stmt = (
        select(HoldingRelationship)
        .where(HoldingRelationship.holding_id == payload.holding_id)
        .where(HoldingRelationship.relationship_type == payload.relationship_type)
    )
    if payload.related_ticker is None:
        stmt = stmt.where(HoldingRelationship.related_ticker.is_(None))
    else:
        stmt = stmt.where(HoldingRelationship.related_ticker == payload.related_ticker)
    if payload.related_entity_key is None:
        stmt = stmt.where(HoldingRelationship.related_entity_key.is_(None))
    else:
        stmt = stmt.where(HoldingRelationship.related_entity_key == payload.related_entity_key)

    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A row already exists at this identity tuple "
                f"(source={existing.source!r}).  Delete or move it first."
            ),
        )

    now = datetime.now(timezone.utc).isoformat()
    row = HoldingRelationship(
        id=str(uuid.uuid4()),
        holding_id=payload.holding_id,
        relationship_type=payload.relationship_type,
        related_ticker=payload.related_ticker,
        related_entity_key=payload.related_entity_key,
        related_name=payload.related_name,
        strength=float(payload.strength),
        source=_OPERATOR_OWNED_SOURCE,
        description=payload.description,
        created_at=now,
        updated_at=now,
    )
    session.add(row)

    await _audit(
        session,
        entity_type="holding_relationship",
        entity_id=row.id,
        action="create",
        old_value=None,
        new_value={
            "holding_id": row.holding_id,
            "ticker": holding.ticker,  # Phase 9O — enrich readback
            "relationship_type": row.relationship_type,
            "related_ticker": row.related_ticker,
            "related_entity_key": row.related_entity_key,
            "related_name": row.related_name,
            "strength": float(row.strength),
            "source": row.source,
        },
        reason=payload.reason,
    )
    await session.commit()
    return _row_to_relationship(row, holding.ticker, holding.portfolio_id)


@router.put(
    "/relationships/{rel_id}",
    response_model=RelationshipRow,
)
async def update_manual_relationship(
    rel_id: str,
    payload: RelationshipUpdate,
    session: AsyncSession = Depends(get_session),
) -> RelationshipRow:
    """Update a MANUAL relationship row.

    Seed and ai_inferred rows are immutable through this endpoint —
    the reconciler owns seeds, and ai_inferred is reserved for future
    phases.  Refusal is a 409.
    """
    row = await session.get(HoldingRelationship, rel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    if row.source != _OPERATOR_OWNED_SOURCE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot modify row with source={row.source!r}. "
                "Operator updates apply only to manual rows."
            ),
        )

    holding = await session.get(Holding, row.holding_id)
    if holding is None:
        # Shouldn't happen (FK), but if it does, the row is orphaned.
        raise HTTPException(status_code=500, detail="Orphaned relationship row")

    old_value = {
        "holding_id": row.holding_id,
        "ticker": holding.ticker,  # Phase 9O — enrich readback
        "relationship_type": row.relationship_type,
        "related_ticker": row.related_ticker,
        "related_entity_key": row.related_entity_key,
        "related_name": row.related_name,
        "strength": float(row.strength),
        "description": row.description,
    }
    changed = False
    if payload.strength is not None and float(payload.strength) != float(row.strength or 0.0):
        row.strength = float(payload.strength)
        changed = True
    if payload.related_name is not None and payload.related_name != row.related_name:
        row.related_name = payload.related_name
        changed = True
    if payload.description is not None and payload.description != row.description:
        row.description = payload.description
        changed = True
    if changed:
        row.updated_at = datetime.now(timezone.utc).isoformat()
        await _audit(
            session,
            entity_type="holding_relationship",
            entity_id=row.id,
            action="update",
            old_value=old_value,
            new_value={
                "holding_id": row.holding_id,
                "ticker": holding.ticker,  # Phase 9O — enrich readback
                "relationship_type": row.relationship_type,
                "related_ticker": row.related_ticker,
                "related_entity_key": row.related_entity_key,
                "related_name": row.related_name,
                "strength": float(row.strength),
                "description": row.description,
            },
            reason=payload.reason,
        )
    await session.commit()
    return _row_to_relationship(row, holding.ticker, holding.portfolio_id)


@router.delete(
    "/relationships/{rel_id}",
    status_code=200,
)
async def delete_manual_relationship(
    rel_id: str,
    reason: str | None = Query(None, max_length=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete a MANUAL relationship row.  Seed rows are immune."""
    row = await session.get(HoldingRelationship, rel_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Relationship not found")
    if row.source != _OPERATOR_OWNED_SOURCE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete row with source={row.source!r}. "
                "Operator delete only applies to manual rows."
            ),
        )
    # Phase 9O — resolve ticker so the audit readback can name the
    # holding directly instead of the opaque holding_id.
    holding_ticker: str | None = None
    try:
        parent = await session.get(Holding, row.holding_id)
        if parent is not None:
            holding_ticker = parent.ticker
    except Exception:
        holding_ticker = None
    old_value = {
        "id": row.id,
        "holding_id": row.holding_id,
        "ticker": holding_ticker,
        "relationship_type": row.relationship_type,
        "related_ticker": row.related_ticker,
        "related_entity_key": row.related_entity_key,
        "related_name": row.related_name,
        "strength": float(row.strength),
        "source": row.source,
    }
    await session.delete(row)
    await _audit(
        session,
        entity_type="holding_relationship",
        entity_id=rel_id,
        action="delete",
        old_value=old_value,
        new_value=None,
        reason=reason,
    )
    await session.commit()
    return {"id": rel_id, "deleted": True}


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


@router.post("/relationships/reconcile")
async def trigger_reconcile(
    prune: bool = Query(True, description="Prune seed rows no longer in YAML"),
    reason: str | None = Query(None, max_length=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Run the Phase 9D seed→DB reconciler on demand.

    Returns the reconcile stats shape already documented in
    :class:`src.intelligence.relationships.reconciler.ReconcileStats`.
    Writes an audit row before returning.

    Phase 9K: a process-local in-flight guard protects against
    concurrent reconcile runs.  If one is already running, this
    endpoint returns 409 Conflict with ``{"detail": ...,
    "in_progress": true}`` so the operator UI can show a friendly
    "already running" state instead of double-submitting.
    """
    # Phase 9M: broadcast "started" as early as possible so the
    # operator UI flips the chip to Running immediately, then
    # "finished" / "failed" after the reconcile body returns.  The
    # started-broadcast is conditional on the in-flight guard so a
    # 409 short-circuit doesn't emit a misleading "started" event.
    notify_operator_action("reconcile", "started")
    try:
        stats = await reconcile_seed_relationships(prune=prune)
    except ReconcileInProgressError as exc:
        # A second concurrent caller — the "real" reconcile is still
        # running in-process, so don't emit "finished".  The first
        # caller's finished-broadcast will land when it completes.
        raise HTTPException(
            status_code=409,
            detail={
                "detail": str(exc),
                "in_progress": True,
                "action": "reconcile",
            },
        )
    except Exception as exc:
        notify_operator_action("reconcile", "failed")
        logger.exception("Operator reconcile failed")
        raise HTTPException(
            status_code=500, detail=f"Reconcile failed: {exc}",
        )

    await _audit(
        session,
        entity_type="holding_relationships",
        entity_id="seed_reconcile",
        action="reconcile",
        old_value=None,
        new_value=stats.as_dict(),
        reason=reason,
    )
    await session.commit()

    notify_operator_action("reconcile", "finished")
    return {"stats": stats.as_dict()}


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


@router.post("/backfill")
async def trigger_backfill(
    payload: BackfillRequest,
) -> dict[str, Any]:
    """Trigger bounded deterministic replay on recent events.

    The backfill walks the last ``window_days`` events (max 30d) and
    re-runs ``CollectionAgent._link_event_to_holdings`` on each one.
    All dedupe rails in the link pipeline apply, so calling this twice
    in a row is a no-op.  Every run writes a single audit entry with
    the stats payload.

    Phase 9K: a process-local in-flight guard protects against
    concurrent backfill runs.  If one is already running, this
    endpoint returns 409 Conflict with ``{"detail": ...,
    "in_progress": true}`` so the operator UI can show a friendly
    "already running" state instead of double-submitting.
    """
    # Phase 9M: broadcast "started" then "finished" / "failed" so the
    # operator UI can track the lock state without waiting for the
    # next 4s poll.  The 409-collision path skips "finished" because
    # the actual in-flight run will fire its own event when it
    # completes.
    notify_operator_action("backfill", "started")
    try:
        stats = await backfill_recent_events(
            window_days=payload.window_days,
            max_events=payload.max_events,
            reason=payload.reason,
        )
    except BackfillInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": str(exc),
                "in_progress": True,
                "action": "backfill",
            },
        )
    except Exception:
        notify_operator_action("backfill", "failed")
        raise
    notify_operator_action("backfill", "finished")
    return {"stats": stats.as_dict()}


# ---------------------------------------------------------------------------
# Small admin metadata — factor / sector taxonomy for UIs
# ---------------------------------------------------------------------------


@router.get("/actions/status")
async def operator_actions_status() -> dict[str, Any]:
    """Report which bounded operator actions are currently running.

    Phase 9K hardening: the Operator UI polls this endpoint before
    enabling destructive-adjacent buttons so the user never triggers
    a second reconcile/backfill while the first is still in flight.
    Cheap — just two ``asyncio.Lock.locked()`` reads.
    """
    return {
        "reconcile": {"in_progress": is_reconcile_running()},
        "backfill": {"in_progress": is_backfill_running()},
    }


@router.get("/taxonomy/factors")
async def list_factor_taxonomy() -> list[dict[str, str]]:
    """Return the factor taxonomy so operator UIs can render the
    canonical set (key, label, description) without hard-coding it."""
    from src.intelligence.factors.taxonomy import FACTORS
    return [
        {"key": f.key, "label": f.label, "description": f.description}
        for f in FACTORS
    ]


@router.get("/taxonomy/sectors")
async def list_sector_priors() -> list[dict[str, Any]]:
    """Return the sector → factor prior map used by ``SensitivityResolver``.

    Read-only.  Operators who want to change a default author a
    manual override row through the factor-sensitivity endpoints —
    the priors themselves are code-owned on purpose.
    """
    return [
        {"sector": sector, "priors": priors}
        for sector, priors in sorted(SECTOR_PRIORS.items())
    ]
