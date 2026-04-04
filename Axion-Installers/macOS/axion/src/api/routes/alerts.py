"""Alert routes for Axion API."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Alert as AlertModel

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AlertResponse(BaseModel):
    """Portfolio alert."""

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
    return AlertResponse(
        id=alert.id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        title=alert.title,
        message=alert.body,
        related_holdings=_safe_json_list(alert.related_holdings),
        related_events=_safe_json_list(alert.related_events),
        acknowledged=bool(alert.acknowledged),
        acknowledged_at=alert.acknowledged_at,
        created_at=alert.created_at,
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
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    """Return unacknowledged (active) alerts for a portfolio."""
    stmt = (
        select(AlertModel)
        .where(AlertModel.acknowledged == 0)
        .where(AlertModel.portfolio_id == portfolio_id)
        .order_by(AlertModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
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
