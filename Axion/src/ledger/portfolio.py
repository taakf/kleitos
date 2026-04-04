"""Portfolio Ledger — canonical source of truth for holdings and trades."""

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func

from src.database.connection import get_db
from src.database.models import AuditLog, Holding, Security

logger = logging.getLogger(__name__)


class PortfolioLedger:
    """Manages the canonical portfolio ledger."""

    async def get_all_holdings(self, portfolio_id: str = "default", status: str = "active") -> list[dict]:
        """Get all holdings for a portfolio with security metadata."""
        async with get_db() as session:
            stmt = (
                select(Holding, Security)
                .outerjoin(Security, Holding.ticker == Security.ticker)
                .where(Holding.portfolio_id == portfolio_id, Holding.status == status)
                .order_by(Holding.weight_pct.desc().nullslast())
            )
            rows = (await session.execute(stmt)).all()

        results = []
        for h, s in rows:
            d = {
                "id": h.id, "ticker": h.ticker, "isin": h.isin, "venue": h.venue,
                "currency": h.currency, "quantity": h.quantity,
                "avg_cost_basis": h.avg_cost_basis, "current_price": h.current_price,
                "market_value": h.market_value, "weight_pct": h.weight_pct,
                "portfolio_id": h.portfolio_id, "status": h.status,
                "created_at": h.created_at, "updated_at": h.updated_at,
            }
            if s:
                d.update({
                    "name": s.name, "sector": s.sector, "subsector": s.subsector,
                    "geography": s.geography, "themes": s.themes,
                })
            results.append(d)
        return results

    async def get_holding(self, holding_id: str) -> dict | None:
        """Get a single holding by ID."""
        async with get_db() as session:
            stmt = (
                select(Holding, Security)
                .outerjoin(Security, Holding.ticker == Security.ticker)
                .where(Holding.id == holding_id)
            )
            row = (await session.execute(stmt)).first()

        if not row:
            return None
        h, s = row
        d = {
            "id": h.id, "ticker": h.ticker, "isin": h.isin, "venue": h.venue,
            "currency": h.currency, "quantity": h.quantity,
            "avg_cost_basis": h.avg_cost_basis, "current_price": h.current_price,
            "market_value": h.market_value, "weight_pct": h.weight_pct,
        }
        if s:
            d.update({"name": s.name, "sector": s.sector, "geography": s.geography, "themes": s.themes})
        return d

    async def get_holding_by_ticker(self, ticker: str, portfolio_id: str = "default") -> dict | None:
        """Get a holding by ticker."""
        async with get_db() as session:
            stmt = select(Holding).where(
                Holding.ticker == ticker.upper(),
                Holding.portfolio_id == portfolio_id,
                Holding.status == "active",
            )
            h = (await session.execute(stmt)).scalars().first()

        if not h:
            return None
        return {
            "id": h.id, "ticker": h.ticker, "quantity": h.quantity,
            "currency": h.currency, "avg_cost_basis": h.avg_cost_basis,
        }

    async def upsert_holding(self, data: dict, agent_id: str = "intake") -> tuple[str, str]:
        """Insert or update a holding. Returns (holding_id, action)."""
        ticker = data["ticker"].upper().strip()
        portfolio_id = data.get("portfolio_id", "main")
        now = datetime.now(timezone.utc).isoformat()

        existing = await self.get_holding_by_ticker(ticker, portfolio_id)

        if existing:
            holding_id = existing["id"]
            async with get_db() as session:
                h = await session.get(Holding, holding_id)
                if h:
                    for field in ["quantity", "avg_cost_basis", "current_price",
                                  "market_value", "weight_pct", "currency", "isin", "venue"]:
                        if field in data and data[field] is not None:
                            setattr(h, field, data[field])
                    h.updated_at = now

                    # Audit
                    session.add(AuditLog(
                        id=str(uuid.uuid4()), entity_type="holding", entity_id=holding_id,
                        action="update", new_value=json.dumps(data), agent_id=agent_id,
                        created_at=now,
                    ))
                    await session.commit()
            return holding_id, "updated"
        else:
            holding_id = str(uuid.uuid4())
            async with get_db() as session:
                session.add(Holding(
                    id=holding_id, ticker=ticker, isin=data.get("isin"),
                    venue=data.get("venue"), currency=data.get("currency", "USD"),
                    quantity=data.get("quantity", 0), avg_cost_basis=data.get("avg_cost_basis"),
                    current_price=data.get("current_price"), market_value=data.get("market_value"),
                    weight_pct=data.get("weight_pct"), portfolio_id=portfolio_id,
                    status="active", created_at=now, updated_at=now,
                ))
                session.add(AuditLog(
                    id=str(uuid.uuid4()), entity_type="holding", entity_id=holding_id,
                    action="create", new_value=json.dumps(data), agent_id=agent_id,
                    created_at=now,
                ))
                await session.commit()
            return holding_id, "created"

    async def parse_csv(self, content: str) -> list[dict]:
        """Parse a CSV portfolio file into holding dicts."""
        reader = csv.DictReader(io.StringIO(content))
        holdings = []

        column_map = {
            "ticker": "ticker", "symbol": "ticker", "stock": "ticker",
            "quantity": "quantity", "shares": "quantity", "qty": "quantity",
            "price": "current_price", "current_price": "current_price", "last_price": "current_price",
            "cost": "avg_cost_basis", "avg_cost": "avg_cost_basis", "cost_basis": "avg_cost_basis",
            "currency": "currency", "ccy": "currency",
            "isin": "isin", "venue": "venue", "exchange": "venue", "market": "venue",
            "weight": "weight_pct", "weight_pct": "weight_pct", "pct": "weight_pct",
            "value": "market_value", "market_value": "market_value",
        }

        for row in reader:
            holding = {}
            for csv_col, value in row.items():
                normalized = csv_col.strip().lower().replace(" ", "_")
                target = column_map.get(normalized)
                if target:
                    if target in ("quantity", "current_price", "avg_cost_basis", "market_value", "weight_pct"):
                        try:
                            holding[target] = float(value.replace(",", "").strip()) if value.strip() else None
                        except (ValueError, AttributeError):
                            holding[target] = None
                    else:
                        holding[target] = value.strip()

            if "ticker" in holding and holding["ticker"]:
                holdings.append(holding)
            else:
                logger.warning("Skipping CSV row without ticker: %s", row)

        return holdings

    async def recalculate_weights(self, portfolio_id: str = "default") -> None:
        """Recalculate market_value and weight_pct for all active holdings.

        market_value = quantity * (current_price or avg_cost_basis)
        weight_pct   = market_value / total_portfolio_value * 100
        """
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            stmt = select(Holding).where(
                Holding.portfolio_id == portfolio_id,
                Holding.status == "active",
            )
            holdings = list((await session.execute(stmt)).scalars().all())

            if not holdings:
                return

            # Compute market values
            mv_map: dict[str, float] = {}
            for h in holdings:
                price = h.current_price if h.current_price is not None else (h.avg_cost_basis or 0)
                mv_map[h.id] = (h.quantity or 0) * price

            total_mv = sum(mv_map.values())
            if total_mv <= 0:
                return

            for h in holdings:
                mv = mv_map[h.id]
                h.market_value = round(mv, 4)
                h.weight_pct = round(mv / total_mv * 100, 4)
                h.updated_at = now

            await session.commit()

        logger.info(
            "Recalculated weights for %d holdings  total_mv=%.2f",
            len(holdings), total_mv,
        )

    async def get_portfolio_summary(self, portfolio_id: str = "default") -> dict:
        """Get portfolio summary statistics."""
        holdings = await self.get_all_holdings(portfolio_id)
        total_value = sum(h.get("market_value", 0) or 0 for h in holdings)
        sectors = set(h.get("sector", "Unknown") for h in holdings)
        geographies = set(h.get("geography", "Unknown") for h in holdings)

        return {
            "portfolio_id": portfolio_id,
            "total_holdings": len(holdings),
            "total_market_value": total_value,
            "unique_sectors": len(sectors),
            "unique_geographies": len(geographies),
            "top_holdings": sorted(holdings, key=lambda h: h.get("weight_pct", 0) or 0, reverse=True)[:10],
        }
