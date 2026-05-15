"""
Axion Notification Dispatcher — Portfolio-scoped grounded delivery.

Phase 9F rewrite:

* Alerts are rendered through
  :func:`src.integrations.telegram.grounded.build_grounded_alert_message`,
  so every push carries severity, title, body, portfolio identity,
  affected holdings, factor/relationship channel, and a grounded
  "why it matters" line sourced from deterministic chains.

* Delivery is routed PER CHAT: each authorized chat has its own
  active portfolio (pinned via ``/portfolio_select``), and an alert
  is only pushed to chats whose active portfolio matches the alert's
  ``portfolio_id``.

* Dedupe + cooldown are enforced via
  :class:`src.database.models.TelegramDelivery` rows, so failed
  sends are never marked delivered, repeated (event, holding, channel)
  tuples are collapsed within a cooldown window, and the same alert
  is never re-sent to the same chat twice.

* Digest delivery uses
  :func:`src.integrations.telegram.grounded.format_grounded_digest_message`
  so the Telegram digest surface matches the Phase 9E grounded shape
  instead of the legacy free-form sections.

The background polling loop still exists (unchanged interface), but
it now drives a single ``deliver_alert`` entry point that honours the
grounded delivery gate on every call.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.database.connection import get_db
from src.database.models import Alert
from src.integrations.telegram.grounded import (
    DEFAULT_PORTFOLIO_ID,
    build_grounded_alert_message,
    format_grounded_digest_message,
    get_active_portfolio_id,
    record_delivery,
    should_deliver,
)

logger = logging.getLogger("axion.notifications")

# Minimum severity to push — "critical" and "high" always push.
# "warning" is skipped by default so Telegram stays premium; users
# can still see warnings in the dashboard /alerts view.
PUSH_SEVERITY = {"critical", "high"}

# Seconds between polls for new alerts.
POLL_INTERVAL = 30


# ---------------------------------------------------------------------------
# Alert fetching
# ---------------------------------------------------------------------------


async def _fetch_candidate_alerts() -> list[Alert]:
    """Pull alerts that might need pushing.

    Phase 9F: we no longer rely solely on ``Alert.delivered == 0``
    because that column is a coarse single-flag bit.  The true
    delivery bookkeeping lives in ``telegram_deliveries`` (per-chat
    per-alert).  The ``Alert.delivered`` column is still set on first
    successful push so the dashboard can still render "delivered"
    badges, but it is NOT the primary source of truth.
    """
    try:
        async with get_db() as session:
            stmt = (
                select(Alert)
                .where(
                    Alert.acknowledged == 0,
                    Alert.delivered == 0,
                )
                .order_by(Alert.created_at.desc())
                .limit(50)
            )
            return list((await session.execute(stmt)).scalars().all())
    except Exception as exc:
        logger.error("Failed to fetch candidate alerts: %s", exc)
        return []


async def _mark_alert_delivered(alert_id: str) -> None:
    """Set ``Alert.delivered=1`` so dashboards reflect first successful push.

    Phase 9F: this is now a best-effort cosmetic write.  The real
    delivery truth is ``telegram_deliveries`` rows keyed by
    (chat_id, alert_id).  We only call this helper after at least
    ONE chat has successfully received the alert.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            await session.execute(
                update(Alert)
                .where(Alert.id == alert_id)
                .values(delivered=1, delivered_at=now)
            )
            await session.commit()
    except Exception as exc:
        logger.error(
            "Failed to stamp alert %s delivered bit: %s",
            alert_id[:8] if alert_id else "?", exc,
        )


# ---------------------------------------------------------------------------
# Per-chat delivery (the single honest gate)
# ---------------------------------------------------------------------------


async def _chat_ids() -> list[int]:
    """Return the list of authorized Telegram chat ids from the bot module."""
    try:
        from src.integrations.telegram.bot import _authorized_chats
    except Exception:
        return []
    return sorted(_authorized_chats)


async def _push_message_to_chat(
    chat_id: int, message: str, parse_mode: str = "Markdown",
) -> tuple[bool, str | None]:
    """Send a raw Markdown message to a single chat.

    Returns ``(ok, error)``.  Never raises.  This is the only call
    site for the low-level ``bot.send_message`` in Phase 9F so every
    delivery path funnels through one exception handler.
    """
    try:
        from src.integrations.telegram.bot import _bot_app
    except Exception as exc:
        return False, f"bot module unavailable: {exc}"

    if _bot_app is None:
        return False, "bot not started"

    try:
        await _bot_app.bot.send_message(
            chat_id=chat_id, text=message, parse_mode=parse_mode,
        )
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


async def deliver_alert(alert: Alert | dict) -> dict:
    """Deliver a single alert to every eligible Telegram chat.

    Returns a summary dict::

        {
            "alert_id": str,
            "sent": int,          # chats that got the message
            "skipped": int,       # chats that passed the gate but were skipped
            "failed": int,        # chats that got an error from Telegram
            "reasons": {chat_id: reason},
        }

    Phase 9F rules (enforced in order):

    1. Severity must be in :data:`PUSH_SEVERITY`.
    2. Each candidate chat must be pinned to the alert's portfolio
       (alerts with NULL ``portfolio_id`` are treated as ``'default'``).
    3. :func:`should_deliver` blocks dedupe and cooldown collisions.
    4. A failed Telegram send is recorded as status='failed' — the
       ``Alert.delivered`` bit is NOT set.  Next poll tick retries.

    On complete success for at least one chat, ``Alert.delivered`` is
    set to 1 so the dashboard reflects that the alert reached a user.
    """
    # Normalise input to (id, severity, portfolio_id)
    if isinstance(alert, Alert):
        alert_id = alert.id
        severity = (alert.severity or "info").lower()
    else:
        alert_id = alert.get("id", "")
        severity = (alert.get("severity") or "info").lower()

    summary: dict = {
        "alert_id": alert_id,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "reasons": {},
    }

    # Rule 1 — severity gate
    if severity not in PUSH_SEVERITY:
        summary["reasons"]["_severity"] = f"skipped (severity={severity})"
        summary["skipped"] += 1
        # Stamp Alert.delivered so we don't re-poll forever.  Writing
        # delivered=1 here is cosmetic; there's no telegram_deliveries
        # row because nothing was actually sent.
        await _mark_alert_delivered(alert_id)
        return summary

    chat_ids = await _chat_ids()
    if not chat_ids:
        # Nothing we can do — mark delivered so the poller doesn't
        # keep re-processing a row we can never push.  Without this
        # an unconfigured install would accumulate infinite retries.
        summary["reasons"]["_no_chats"] = "no authorized chats configured"
        summary["skipped"] += 1
        await _mark_alert_delivered(alert_id)
        return summary

    # Build the grounded message ONCE per alert (portfolio-aware, but
    # not chat-aware).  The same Markdown body is then pushed to every
    # eligible chat; the per-chat gate below decides WHICH chats.
    async with get_db() as session:
        message, meta = await build_grounded_alert_message(session, alert)

    alert_portfolio = meta["portfolio_id"]
    event_id = meta["event_id"]
    holding_id = meta["holding_id"]
    channel = meta["channel"]

    any_sent = False

    for chat_id in chat_ids:
        async with get_db() as session:
            chat_portfolio = await get_active_portfolio_id(session, chat_id)
            decision = await should_deliver(
                session,
                chat_id=chat_id,
                alert_id=alert_id,
                alert_portfolio_id=alert_portfolio,
                chat_portfolio_id=chat_portfolio,
                event_id=event_id,
                holding_id=holding_id,
                channel=channel,
            )

            if not decision.should_send:
                summary["reasons"][chat_id] = decision.reason
                if decision.reason in ("already_delivered", "cooldown"):
                    summary["skipped"] += 1
                else:
                    summary["skipped"] += 1
                # We intentionally do NOT record a delivery row for
                # wrong_portfolio / cooldown / already_delivered —
                # that would pollute the audit trail.
                continue

        # Send OUTSIDE the transaction so the DB isn't held during
        # the network call.  Record the result in a fresh session.
        ok, error = await _push_message_to_chat(chat_id, message)

        async with get_db() as session:
            await record_delivery(
                session,
                chat_id=chat_id,
                alert_id=alert_id,
                portfolio_id=alert_portfolio,
                dedup_key=decision.dedup_key,
                status="sent" if ok else "failed",
                error=error,
            )

        if ok:
            summary["sent"] += 1
            summary["reasons"][chat_id] = "sent"
            any_sent = True
        else:
            summary["failed"] += 1
            summary["reasons"][chat_id] = f"failed: {error}"

    if any_sent:
        await _mark_alert_delivered(alert_id)
        logger.info(
            "Alert delivered — id=%s severity=%s sent=%d failed=%d skipped=%d",
            (alert_id or "?")[:8], severity,
            summary["sent"], summary["failed"], summary["skipped"],
        )
    else:
        logger.warning(
            "Alert not delivered to any chat — id=%s severity=%s failed=%d skipped=%d",
            (alert_id or "?")[:8], severity,
            summary["failed"], summary["skipped"],
        )

    return summary


# ---------------------------------------------------------------------------
# Digest delivery (Phase 9E grounded shape)
# ---------------------------------------------------------------------------


async def deliver_digest(digest: dict) -> bool:
    """Deliver a grounded digest to every authorized Telegram chat
    whose active portfolio matches the digest's portfolio.

    The digest is rendered through
    :func:`src.integrations.telegram.grounded.format_grounded_digest_message`
    so the Telegram surface is aligned with the Phase 9E grounded
    digest JSON (headline, portfolio_assessment, risk_flags,
    holdings_requiring_attention, key_developments).

    Returns ``True`` if at least one chat received the digest.
    """
    pid = (digest or {}).get("portfolio_id") or DEFAULT_PORTFOLIO_ID
    message = format_grounded_digest_message(digest, portfolio_id=pid)

    chat_ids = await _chat_ids()
    if not chat_ids:
        return False

    any_sent = False
    for chat_id in chat_ids:
        async with get_db() as session:
            chat_portfolio = await get_active_portfolio_id(session, chat_id)
        if chat_portfolio != pid:
            continue
        ok, _err = await _push_message_to_chat(chat_id, message)
        any_sent = any_sent or ok

    if any_sent:
        logger.info("Digest delivered (portfolio=%s)", pid)
    return any_sent


# ---------------------------------------------------------------------------
# Phase 13 — Insight delivery
# ---------------------------------------------------------------------------


async def deliver_insight(insight: dict) -> dict:
    """Deliver one Phase 13 insight notification to authorised chats.

    Expected ``insight`` keys (all strings): ``card_key``,
    ``portfolio_id``, ``severity``, ``category``, ``title``,
    ``summary``, ``state`` (``new`` | ``escalated``).  No prompt
    bodies, no uploaded document content — the dispatcher only
    surfaces the deterministic card body Phase 12 already renders.

    Returns ``{"delivered": bool, "status": "delivered"|"skipped"|"failed",
    "sent_to": [chat_id, ...]}``.  Never raises.

    Telegram filtering:

    * The bot must be running and have ≥1 authorised chat.
    * Each candidate chat's active portfolio must match
      ``insight['portfolio_id']`` (operator pin from Phase 9F).
    * On send failure, the row stays unmarked so the next pass can
      retry.
    """
    result: dict = {"delivered": False, "status": "skipped", "sent_to": []}
    portfolio_id = insight.get("portfolio_id") or DEFAULT_PORTFOLIO_ID

    chat_ids = await _chat_ids()
    if not chat_ids:
        result["status"] = "skipped"
        return result

    state = (insight.get("state") or "new").lower()
    state_pill = "🆕 New" if state == "new" else "⬆️ Escalated"
    severity = (insight.get("severity") or "info").lower()
    sev_pill = {
        "critical": "🚨 Critical",
        "high":     "🔴 High",
        "medium":   "🟠 Medium",
        "low":      "🟡 Low",
        "info":     "ℹ️ Info",
    }.get(severity, severity)
    title = str(insight.get("title") or "Insight")
    summary = str(insight.get("summary") or "")
    if len(summary) > 320:
        summary = summary[:317] + "…"
    message = (
        f"*{state_pill}* — {sev_pill}\n"
        f"*{_escape_md(title)}*\n"
        f"{_escape_md(summary)}\n"
        f"\n_Portfolio: {_escape_md(portfolio_id)}_"
    )

    any_sent = False
    any_failed = False
    sent_to: list[int] = []
    for chat_id in chat_ids:
        async with get_db() as session:
            chat_portfolio = await get_active_portfolio_id(session, chat_id)
        if chat_portfolio != portfolio_id:
            continue
        ok, _err = await _push_message_to_chat(chat_id, message)
        if ok:
            any_sent = True
            sent_to.append(chat_id)
        else:
            any_failed = True

    if any_sent:
        result["delivered"] = True
        result["status"] = "delivered"
        result["sent_to"] = sent_to
        logger.info(
            "Insight delivered to %d chat(s) (portfolio=%s, card_key=%s)",
            len(sent_to), portfolio_id, insight.get("card_key"),
        )
    elif any_failed:
        result["status"] = "failed"
    return result


def _escape_md(text: str) -> str:
    """Minimal Markdown V1 escape: backticks + asterisks + underscores."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )


# ---------------------------------------------------------------------------
# Background polling loop
# ---------------------------------------------------------------------------
_running = False
_task: asyncio.Task | None = None


async def _poll_loop():
    """Background loop that polls for candidate alerts and pushes them."""
    global _running
    logger.info(
        "Notification dispatcher started (poll=%ds, push severities=%s)",
        POLL_INTERVAL, sorted(PUSH_SEVERITY),
    )

    while _running:
        try:
            alerts = await _fetch_candidate_alerts()
            for alert in alerts:
                await deliver_alert(alert)
        except Exception as exc:
            logger.error("Notification poll error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)

    logger.info("Notification dispatcher stopped")


def start_dispatcher():
    """Start the background notification dispatcher."""
    global _running, _task
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_poll_loop())
    logger.info("Notification dispatcher task created")


def stop_dispatcher():
    """Stop the background notification dispatcher."""
    global _running, _task
    _running = False
    if _task and not _task.done():
        _task.cancel()
    _task = None
