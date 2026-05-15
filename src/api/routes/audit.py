"""Audit log routes for Axion API.

Phase 9O — adds a thin ``/api/v1/audit/recent`` shaping endpoint on
top of the existing list route.  It reuses the same ``audit_log``
rows and the shared :mod:`src.intelligence.traceability` helper, so
every user-facing surface (operator panel recent-actions card, future
audit side-panel) renders the exact same shapes and labels.

No schema changes.  No new tables.  No auth changes.
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import (
    AuditLog,
    Holding,
    HoldingFactorSensitivity,
    HoldingRelationship,
)
from src.intelligence.traceability import (
    _OPERATOR_ENTITY_TYPES,
    CATEGORY_LABELS,
    group_evidence_refs,
    is_operator_entity_type,
    select_recent_operator_entries,
)

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AuditEntry(BaseModel):
    """Single audit log entry."""

    id: str
    entity_type: str
    entity_id: str
    action: str
    old_value: dict | list | str | None
    new_value: dict | list | str | None
    agent_id: str | None
    user_id: str
    reason: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_json_field(raw: str | None):
    """Attempt to parse a JSON text field; return the raw string on failure."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[AuditEntry])
async def list_audit_log(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    agent_id: str | None = Query(None, description="Filter by agent id"),
    date_from: datetime | None = Query(None, description="Start of date range (ISO-8601)"),
    date_to: datetime | None = Query(None, description="End of date range (ISO-8601)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEntry]:
    """Query the audit log with optional filters for entity, agent, and date range."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())

    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if agent_id is not None:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= date_from.isoformat())
    if date_to is not None:
        stmt = stmt.where(AuditLog.created_at <= date_to.isoformat())

    stmt = stmt.offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    return [
        AuditEntry(
            id=row.id,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            action=row.action,
            old_value=_parse_json_field(row.old_value),
            new_value=_parse_json_field(row.new_value),
            agent_id=row.agent_id,
            user_id=row.user_id,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Phase 9O — Recent operator actions (shaped)
# ---------------------------------------------------------------------------


class TraceabilityEntryResponse(BaseModel):
    """Phase 9O compact, UI-ready shape of an operator audit row.

    Produced by :func:`src.intelligence.traceability.shape_audit_entry`,
    which is a pure function over the same ``audit_log`` rows returned
    by the list route above.  Keeping the shaping layer separate from
    the raw list route means the existing /api/v1/audit contract
    doesn't break, and every frontend surface that needs a compact
    recent-actions view reads from exactly one endpoint.
    """

    id: str
    title: str
    timestamp: str
    actor: str
    entity_type: str
    entity_type_label: str
    entity_id: str
    action: str
    summary: str
    old_highlights: dict | None = None
    new_highlights: dict | None = None
    evidence_refs: list[str] = []
    reason: str | None = None
    portfolio_id: str | None = None
    #: Phase 9Q — structured deep-link target for this audit row.
    #: Built by :func:`src.intelligence.navigation.target_for_operator_entry`
    #: and scoped to the resolved portfolio when known.
    nav_target: dict | None = None


async def _resolve_portfolio_ids(
    session: AsyncSession, rows: list[AuditLog],
) -> dict[str, str]:
    """Resolve ``audit_log.entity_id`` → ``portfolio_id`` for rows that
    reference holdings.

    Only used by the optional ``portfolio_id`` filter on
    ``/api/v1/audit/recent``.  Returns a map keyed by audit row id.
    Keys that cannot be resolved are omitted — the caller treats
    unresolved rows as global (not portfolio-scoped).

    We do two small IN-queries instead of a row-by-row join:
      1. ``holding_factor_sensitivity`` → holding_id → portfolio_id
      2. ``holding_relationship`` → holding_id → portfolio_id

    Reconcile + backfill rows are intentionally *not* resolved — they
    are global maintenance actions and should appear on every portfolio.
    """
    out: dict[str, str] = {}

    # --- Factor overrides ------------------------------------------
    fs_ids = [
        r.entity_id
        for r in rows
        if r.entity_type == "holding_factor_sensitivity" and r.entity_id
    ]
    if fs_ids:
        stmt = (
            select(HoldingFactorSensitivity.id, Holding.portfolio_id)
            .join(Holding, Holding.id == HoldingFactorSensitivity.holding_id)
            .where(HoldingFactorSensitivity.id.in_(fs_ids))
        )
        rows_fs = (await session.execute(stmt)).all()
        fs_map = {row_id: portfolio_id for row_id, portfolio_id in rows_fs}
        for r in rows:
            if r.entity_type == "holding_factor_sensitivity":
                pid = fs_map.get(r.entity_id)
                if pid:
                    out[r.id] = pid

    # --- Manual relationships --------------------------------------
    rel_ids = [
        r.entity_id
        for r in rows
        if r.entity_type == "holding_relationship" and r.entity_id
    ]
    if rel_ids:
        stmt = (
            select(HoldingRelationship.id, Holding.portfolio_id)
            .join(Holding, Holding.id == HoldingRelationship.holding_id)
            .where(HoldingRelationship.id.in_(rel_ids))
        )
        rows_rel = (await session.execute(stmt)).all()
        rel_map = {row_id: portfolio_id for row_id, portfolio_id in rows_rel}
        for r in rows:
            if r.entity_type == "holding_relationship":
                pid = rel_map.get(r.entity_id)
                if pid:
                    out[r.id] = pid

    return out


@router.get("/recent", response_model=list[TraceabilityEntryResponse])
async def recent_operator_actions(
    portfolio_id: str | None = Query(
        None,
        description=(
            "Optional portfolio filter. When provided, factor-override "
            "and manual-relationship rows are joined to the holding and "
            "filtered to that portfolio. Global maintenance actions "
            "(reconcile, backfill) are always included."
        ),
    ),
    entity_type: str | None = Query(
        None,
        description="Optional explicit entity_type filter (operator registry only).",
    ),
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[TraceabilityEntryResponse]:
    """Phase 9O — compact, UI-ready list of recent operator actions.

    Filters to operator-owned entity types (factor overrides, manual
    relationships, reconcile passes, backfill runs), shapes each row
    via :mod:`src.intelligence.traceability`, applies the no-op
    reconcile dedupe rule, and returns the most recent ``limit`` rows.

    This route is a thin reader over the existing ``audit_log`` table —
    no new tables, no writes, no auth layer.
    """
    # Validate explicit entity_type filter up front so we never leak
    # non-operator rows through the recent surface.
    if entity_type is not None and not is_operator_entity_type(entity_type):
        return []

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())

    if entity_type is not None:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    else:
        stmt = stmt.where(AuditLog.entity_type.in_(_OPERATOR_ENTITY_TYPES))

    # Read a generous window so the dedupe / portfolio filter still
    # has room to land ``limit`` final rows after culling.
    stmt = stmt.limit(max(limit * 5, 50))

    rows = list((await session.execute(stmt)).scalars().all())

    # Optional portfolio filter — resolve entity_id → portfolio_id and
    # drop anything that doesn't belong to the requested portfolio.
    # Global maintenance rows (reconcile / backfill) pass through.
    if portfolio_id is not None and rows:
        try:
            portfolio_map = await _resolve_portfolio_ids(session, rows)
        except Exception:
            portfolio_map = {}

        def _keep(row: AuditLog) -> bool:
            if row.entity_type in ("holding_relationships", "intelligence_backfill"):
                return True  # global maintenance
            pid = portfolio_map.get(row.id)
            return pid == portfolio_id

        rows = [r for r in rows if _keep(r)]

    entries = select_recent_operator_entries(rows, limit=limit)

    # Attach the resolved portfolio_id to each entry when we know it,
    # so the frontend can show a per-row context tag. Global rows keep
    # portfolio_id = None.
    portfolio_map_all: dict[str, str] = {}
    try:
        if rows:
            portfolio_map_all = await _resolve_portfolio_ids(session, rows)
    except Exception:
        portfolio_map_all = {}

    # Phase 9Q — lazy-import to avoid touching the traceability
    # module's public surface with navigation concerns.
    from src.intelligence.navigation import target_for_operator_entry

    results: list[TraceabilityEntryResponse] = []
    for entry in entries:
        resolved = portfolio_map_all.get(entry.id)
        d = entry.to_dict()
        if resolved:
            d["portfolio_id"] = resolved
        # Phase 9Q — attach a deep-link target scoped to the resolved
        # portfolio (or the query filter, or "default" as last resort).
        effective_pid = resolved or portfolio_id or "default"
        try:
            nav = target_for_operator_entry(d, effective_pid)
            d["nav_target"] = nav.to_dict() if nav is not None else None
        except Exception:
            d["nav_target"] = None
        results.append(TraceabilityEntryResponse(**d))
    return results


@router.get("/categories", response_model=dict)
async def evidence_ref_categories() -> dict[str, Any]:
    """Return the static category label map for Phase 9N evidence refs.

    The frontend uses this to render consistent "Grounded in" sub-
    headings without hard-coding the vocabulary twice.  The response
    is immutable — safe to cache on the client for the session.
    """
    return {
        "categories": dict(CATEGORY_LABELS),
        "order": list(CATEGORY_LABELS.keys()),
    }


@router.get("/group-refs", response_model=dict)
async def group_refs_endpoint(
    ref: list[str] = Query(
        default_factory=list,
        description="Repeatable ref param, e.g. ?ref=factor:interest_rate&ref=alert:abc",
    ),
) -> dict[str, Any]:
    """Utility endpoint for grouping a set of Phase 9N evidence refs.

    Exposed for debugging / tests — the frontend mirrors the same
    logic in ``dashboard/js/app.js`` so we don't round-trip for every
    action card.  Having the endpoint means tests can snapshot the
    exact server-side categorisation.
    """
    return {"groups": group_evidence_refs(ref)}
