"""Digest routes for Axion API."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Digest as DigestModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/digests", tags=["digests"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class DigestSection(BaseModel):
    title: str
    content: str


class DigestResponse(BaseModel):
    """Daily / on-demand portfolio intelligence digest."""

    id: str
    digest_type: str  # daily, weekly, ad-hoc
    created_at: str
    period_start: str
    period_end: str
    sections: list[DigestSection]
    event_count: int
    alert_count: int
    holding_count: int


class DigestGenerateRequest(BaseModel):
    digest_type: str = "ad-hoc"
    scope: str = "portfolio"
    portfolio_id: str = "default"


class DigestGenerateResponse(BaseModel):
    run_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_content_to_sections(raw: str) -> list[DigestSection]:
    """Parse JSON content string into a list of DigestSection objects.

    The content JSON may have different shapes:
    - A dict with top-level keys that each become a section
    - A list of {title, content} dicts
    """
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        # Assume list of {title, content} objects
        return [
            DigestSection(
                title=item.get("title", "Untitled"),
                content=item.get("content", "") if isinstance(item.get("content"), str)
                else json.dumps(item.get("content", "")),
            )
            for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        # Each top-level key becomes a section
        sections = []
        for key, value in data.items():
            if isinstance(value, str):
                content_str = value
            else:
                content_str = json.dumps(value)
            # Make title human-friendly
            title = key.replace("_", " ").title()
            sections.append(DigestSection(title=title, content=content_str))
        return sections

    return []


def _digest_to_response(digest: DigestModel) -> DigestResponse:
    return DigestResponse(
        id=digest.id,
        digest_type=digest.digest_type,
        created_at=digest.created_at,
        period_start=digest.period_start,
        period_end=digest.period_end,
        sections=_parse_content_to_sections(digest.content),
        event_count=digest.event_count or 0,
        alert_count=digest.alert_count or 0,
        holding_count=digest.holding_count or 0,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[DigestResponse])
async def list_digests(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    digest_type: str | None = Query(None, description="Filter by digest type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[DigestResponse]:
    """List digests with optional type filter."""
    stmt = select(DigestModel).where(DigestModel.portfolio_id == portfolio_id)

    if digest_type:
        stmt = stmt.where(DigestModel.digest_type == digest_type)

    stmt = (
        stmt
        .order_by(DigestModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).scalars().all()
    return [_digest_to_response(d) for d in rows]


@router.get("/latest", response_model=DigestResponse)
async def latest_digest(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    session: AsyncSession = Depends(get_session),
) -> DigestResponse:
    """Return the most recently generated digest for a portfolio."""
    stmt = (
        select(DigestModel)
        .where(DigestModel.portfolio_id == portfolio_id)
        .order_by(DigestModel.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()

    if row is None:
        raise HTTPException(status_code=404, detail="No digests found")

    return _digest_to_response(row)


async def _generate_digest_in_background(digest_type: str, portfolio_id: str = "default") -> None:
    """Background task that generates a digest for a specific portfolio.

    Phase 9A consistency fix: this path previously used the legacy
    ``DigestGenerator`` in ``src/reporting/digests.py``, which did
    NOT include the deterministic ``macro_factor_touchpoints`` field.
    That meant manual dashboard / Telegram / API-triggered digests
    silently lacked Phase 9A factor intelligence, while
    scheduler-triggered digests (via ``AnalysisAgent.generate_digest``)
    included it.

    Now unified: every write path — scheduler, OpenClaw bridge, and
    this API background task — goes through
    ``AnalysisAgent.generate_digest`` and therefore produces a digest
    with consistent content including ``macro_factor_touchpoints``.
    """
    from src.agents.analysis import AnalysisAgent

    # Map API-level ``digest_type`` onto ``AnalysisAgent``'s ``period``
    # parameter.  "ad-hoc" and "daily" both use the 1-day window; the
    # difference is purely in how the row is labelled downstream, and
    # ``AnalysisAgent`` uses ``period`` as the ``digest_type`` it writes.
    period_map = {
        "daily": "daily",
        "ad-hoc": "daily",
        "weekly": "weekly",
        "monthly": "monthly",
    }
    if digest_type not in period_map:
        logger.error(
            "Unsupported digest type: %r (supported: %s)",
            digest_type, sorted(period_map),
        )
        return

    try:
        agent = AnalysisAgent()
        await agent.run(
            digest=True,
            period=period_map[digest_type],
            portfolio_id=portfolio_id,
        )
    except Exception:
        logger.exception("Background digest generation failed (type=%s)", digest_type)


@router.post("/generate", response_model=DigestGenerateResponse, status_code=202)
async def generate_digest(
    request: DigestGenerateRequest,
    background_tasks: BackgroundTasks,
) -> DigestGenerateResponse:
    """Trigger generation of a new digest.

    Returns 202 Accepted - digest generation runs asynchronously.
    """
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_generate_digest_in_background, request.digest_type, request.portfolio_id)
    return DigestGenerateResponse(
        run_id=run_id,
        status="accepted",
        message=f"Digest generation queued (type={request.digest_type}, scope={request.scope}).",
    )
