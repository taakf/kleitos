"""Audit log routes for Axion API."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import AuditLog

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
