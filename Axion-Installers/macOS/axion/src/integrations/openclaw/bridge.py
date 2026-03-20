"""
Axion OpenClaw Bridge — Exposes Axion agents as callable tools.

This module provides:
  1. Tool functions that OpenClaw agents can invoke via MCP or HTTP
  2. A FastAPI sub-router that serves as the OpenClaw tool interface
  3. Agent wrappers that translate between OpenClaw and Axion

Each Axion agent becomes a set of tools that OpenClaw can call:
  - portfolio.summary, portfolio.holdings, portfolio.exposure
  - events.list, events.recent
  - alerts.active, alerts.acknowledge
  - agents.run_collection, agents.run_analysis, agents.run_risk, etc.
  - digests.latest, digests.generate
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("axion.openclaw")

router = APIRouter(prefix="/api/v1/openclaw", tags=["openclaw"])


# ---------------------------------------------------------------------------
# Tool Registry — all tools OpenClaw agents can call
# ---------------------------------------------------------------------------
TOOL_REGISTRY: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, agent: str = "commander"):
    """Decorator to register a function as an OpenClaw-callable tool."""
    def decorator(func):
        TOOL_REGISTRY[name] = {
            "name": name,
            "description": description,
            "agent": agent,
            "handler": func,
        }
        return func
    return decorator


# ---------------------------------------------------------------------------
# Portfolio Tools
# ---------------------------------------------------------------------------
@tool("portfolio.summary", "Get portfolio summary stats (total value, P&L, holding count, sectors)")
async def tool_portfolio_summary(**kwargs) -> dict:
    from src.ledger.portfolio import PortfolioLedger
    from src.database.connection import get_db
    from src.database.models import Holding, Security
    from sqlalchemy import select

    async with get_db() as session:
        stmt = (
            select(Holding, Security)
            .outerjoin(Security, Holding.ticker == Security.ticker)
            .where(Holding.status == "active")
        )
        rows = (await session.execute(stmt)).all()

    total_mv = sum(h.market_value or 0 for h, s in rows)
    total_cost = sum((h.quantity or 0) * (h.avg_cost_basis or 0) for h, s in rows)
    sectors = set(s.sector for h, s in rows if s and s.sector)

    pnl = total_mv - total_cost
    pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else None

    return {
        "total_market_value": round(total_mv, 2),
        "total_cost_basis": round(total_cost, 2),
        "total_pnl": round(pnl, 2),
        "total_pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
        "holding_count": len(rows),
        "sector_count": len(sectors),
    }


@tool("portfolio.holdings", "Get all portfolio holdings with details", agent="intake")
async def tool_portfolio_holdings(limit: int = 50, **kwargs) -> list[dict]:
    ledger = __import__("src.ledger.portfolio", fromlist=["PortfolioLedger"]).PortfolioLedger()
    holdings = await ledger.get_all_holdings()
    return holdings[:limit]


@tool("portfolio.exposure", "Get portfolio exposure breakdown by dimension (sector/geography/currency/theme)")
async def tool_portfolio_exposure(dimension: str = "sector", **kwargs) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"http://localhost:7777/api/v1/portfolio/exposure", params={"dimension": dimension})
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Event Tools
# ---------------------------------------------------------------------------
@tool("events.recent", "Get recent events with optional ticker filter", agent="collection")
async def tool_events_recent(limit: int = 20, ticker: str = None, **kwargs) -> list[dict]:
    from src.database.connection import get_db
    from src.database.models import Event
    from sqlalchemy import select

    async with get_db() as session:
        stmt = select(Event).order_by(Event.fetched_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id": e.id,
            "title": e.title,
            "event_type": e.event_type,
            "materiality": getattr(e, "materiality", None),
            "source_id": e.source_id,
            "published_at": e.published_at,
            "url": e.url,
        }
        for e in rows
    ]


# ---------------------------------------------------------------------------
# Alert Tools
# ---------------------------------------------------------------------------
@tool("alerts.active", "Get all active (unacknowledged) alerts", agent="risk")
async def tool_alerts_active(**kwargs) -> list[dict]:
    from src.database.connection import get_db
    from src.database.models import Alert
    from sqlalchemy import select

    async with get_db() as session:
        stmt = (
            select(Alert)
            .where(Alert.acknowledged == 0)
            .order_by(Alert.created_at.desc())
            .limit(50)
        )
        rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id": a.id,
            "severity": a.severity,
            "title": a.title,
            "body": a.body,
            "alert_type": a.alert_type,
            "related_holdings": a.related_holdings,
            "agent_id": a.agent_id,
            "created_at": a.created_at,
        }
        for a in rows
    ]


@tool("alerts.acknowledge", "Acknowledge an alert by ID", agent="commander")
async def tool_alert_acknowledge(alert_id: str, **kwargs) -> dict:
    from src.database.connection import get_db
    from src.database.models import Alert
    from sqlalchemy import update

    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as session:
        stmt = (
            update(Alert)
            .where(Alert.id == alert_id, Alert.acknowledged == 0)
            .values(acknowledged=1, acknowledged_at=now)
        )
        result = await session.execute(stmt)
        await session.commit()
        return {"acknowledged": result.rowcount > 0, "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Agent Run Tools
# ---------------------------------------------------------------------------
@tool("agents.run_collection", "Trigger news & event collection from all sources", agent="collection")
async def tool_run_collection(**kwargs) -> dict:
    from src.agents.collection import CollectionAgent
    agent = CollectionAgent()
    result = await agent.run()
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


@tool("agents.run_analysis", "Run impact analysis on recent events", agent="analysis")
async def tool_run_analysis(**kwargs) -> dict:
    from src.agents.analysis import AnalysisAgent
    agent = AnalysisAgent()
    result = await agent.run()
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


@tool("agents.run_classification", "Classify unclassified securities (sector, geography, themes)", agent="classification")
async def tool_run_classification(**kwargs) -> dict:
    from src.agents.classification import ClassificationAgent
    agent = ClassificationAgent()
    result = await agent.run()
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


@tool("agents.run_risk", "Run risk assessment (concentration, calendar, correlation checks)", agent="risk")
async def tool_run_risk(**kwargs) -> dict:
    from src.agents.risk import RiskAgent
    agent = RiskAgent()
    result = await agent.run()
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


@tool("agents.run_coverage", "Check event coverage gaps across holdings", agent="coverage")
async def tool_run_coverage(**kwargs) -> dict:
    from src.agents.coverage_qa import CoverageQAAgent
    agent = CoverageQAAgent()
    result = await agent.run()
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


# ---------------------------------------------------------------------------
# Digest Tools
# ---------------------------------------------------------------------------
@tool("digests.latest", "Get the most recent intelligence digest", agent="analysis")
async def tool_digest_latest(**kwargs) -> dict | None:
    from src.database.connection import get_db
    from src.database.models import Digest
    from sqlalchemy import select

    async with get_db() as session:
        stmt = select(Digest).order_by(Digest.created_at.desc()).limit(1)
        row = (await session.execute(stmt)).scalars().first()

    if not row:
        return None
    return {
        "id": row.id,
        "digest_type": row.digest_type,
        "content": row.content,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "created_at": row.created_at,
    }


@tool("digests.generate", "Generate a fresh intelligence digest", agent="analysis")
async def tool_digest_generate(period: str = "daily", **kwargs) -> dict:
    from src.agents.analysis import AnalysisAgent
    agent = AnalysisAgent()
    result = await agent.generate_digest(period=period)
    return result if isinstance(result, dict) else {"status": "completed", "result": str(result)}


# ---------------------------------------------------------------------------
# API Routes — HTTP interface for OpenClaw
# ---------------------------------------------------------------------------
@router.get("/tools")
async def list_tools():
    """List all available tools for OpenClaw agents."""
    return [
        {
            "name": info["name"],
            "description": info["description"],
            "agent": info["agent"],
        }
        for info in TOOL_REGISTRY.values()
    ]


@router.post("/call/{tool_name}")
async def call_tool(tool_name: str, body: dict = None):
    """Invoke a tool by name. Used by OpenClaw agents via HTTP."""
    if tool_name not in TOOL_REGISTRY:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    handler = TOOL_REGISTRY[tool_name]["handler"]
    try:
        result = await handler(**(body or {}))
        return {"tool": tool_name, "status": "ok", "result": result}
    except Exception as e:
        logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
        return {"tool": tool_name, "status": "error", "error": str(e)}


@router.get("/status")
async def openclaw_status():
    """OpenClaw bridge health check."""
    return {
        "status": "ok",
        "tools_registered": len(TOOL_REGISTRY),
        "tool_names": list(TOOL_REGISTRY.keys()),
    }
