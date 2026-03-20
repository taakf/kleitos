"""Portfolio routes for Axion API."""

import json
import logging
from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Holding as HoldingModel, Security, Trade as TradeModel
from src.ledger.portfolio import PortfolioLedger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


# ---------------------------------------------------------------------------
# Enums & Response models
# ---------------------------------------------------------------------------
class ExposureDimension(str, Enum):
    sector = "sector"
    subsector = "subsector"
    geography = "geography"
    currency = "currency"
    theme = "theme"


class HoldingResponse(BaseModel):
    """Single portfolio holding."""

    id: str
    ticker: str
    name: str | None = None
    sector: str | None = None
    geography: str | None = None
    currency: str
    quantity: float
    avg_cost_basis: float | None = None
    current_price: float | None = None
    market_value: float | None = None
    weight_pct: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    themes: list[str] = []
    updated_at: str


class HoldingDetailResponse(HoldingResponse):
    """Extended holding with extra analytics."""

    isin: str | None = None
    venue: str | None = None
    subsector: str | None = None
    status: str
    recent_events_count: int = 0


class ExposureBucket(BaseModel):
    label: str
    market_value: float
    weight_pct: float
    holding_count: int


class ExposureBreakdown(BaseModel):
    dimension: str
    buckets: list[ExposureBucket]


class PortfolioSummary(BaseModel):
    total_market_value: float
    total_cost_basis: float
    total_pnl: float
    total_pnl_pct: float | None
    holding_count: int
    sector_count: int
    currency_count: int
    last_updated: str | None


class UploadResult(BaseModel):
    status: str
    holdings_imported: int
    holdings_updated: int
    errors: list[str]


class TradeType(str, Enum):
    buy = "buy"
    sell = "sell"
    dividend = "dividend"


class TradeRequest(BaseModel):
    """Request body for submitting a trade."""

    ticker: str
    trade_type: TradeType
    quantity: float
    price: float
    trade_date: str
    settlement_date: str | None = None
    currency: str = "USD"
    notes: str | None = None

    @field_validator("ticker")
    @classmethod
    def ticker_not_empty(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("ticker is required")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("price must be >= 0")
        return v

    @field_validator("trade_date")
    @classmethod
    def trade_date_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("trade_date is required")
        return v.strip()


class TradeResponse(BaseModel):
    """Single trade record."""

    id: str
    holding_id: str | None = None
    ticker: str
    trade_type: str
    quantity: float
    price: float | None = None
    currency: str | None = None
    trade_date: str
    settlement_date: str | None = None
    notes: str | None = None
    source: str | None = None
    created_at: str


class HoldingCreateRequest(BaseModel):
    """Request body for creating a new holding."""
    ticker: str
    quantity: float
    avg_cost_basis: float | None = None
    current_price: float | None = None
    currency: str = "USD"
    isin: str | None = None
    venue: str | None = None

    @field_validator("ticker")
    @classmethod
    def ticker_not_empty(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("ticker is required")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("avg_cost_basis", "current_price")
    @classmethod
    def price_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("price must be >= 0")
        return v


class HoldingUpdateRequest(BaseModel):
    """Request body for updating a holding."""
    quantity: float | None = None
    avg_cost_basis: float | None = None
    current_price: float | None = None
    currency: str | None = None

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("avg_cost_basis", "current_price")
    @classmethod
    def price_non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("price must be >= 0")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_themes(themes_str: str | None) -> list[str]:
    """Parse JSON themes string into a list."""
    if not themes_str:
        return []
    try:
        parsed = json.loads(themes_str)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _compute_pnl(
    quantity: float | None,
    avg_cost_basis: float | None,
    current_price: float | None,
    market_value: float | None,
) -> tuple[float | None, float | None]:
    """Compute PnL and PnL % from available data."""
    cost = (quantity or 0) * (avg_cost_basis or 0) if quantity and avg_cost_basis else None
    mv = market_value

    if cost and mv:
        pnl = mv - cost
        pnl_pct = (pnl / cost * 100) if cost != 0 else None
        return round(pnl, 4), round(pnl_pct, 4) if pnl_pct is not None else None
    return None, None


def _row_to_holding(h: HoldingModel, s: Security | None) -> HoldingResponse:
    """Convert a Holding + Security DB row pair to HoldingResponse."""
    themes = _parse_themes(s.themes if s else None)
    pnl, pnl_pct = _compute_pnl(h.quantity, h.avg_cost_basis, h.current_price, h.market_value)

    return HoldingResponse(
        id=h.id,
        ticker=h.ticker,
        name=s.name if s else None,
        sector=s.sector if s else None,
        geography=s.geography if s else None,
        currency=h.currency,
        quantity=h.quantity,
        avg_cost_basis=h.avg_cost_basis,
        current_price=h.current_price,
        market_value=h.market_value,
        weight_pct=h.weight_pct,
        pnl=pnl,
        pnl_pct=pnl_pct,
        themes=themes,
        updated_at=h.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/holdings", response_model=list[HoldingResponse])
async def list_holdings(
    sector: str | None = Query(None, description="Filter by sector"),
    geography: str | None = Query(None, description="Filter by geography"),
    currency: str | None = Query(None, description="Filter by currency"),
    min_weight: float | None = Query(None, ge=0, description="Minimum portfolio weight %"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[HoldingResponse]:
    """List all portfolio holdings with optional filters."""
    stmt = (
        select(HoldingModel, Security)
        .outerjoin(Security, HoldingModel.ticker == Security.ticker)
        .where(HoldingModel.status == "active")
        .order_by(HoldingModel.weight_pct.desc().nullslast())
    )

    if sector:
        stmt = stmt.where(Security.sector == sector)
    if geography:
        stmt = stmt.where(Security.geography == geography)
    if currency:
        stmt = stmt.where(HoldingModel.currency == currency)
    if min_weight is not None:
        stmt = stmt.where(HoldingModel.weight_pct >= min_weight)

    stmt = stmt.limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    return [_row_to_holding(h, s) for h, s in rows]


@router.get("/holdings/{holding_id}", response_model=HoldingDetailResponse)
async def get_holding(
    holding_id: str,
    session: AsyncSession = Depends(get_session),
) -> HoldingDetailResponse:
    """Get detailed information for a single holding."""
    stmt = (
        select(HoldingModel, Security)
        .outerjoin(Security, HoldingModel.ticker == Security.ticker)
        .where(HoldingModel.id == holding_id)
    )
    row = (await session.execute(stmt)).first()

    if not row:
        raise HTTPException(status_code=404, detail="Holding not found")

    h, s = row
    themes = _parse_themes(s.themes if s else None)
    pnl, pnl_pct = _compute_pnl(h.quantity, h.avg_cost_basis, h.current_price, h.market_value)

    # Count recent events linked to this ticker via EventLinks
    from src.database.models import EventLink, Event

    event_count_stmt = (
        select(func.count())
        .select_from(EventLink)
        .join(Event, EventLink.event_id == Event.id)
        .where(EventLink.link_target == h.id)
    )
    recent_events_count = (await session.execute(event_count_stmt)).scalar_one()

    return HoldingDetailResponse(
        id=h.id,
        ticker=h.ticker,
        name=s.name if s else None,
        sector=s.sector if s else None,
        geography=s.geography if s else None,
        currency=h.currency,
        quantity=h.quantity,
        avg_cost_basis=h.avg_cost_basis,
        current_price=h.current_price,
        market_value=h.market_value,
        weight_pct=h.weight_pct,
        pnl=pnl,
        pnl_pct=pnl_pct,
        themes=themes,
        updated_at=h.updated_at,
        isin=h.isin,
        venue=h.venue,
        subsector=s.subsector if s else None,
        status=h.status,
        recent_events_count=recent_events_count,
    )


@router.post("/holdings", response_model=HoldingResponse, status_code=201)
async def create_holding(
    body: HoldingCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> HoldingResponse:
    """Create a new holding manually."""
    ledger = PortfolioLedger()
    data = {
        "ticker": body.ticker,
        "quantity": body.quantity,
        "avg_cost_basis": body.avg_cost_basis,
        "current_price": body.current_price,
        "currency": body.currency,
        "isin": body.isin,
        "venue": body.venue,
    }
    # Compute market_value if we have price info
    price = body.current_price or body.avg_cost_basis
    if price:
        data["market_value"] = round(price * body.quantity, 4)

    holding_id, action = await ledger.upsert_holding(data, agent_id="manual")
    await ledger.recalculate_weights()

    # Return the full holding response
    return await get_holding_simple(holding_id, session)


async def get_holding_simple(holding_id: str, session: AsyncSession) -> HoldingResponse:
    """Fetch holding + security for response."""
    stmt = (
        select(HoldingModel, Security)
        .outerjoin(Security, HoldingModel.ticker == Security.ticker)
        .where(HoldingModel.id == holding_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Holding not found")
    h, s = row
    return _row_to_holding(h, s)


@router.put("/holdings/{holding_id}", response_model=HoldingDetailResponse)
async def update_holding(
    holding_id: str,
    body: HoldingUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> HoldingDetailResponse:
    """Update a holding's quantity, cost basis, or price."""
    holding = await session.get(HoldingModel, holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    now = datetime.now(timezone.utc).isoformat()

    if body.quantity is not None:
        holding.quantity = body.quantity
    if body.avg_cost_basis is not None:
        holding.avg_cost_basis = body.avg_cost_basis
    if body.current_price is not None:
        holding.current_price = body.current_price
        holding.market_value = body.current_price * (holding.quantity or 0)
    if body.currency is not None:
        holding.currency = body.currency

    holding.updated_at = now
    await session.commit()

    # Recalculate portfolio weights
    ledger = PortfolioLedger()
    await ledger.recalculate_weights()

    # Re-fetch with security join for full response
    return await get_holding(holding_id, session)


@router.delete("/holdings/{holding_id}")
async def delete_holding(
    holding_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Soft-delete a holding by setting status to 'closed'."""
    holding = await session.get(HoldingModel, holding_id)
    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    if holding.status == "closed":
        raise HTTPException(status_code=409, detail="Holding already closed")

    now = datetime.now(timezone.utc).isoformat()
    holding.status = "closed"
    holding.updated_at = now

    # Audit log
    import uuid
    from src.database.models import AuditLog
    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="holdings",
        entity_id=holding_id,
        action="soft_deleted",
        new_value=json.dumps({"status": "closed", "ticker": holding.ticker}),
        agent_id=None,
        user_id="operator",
        created_at=now,
    )
    session.add(audit)
    await session.commit()

    return {"id": holding_id, "status": "closed", "message": "Holding closed successfully"}


# ---------------------------------------------------------------------------
# Trade routes (B2)
# ---------------------------------------------------------------------------
@router.post("/trades", response_model=TradeResponse, status_code=201)
async def submit_trade(
    body: TradeRequest,
    session: AsyncSession = Depends(get_session),
) -> TradeResponse:
    """Submit a single trade (buy/sell/dividend).

    Delegates to IntakeAgent.ingest_trades() for validation, holding updates,
    and audit logging.
    """
    from src.agents.intake import IntakeAgent

    agent = IntakeAgent()
    trade_dict = {
        "ticker": body.ticker,
        "trade_type": body.trade_type.value,
        "quantity": body.quantity,
        "price": body.price,
        "trade_date": body.trade_date,
        "settlement_date": body.settlement_date,
        "currency": body.currency,
        "notes": body.notes,
    }

    result = await agent.ingest_trades([trade_dict])

    if result.errors:
        raise HTTPException(status_code=400, detail=result.errors[0])

    if not result.trades_created:
        raise HTTPException(status_code=500, detail="Trade was not created")

    created = result.trades_created[0]
    trade_id = created["id"]

    # Fetch the persisted trade for the response
    from src.database.connection import get_db

    async with get_db() as db_session:
        trade = await db_session.get(TradeModel, trade_id)

    if not trade:
        raise HTTPException(status_code=500, detail="Trade created but could not be retrieved")

    return TradeResponse(
        id=trade.id,
        holding_id=trade.holding_id,
        ticker=trade.ticker,
        trade_type=trade.trade_type,
        quantity=trade.quantity,
        price=trade.price,
        currency=trade.currency,
        trade_date=trade.trade_date,
        settlement_date=trade.settlement_date,
        notes=trade.notes,
        source=trade.source,
        created_at=trade.created_at,
    )


@router.get("/trades", response_model=list[TradeResponse])
async def list_trades(
    ticker: str | None = Query(None, description="Filter by ticker"),
    trade_type: str | None = Query(None, description="Filter by trade type (buy/sell/dividend)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[TradeResponse]:
    """List trade history with optional filters."""
    stmt = select(TradeModel).order_by(TradeModel.trade_date.desc(), TradeModel.created_at.desc())

    if ticker:
        stmt = stmt.where(TradeModel.ticker == ticker.strip().upper())
    if trade_type:
        stmt = stmt.where(TradeModel.trade_type == trade_type.strip().lower())

    stmt = stmt.limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        TradeResponse(
            id=t.id,
            holding_id=t.holding_id,
            ticker=t.ticker,
            trade_type=t.trade_type,
            quantity=t.quantity,
            price=t.price,
            currency=t.currency,
            trade_date=t.trade_date,
            settlement_date=t.settlement_date,
            notes=t.notes,
            source=t.source,
            created_at=t.created_at,
        )
        for t in rows
    ]


@router.get("/trades/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: str,
    session: AsyncSession = Depends(get_session),
) -> TradeResponse:
    """Get a single trade by ID."""
    trade = await session.get(TradeModel, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")

    return TradeResponse(
        id=trade.id,
        holding_id=trade.holding_id,
        ticker=trade.ticker,
        trade_type=trade.trade_type,
        quantity=trade.quantity,
        price=trade.price,
        currency=trade.currency,
        trade_date=trade.trade_date,
        settlement_date=trade.settlement_date,
        notes=trade.notes,
        source=trade.source,
        created_at=trade.created_at,
    )


@router.post("/upload", response_model=UploadResult)
async def upload_portfolio(file: UploadFile = File(...)) -> UploadResult:
    """Upload a CSV portfolio file to import/update holdings."""
    # Accept common CSV content types + check filename extension as fallback
    allowed_types = {"text/csv", "application/vnd.ms-excel", "application/octet-stream", "text/plain"}
    filename = file.filename or ""
    if file.content_type not in allowed_types and not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV.")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 10 MB.")
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded.")

    ledger = PortfolioLedger()
    holdings_data = await ledger.parse_csv(text)

    if not holdings_data:
        raise HTTPException(status_code=400, detail="No valid holdings found in CSV.")

    imported = 0
    updated = 0
    errors: list[str] = []

    for row in holdings_data:
        try:
            _id, action = await ledger.upsert_holding(row, agent_id="csv_upload")
            if action == "created":
                imported += 1
            else:
                updated += 1
        except Exception as exc:
            ticker = row.get("ticker", "UNKNOWN")
            errors.append(f"{ticker}: {exc}")
            logger.warning("Error upserting holding %s: %s", ticker, exc)

    # Recalculate market_value and weight_pct for all holdings after upload
    await ledger.recalculate_weights()

    return UploadResult(
        status="success" if not errors else "partial",
        holdings_imported=imported,
        holdings_updated=updated,
        errors=errors,
    )


@router.get("/exposure", response_model=ExposureBreakdown)
async def get_exposure(
    dimension: ExposureDimension = Query(ExposureDimension.sector, description="Breakdown dimension"),
    session: AsyncSession = Depends(get_session),
) -> ExposureBreakdown:
    """Get portfolio exposure breakdown by sector, geography, currency or theme."""
    stmt = (
        select(HoldingModel, Security)
        .outerjoin(Security, HoldingModel.ticker == Security.ticker)
        .where(HoldingModel.status == "active")
    )
    rows = (await session.execute(stmt)).all()

    buckets_map: dict[str, dict] = {}

    for h, s in rows:
        mv = h.market_value or 0.0
        wt = h.weight_pct or 0.0

        if dimension == ExposureDimension.sector:
            labels = [s.sector if s and s.sector else "Unknown"]
        elif dimension == ExposureDimension.subsector:
            labels = [s.subsector if s and s.subsector else "Unknown"]
        elif dimension == ExposureDimension.geography:
            labels = [s.geography if s and s.geography else "Unknown"]
        elif dimension == ExposureDimension.currency:
            labels = [h.currency]
        elif dimension == ExposureDimension.theme:
            themes = _parse_themes(s.themes if s else None)
            labels = themes if themes else ["Unclassified"]
        else:
            labels = ["Unknown"]

        for label in labels:
            if label not in buckets_map:
                buckets_map[label] = {"market_value": 0.0, "weight_pct": 0.0, "holding_count": 0}
            buckets_map[label]["market_value"] += mv
            buckets_map[label]["weight_pct"] += wt
            buckets_map[label]["holding_count"] += 1

    buckets = sorted(
        [
            ExposureBucket(
                label=label,
                market_value=round(data["market_value"], 2),
                weight_pct=round(data["weight_pct"], 4),
                holding_count=data["holding_count"],
            )
            for label, data in buckets_map.items()
        ],
        key=lambda b: b.market_value,
        reverse=True,
    )

    return ExposureBreakdown(dimension=dimension.value, buckets=buckets)


@router.get("/summary", response_model=PortfolioSummary)
async def get_summary(
    session: AsyncSession = Depends(get_session),
) -> PortfolioSummary:
    """Return high-level portfolio summary statistics."""
    stmt = (
        select(HoldingModel, Security)
        .outerjoin(Security, HoldingModel.ticker == Security.ticker)
        .where(HoldingModel.status == "active")
    )
    rows = (await session.execute(stmt)).all()

    total_mv = 0.0
    total_cost = 0.0
    sectors: set[str] = set()
    currencies: set[str] = set()
    last_updated: str | None = None

    for h, s in rows:
        mv = h.market_value if h.market_value is not None else (h.current_price or 0) * (h.quantity or 0)
        total_mv += mv
        cost = (h.quantity or 0) * (h.avg_cost_basis or 0)
        total_cost += cost
        if s and s.sector:
            sectors.add(s.sector)
        currencies.add(h.currency)
        if last_updated is None or h.updated_at > (last_updated or ""):
            last_updated = h.updated_at

    total_pnl = total_mv - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else None

    return PortfolioSummary(
        total_market_value=round(total_mv, 2),
        total_cost_basis=round(total_cost, 2),
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 4) if total_pnl_pct is not None else None,
        holding_count=len(rows),
        sector_count=len(sectors),
        currency_count=len(currencies),
        last_updated=last_updated,
    )


# ---------------------------------------------------------------------------
# Reset portfolio (clear all data for a fresh start)
# ---------------------------------------------------------------------------
@router.post("/reset")
async def reset_portfolio(session: AsyncSession = Depends(get_session)):
    """Remove ALL portfolio data — holdings, trades, events, alerts, analysis.

    Use this to clear demo/fake data and start fresh with real data.
    This is irreversible.
    """
    from src.database.models import (
        Alert, AnalysisNote, AuditLog, CoverageReport,
        Digest, Event, EventLink, AgentRun,
    )
    from sqlalchemy import delete

    counts = {}
    for model, name in [
        (EventLink, "event_links"),
        (AnalysisNote, "analysis_notes"),
        (CoverageReport, "coverage_reports"),
        (Digest, "digests"),
        (Alert, "alerts"),
        (Event, "events"),
        (AgentRun, "agent_runs"),
        (AuditLog, "audit_log"),
        (TradeModel, "trades"),
        (Security, "securities"),
        (HoldingModel, "holdings"),
    ]:
        result = await session.execute(delete(model))
        counts[name] = result.rowcount

    await session.commit()
    logger.info("Portfolio reset: %s", counts)

    return {
        "status": "reset_complete",
        "deleted": counts,
        "message": "All portfolio data has been cleared. You can now add your real holdings.",
    }
