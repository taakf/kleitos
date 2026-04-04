"""Analysis routes for Axion API."""

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
from src.database.models import AnalysisNote as AnalysisNoteModel, Holding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class AnalysisNoteResponse(BaseModel):
    """AI-generated analysis note."""

    id: str
    ticker: str | None = None
    title: str
    note_type: str  # earnings, risk, thematic, ad-hoc, impact_analysis
    created_at: str
    agent: str
    summary: str


class AnalysisNoteDetail(AnalysisNoteResponse):
    """Full analysis note with body and metadata."""

    body: dict
    event_id: str | None = None
    holding_id: str | None = None
    confidence: float | None = None
    materiality: str | None = None


class AnalysisRunRequest(BaseModel):
    """Request body for triggering an analysis run."""

    ticker: str | None = None
    scope: str = "portfolio"  # portfolio, single, thematic
    prompt: str | None = None


class AnalysisRunResponse(BaseModel):
    """Response after triggering an analysis run."""

    run_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_content(raw: str) -> dict:
    """Safely parse JSON content from an AnalysisNote."""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _note_to_response(note: AnalysisNoteModel, ticker: str | None) -> AnalysisNoteResponse:
    content = _parse_content(note.content)
    title = content.get("title") or content.get("impact_direction") or note.note_type
    summary = (
        content.get("summary")
        or content.get("short_term_outlook")
        or note.content[:200]
    )
    return AnalysisNoteResponse(
        id=note.id,
        ticker=ticker or content.get("ticker"),
        title=title,
        note_type=note.note_type,
        created_at=note.created_at,
        agent=note.agent_id,
        summary=summary,
    )


def _note_to_detail(note: AnalysisNoteModel, ticker: str | None) -> AnalysisNoteDetail:
    content = _parse_content(note.content)
    title = content.get("title") or content.get("impact_direction") or note.note_type
    summary = (
        content.get("summary")
        or content.get("short_term_outlook")
        or note.content[:200]
    )
    confidence_val: float | None = None
    if note.confidence is not None:
        try:
            confidence_val = float(note.confidence)
        except (ValueError, TypeError):
            confidence_val = None

    return AnalysisNoteDetail(
        id=note.id,
        ticker=ticker or content.get("ticker"),
        title=title,
        note_type=note.note_type,
        created_at=note.created_at,
        agent=note.agent_id,
        summary=summary,
        body=content,
        event_id=note.event_id,
        holding_id=note.holding_id,
        confidence=confidence_val,
        materiality=note.materiality,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/notes", response_model=list[AnalysisNoteResponse])
async def list_notes(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    ticker: str | None = Query(None, description="Filter by ticker"),
    note_type: str | None = Query(None, description="Filter by note type"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[AnalysisNoteResponse]:
    """List analysis notes with optional filters, scoped to portfolio via holding."""
    stmt = (
        select(AnalysisNoteModel, Holding.ticker)
        .outerjoin(Holding, AnalysisNoteModel.holding_id == Holding.id)
        .where(Holding.portfolio_id == portfolio_id)
    )

    if ticker:
        stmt = stmt.where(Holding.ticker == ticker)
    if note_type:
        stmt = stmt.where(AnalysisNoteModel.note_type == note_type)
    if date_from:
        stmt = stmt.where(AnalysisNoteModel.created_at >= date_from.isoformat())
    if date_to:
        stmt = stmt.where(AnalysisNoteModel.created_at <= date_to.isoformat())

    stmt = (
        stmt
        .order_by(AnalysisNoteModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [_note_to_response(note, hticker) for note, hticker in rows]


@router.get("/notes/{note_id}", response_model=AnalysisNoteDetail)
async def get_note(
    note_id: str,
    session: AsyncSession = Depends(get_session),
) -> AnalysisNoteDetail:
    """Get a single analysis note with full detail."""
    stmt = (
        select(AnalysisNoteModel, Holding.ticker)
        .outerjoin(Holding, AnalysisNoteModel.holding_id == Holding.id)
        .where(AnalysisNoteModel.id == note_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis note not found")

    note, hticker = row
    return _note_to_detail(note, hticker)


async def _run_analysis_in_background(
    scope: str,
    ticker: str | None,
) -> None:
    """Background task that instantiates and runs the AnalysisAgent."""
    from src.agents.analysis import AnalysisAgent

    try:
        agent = AnalysisAgent()
        await agent.run()
    except Exception:
        logger.exception("Background analysis run failed (scope=%s, ticker=%s)", scope, ticker)


@router.post("/run", response_model=AnalysisRunResponse, status_code=202)
async def trigger_analysis_run(
    request: AnalysisRunRequest,
    background_tasks: BackgroundTasks,
) -> AnalysisRunResponse:
    """Trigger an analysis agent run.

    Returns 202 Accepted - the run executes asynchronously.
    """
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_analysis_in_background, request.scope, request.ticker)
    return AnalysisRunResponse(
        run_id=run_id,
        status="accepted",
        message=f"Analysis run queued for scope={request.scope}, ticker={request.ticker}.",
    )
