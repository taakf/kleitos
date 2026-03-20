"""
Axion Notification Dispatcher — Pushes alerts and digests to Telegram.

Runs as a background task that:
  1. Polls for undelivered alerts and pushes to Telegram
  2. Delivers daily digests when they're generated
  3. Marks alerts as delivered after successful push

Hooks into the scheduler so notifications go out automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update, text

from src.database.connection import get_db
from src.database.models import Alert

logger = logging.getLogger("axion.notifications")

# Minimum severity to push — "critical" and "high" always push
PUSH_SEVERITY = {"critical", "high"}
# Seconds between polls for new alerts
POLL_INTERVAL = 30


async def _get_undelivered_alerts() -> list[dict]:
    """Fetch alerts that haven't been delivered to Telegram yet."""
    try:
        async with get_db() as session:
            stmt = (
                select(Alert)
                .where(
                    Alert.acknowledged == 0,
                    Alert.delivered == 0,
                )
                .order_by(Alert.created_at.desc())
                .limit(20)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "id": a.id,
                    "alert_type": a.alert_type,
                    "severity": a.severity,
                    "title": a.title,
                    "body": a.body,
                    "related_holdings": a.related_holdings,
                    "agent_id": a.agent_id,
                    "created_at": a.created_at,
                }
                for a in rows
            ]
    except Exception as e:
        logger.error("Failed to fetch undelivered alerts: %s", e)
        return []


async def _mark_delivered(alert_id: str) -> None:
    """Mark an alert as delivered."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as session:
            stmt = (
                update(Alert)
                .where(Alert.id == alert_id)
                .values(delivered=1, delivered_at=now)
            )
            await session.execute(stmt)
            await session.commit()
    except Exception as e:
        logger.error("Failed to mark alert %s as delivered: %s", alert_id[:8], e)


def _format_alert_message(alert: dict) -> str:
    """Format an alert for Telegram delivery."""
    severity = alert.get("severity", "info").upper()
    severity_icons = {
        "CRITICAL": "\U0001F6A8",  # rotating light
        "HIGH": "\U0001F534",       # red circle
        "WARNING": "\U0001F7E1",    # yellow circle
        "INFO": "\U0001F535",       # blue circle
    }
    icon = severity_icons.get(severity, "\u2022")

    title = alert.get("title", "Untitled Alert")
    body = alert.get("body") or ""
    alert_type = alert.get("alert_type", "")
    agent = alert.get("agent_id", "system")

    # Parse related holdings
    holdings_raw = alert.get("related_holdings", "[]")
    try:
        holdings = json.loads(holdings_raw) if isinstance(holdings_raw, str) else (holdings_raw or [])
    except (json.JSONDecodeError, TypeError):
        holdings = []
    holdings_str = ", ".join(f"`{h}`" for h in holdings) if holdings else ""

    lines = [
        f"{icon} *\\[{severity}\\] {title}*",
    ]
    if body:
        lines.append(f"\n{body[:500]}")
    if holdings_str:
        lines.append(f"\nHoldings: {holdings_str}")
    if alert_type:
        lines.append(f"\nType: {alert_type} | Agent: {agent}")

    return "\n".join(lines)


def _format_digest_message(digest: dict) -> str:
    """Format a digest for Telegram delivery."""
    dtype = digest.get("digest_type", "daily")
    start = digest.get("period_start", "?")
    end = digest.get("period_end", "?")

    lines = [
        f"\U0001F4CA *INTELLIGENCE DIGEST* ({dtype})",
        f"Period: {start} to {end}",
        "",
    ]

    sections = digest.get("sections", [])
    for s in sections[:6]:
        lines.append(f"*{s.get('title', 'Section')}*")
        content = s.get("content", "")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    for item in parsed[:3]:
                        if isinstance(item, dict):
                            lines.append(f"  \u2022 {item.get('title', item.get('name', str(item)[:80]))}")
                        else:
                            lines.append(f"  \u2022 {str(item)[:80]}")
                else:
                    lines.append(str(parsed)[:200])
            except (json.JSONDecodeError, TypeError):
                lines.append(content[:200])
        lines.append("")

    if not sections:
        summary = digest.get("summary") or digest.get("content") or "No content available."
        lines.append(str(summary)[:500])

    return "\n".join(lines)


async def deliver_alert(alert: dict) -> bool:
    """Deliver a single alert to Telegram."""
    from src.integrations.telegram.bot import push_to_all

    severity = (alert.get("severity") or "info").lower()

    # Only push critical and high by default
    if severity not in PUSH_SEVERITY:
        # Still mark as delivered so we don't re-process
        await _mark_delivered(alert["id"])
        return True

    message = _format_alert_message(alert)
    try:
        sent = await push_to_all(message, parse_mode="Markdown")
        if sent > 0:
            await _mark_delivered(alert["id"])
            logger.info("Alert delivered to %d chats: [%s] %s", sent, severity, alert.get("title", "?")[:50])
            return True
        else:
            logger.warning("Alert not delivered (no chats): %s", alert.get("title", "?")[:50])
            # Mark delivered anyway to prevent infinite retry
            await _mark_delivered(alert["id"])
            return False
    except Exception as e:
        logger.error("Failed to deliver alert: %s", e)
        return False


async def deliver_digest(digest: dict) -> bool:
    """Deliver a digest to Telegram."""
    from src.integrations.telegram.bot import push_to_all

    message = _format_digest_message(digest)
    try:
        sent = await push_to_all(message, parse_mode="Markdown")
        logger.info("Digest delivered to %d chats", sent)
        return sent > 0
    except Exception as e:
        logger.error("Failed to deliver digest: %s", e)
        return False


# ---------------------------------------------------------------------------
# Background polling loop
# ---------------------------------------------------------------------------
_running = False
_task: asyncio.Task | None = None


async def _poll_loop():
    """Background loop that polls for undelivered alerts and pushes them."""
    global _running
    logger.info("Notification dispatcher started (poll interval: %ds)", POLL_INTERVAL)

    while _running:
        try:
            alerts = await _get_undelivered_alerts()
            for alert in alerts:
                await deliver_alert(alert)
        except Exception as e:
            logger.error("Notification poll error: %s", e)

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
