"""Axion Command Center — Chat / Query API.

Provides a conversational query endpoint grounded in live Axion data.
This is the backend for:
- the in-app command center UI (future dashboard tab)
- any surface that wants Axion-native conversational intelligence

The endpoint assembles context from the Axion database, sends the
user query to the configured LLM provider, and returns a structured
response with source references and mode indicators.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.llm.client import call_llm_text, is_llm_available
from src.llm.context import AXION_SYSTEM_PROMPT, AxionContext, assemble_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """User query to the Axion command center."""
    query: str
    scope: str = "portfolio"  # future: could be "holding:NVDA", etc.


class ChatResponse(BaseModel):
    """Structured response from the command center."""
    answer: str
    mode: str              # "ai-enhanced", "rule-based", "unavailable"
    provider: str | None = None
    context_summary: str
    data_refs: dict[str, Any] = {}
    actions_taken: list[str] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Safe action triggers
# ---------------------------------------------------------------------------
_SAFE_ACTIONS = {
    "collect": ("collection", "/api/v1/agents/collection/run"),
    "analyze": ("analysis", "/api/v1/agents/analysis/run"),
    "classify": ("classification", "/api/v1/agents/classification/run"),
    "risk": ("risk", "/api/v1/agents/risk/run"),
    "digest": ("digest", "/api/v1/digests/generate"),
}


async def _try_action_trigger(query_lower: str, session: AsyncSession) -> list[str]:
    """Check if the user query requests a safe action trigger.

    Returns list of action names that were triggered.
    Only fires for explicit requests like "run collection" or "generate digest".
    """
    import httpx

    triggered = []
    action_phrases = {
        "collect": ["run collection", "collect now", "fetch news", "trigger collection"],
        "analyze": ["run analysis", "analyze now", "trigger analysis"],
        "classify": ["run classification", "classify now", "classify holdings"],
        "risk": ["run risk", "check risk", "risk assessment now"],
        "digest": ["generate digest", "generate a digest", "create digest", "make digest", "morning brief now"],
    }

    for action_key, phrases in action_phrases.items():
        if any(phrase in query_lower for phrase in phrases):
            agent_name, endpoint = _SAFE_ACTIONS[action_key]
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                            f"http://127.0.0.1:7777{endpoint}",
                            json={},
                        )
                    if resp.status_code < 300:
                        triggered.append(f"{agent_name}_triggered")
                        logger.info("Chat action triggered: %s", agent_name)
            except Exception as exc:
                logger.warning("Failed to trigger %s: %s", agent_name, exc)

    return triggered


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
async def chat_query(
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """Process a conversational query against the Axion intelligence engine.

    Assembles live portfolio context, sends to the configured LLM provider,
    and returns a grounded response with data references.
    """
    query = req.query.strip()
    if not query:
        return ChatResponse(
            answer="Please enter a question about your portfolio.",
            mode="rule-based",
            context_summary="",
            warnings=["Empty query"],
        )

    # Assemble context from live database
    ctx = await assemble_context(session)

    # Check for safe action triggers
    actions = await _try_action_trigger(query.lower(), session)

    # Data refs for the response
    data_refs = {
        "holdings_count": ctx.holding_count,
        "alerts_count": len(ctx.alerts),
        "events_count": len(ctx.events),
        "analysis_count": len(ctx.analysis_notes),
        "total_value": ctx.total_value,
    }

    warnings: list[str] = []

    # Try LLM-powered response
    if ctx.llm_available:
        system = AXION_SYSTEM_PROMPT.format(context=ctx.to_prompt_block())

        # If actions were triggered, append that info
        if actions:
            query_with_actions = (
                f"{query}\n\n[System note: The following actions were triggered "
                f"by this request: {', '.join(actions)}. Acknowledge them in your response.]"
            )
        else:
            query_with_actions = query

        answer = await call_llm_text(query_with_actions, system=system)

        if answer:
            return ChatResponse(
                answer=answer,
                mode="ai-enhanced",
                provider=ctx.provider,
                context_summary=ctx.to_summary_line(),
                data_refs=data_refs,
                actions_taken=actions,
            )
        else:
            warnings.append("AI provider returned an empty response.")

    # Rule-based fallback: build a helpful structured response
    answer_parts = []

    if actions:
        answer_parts.append(
            "**Actions triggered:** " + ", ".join(a.replace("_", " ").title() for a in actions)
        )

    answer_parts.append(f"**Portfolio:** {ctx.holding_count} holdings, ${ctx.total_value:,.0f} total value")

    if ctx.alerts:
        answer_parts.append(f"\n**Active Alerts ({len(ctx.alerts)}):**")
        for a in ctx.alerts[:5]:
            answer_parts.append(f"  - [{a['severity']}] {a['title']}")

    if ctx.analysis_notes:
        answer_parts.append(f"\n**Recent Analysis ({len(ctx.analysis_notes)} notes):**")
        for n in ctx.analysis_notes[:5]:
            answer_parts.append(
                f"  - {n['ticker']}: {n['direction']}/{n['materiality']}"
            )

    if ctx.events:
        answer_parts.append(f"\n**Recent Events ({len(ctx.events)}):**")
        for e in ctx.events[:5]:
            answer_parts.append(f"  - {e['title'][:60]}")

    if not ctx.llm_available:
        warnings.append(
            "Running in rule-based mode. Configure an Anthropic API key "
            "in Settings for conversational AI responses."
        )

    return ChatResponse(
        answer="\n".join(answer_parts),
        mode="rule-based",
        provider=None,
        context_summary=ctx.to_summary_line(),
        data_refs=data_refs,
        actions_taken=actions,
        warnings=warnings,
    )
