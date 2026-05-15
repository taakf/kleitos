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
from src.llm.client import call_llm_text
from src.llm.grounded import (
    assemble_chat_context,
    build_chat_system_prompt,
    render_deterministic_chat_answer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """User query to the Axion command center.

    Phase 9E: ``portfolio_id`` is now a first-class field so the
    assembler can scope holdings, alerts, events, and analysis notes
    to exactly the active portfolio.  Defaults to ``"default"`` for
    backward compatibility with pre-9E clients.
    """
    query: str
    scope: str = "portfolio"  # future: could be "holding:NVDA", etc.
    portfolio_id: str = "default"


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
                from src.config import get_settings
                _port = get_settings().api.port
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                            f"http://127.0.0.1:{_port}{endpoint}",
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

    # Phase 9E: assemble a portfolio-scoped grounded context.  Every
    # downstream list (holdings, alerts, events, factor touchpoints,
    # relationship touchpoints, analysis highlights) is filtered to
    # the active portfolio so cross-portfolio leakage is structurally
    # impossible.
    ctx = await assemble_chat_context(
        session, portfolio_id=req.portfolio_id or "default",
    )

    # Check for safe action triggers
    actions = await _try_action_trigger(query.lower(), session)

    # Data refs for the response
    data_refs = {
        "portfolio_id": ctx.portfolio_id,
        "holdings_count": ctx.holding_count,
        "alerts_count": len(ctx.active_alerts),
        "events_count": len(ctx.recent_events),
        "analysis_count": len(ctx.analysis_highlights),
        "factor_touchpoints_count": len(ctx.factor_touchpoints),
        "relationship_touchpoints_count": len(ctx.relationship_touchpoints),
        "total_value": ctx.total_value,
    }

    warnings: list[str] = []

    # Try LLM-powered response
    if ctx.llm_available:
        system = build_chat_system_prompt(ctx)

        # If actions were triggered, append that info
        if actions:
            query_with_actions = (
                f"{query}\n\n[System note: The following actions were triggered "
                f"by this request: {', '.join(actions)}. Acknowledge them in your response.]"
            )
        else:
            query_with_actions = query

        answer = await call_llm_text(query_with_actions, system=system)

        if answer and not answer.startswith("[Axion]"):
            return ChatResponse(
                answer=answer,
                mode="ai-enhanced",
                provider=ctx.provider,
                context_summary=ctx.summary_line(),
                data_refs=data_refs,
                actions_taken=actions,
            )
        else:
            if answer and answer.startswith("[Axion]"):
                warnings.append("AI provider temporarily unavailable — showing portfolio summary instead.")
            else:
                warnings.append("AI provider returned an empty response.")

    # Phase 9E: deterministic fallback built from the grounded
    # context.  Carries factor + relationship touchpoints so the
    # fallback feels authoritative rather than empty.
    answer = render_deterministic_chat_answer(ctx, query)

    if actions:
        answer = (
            "**Actions triggered:** "
            + ", ".join(a.replace("_", " ").title() for a in actions)
            + "\n\n"
            + answer
        )

    if not ctx.llm_available:
        warnings.append(
            "Running in rule-based mode. Configure an Anthropic API key "
            "in Settings for conversational AI responses."
        )

    return ChatResponse(
        answer=answer,
        mode="rule-based",
        provider=None,
        context_summary=ctx.summary_line(),
        data_refs=data_refs,
        actions_taken=actions,
        warnings=warnings,
    )
