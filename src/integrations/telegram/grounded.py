"""Phase 9F — Grounded, portfolio-scoped Telegram intelligence layer.

This module is the SINGLE source of truth for every Telegram surface
that touches portfolio intelligence:

  * alert delivery formatting
  * free-text chat answers (LLM + deterministic fallback)
  * digest rendering

It reuses the Phase 9E ``src/llm/grounded`` contract, so every
Telegram-facing surface carries the same anti-hallucination guarantees
as the dashboard/API paths.  There is no second chat stack.

The module also owns:

  * ``TelegramSession`` storage (per-chat active portfolio pin)
  * dedupe + cooldown bookkeeping via ``TelegramDelivery`` rows
  * ``should_deliver`` — the single gate used by ``notifications.py``
    to decide whether to push a given alert to a given chat

All reads are portfolio-scoped.  Cross-portfolio leakage is
structurally impossible because the chat session resolves the active
portfolio BEFORE any downstream assembler runs.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Alert,
    EventLink,
    Holding,
    TelegramDelivery,
    TelegramSession,
)
from src.llm.grounded import (
    GroundedChatContext,
    _link_to_grounded_chain,
    assemble_chat_context,
    build_chat_system_prompt,
    render_deterministic_chat_answer,
)

logger = logging.getLogger("axion.telegram.grounded")


DEFAULT_PORTFOLIO_ID = "default"

# Spam-control constants.  A second alert for the same
# (event_id, holding_id, channel) tuple landing within COOLDOWN_SECONDS
# of a prior successful delivery is collapsed.  A second delivery
# attempt for the same (chat_id, alert_id) tuple is ALWAYS collapsed
# (dedupe — we never send the same alert row twice).
COOLDOWN_SECONDS = 30 * 60  # 30 minutes

# Minimum confidence (0-1) an alert chain must reach before the
# "why it matters" section is appended.  Below this we still send the
# base alert, we just don't dress it with a weak chain.
MIN_CHAIN_CONFIDENCE = 0.15


# ---------------------------------------------------------------------------
# Session store (per-Telegram-chat active portfolio pin)
# ---------------------------------------------------------------------------


async def get_active_portfolio_id(
    session: AsyncSession, chat_id: int,
) -> str:
    """Return the portfolio_id currently active for ``chat_id``.

    Falls back to ``'default'`` for chats that have never issued
    ``/portfolio_select`` — so pre-9F users keep working with zero
    configuration.  Never raises.
    """
    row = await session.get(TelegramSession, chat_id)
    if row and row.active_portfolio_id:
        return row.active_portfolio_id
    return DEFAULT_PORTFOLIO_ID


async def set_active_portfolio_id(
    session: AsyncSession, chat_id: int, portfolio_id: str,
) -> str:
    """Pin ``chat_id`` to ``portfolio_id``.

    Upsert semantics: creates the row if missing, updates it otherwise.
    Returns the portfolio_id that was stored (echo).  Does NOT validate
    that the portfolio exists — the caller is responsible for passing
    a known portfolio id so we never silently redirect a user to a
    non-existent portfolio.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = await session.get(TelegramSession, chat_id)
    if row is None:
        row = TelegramSession(
            chat_id=chat_id,
            active_portfolio_id=portfolio_id,
            updated_at=now,
        )
        session.add(row)
    else:
        row.active_portfolio_id = portfolio_id
        row.updated_at = now
    await session.commit()
    return portfolio_id


# ---------------------------------------------------------------------------
# Delivery bookkeeping (dedupe, cooldown, retry-safety)
# ---------------------------------------------------------------------------


@dataclass
class DeliveryDecision:
    """Result of the dedupe / cooldown gate."""

    should_send: bool
    reason: str          # "ok" | "already_delivered" | "cooldown" | "wrong_portfolio"
    dedup_key: str | None = None


async def should_deliver(
    session: AsyncSession,
    *,
    chat_id: int,
    alert_id: str,
    alert_portfolio_id: str | None,
    chat_portfolio_id: str,
    event_id: str | None = None,
    holding_id: str | None = None,
    channel: str | None = None,
) -> DeliveryDecision:
    """Decide whether a specific alert should be pushed to a specific chat.

    Phase 9F rules (checked in order):

    1. **Portfolio match** — an alert belonging to portfolio X is never
       sent to a chat currently pinned to portfolio Y.  Alerts with a
       NULL portfolio_id are treated as 'default'.
    2. **Dedupe** — if a ``TelegramDelivery`` row already exists for
       (chat_id, alert_id) with status 'sent', we NEVER retry.
    3. **Cooldown** — if the same (event_id, holding_id, channel)
       tuple was delivered to this chat within the cooldown window,
       collapse the new alert.

    Returns a :class:`DeliveryDecision` with ``should_send`` and a
    human-readable reason, plus the dedup_key so the caller can pass
    it straight into :func:`record_delivery`.
    """
    effective_alert_pid = alert_portfolio_id or DEFAULT_PORTFOLIO_ID

    # Rule 1 — portfolio match
    if effective_alert_pid != chat_portfolio_id:
        return DeliveryDecision(
            should_send=False,
            reason="wrong_portfolio",
            dedup_key=None,
        )

    # Rule 2 — dedupe by (chat_id, alert_id)
    existing = (await session.execute(
        select(TelegramDelivery).where(
            and_(
                TelegramDelivery.chat_id == chat_id,
                TelegramDelivery.alert_id == alert_id,
            )
        )
    )).scalars().first()
    if existing and existing.status == "sent":
        return DeliveryDecision(
            should_send=False,
            reason="already_delivered",
            dedup_key=existing.dedup_key,
        )

    # Rule 3 — cooldown on (event_id, holding_id, channel) per chat
    dedup_key = _make_dedup_key(event_id, holding_id, channel)
    if dedup_key:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=COOLDOWN_SECONDS)).isoformat()
        recent = (await session.execute(
            select(TelegramDelivery).where(
                and_(
                    TelegramDelivery.chat_id == chat_id,
                    TelegramDelivery.dedup_key == dedup_key,
                    TelegramDelivery.status == "sent",
                    TelegramDelivery.sent_at >= cutoff,
                )
            )
        )).scalars().first()
        if recent:
            return DeliveryDecision(
                should_send=False,
                reason="cooldown",
                dedup_key=dedup_key,
            )

    return DeliveryDecision(
        should_send=True,
        reason="ok",
        dedup_key=dedup_key,
    )


async def record_delivery(
    session: AsyncSession,
    *,
    chat_id: int,
    alert_id: str,
    portfolio_id: str | None,
    dedup_key: str | None,
    status: str,
    error: str | None = None,
) -> None:
    """Write (or upsert) a delivery row.

    The ``(chat_id, alert_id)`` tuple is UNIQUE, so the second call
    for the same tuple updates the existing row instead of failing.
    This is critical for retry: a failed send writes status='failed',
    and a subsequent successful send updates the same row to 'sent'.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = (await session.execute(
        select(TelegramDelivery).where(
            and_(
                TelegramDelivery.chat_id == chat_id,
                TelegramDelivery.alert_id == alert_id,
            )
        )
    )).scalars().first()
    if existing is None:
        session.add(TelegramDelivery(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            alert_id=alert_id,
            portfolio_id=portfolio_id,
            dedup_key=dedup_key,
            status=status,
            error=error,
            sent_at=now,
        ))
    else:
        existing.status = status
        existing.error = error
        existing.sent_at = now
        if dedup_key and not existing.dedup_key:
            existing.dedup_key = dedup_key
    await session.commit()


def _make_dedup_key(
    event_id: str | None, holding_id: str | None, channel: str | None,
) -> str | None:
    parts = [event_id or "*", holding_id or "*", channel or "*"]
    if parts == ["*", "*", "*"]:
        return None
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Grounded alert formatting
# ---------------------------------------------------------------------------


_SEVERITY_ICONS = {
    "critical": "\U0001F6A8",   # rotating light
    "high":     "\U0001F534",   # red circle
    "warning":  "\U0001F7E1",   # yellow circle
    "medium":   "\U0001F7E0",   # orange circle
    "info":     "\U0001F535",   # blue circle
    "low":      "\U0001F535",
}


def _severity_icon(severity: str | None) -> str:
    return _SEVERITY_ICONS.get((severity or "info").lower(), "\u2022")


def _parse_related_holdings(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


async def build_grounded_alert_message(
    session: AsyncSession,
    alert: Alert | dict,
) -> tuple[str, dict[str, Any]]:
    """Render a grounded Markdown message for a Telegram alert push.

    The message contains (in this order):

    1. Severity icon + ``[SEVERITY] Title``
    2. Alert body (truncated)
    3. Portfolio identity (``Portfolio: <id>``)
    4. Affected holdings (``Holdings: AAPL MSFT``)
    5. Factor / relationship channel row, if any
    6. Grounded "Why it matters" one-liner sourced from the dominant
       deterministic chain for the alert's event+holding pair.
    7. Confidence (if the chain carries one above the floor)

    Returns ``(message, meta)`` where ``meta`` carries ``event_id``,
    ``holding_id`` and ``channel`` for the delivery gate to use as
    the cooldown dedup key.  Never raises; on any DB hiccup we fall
    back to the plain alert body (the ground truth is always the
    alert row itself).
    """
    # Normalise input — accept either an ORM row or a dict
    if isinstance(alert, Alert):
        a_id = alert.id
        a_severity = alert.severity
        a_title = alert.title
        a_body = alert.body or ""
        a_type = alert.alert_type
        a_portfolio_id = alert.portfolio_id or DEFAULT_PORTFOLIO_ID
        a_related_holdings = alert.related_holdings
        a_related_events = alert.related_events
    else:
        a_id = alert.get("id", "")
        a_severity = alert.get("severity", "info")
        a_title = alert.get("title", "Untitled")
        a_body = alert.get("body") or ""
        a_type = alert.get("alert_type", "")
        a_portfolio_id = alert.get("portfolio_id") or DEFAULT_PORTFOLIO_ID
        a_related_holdings = alert.get("related_holdings")
        a_related_events = alert.get("related_events")

    tickers = _parse_related_holdings(a_related_holdings)
    event_ids = _parse_related_holdings(a_related_events)

    # Resolve the holding_id + grounded chain, if any.  We only look
    # at the first related (event, holding) pair — the alert row
    # itself is the source of truth; the chain is decoration.
    event_id: str | None = event_ids[0] if event_ids else None
    holding_id: str | None = None
    channel: str | None = None
    chain_line: str | None = None
    confidence_line: str | None = None

    if event_id and tickers:
        try:
            holding_row = (await session.execute(
                select(Holding).where(
                    Holding.portfolio_id == a_portfolio_id,
                    Holding.ticker == tickers[0],
                    Holding.status == "active",
                )
            )).scalars().first()
            if holding_row is not None:
                holding_id = holding_row.id
                link_rows = (await session.execute(
                    select(EventLink).where(
                        EventLink.event_id == event_id,
                        EventLink.link_target == holding_id,
                    ).order_by(EventLink.relevance_score.desc())
                )).scalars().all()
                if link_rows:
                    # Pick the strongest chain as the "why it matters"
                    best = link_rows[0]
                    gc = _link_to_grounded_chain(best, holding_row.ticker)
                    channel = gc.channel
                    chain_line = _format_chain_line(gc)
                    if gc.effect_confidence is not None and gc.effect_confidence >= MIN_CHAIN_CONFIDENCE:
                        confidence_line = (
                            f"Confidence: {gc.effect_confidence:.2f} "
                            f"({gc.origin.replace('_', ' ')})"
                        )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "Grounded alert enrichment failed for alert %s: %s",
                a_id[:8] if a_id else "?", exc,
            )

    # ---- Render ----
    icon = _severity_icon(a_severity)
    severity_upper = (a_severity or "info").upper()
    holdings_str = " ".join(f"`{t}`" for t in tickers) if tickers else ""

    lines: list[str] = [
        f"{icon} **[{severity_upper}] {a_title}**",
    ]
    if a_body:
        lines.append("")
        lines.append(a_body[:500])
    lines.append("")
    lines.append(f"Portfolio: `{a_portfolio_id}`")
    if holdings_str:
        lines.append(f"Holdings: {holdings_str}")
    if chain_line:
        lines.append(f"Why: {chain_line}")
    if confidence_line:
        lines.append(confidence_line)
    if a_type:
        lines.append(f"Type: {a_type}")

    meta = {
        "alert_id": a_id,
        "portfolio_id": a_portfolio_id,
        "event_id": event_id,
        "holding_id": holding_id,
        "channel": channel,
        "tickers": tickers,
    }
    return "\n".join(lines), meta


def _format_chain_line(chain) -> str:
    """Single-sentence 'why it matters' line drawn from a ``GroundedChain``.

    Never invents anything — if the chain has no rationale, we describe
    the link type + channel.  The wording is deliberately plain so
    Telegram Markdown stays safe.
    """
    if chain.rationale_summary:
        return chain.rationale_summary
    if chain.origin == "deterministic_factor":
        return (
            f"Deterministic {chain.channel_label or chain.channel or 'factor'} "
            f"touchpoint"
        )
    if chain.origin == "relationship":
        if chain.related_entity:
            return f"{chain.related_entity} via {chain.channel or 'relationship'}"
        return f"{chain.channel or 'relationship'} link"
    if chain.origin == "direct_match":
        return f"Direct mention of {chain.holding_ticker}"
    return f"{chain.origin} link"


# ---------------------------------------------------------------------------
# Grounded chat reply (same contract as dashboard/API chat)
# ---------------------------------------------------------------------------


async def render_grounded_telegram_reply(
    session: AsyncSession,
    *,
    chat_id: int,
    query: str,
) -> tuple[str, str, str]:
    """Build a grounded reply for a Telegram free-text message.

    Returns ``(answer_text, mode, portfolio_id)`` where ``mode`` is
    one of ``"ai-enhanced"`` or ``"rule-based"``.  The reply is
    identical in kind to what the ``/api/v1/chat`` endpoint produces:

    * portfolio scope comes from the Telegram session store
    * LLM prompts are built by
      :func:`src.llm.grounded.build_chat_system_prompt`
    * fallback is
      :func:`src.llm.grounded.render_deterministic_chat_answer`

    This is the ONLY function the bot's free-text handler should call —
    there is no second chat stack.  Never raises; worst case returns
    a plain "rule-based" deterministic answer.
    """
    portfolio_id = await get_active_portfolio_id(session, chat_id)
    ctx: GroundedChatContext = await assemble_chat_context(
        session, portfolio_id=portfolio_id,
    )

    mode = "rule-based"
    answer: str | None = None

    if ctx.llm_available:
        try:
            from src.llm.client import call_llm_text
            system = build_chat_system_prompt(ctx)
            llm_answer = await call_llm_text(query, system=system)
            if llm_answer and not llm_answer.startswith("[Axion]"):
                answer = llm_answer
                mode = "ai-enhanced"
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Telegram grounded chat LLM call failed: %s", exc)

    if answer is None:
        answer = render_deterministic_chat_answer(ctx, query)

    return answer, mode, portfolio_id


# ---------------------------------------------------------------------------
# Grounded digest rendering (Phase 9E shape)
# ---------------------------------------------------------------------------


def format_grounded_digest_message(
    digest: dict | str,
    *,
    portfolio_id: str | None = None,
) -> str:
    """Render a grounded digest as a Telegram Markdown message.

    Accepts either a dict (the direct return value from the
    ``AnalysisAgent.generate_digest`` grounded path) or a JSON string
    (the shape used by ``Digest.content``).  Reads the Phase 9E fields
    directly so there's no drift between dashboard, API, and Telegram
    digest surfaces.

    Never raises; if the input is malformed we render a short
    "digest unavailable" stub with the portfolio id so the chat gets
    a non-empty reply.
    """
    body: dict = {}
    if isinstance(digest, dict):
        body = digest
    elif isinstance(digest, str):
        try:
            parsed = json.loads(digest)
            if isinstance(parsed, dict):
                body = parsed
        except (json.JSONDecodeError, TypeError):
            body = {}

    pid = (
        portfolio_id
        or body.get("portfolio_id")
        or DEFAULT_PORTFOLIO_ID
    )

    headline = body.get("headline") or "Intelligence digest"
    assessment = body.get("portfolio_assessment") or ""
    risk_flags = body.get("risk_flags") or []
    attention = body.get("holdings_requiring_attention") or []
    key_devs = body.get("key_developments") or []
    market_ctx = body.get("market_context") or ""

    lines = [
        "\U0001F4CA **INTELLIGENCE DIGEST**",
        f"Portfolio: `{pid}`",
        "",
        f"*{headline}*",
    ]
    if assessment:
        lines.append("")
        lines.append(assessment[:600])
    if risk_flags:
        lines.append("")
        lines.append("**Risk flags:**")
        for flag in risk_flags[:5]:
            lines.append(f"  - {flag}")
    if attention:
        lines.append("")
        lines.append(
            "**Holdings needing attention:** "
            + ", ".join(f"`{t}`" for t in attention[:6])
        )
    if key_devs:
        lines.append("")
        lines.append("**Key developments:**")
        for dev in key_devs[:5]:
            lines.append(f"  - {str(dev)[:160]}")
    if market_ctx and "Deterministic fallback" not in market_ctx:
        lines.append("")
        lines.append(market_ctx[:400])

    return "\n".join(lines)
