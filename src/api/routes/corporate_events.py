"""Phase 9 — Corporate-events API.

Top-level *Events* tab in the dashboard maps to this router.  Kept
deliberately separate from :mod:`src.api.routes.events` (news) so the
two surfaces evolve independently.  Customer-facing language is:

* **News**    — Insights → News sub-tab — backed by ``/api/v1/events``
* **Events**  — top-level Events tab    — backed by THIS router

Routes
------
``GET  /api/v1/corporate-events``      list with filters + pagination headers
``GET  /api/v1/corporate-events/{id}`` single-event detail
``POST /api/v1/corporate-events/import`` operator-uploaded CSV
``POST /api/v1/corporate-events/refresh`` ATHEX fetcher trigger (honest
        degraded today — see :mod:`src.corporate_events.athex`).

Every URL field returned by this router is scrubbed of
``apiKey=`` / ``token=`` / ``Bearer …`` style secrets via the same
helper Phase 8 introduced.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.api.routes.events import _scrub_url
from src.corporate_events.athex import fetch_athex_events
from src.corporate_events.manual_import import import_csv
from src.database.models import CorporateEvent, Holding

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/corporate-events", tags=["corporate-events"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CorporateEventResponse(BaseModel):
    """A single corporate-event row, scrubbed and JSON-safe."""

    id: str
    portfolio_id: str
    holding_id: str | None = None
    ticker: str | None = None
    isin: str | None = None
    exchange: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    event_type: str
    title: str
    description: str | None = None
    event_date: str
    event_time: str | None = None
    timezone: str | None = None
    status: str | None = None
    confidence: str
    match_method: str | None = None
    external_id: str | None = None
    created_at: str
    updated_at: str


class CorporateEventListEnvelope(BaseModel):
    """List wrapper with pagination metadata."""

    items: list[CorporateEventResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class ImportResponse(BaseModel):
    """Body returned by ``POST /import``."""

    imported: int
    skipped_duplicate: int
    matched_by_isin: int
    matched_by_ticker: int
    unmatched: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    batch_id: str


class RefreshResponse(BaseModel):
    """Body returned by ``POST /refresh``."""

    status: str          # "active" | "degraded" | "unsupported" | "error"
    reason: str
    fetched_at: str
    imported: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_response(ev: CorporateEvent) -> CorporateEventResponse:
    return CorporateEventResponse(
        id=ev.id,
        portfolio_id=ev.portfolio_id,
        holding_id=ev.holding_id,
        ticker=ev.ticker,
        isin=ev.isin,
        exchange=ev.exchange,
        source_id=ev.source_id,
        source_name=ev.source_name,
        source_url=_scrub_url(ev.source_url),
        event_type=ev.event_type,
        title=ev.title,
        description=ev.description,
        event_date=ev.event_date,
        event_time=ev.event_time,
        timezone=ev.timezone,
        status=ev.status,
        confidence=ev.confidence,
        match_method=ev.match_method,
        external_id=ev.external_id,
        created_at=ev.created_at,
        updated_at=ev.updated_at,
    )


def _month_to_range(month: str) -> tuple[str, str]:
    """Return ``(start, end_exclusive)`` ISO dates for ``YYYY-MM``."""
    year, mm = [int(x) for x in month.split("-")]
    start = date(year, mm, 1)
    end = date(year + (1 if mm == 12 else 0), 1 if mm == 12 else mm + 1, 1)
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=None)
async def list_corporate_events(
    response: Response,
    portfolio_id: str = Query("default", description="Active portfolio id"),
    ticker: str | None = Query(None, description="Filter by ticker (exact, case-insensitive)"),
    holding_id: str | None = Query(None, description="Filter by holding_id"),
    isin: str | None = Query(None, description="Filter by ISIN"),
    event_type: str | None = Query(None, description="Filter by event_type"),
    exchange: str | None = Query(None, description="Filter by exchange (e.g. ATHEX)"),
    source: str | None = Query(None, alias="source", description="Filter by source_id"),
    month: str | None = Query(None, description="YYYY-MM convenience filter"),
    date_from: str | None = Query(None, description="ISO date (inclusive)"),
    date_to: str | None = Query(None, description="ISO date (inclusive)"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    envelope: bool = Query(False, description="Wrap response in {items, total, …}"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """List corporate events with filters, pagination, and headers.

    Portfolio-scoped — every query is bounded by ``portfolio_id`` so
    pA's calendar never leaks into pB's.

    URLs are scrubbed via the Phase 8 helper before being returned.
    """
    conds: list[Any] = [CorporateEvent.portfolio_id == portfolio_id]
    if ticker:
        conds.append(CorporateEvent.ticker == ticker.strip().upper())
    if holding_id:
        conds.append(CorporateEvent.holding_id == holding_id)
    if isin:
        conds.append(CorporateEvent.isin == isin.strip().upper())
    if event_type:
        conds.append(CorporateEvent.event_type == event_type.strip().lower())
    if exchange:
        conds.append(CorporateEvent.exchange == exchange.strip().upper())
    if source:
        conds.append(CorporateEvent.source_id == source)
    if month:
        try:
            start, end = _month_to_range(month)
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid 'month' — expected YYYY-MM")
        conds.append(CorporateEvent.event_date >= start)
        conds.append(CorporateEvent.event_date < end)
    if date_from:
        try:
            date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'date_from'")
        conds.append(CorporateEvent.event_date >= date_from)
    if date_to:
        try:
            date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'date_to'")
        conds.append(CorporateEvent.event_date <= date_to)

    # COUNT for pagination metadata
    count_stmt = select(func.count()).select_from(CorporateEvent)
    for c in conds:
        count_stmt = count_stmt.where(c)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    stmt = select(CorporateEvent)
    for c in conds:
        stmt = stmt.where(c)
    stmt = stmt.order_by(
        CorporateEvent.event_date.asc(),
        CorporateEvent.created_at.asc(),
    ).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()

    items = [_row_to_response(r) for r in rows]
    has_more = (offset + len(items)) < total

    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Has-More"] = "true" if has_more else "false"

    if envelope:
        return CorporateEventListEnvelope(
            items=items, total=total, limit=limit, offset=offset, has_more=has_more,
        )
    return items


@router.get("/{event_id}", response_model=CorporateEventResponse)
async def get_corporate_event(
    event_id: str,
    portfolio_id: str = Query("default"),
    session: AsyncSession = Depends(get_session),
) -> CorporateEventResponse:
    row = (await session.execute(
        select(CorporateEvent).where(
            CorporateEvent.id == event_id,
            CorporateEvent.portfolio_id == portfolio_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Corporate event not found")
    return _row_to_response(row)


# --- POST /import — manual CSV upload --------------------------------------


class ImportPayload(BaseModel):
    """Body for ``POST /import``.

    The CSV is sent inline as a string so the operator can paste a
    small file straight into the API explorer.  A separate
    ``multipart/form-data`` upload is intentionally NOT added here —
    the Phase 9 dashboard sends the same inline payload, and limiting
    the surface keeps the auth story simple.
    """

    portfolio_id: str = Field(..., description="Target portfolio id")
    csv_text: str = Field(..., description="Full CSV body")
    source_id: str = "manual_csv"
    source_name: str = "Manual CSV Import"


@router.post("/import", response_model=ImportResponse)
async def import_corporate_events(
    payload: ImportPayload = Body(...),
    session: AsyncSession = Depends(get_session),
) -> ImportResponse:
    """Phase 9 — operator-supplied CSV import.

    Returns the per-row summary so the UI can show row-level errors.
    """
    if not payload.csv_text or not payload.csv_text.strip():
        raise HTTPException(status_code=400, detail="csv_text is empty")
    if not payload.portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")

    # Validate the portfolio exists so we don't write into a ghost id.
    from src.database.models import Portfolio
    pf = (await session.execute(
        select(Portfolio.id).where(Portfolio.id == payload.portfolio_id)
    )).first()
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    summary = await import_csv(
        session,
        portfolio_id=payload.portfolio_id,
        csv_text=payload.csv_text,
        source_id=payload.source_id,
        source_name=payload.source_name,
    )
    body = summary.to_dict()
    return ImportResponse(**body)


# --- POST /refresh — ATHEX fetcher trigger ---------------------------------


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_athex_events(
    portfolio_id: str = Query("default"),
    session: AsyncSession = Depends(get_session),
) -> RefreshResponse:
    """Phase 9 — fetch corporate events from ATHEX.

    The default build returns ``status="unsupported"`` with a
    customer-safe reason because Athens Exchange does not publish a
    stable machine-readable corporate-events feed.  See
    :mod:`src.corporate_events.athex` for the extension point.
    """
    # Find Greek-listed holdings to scope the fetch (passed through to
    # the parser when implemented).
    from src.intelligence.listing import filter_athex_holdings
    holdings_rows = (await session.execute(
        select(Holding).where(Holding.portfolio_id == portfolio_id)
    )).scalars().all()
    athex_holdings = filter_athex_holdings(list(holdings_rows))

    result = await fetch_athex_events(
        holdings=athex_holdings,
        config={"unsupported": True},
    )
    # In a future build, ``result.events`` would be passed through the
    # importer here.  Today the default config short-circuits to
    # status="unsupported" so there's nothing to import.
    return RefreshResponse(
        status=result.status,
        reason=result.reason,
        fetched_at=result.fetched_at,
        imported=0,
    )
