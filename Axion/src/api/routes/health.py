"""Health-check routes for Axion API."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Source, Event

router = APIRouter(prefix="/api/v1", tags=["health"])

_START_TIME = time.monotonic()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class HealthStatus(BaseModel):
    """System health snapshot."""

    status: str
    database: str
    scheduler: str
    sources_active: int
    sources_total: int
    last_collection: datetime | None
    uptime_seconds: float
    version: str
    llm_available: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthStatus)
async def get_health(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HealthStatus:
    """Return current system health status.

    Fast, no auth required.  Used by load-balancers and monitoring.
    """
    try:
        # Total sources
        total_result = await session.execute(
            select(func.count()).select_from(Source)
        )
        sources_total = total_result.scalar_one()

        # Active sources
        active_result = await session.execute(
            select(func.count()).select_from(Source).where(Source.enabled == 1)
        )
        sources_active = active_result.scalar_one()

        # Last collection time — most recent event fetched_at
        last_fetch_result = await session.execute(
            select(Event.fetched_at)
            .order_by(Event.fetched_at.desc())
            .limit(1)
        )
        last_fetched_at_str = last_fetch_result.scalar_one_or_none()

        last_collection: datetime | None = None
        if last_fetched_at_str:
            try:
                last_collection = datetime.fromisoformat(last_fetched_at_str)
            except (ValueError, TypeError):
                last_collection = None

        db_status = "connected"
    except Exception:
        db_status = "error"
        sources_total = 0
        sources_active = 0
        last_collection = None

    uptime = time.monotonic() - _START_TIME

    from src.llm.client import is_llm_available
    llm_ok = is_llm_available()

    return HealthStatus(
        status="ok" if db_status == "connected" else "degraded",
        database=db_status,
        scheduler=("running" if getattr(getattr(request.app.state, "scheduler", None), "is_running", False) else "stopped"),
        sources_active=sources_active,
        sources_total=sources_total,
        last_collection=last_collection,
        uptime_seconds=round(uptime, 2),
        version="1.0.0",
        llm_available=llm_ok,
    )


@router.post("/shutdown")
async def shutdown_server():
    """Gracefully shut down the Axion server.

    This stops the uvicorn process after a brief delay so the response
    can be sent back to the client first.
    """
    import asyncio
    import os
    import signal
    import logging

    logger = logging.getLogger("axion.health")
    logger.info("Shutdown requested via API")

    async def _delayed_shutdown():
        await asyncio.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.get_event_loop().create_task(_delayed_shutdown())
    return {"status": "shutting_down", "message": "Axion is shutting down..."}
