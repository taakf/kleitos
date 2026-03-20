"""Event routes for Axion API."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Event as EventModel, EventLink as EventLinkModel, Holding as HoldingModel, Source

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class EventLinkResponse(BaseModel):
    """Link attached to an event."""

    id: str
    link_type: str
    link_target: str
    relevance_score: float | None = None
    impact_channel: str | None = None
    link_source: str | None = None


class EventResponse(BaseModel):
    """Portfolio-relevant event."""

    id: str
    title: str
    summary: str | None = None
    url: str | None = None
    event_type: str | None = None
    materiality: str
    confidence: str
    scope: str | None = None
    direction: str | None = None
    horizon: str | None = None
    source_name: str | None = None
    published_at: str | None = None
    fetched_at: str


class EventDetailResponse(EventResponse):
    """Event with full content and links."""

    content: str | None = None
    external_id: str | None = None
    links: list[EventLinkResponse] = []
    linked_tickers: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_event(ev: EventModel, source_name: str | None) -> EventResponse:
    return EventResponse(
        id=ev.id,
        title=ev.title,
        summary=ev.summary,
        url=ev.url,
        event_type=ev.event_type,
        materiality=ev.materiality,
        confidence=ev.confidence,
        scope=ev.scope,
        direction=ev.direction,
        horizon=ev.horizon,
        source_name=source_name,
        published_at=ev.published_at,
        fetched_at=ev.fetched_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[EventResponse])
async def list_events(
    ticker: str | None = Query(None, description="Filter by ticker (via event links)"),
    event_type: str | None = Query(None, description="Filter by event type"),
    materiality: str | None = Query(None, description="Filter by materiality level"),
    date_from: datetime | None = Query(None, description="Start of date range (published_at)"),
    date_to: datetime | None = Query(None, description="End of date range (published_at)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """List events with optional filters for ticker, date range, materiality, and type."""
    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
    )

    if ticker:
        # Sub-query: event IDs linked to holdings with this ticker.
        # EventLink.link_target stores the holding UUID, so we join
        # through the Holding table to match by ticker string.
        ticker_subq = (
            select(EventLinkModel.event_id)
            .join(HoldingModel, EventLinkModel.link_target == HoldingModel.id)
            .where(HoldingModel.ticker == ticker.upper())
        )
        stmt = stmt.where(EventModel.id.in_(ticker_subq))

    if event_type:
        stmt = stmt.where(EventModel.event_type == event_type)
    if materiality:
        stmt = stmt.where(EventModel.materiality == materiality)
    if date_from:
        stmt = stmt.where(EventModel.published_at >= date_from.isoformat())
    if date_to:
        stmt = stmt.where(EventModel.published_at <= date_to.isoformat())

    stmt = stmt.order_by(EventModel.fetched_at.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    return [_row_to_event(ev, src_name) for ev, src_name in rows]


@router.get("/recent", response_model=list[EventResponse])
async def recent_events(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """Return the most recent events from the last 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
        .where(EventModel.fetched_at >= cutoff)
        .order_by(EventModel.fetched_at.desc())
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [_row_to_event(ev, src_name) for ev, src_name in rows]


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: str,
    session: AsyncSession = Depends(get_session),
) -> EventDetailResponse:
    """Get full event detail including links."""
    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
        .where(EventModel.id == event_id)
    )
    row = (await session.execute(stmt)).first()

    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    ev, source_name = row

    # Fetch event links
    links_stmt = select(EventLinkModel).where(EventLinkModel.event_id == event_id)
    links = (await session.execute(links_stmt)).scalars().all()

    link_responses = [
        EventLinkResponse(
            id=lnk.id,
            link_type=lnk.link_type,
            link_target=lnk.link_target,
            relevance_score=lnk.relevance_score,
            impact_channel=lnk.impact_channel,
            link_source=lnk.link_source,
        )
        for lnk in links
    ]

    # Extract tickers from links by resolving holding UUIDs
    holding_ids = [lnk.link_target for lnk in links]
    linked_tickers: list[str] = []
    if holding_ids:
        ticker_stmt = (
            select(HoldingModel.ticker)
            .where(HoldingModel.id.in_(holding_ids))
            .distinct()
        )
        linked_tickers = list(
            (await session.execute(ticker_stmt)).scalars().all()
        )

    return EventDetailResponse(
        id=ev.id,
        title=ev.title,
        summary=ev.summary,
        url=ev.url,
        event_type=ev.event_type,
        materiality=ev.materiality,
        confidence=ev.confidence,
        scope=ev.scope,
        direction=ev.direction,
        horizon=ev.horizon,
        source_name=source_name,
        published_at=ev.published_at,
        fetched_at=ev.fetched_at,
        content=ev.content,
        external_id=ev.external_id,
        links=link_responses,
        linked_tickers=linked_tickers,
    )
