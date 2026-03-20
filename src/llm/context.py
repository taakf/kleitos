"""Shared context assembly for Axion conversational queries.

Builds a structured context block from live Axion database state,
suitable for injection into LLM system prompts. Used by:

- ``/api/v1/chat`` endpoint (command center backend)
- Telegram bot (can be migrated to use this)
- Future conversational surfaces

The context is grounded in real Axion data — holdings, alerts, events,
exposures — not generic model knowledge.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Alert,
    AnalysisNote,
    Event,
    EventLink,
    Holding,
    Security,
)

logger = logging.getLogger(__name__)


@dataclass
class AxionContext:
    """Structured context assembled from live Axion data."""

    # Summary
    holding_count: int = 0
    total_value: float = 0.0
    sector_count: int = 0
    currency_count: int = 0

    # Detail lists
    holdings: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    analysis_notes: list[dict[str, Any]] = field(default_factory=list)

    # System state
    llm_available: bool = False
    provider: str | None = None

    def to_prompt_block(self) -> str:
        """Format as a text block suitable for LLM system prompt injection."""
        parts = []

        # Portfolio summary
        parts.append(
            f"Portfolio: {self.holding_count} holdings, "
            f"total value ${self.total_value:,.0f}, "
            f"{self.sector_count} sectors, "
            f"{self.currency_count} currencies."
        )

        # Top holdings
        if self.holdings:
            top = ", ".join(
                f"{h['ticker']} ({h.get('weight_pct', 0):.1f}%, "
                f"${h.get('market_value', 0):,.0f})"
                for h in self.holdings[:10]
            )
            parts.append(f"Top holdings: {top}")

        # Active alerts
        if self.alerts:
            alert_lines = "; ".join(
                f"[{a.get('severity', '?')}] {a.get('title', '?')}"
                for a in self.alerts[:5]
            )
            parts.append(f"Active alerts ({len(self.alerts)}): {alert_lines}")
        else:
            parts.append("No active alerts.")

        # Recent events
        if self.events:
            event_lines = "; ".join(
                f"{e.get('title', '?')[:60]}" for e in self.events[:5]
            )
            parts.append(f"Recent events ({len(self.events)}): {event_lines}")

        # Analysis highlights
        if self.analysis_notes:
            note_lines = "; ".join(
                f"{n.get('ticker', '?')}: {n.get('direction', '?')}/{n.get('materiality', '?')}"
                for n in self.analysis_notes[:5]
            )
            parts.append(f"Recent analysis: {note_lines}")

        # Mode
        if self.llm_available:
            parts.append(f"Analysis mode: AI-enhanced ({self.provider})")
        else:
            parts.append("Analysis mode: Rule-based (no AI provider configured)")

        return "\n".join(parts)

    def to_summary_line(self) -> str:
        """One-line summary for response metadata."""
        return (
            f"{self.holding_count} holdings, "
            f"{len(self.alerts)} active alerts, "
            f"{len(self.events)} recent events"
        )


# ---------------------------------------------------------------------------
# System prompt for Axion chat
# ---------------------------------------------------------------------------
AXION_SYSTEM_PROMPT = """You are Axion, a portfolio intelligence system built by 4Labs.
You are assisting a hedge fund portfolio manager.

You have access to the following real-time portfolio data:

{context}

Rules:
- Be concise, professional, and factual
- Never recommend buying or selling securities
- Use the data above to answer questions accurately
- If asked about something not in the data, say so honestly
- When referencing events or alerts, mention the source or title
- Distinguish between high-signal developments and noise
- Keep responses under 400 words
- If the user asks to run a command (collect, analyze, digest), confirm it was triggered"""


# ---------------------------------------------------------------------------
# Context assembly (database-direct, no HTTP)
# ---------------------------------------------------------------------------
async def assemble_context(session: AsyncSession) -> AxionContext:
    """Build a full context snapshot from the current database state.

    This queries the database directly (not via HTTP) for maximum
    efficiency and is the shared foundation for all conversational surfaces.
    """
    ctx = AxionContext()

    # LLM availability
    from src.llm.client import is_llm_available
    ctx.llm_available = is_llm_available()
    if ctx.llm_available:
        from src.config import get_settings
        ctx.provider = get_settings().llm.provider

    # Holdings
    h_rows = (await session.execute(
        select(Holding).where(Holding.status == "active").order_by(Holding.weight_pct.desc())
    )).scalars().all()
    ctx.holding_count = len(h_rows)
    ctx.total_value = sum((h.current_price or 0) * (h.quantity or 0) for h in h_rows)
    sectors = set()
    currencies = set()
    for h in h_rows:
        if h.currency:
            currencies.add(h.currency)
        # Get sector from securities table
        sec = (await session.execute(
            select(Security.sector).where(Security.ticker == h.ticker)
        )).scalar()
        if sec and sec != "Unknown":
            sectors.add(sec)
        ctx.holdings.append({
            "ticker": h.ticker,
            "quantity": h.quantity,
            "market_value": (h.current_price or 0) * (h.quantity or 0),
            "weight_pct": h.weight_pct or 0,
            "sector": sec or "Unknown",
        })
    ctx.sector_count = len(sectors)
    ctx.currency_count = len(currencies)

    # Alerts (active, unacknowledged)
    a_rows = (await session.execute(
        select(Alert).where(Alert.acknowledged == 0).order_by(Alert.created_at.desc()).limit(10)
    )).scalars().all()
    ctx.alerts = [
        {"severity": a.severity, "title": a.title, "type": a.alert_type}
        for a in a_rows
    ]

    # Recent events (last 50)
    e_rows = (await session.execute(
        select(Event).order_by(Event.created_at.desc()).limit(50)
    )).scalars().all()
    ctx.events = [
        {"title": e.title, "url": e.url, "type": e.event_type,
         "materiality": e.materiality, "source_id": e.source_id}
        for e in e_rows
    ]

    # Recent analysis notes (last 20)
    n_rows = (await session.execute(
        select(AnalysisNote).order_by(AnalysisNote.created_at.desc()).limit(20)
    )).scalars().all()
    for n in n_rows:
        try:
            content = json.loads(n.content) if n.content else {}
        except (json.JSONDecodeError, TypeError):
            content = {}
        ctx.analysis_notes.append({
            "ticker": content.get("ticker", "?"),
            "direction": content.get("impact_direction", "?"),
            "magnitude": content.get("impact_magnitude", "?"),
            "materiality": n.materiality or content.get("materiality", "?"),
            "outlook": content.get("short_term_outlook", ""),
        })

    return ctx
