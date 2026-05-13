"""Alert routes for Axion API."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Alert as AlertModel

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# Phase 9G: explicit severity ordering used by ``priority_ordered``.
# Lower rank = higher visual priority.  Unknown severities sort last.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "warning":  2,
    "medium":   2,
    "info":     3,
    "low":      3,
}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AlertResponse(BaseModel):
    """Portfolio alert.

    Phase 9N — adds ``suggested_action``: a tiny operator-facing
    next-step string derived from ``severity`` + ``alert_type`` +
    ``related_holdings`` by :func:`src.intelligence.actions.suggest_next_step_for_alert`.
    The field is always present (possibly null).  It never contains
    trading advice — it just tells the operator where to look next.

    Phase 9Q — adds ``evidence_targets``: a parallel list of
    structured navigation targets derived from ``related_events`` +
    ``related_holdings``.  Each entry is
    ``{"ref": "event:<id>" | "holding:<id>", "nav_target": {...} | None}``.
    The frontend uses these to make the "Based on" chips clickable.
    """

    id: str
    alert_type: str
    severity: str  # info, warning, high, critical
    title: str
    message: str
    related_holdings: list[str]
    related_events: list[str]
    acknowledged: bool
    acknowledged_at: str | None = None
    created_at: str
    suggested_action: str | None = None
    evidence_targets: list[dict[str, Any]] = Field(default_factory=list)


class AcknowledgeResponse(BaseModel):
    id: str
    acknowledged: bool
    acknowledged_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_json_list(raw: str | None) -> list[str]:
    """Parse a JSON string that should be a list, returning [] on failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _alert_to_response(alert: AlertModel) -> AlertResponse:
    related_holdings = _safe_json_list(alert.related_holdings)
    related_events = _safe_json_list(alert.related_events)
    # Phase 9N — attach a grounded one-line next-step hint.  Pure
    # function of the alert's own fields; never reads other rows.
    suggested_action: str | None = None
    try:
        from src.intelligence.actions import suggest_next_step_for_alert
        suggested_action = suggest_next_step_for_alert({
            "severity": alert.severity,
            "alert_type": alert.alert_type,
            "related_holdings": related_holdings,
        })
    except Exception:
        suggested_action = None

    # Phase 9Q — build a parallel list of navigation targets for the
    # "Based on" evidence chips (related_events → event modal,
    # related_holdings → portfolio tab).  Scoped to the alert's own
    # ``portfolio_id`` so jumps never land on the wrong portfolio.
    evidence_targets: list[dict[str, Any]] = []
    try:
        from src.intelligence.navigation import enrich_evidence_refs
        pid = str(alert.portfolio_id or "default")
        refs: list[str] = []
        for e_id in related_events[:2]:
            refs.append(f"event:{e_id}")
        for h in related_holdings[:2]:
            refs.append(f"holding:{h}")
        evidence_targets = enrich_evidence_refs(refs, pid)
    except Exception:
        evidence_targets = []

    return AlertResponse(
        id=alert.id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        title=alert.title,
        message=alert.body,
        related_holdings=related_holdings,
        related_events=related_events,
        acknowledged=bool(alert.acknowledged),
        acknowledged_at=alert.acknowledged_at,
        created_at=alert.created_at,
        suggested_action=suggested_action,
        evidence_targets=evidence_targets,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    severity: str | None = Query(None, description="Filter by severity"),
    acknowledged: bool | None = Query(None, description="Filter by acknowledged status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    """List alerts with optional severity and acknowledged filters."""
    stmt = select(AlertModel).where(AlertModel.portfolio_id == portfolio_id)

    if severity:
        stmt = stmt.where(AlertModel.severity == severity)
    if acknowledged is not None:
        stmt = stmt.where(AlertModel.acknowledged == (1 if acknowledged else 0))

    stmt = (
        stmt
        .order_by(AlertModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).scalars().all()
    return [_alert_to_response(a) for a in rows]


@router.get("/active", response_model=list[AlertResponse])
async def active_alerts(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    priority_ordered: bool = Query(
        False,
        description=(
            "If true, sort alerts by severity (critical → high → warning → "
            "info), then by created_at descending.  Useful for premium "
            "overview surfaces where a fresh info alert must not bump an "
            "older critical alert off the screen.  Phase 9G."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    """Return unacknowledged (active) alerts for a portfolio.

    Default ordering is chronological (newest first) to preserve
    pre-9G behavior.  Callers that want severity-first prioritization
    must pass ``priority_ordered=true`` explicitly.
    """
    stmt = (
        select(AlertModel)
        .where(AlertModel.acknowledged == 0)
        .where(AlertModel.portfolio_id == portfolio_id)
    )

    if priority_ordered:
        # Map severity → numeric rank inside SQL so the sort is stable
        # and the DB does the work.  Rows with unrecognised severity
        # strings get a very high rank so they sort after everything.
        severity_expr = case(
            (AlertModel.severity == "critical", 0),
            (AlertModel.severity == "high", 1),
            (AlertModel.severity == "warning", 2),
            (AlertModel.severity == "medium", 2),
            (AlertModel.severity == "info", 3),
            (AlertModel.severity == "low", 3),
            else_=9,
        )
        stmt = stmt.order_by(severity_expr.asc(), AlertModel.created_at.desc())
    else:
        stmt = stmt.order_by(AlertModel.created_at.desc())

    stmt = stmt.offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [_alert_to_response(a) for a in rows]


@router.post("/acknowledge-all")
async def acknowledge_all_alerts(
    session: AsyncSession = Depends(get_session),
):
    """Bulk acknowledge all unacknowledged alerts."""
    now = datetime.now(timezone.utc).isoformat()

    stmt = select(AlertModel).where(AlertModel.acknowledged == 0)
    alerts = (await session.execute(stmt)).scalars().all()

    count = 0
    for alert in alerts:
        alert.acknowledged = 1
        alert.acknowledged_at = now
        count += 1

    await session.commit()

    return {"acknowledged_count": count, "acknowledged_at": now}


@router.post("/{alert_id}/acknowledge", response_model=AcknowledgeResponse)
async def acknowledge_alert(
    alert_id: str,
    session: AsyncSession = Depends(get_session),
) -> AcknowledgeResponse:
    """Acknowledge an alert so it no longer appears in the active list."""
    stmt = select(AlertModel).where(AlertModel.id == alert_id)
    alert = (await session.execute(stmt)).scalars().first()

    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.acknowledged:
        raise HTTPException(status_code=409, detail="Alert already acknowledged")

    now = datetime.now(timezone.utc).isoformat()
    alert.acknowledged = 1
    alert.acknowledged_at = now
    await session.commit()

    return AcknowledgeResponse(
        id=alert.id,
        acknowledged=True,
        acknowledged_at=now,
    )


@router.delete("/{alert_id}")
async def delete_alert(
    alert_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete an alert permanently."""
    alert = await session.get(AlertModel, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    await session.delete(alert)
    await session.commit()

    return {"id": alert_id, "message": "Alert deleted"}
