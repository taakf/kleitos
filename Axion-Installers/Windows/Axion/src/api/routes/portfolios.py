"""Portfolio management routes for Axion API."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import Portfolio, Holding

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])

DEFAULT_PORTFOLIO_ID = "default"


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------

class PortfolioResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    base_currency: str
    is_default: bool
    created_at: str
    updated_at: str
    holding_count: int = 0

    model_config = {"from_attributes": True}


class PortfolioCreateRequest(BaseModel):
    name: str
    description: str | None = None
    base_currency: str = "USD"


class PortfolioUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    base_currency: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[PortfolioResponse])
async def list_portfolios(
    session: AsyncSession = Depends(get_session),
) -> list[PortfolioResponse]:
    """List all portfolios."""
    stmt = select(Portfolio).order_by(Portfolio.is_default.desc(), Portfolio.name)
    rows = (await session.execute(stmt)).scalars().all()

    result = []
    for p in rows:
        # Count active holdings per portfolio
        count_stmt = select(Holding).where(
            Holding.portfolio_id == p.id,
            Holding.status == "active",
        )
        holdings = (await session.execute(count_stmt)).scalars().all()
        result.append(PortfolioResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            base_currency=p.base_currency,
            is_default=bool(p.is_default),
            created_at=p.created_at,
            updated_at=p.updated_at,
            holding_count=len(holdings),
        ))
    return result


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: str,
    session: AsyncSession = Depends(get_session),
) -> PortfolioResponse:
    """Get a single portfolio by ID."""
    portfolio = await session.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    count_stmt = select(Holding).where(
        Holding.portfolio_id == portfolio_id,
        Holding.status == "active",
    )
    holdings = (await session.execute(count_stmt)).scalars().all()

    return PortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        description=portfolio.description,
        base_currency=portfolio.base_currency,
        is_default=bool(portfolio.is_default),
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holding_count=len(holdings),
    )


@router.post("", response_model=PortfolioResponse, status_code=201)
async def create_portfolio(
    body: PortfolioCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> PortfolioResponse:
    """Create a new portfolio."""
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=422, detail="Portfolio name is required.")

    portfolio_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    portfolio = Portfolio(
        id=portfolio_id,
        name=body.name.strip(),
        description=body.description,
        base_currency=body.base_currency or "USD",
        is_default=0,
        created_at=now,
        updated_at=now,
    )

    session.add(portfolio)
    await session.commit()

    return PortfolioResponse(
        id=portfolio.id,
        name=portfolio.name,
        description=portfolio.description,
        base_currency=portfolio.base_currency,
        is_default=False,
        created_at=portfolio.created_at,
        updated_at=portfolio.updated_at,
        holding_count=0,
    )


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(
    portfolio_id: str,
    body: PortfolioUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> PortfolioResponse:
    """Update a portfolio's name, description, or base currency."""
    portfolio = await session.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=422, detail="Portfolio name cannot be empty.")
        portfolio.name = body.name.strip()
    if body.description is not None:
        portfolio.description = body.description
    if body.base_currency is not None:
        portfolio.base_currency = body.base_currency

    portfolio.updated_at = datetime.now(timezone.utc).isoformat()
    await session.commit()

    return await get_portfolio(portfolio_id, session)


@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a portfolio.

    Rules:
    - Cannot delete the default portfolio.
    - Cannot delete a portfolio that has active holdings.
      (Close or move all holdings first.)
    """
    portfolio = await session.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if portfolio.is_default:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the default portfolio. Rename it instead.",
        )

    # Check for active holdings
    active_stmt = select(Holding).where(
        Holding.portfolio_id == portfolio_id,
        Holding.status == "active",
    )
    active_holdings = (await session.execute(active_stmt)).scalars().all()

    if active_holdings:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete portfolio with {len(active_holdings)} active holding(s). "
                   f"Close or move all holdings first.",
        )

    await session.delete(portfolio)
    await session.commit()

    return {"id": portfolio_id, "message": "Portfolio deleted successfully"}
