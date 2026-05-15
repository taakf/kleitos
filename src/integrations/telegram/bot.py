"""
Axion Telegram Bot — Complete client interface.

The client can do EVERYTHING through this Telegram chat:
  - View portfolio, holdings, exposures, trades
  - Check alerts, risk, events, analysis
  - Get digests and reports
  - Upload CSV files (just send the file in chat)
  - Download reports as files (CSV exports, digest docs)
  - Talk naturally — Claude-powered conversational AI
  - Receive push notifications for critical alerts and digests

Requires:
  KLEITOS_TELEGRAM_TOKEN  — BotFather token (env var kept for backward compat)
  KLEITOS_TELEGRAM_CHAT_ID — Authorized chat ID(s), comma-separated
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from telegram import Update, BotCommand, InputFile
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from src.database.connection import get_db

logger = logging.getLogger("axion.telegram")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_BASE_URL = "http://localhost:7777/api/v1"


def _api(path: str) -> str:
    return f"{_BASE_URL}{path}"


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------
_authorized_chats: set[int] = set()


def is_authorized(update: Update) -> bool:
    if not _authorized_chats:
        return True
    chat_id = update.effective_chat.id if update.effective_chat else 0
    return chat_id in _authorized_chats


def auth_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update):
            await update.message.reply_text("Unauthorized. Contact your administrator.")
            return
        return await func(update, context)
    return wrapper


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def _active_portfolio_id(chat_id: int) -> str:
    """Resolve the active portfolio id for a Telegram chat.

    Phase 9F: per-chat state lives in the ``telegram_sessions`` table.
    Fallback is ``'default'`` so pre-9F users keep working with zero
    configuration.  Never raises.
    """
    try:
        from src.integrations.telegram.grounded import get_active_portfolio_id

        async with get_db() as session:
            return await get_active_portfolio_id(session, chat_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Could not resolve active portfolio: %s", exc)
        return "default"


async def _api_get(path: str, params: dict | None = None) -> Any:
    try:
        r = await _get_client().get(_api(path), params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("API GET %s failed: %s", path, e)
        return None


async def _api_post(path: str, data: dict | None = None) -> Any:
    try:
        r = await _get_client().post(_api(path), json=data or {})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("API POST %s failed: %s", path, e)
        return None


async def _api_get_raw(path: str) -> bytes | None:
    """GET raw bytes (for file downloads)."""
    try:
        r = await _get_client().get(_api(path))
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.error("API GET raw %s failed: %s", path, e)
        return None


async def _api_upload(path: str, filename: str, content: bytes) -> Any:
    """Upload a file to the API."""
    try:
        files = {"file": (filename, content, "text/csv")}
        r = await _get_client().post(_api(path), files=files)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("API upload %s failed: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_currency(val: float | None, ccy: str = "USD") -> str:
    if val is None:
        return "N/A"
    symbols = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5", "CHF": "CHF "}
    prefix = symbols.get(ccy, f"{ccy} ")
    return f"{prefix}{val:,.0f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _safe_text(text: str, limit: int = 4000) -> str:
    """Truncate text to Telegram's message limit."""
    if len(text) > limit:
        return text[: limit - 10] + "\n..."
    return text


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------
@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Welcome to *Axion* \u2014 Portfolio Intelligence by 4Labs\n\n"
        "I monitor your portfolio 24/7\\. You can do everything from this chat\\.\n\n"
        "*Portfolio*\n"
        "/portfolio \u2014 Summary & top holdings\n"
        "/portfolio\\_list \u2014 List portfolios & show active pin\n"
        "/portfolio\\_select \u2014 Switch this chat's active portfolio\n"
        "/holdings \u2014 Full holdings list\n"
        "/exposure \u2014 Sector/geo/currency breakdown\n"
        "/trades \u2014 Recent trade history\n\n"
        "*Intelligence*\n"
        "/events \u2014 Recent events\n"
        "/alerts \u2014 Active alerts\n"
        "/digest \u2014 Latest intelligence digest\n\n"
        "*Actions*\n"
        "/collect \u2014 Trigger news collection\n"
        "/analyze \u2014 Run event analysis\n"
        "/risk \u2014 Run risk assessment\n"
        "/classify \u2014 Classify holdings\n\n"
        "*Reports & Files*\n"
        "/report \u2014 Get portfolio CSV export\n"
        "/report alerts \u2014 Get alerts report\n"
        "/report events \u2014 Get events report\n"
        "Send a CSV file to upload portfolio data\n\n"
        "*System*\n"
        "/status \u2014 Health & agent status\n"
        "/audit \u2014 Recent audit trail\n"
        "/reset CONFIRM \u2014 Clear all data\n"
        "/help \u2014 This menu\n\n"
        "Or just type naturally \u2014 I understand plain English\\.\n\n"
        f"Your chat ID: `{chat_id}`",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@auth_required
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ---- Portfolio context (Phase 9F) ----
@auth_required
async def cmd_portfolio_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List known portfolios and mark which one this chat is pinned to."""
    await update.message.chat.send_action(ChatAction.TYPING)
    chat_id = update.effective_chat.id if update.effective_chat else 0

    active = await _active_portfolio_id(chat_id)
    portfolios: list[dict[str, Any]] = []
    try:
        from sqlalchemy import select
        from src.database.models import Portfolio

        async with get_db() as session:
            rows = (await session.execute(
                select(Portfolio).order_by(Portfolio.is_default.desc(), Portfolio.id)
            )).scalars().all()
            for p in rows:
                portfolios.append({
                    "id": p.id, "name": p.name or p.id,
                    "is_default": bool(p.is_default),
                })
    except Exception as exc:
        logger.warning("Portfolio list fetch failed: %s", exc)

    if not portfolios:
        await update.message.reply_text(
            "No portfolios found.  Active: `default`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["**Portfolios**\n"]
    for p in portfolios:
        marker = " \u2b50" if p["id"] == active else ""
        default_tag = " (default)" if p["is_default"] else ""
        lines.append(f"  - `{p['id']}` \u2014 {p['name']}{default_tag}{marker}")
    lines.append(f"\nActive: `{active}`")
    lines.append("Switch with: `/portfolio_select <id>`")

    await update.message.reply_text(
        _safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN,
    )


@auth_required
async def cmd_portfolio_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pin this Telegram chat to a specific portfolio.

    Usage: ``/portfolio_select <id>``.  All subsequent commands
    (``/portfolio``, ``/holdings``, ``/alerts``, ``/digest``,
    ``/events``) and free-text chat are scoped to the selected
    portfolio.  If the id doesn't exist the command is a no-op
    and we report the error — we never silently redirect.
    """
    chat_id = update.effective_chat.id if update.effective_chat else 0
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/portfolio_select <id>` \u2014 run `/portfolio_list` "
            "to see available portfolios.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target = args[0].strip()
    try:
        from sqlalchemy import select

        from src.database.models import Portfolio
        from src.integrations.telegram.grounded import set_active_portfolio_id

        async with get_db() as session:
            row = (await session.execute(
                select(Portfolio).where(Portfolio.id == target)
            )).scalars().first()
            if row is None:
                await update.message.reply_text(
                    f"Portfolio `{target}` not found. Use /portfolio_list.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            await set_active_portfolio_id(session, chat_id, target)
    except Exception as exc:
        await update.message.reply_text(f"Portfolio switch failed: {exc}")
        return

    await update.message.reply_text(
        f"\u2705 Active portfolio for this chat: `{target}`",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---- Portfolio ----
@auth_required
async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    chat_id = update.effective_chat.id if update.effective_chat else 0
    pid = await _active_portfolio_id(chat_id)

    summary = await _api_get("/portfolio/summary", {"portfolio_id": pid})
    holdings = await _api_get(
        "/portfolio/holdings", {"limit": 10, "portfolio_id": pid},
    )

    if not summary:
        await update.message.reply_text("Could not fetch portfolio data. Is Axion running?")
        return

    lines = [
        f"**PORTFOLIO SUMMARY** (`{pid}`)\n",
        f"Total Value: {_fmt_currency(summary.get('total_market_value'))}",
        f"Total P&L: {_fmt_currency(summary.get('total_pnl'))} ({_fmt_pct(summary.get('total_pnl_pct'))})",
        f"Holdings: {summary.get('holding_count', 0)}",
        f"Sectors: {summary.get('sector_count', 0)}",
        f"Currencies: {summary.get('currency_count', 0)}",
        "",
    ]

    if holdings:
        h_list = holdings if isinstance(holdings, list) else []
        if h_list:
            lines.append("**TOP HOLDINGS**")
            lines.append("```")
            lines.append(f"{'Ticker':<8} {'Weight':>7} {'MktVal':>12} {'P&L%':>8}")
            lines.append("-" * 38)
            for h in h_list[:10]:
                ticker = h.get("ticker", "?")
                wt = f"{h.get('weight_pct', 0):.1f}%" if h.get("weight_pct") else "N/A"
                mv = _fmt_currency(h.get("market_value"))
                pnl = _fmt_pct(h.get("pnl_pct"))
                lines.append(f"{ticker:<8} {wt:>7} {mv:>12} {pnl:>8}")
            lines.append("```")

    lines.append("\nUse /holdings for full list, /exposure for breakdown, /report for CSV export.")
    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


@auth_required
async def cmd_holdings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    chat_id = update.effective_chat.id if update.effective_chat else 0
    pid = await _active_portfolio_id(chat_id)

    holdings = await _api_get(
        "/portfolio/holdings", {"limit": 50, "portfolio_id": pid},
    )
    if not holdings:
        await update.message.reply_text("No holdings found. Upload a CSV or use /help to add holdings.")
        return

    h_list = holdings if isinstance(holdings, list) else []
    if not h_list:
        await update.message.reply_text("No holdings found.")
        return

    lines = ["**ALL HOLDINGS**\n```"]
    lines.append(f"{'Ticker':<8} {'Shares':>8} {'Price':>10} {'MktVal':>12} {'Wt%':>6}")
    lines.append("-" * 48)
    for h in h_list:
        ticker = h.get("ticker", "?")
        qty = f"{h.get('quantity', 0):,.0f}"
        price = f"{h.get('current_price', 0):,.2f}" if h.get("current_price") else "N/A"
        mv = _fmt_currency(h.get("market_value"))
        wt = f"{h.get('weight_pct', 0):.1f}" if h.get("weight_pct") else "N/A"
        lines.append(f"{ticker:<8} {qty:>8} {price:>10} {mv:>12} {wt:>6}")
    lines.append("```")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Exposure ----
@auth_required
async def cmd_exposure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    args = context.args
    dim = args[0] if args and args[0] in ("sector", "geography", "currency", "theme") else None

    dims = [dim] if dim else ["sector", "geography", "currency", "theme"]
    labels = {"sector": "SECTOR", "geography": "GEOGRAPHY", "currency": "CURRENCY", "theme": "THEME"}

    lines = ["**PORTFOLIO EXPOSURE**\n"]
    for d in dims:
        data = await _api_get("/portfolio/exposure", {"dimension": d})
        buckets = data.get("buckets", []) if data else []
        lines.append(f"**{labels.get(d, d.upper())}**")
        if buckets:
            for b in buckets[:10]:
                label = b.get("label", "?")
                pct = b.get("weight_pct", 0)
                bar_len = int(min(pct, 100) / 5)
                bar = "\u2588" * bar_len
                lines.append(f"  {label:<20} {pct:5.1f}% {bar}")
        else:
            lines.append("  No data")
        lines.append("")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Trades ----
@auth_required
async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    args = context.args
    params = {"limit": 20}
    if args:
        params["ticker"] = args[0].upper()

    trades = await _api_get("/portfolio/trades", params)
    t_list = trades if isinstance(trades, list) else (trades or {}).get("items", [])

    if not t_list:
        await update.message.reply_text("No trades recorded yet.")
        return

    lines = ["**TRADE HISTORY**\n```"]
    lines.append(f"{'Date':<12} {'Type':<6} {'Ticker':<8} {'Qty':>8} {'Price':>10}")
    lines.append("-" * 48)
    for t in t_list:
        date = (t.get("trade_date") or "")[:10]
        ttype = t.get("trade_type", "?")
        ticker = t.get("ticker", "?")
        qty = f"{t.get('quantity', 0):,.0f}"
        price = f"{t.get('price', 0):,.2f}"
        lines.append(f"{date:<12} {ttype:<6} {ticker:<8} {qty:>8} {price:>10}")
    lines.append("```")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Alerts ----
@auth_required
async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    chat_id = update.effective_chat.id if update.effective_chat else 0
    pid = await _active_portfolio_id(chat_id)

    alerts = await _api_get("/alerts/active", {"portfolio_id": pid})
    alerts_list = alerts if isinstance(alerts, list) else (alerts or {}).get("items", (alerts or {}).get("alerts", []))

    if not alerts_list:
        await update.message.reply_text("\u2705 No active alerts. All clear.")
        return

    severity_icons = {"critical": "\U0001F6A8", "high": "\U0001F534", "warning": "\U0001F7E1", "info": "\U0001F535"}

    lines = [f"**ACTIVE ALERTS** ({len(alerts_list)})\n"]
    for a in alerts_list[:20]:
        sev = a.get("severity", "info")
        icon = severity_icons.get(sev, "\u2022")
        title = a.get("title", "Untitled")
        body = a.get("body") or a.get("message") or ""
        holdings = a.get("related_holdings", [])
        if isinstance(holdings, str):
            try:
                holdings = json.loads(holdings)
            except (json.JSONDecodeError, TypeError):
                holdings = []
        ticker_str = " ".join(f"`{t}`" for t in holdings) if holdings else ""

        lines.append(f"{icon} **[{sev.upper()}]** {title}")
        if body:
            lines.append(f"   {body[:200]}")
        if ticker_str:
            lines.append(f"   Holdings: {ticker_str}")
        lines.append("")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Risk ----
@auth_required
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\U0001F50D Running risk assessment...")
    await update.message.chat.send_action(ChatAction.TYPING)

    await _api_post("/agents/risk/run")

    # Always show current alerts after risk run
    alerts = await _api_get("/alerts/active")
    alerts_list = alerts if isinstance(alerts, list) else (alerts or {}).get("items", [])

    if not alerts_list:
        await update.message.reply_text("\u2705 Risk assessment complete. No alerts triggered.")
        return

    severity_icons = {"critical": "\U0001F6A8", "high": "\U0001F534", "warning": "\U0001F7E1", "info": "\U0001F535"}
    lines = [f"\u26A0\uFE0F Risk assessment complete. **{len(alerts_list)} active alerts:**\n"]
    for a in alerts_list[:10]:
        sev = a.get("severity", "info")
        icon = severity_icons.get(sev, "\u2022")
        lines.append(f"{icon} **[{sev.upper()}]** {a.get('title', '?')}")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Digest (Phase 9F: grounded shape) ----
@auth_required
async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the latest grounded digest for the chat's active portfolio.

    Phase 9F: the digest is rendered via
    :func:`src.integrations.telegram.grounded.format_grounded_digest_message`
    which reads the Phase 9E grounded digest JSON shape
    (``headline``, ``portfolio_assessment``, ``risk_flags``,
    ``holdings_requiring_attention``, ``key_developments``) directly
    from ``Digest.content``.  No legacy free-form sections path.
    """
    await update.message.chat.send_action(ChatAction.TYPING)

    chat_id = update.effective_chat.id if update.effective_chat else 0
    pid = await _active_portfolio_id(chat_id)

    args = context.args
    if args and args[0] == "new":
        await update.message.reply_text("Generating fresh digest...")
        await _api_post(
            "/digests/generate",
            {"digest_type": "ad-hoc", "scope": "portfolio", "portfolio_id": pid},
        )
        await asyncio.sleep(3)

    digest = await _api_get("/digests/latest", {"portfolio_id": pid})
    if not digest:
        await update.message.reply_text(
            f"No digest available for portfolio `{pid}`.\n\nUse `/digest new` to generate one.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # The API returns a wrapper row whose ``content`` field is the
    # Phase 9E grounded JSON.  The grounded formatter accepts either
    # the wrapper dict or the inner JSON string.
    from src.integrations.telegram.grounded import format_grounded_digest_message

    inner = digest.get("content")
    if isinstance(inner, str):
        body = inner
    elif isinstance(inner, dict):
        body = inner
    else:
        body = digest  # last-resort: render whatever we got

    message = format_grounded_digest_message(body, portfolio_id=pid)
    await update.message.reply_text(
        _safe_text(message), parse_mode=ParseMode.MARKDOWN,
    )


# ---- Events ----
@auth_required
async def cmd_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    args = context.args
    params = {"limit": 15}
    if args:
        params["ticker"] = args[0].upper()

    events = await _api_get("/events/recent", params)
    if not events:
        events = await _api_get("/events", params)

    event_list = events if isinstance(events, list) else (events or {}).get("items", [])

    if not event_list:
        await update.message.reply_text("No events collected yet. Use /collect to fetch news.")
        return

    mat_icons = {"critical": "\U0001F6A8", "high": "\U0001F534", "important": "\U0001F7E0", "watch": "\U0001F7E1"}

    lines = [f"**RECENT EVENTS** ({len(event_list)})\n"]
    for ev in event_list[:15]:
        mat = ev.get("materiality", "unscored")
        icon = mat_icons.get(mat, "\u2022")
        title = ev.get("title", "Untitled")[:80]
        etype = ev.get("event_type", "")
        source = ev.get("source_id", "")
        url = ev.get("url", "")
        lines.append(f"{icon} {title}")
        meta_parts = []
        if etype:
            meta_parts.append(etype)
        if source:
            meta_parts.append(source)
        if meta_parts:
            lines.append(f"   {' | '.join(meta_parts)}")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Collect ----
@auth_required
async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\U0001F4E1 Starting news collection...")
    await update.message.chat.send_action(ChatAction.TYPING)
    result = await _api_post("/agents/collection/run")
    status = (result or {}).get("status", (result or {}).get("run_id", "started"))
    await update.message.reply_text(f"Collection triggered: {status}\n\nUse /events to see results.")


# ---- Analyze ----
@auth_required
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\U0001F9E0 Running event analysis...")
    await update.message.chat.send_action(ChatAction.TYPING)
    result = await _api_post("/agents/analysis/run")
    status = (result or {}).get("status", "triggered")
    await update.message.reply_text(f"Analysis {status}.\n\nUse /digest to see the results.")


# ---- Classify ----
@auth_required
async def cmd_classify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("\U0001F3F7 Classifying holdings...")
    await update.message.chat.send_action(ChatAction.TYPING)
    result = await _api_post("/agents/classification/run")
    status = (result or {}).get("status", "triggered")
    await update.message.reply_text(f"Classification {status}.\n\nUse /exposure to see updated breakdowns.")


# ---- Audit ----
@auth_required
async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    data = await _api_get("/audit")
    entries = data if isinstance(data, list) else (data or {}).get("items", (data or {}).get("entries", []))

    if not entries:
        await update.message.reply_text("No audit entries found.")
        return

    lines = ["**RECENT AUDIT TRAIL**\n"]
    for e in entries[:15]:
        entity = e.get("entity_type", "?")
        action = e.get("action", "?")
        agent = e.get("agent_id", e.get("user_id", "system"))
        created = (e.get("created_at") or "")[:16]
        lines.append(f"\u2022 `{created}` **{action}** {entity} (by {agent})")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Status ----
@auth_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)

    health = await _api_get("/health")
    agents = await _api_get("/agents/status")

    if not health:
        await update.message.reply_text("\u274C Could not reach Axion API.")
        return

    status = health.get("status", "unknown")
    status_icon = "\u2705" if status in ("ok", "healthy") else "\u26A0\uFE0F"
    uptime = health.get("uptime_seconds")
    uptime_str = ""
    if uptime:
        h = int(uptime // 3600)
        m = int((uptime % 3600) // 60)
        uptime_str = f"{h}h {m}m" if h < 48 else f"{h // 24}d {h % 24}h"

    lines = [
        f"{status_icon} **SYSTEM STATUS**: {status.upper()}\n",
        f"Database: {health.get('database', 'unknown')}",
        f"Scheduler: {health.get('scheduler', 'unknown')}",
        f"Sources: {health.get('sources_active', '?')}/{health.get('sources_total', '?')} active",
        f"Version: {health.get('version', '?')}",
    ]
    if uptime_str:
        lines.append(f"Uptime: {uptime_str}")

    agent_list = agents if isinstance(agents, list) else (agents or {}).get("agents", (agents or {}).get("items", []))
    if agent_list:
        lines.append("\n**AGENTS**")
        for a in agent_list:
            name = a.get("name", a.get("agent_id", "?"))
            st = a.get("status", "idle")
            st_icon = "\U0001F7E2" if st in ("ok", "idle", "completed") else "\U0001F534"
            runs = a.get("run_count", 0)
            errors = a.get("error_count", 0)
            lines.append(f"  {st_icon} {name}: {runs} runs, {errors} errors")

    await update.message.reply_text(_safe_text("\n".join(lines)), parse_mode=ParseMode.MARKDOWN)


# ---- Reset ----
@auth_required
async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or args[0].upper() != "CONFIRM":
        await update.message.reply_text(
            "\u26A0\uFE0F This will permanently delete ALL portfolio data "
            "(holdings, trades, events, alerts, analysis).\n\n"
            "To confirm, type: `/reset CONFIRM`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    result = await _api_post("/portfolio/reset")
    if result:
        deleted = result.get("deleted", {})
        total = sum(deleted.values())
        await update.message.reply_text(
            f"\u2705 All data cleared ({total} records deleted).\n\n"
            "You can now:\n"
            "\u2022 Send a CSV file to upload your portfolio\n"
            "\u2022 Use the web dashboard to add holdings\n"
            "\u2022 Type 'add 100 AAPL at 180' to add holdings via chat"
        )
    else:
        await update.message.reply_text("Reset failed. Check API connectivity.")


# ---- Reports (file downloads) ----
@auth_required
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send a report as a file."""
    await update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    args = context.args
    report_type = args[0].lower() if args else "portfolio"

    if report_type in ("portfolio", "holdings"):
        # Build CSV from holdings data
        holdings = await _api_get("/portfolio/holdings", {"limit": 500})
        h_list = holdings if isinstance(holdings, list) else []

        if not h_list:
            await update.message.reply_text("No holdings to export.")
            return

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Ticker", "Name", "Sector", "Geography", "Currency", "Quantity",
                         "Avg Cost", "Current Price", "Market Value", "Weight %", "P&L", "P&L %"])
        for h in h_list:
            writer.writerow([
                h.get("ticker", ""),
                h.get("name", ""),
                h.get("sector", ""),
                h.get("geography", ""),
                h.get("currency", "USD"),
                h.get("quantity", 0),
                h.get("avg_cost_basis", ""),
                h.get("current_price", ""),
                h.get("market_value", ""),
                f"{h.get('weight_pct', 0):.2f}" if h.get("weight_pct") else "",
                h.get("pnl", ""),
                f"{h.get('pnl_pct', 0):.4f}" if h.get("pnl_pct") is not None else "",
            ])

        data = buf.getvalue().encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(data), filename=f"axion_portfolio_{ts}.csv"),
            caption=f"\U0001F4CA Portfolio export ({len(h_list)} holdings)",
        )

    elif report_type == "alerts":
        alerts = await _api_get("/alerts/active")
        a_list = alerts if isinstance(alerts, list) else (alerts or {}).get("items", [])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Severity", "Title", "Body", "Type", "Holdings", "Agent", "Created"])
        for a in a_list:
            holdings = a.get("related_holdings", [])
            if isinstance(holdings, str):
                try:
                    holdings = json.loads(holdings)
                except (json.JSONDecodeError, TypeError):
                    holdings = []
            writer.writerow([
                a.get("severity", ""),
                a.get("title", ""),
                a.get("body", a.get("message", "")),
                a.get("alert_type", ""),
                ", ".join(holdings) if holdings else "",
                a.get("agent_id", ""),
                a.get("created_at", ""),
            ])

        data = buf.getvalue().encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(data), filename=f"axion_alerts_{ts}.csv"),
            caption=f"\u26A0\uFE0F Alerts report ({len(a_list)} active alerts)",
        )

    elif report_type == "events":
        events = await _api_get("/events", {"limit": 100})
        e_list = events if isinstance(events, list) else (events or {}).get("items", [])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Title", "Type", "Materiality", "Source", "Published", "URL"])
        for e in e_list:
            writer.writerow([
                e.get("title", ""),
                e.get("event_type", ""),
                e.get("materiality", ""),
                e.get("source_id", ""),
                e.get("published_at", ""),
                e.get("url", ""),
            ])

        data = buf.getvalue().encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(data), filename=f"axion_events_{ts}.csv"),
            caption=f"\U0001F4F0 Events report ({len(e_list)} events)",
        )

    elif report_type in ("digest", "briefing"):
        digest = await _api_get("/digests/latest")
        if not digest:
            await update.message.reply_text("No digest available. Use `/digest new` to generate one.", parse_mode=ParseMode.MARKDOWN)
            return

        # Build a text report
        lines = [
            "AXION INTELLIGENCE DIGEST",
            f"Type: {digest.get('digest_type', digest.get('period', 'daily'))}",
            f"Period: {digest.get('period_start', digest.get('start_date', '?'))} to {digest.get('period_end', digest.get('end_date', '?'))}",
            f"Generated: {digest.get('generated_at', '?')}",
            "=" * 60,
            "",
        ]

        sections = digest.get("sections", [])
        content = digest.get("content", "")

        if sections:
            for s in sections:
                lines.append(s.get("title", "Section").upper())
                lines.append("-" * 40)
                c = s.get("content", "")
                if isinstance(c, str):
                    try:
                        parsed = json.loads(c)
                        lines.append(json.dumps(parsed, indent=2))
                    except (json.JSONDecodeError, TypeError):
                        lines.append(c)
                else:
                    lines.append(str(c))
                lines.append("")
        elif content:
            lines.append(content)

        data = "\n".join(lines).encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(data), filename=f"axion_digest_{ts}.txt"),
            caption="\U0001F4CA Intelligence Digest",
        )

    elif report_type == "full":
        # Full portfolio report: summary + holdings + exposure + alerts
        await update.message.reply_text("Generating full report...")

        summary = await _api_get("/portfolio/summary")
        holdings = await _api_get("/portfolio/holdings", {"limit": 500})
        alerts = await _api_get("/alerts/active")
        h_list = holdings if isinstance(holdings, list) else []
        a_list = alerts if isinstance(alerts, list) else (alerts or {}).get("items", [])

        lines = [
            "AXION PORTFOLIO INTELLIGENCE REPORT",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "=" * 60,
            "",
        ]

        if summary:
            lines.extend([
                "PORTFOLIO SUMMARY",
                "-" * 40,
                f"Total Market Value: {_fmt_currency(summary.get('total_market_value'))}",
                f"Total Cost Basis:   {_fmt_currency(summary.get('total_cost_basis'))}",
                f"Total P&L:          {_fmt_currency(summary.get('total_pnl'))} ({_fmt_pct(summary.get('total_pnl_pct'))})",
                f"Holdings:           {summary.get('holding_count', 0)}",
                f"Sectors:            {summary.get('sector_count', 0)}",
                f"Currencies:         {summary.get('currency_count', 0)}",
                "",
            ])

        if h_list:
            lines.extend(["HOLDINGS", "-" * 40])
            lines.append(f"{'Ticker':<8} {'Sector':<15} {'Qty':>10} {'Price':>10} {'MktVal':>12} {'Wt%':>6} {'P&L%':>8}")
            for h in h_list:
                lines.append(
                    f"{h.get('ticker', '?'):<8} "
                    f"{(h.get('sector') or '-'):<15} "
                    f"{h.get('quantity', 0):>10,.0f} "
                    f"{h.get('current_price', 0):>10,.2f} "
                    f"{_fmt_currency(h.get('market_value')):>12} "
                    f"{h.get('weight_pct', 0):>5.1f}% "
                    f"{_fmt_pct(h.get('pnl_pct')):>8}"
                )
            lines.append("")

        if a_list:
            lines.extend(["ACTIVE ALERTS", "-" * 40])
            for a in a_list:
                lines.append(f"[{a.get('severity', '?').upper()}] {a.get('title', '?')}")
                body = a.get("body") or a.get("message") or ""
                if body:
                    lines.append(f"  {body[:200]}")
            lines.append("")

        data = "\n".join(lines).encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(data), filename=f"axion_full_report_{ts}.txt"),
            caption=f"\U0001F4CB Full Portfolio Report ({len(h_list)} holdings, {len(a_list)} alerts)",
        )
    else:
        await update.message.reply_text(
            "Available reports:\n"
            "/report - Portfolio holdings CSV\n"
            "/report alerts - Active alerts CSV\n"
            "/report events - Events CSV\n"
            "/report digest - Latest digest as file\n"
            "/report full - Complete portfolio report"
        )


# ---------------------------------------------------------------------------
# File Upload Handler — client sends a CSV, we upload it to Axion
# ---------------------------------------------------------------------------
@auth_required
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded files — auto-detect CSV and process as portfolio upload."""
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "upload"
    is_csv = filename.lower().endswith(".csv") or doc.mime_type in ("text/csv", "application/csv")

    if not is_csv:
        await update.message.reply_text(
            f"Received: {filename}\n\n"
            "I can process CSV files for portfolio uploads.\n"
            "The CSV should have columns like: ticker, quantity, price, cost, currency"
        )
        return

    await update.message.reply_text(f"\U0001F4C4 Processing {filename}...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        # Download the file from Telegram
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        # Upload to Axion API
        result = await _api_upload("/portfolio/upload", filename, bytes(file_bytes))

        if result:
            imported = result.get("holdings_imported", result.get("imported_count", result.get("added", 0)))
            updated = result.get("holdings_updated", result.get("updated_count", result.get("updated", 0)))
            errors = result.get("errors", [])

            lines = ["\u2705 **Portfolio uploaded successfully!**\n"]
            if imported:
                lines.append(f"  \u2022 {imported} holdings imported")
            if updated:
                lines.append(f"  \u2022 {updated} holdings updated")
            if errors:
                lines.append(f"  \u2022 {len(errors)} errors:")
                for err in errors[:5]:
                    lines.append(f"    - {err}")
            lines.append("\nUse /portfolio to see your updated portfolio.")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "\u274C Upload failed. Make sure the CSV has the right format:\n"
                "ticker, quantity, price, cost, currency"
            )
    except Exception as e:
        logger.error("File upload handler error: %s", e, exc_info=True)
        await update.message.reply_text(f"\u274C Upload error: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# Natural Language Handler — Phase 9F grounded chat
# ---------------------------------------------------------------------------
@auth_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages by routing through the Phase 9E grounded
    chat layer — the same stack as the dashboard/API ``/chat`` endpoint.

    Phase 9F: the chat is PORTFOLIO-SCOPED via the
    ``telegram_sessions`` row for this chat.  Cross-portfolio leakage
    is structurally impossible because ``assemble_chat_context`` joins
    every downstream read through ``Holding.portfolio_id``.  The LLM
    system prompt is built by ``build_chat_system_prompt`` so the
    Phase 9E grounding contract is enforced.  Deterministic fallback
    (:func:`render_deterministic_chat_answer`) is used whenever the
    LLM is unavailable or returns an ``[Axion]`` error stub.
    """
    text = update.message.text
    if not text or not text.strip():
        return
    text = text.strip()

    await update.message.chat.send_action(ChatAction.TYPING)
    lower = text.lower()

    # Lightweight keyword routing for command-style phrases — these
    # still go through the dedicated cmd_* handlers (which are now
    # portfolio-scoped via _active_portfolio_id).
    routing = [
        (["exposure", "breakdown", "sector exposure", "allocation"], cmd_exposure),
        (["trade history", "my trades", "recent trades"], cmd_trades),
        (["audit", "audit trail", "log", "who changed"], cmd_audit),
        (["report", "export", "download", "csv", "send me"], cmd_report),
    ]
    for keywords, handler in routing:
        if any(w in lower for w in keywords):
            await handler(update, context)
            return

    # Everything else goes through the grounded chat layer.
    chat_id = update.effective_chat.id if update.effective_chat else 0
    try:
        from src.integrations.telegram.grounded import render_grounded_telegram_reply

        async with get_db() as session:
            answer, mode, portfolio_id = await render_grounded_telegram_reply(
                session, chat_id=chat_id, query=text,
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Grounded chat path failed: %s", exc)
        await update.message.reply_text(
            "I couldn't reach the intelligence engine right now.  "
            "Try /portfolio, /alerts, or /digest in the meantime."
        )
        return

    # Prefix with a tiny context hint so the user always knows which
    # portfolio this chat is scoped to.
    prefix = f"_[portfolio=`{portfolio_id}` \u00b7 {mode}]_\n"
    await update.message.reply_text(
        _safe_text(prefix + answer), parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# Notification Push
# ---------------------------------------------------------------------------
_bot_app: Optional[Application] = None


async def push_notification(chat_id: int, message: str, parse_mode: str = "Markdown") -> bool:
    if _bot_app is None:
        return False
    try:
        await _bot_app.bot.send_message(chat_id=chat_id, text=message, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.error("Push notification failed for chat %s: %s", chat_id, e)
        return False


async def push_to_all(message: str, parse_mode: str = "Markdown") -> int:
    if not _authorized_chats:
        return 0
    sent = 0
    for chat_id in _authorized_chats:
        if await push_notification(chat_id, message, parse_mode):
            sent += 1
    return sent


async def push_document_to_all(data: bytes, filename: str, caption: str = "") -> int:
    """Push a file to all authorized chats."""
    if not _bot_app or not _authorized_chats:
        return 0
    sent = 0
    for chat_id in _authorized_chats:
        try:
            await _bot_app.bot.send_document(
                chat_id=chat_id,
                document=InputFile(io.BytesIO(data), filename=filename),
                caption=caption,
            )
            sent += 1
        except Exception as e:
            logger.error("Push document failed for chat %s: %s", chat_id, e)
    return sent


# ---------------------------------------------------------------------------
# Bot Lifecycle
# ---------------------------------------------------------------------------
async def start_bot(token: str, chat_ids: list[int] | None = None) -> Application:
    global _bot_app, _authorized_chats

    if chat_ids:
        _authorized_chats = set(chat_ids)

    _bot_app = Application.builder().token(token).build()

    # Register command handlers
    commands = [
        ("start", cmd_start),
        ("help", cmd_help),
        ("portfolio", cmd_portfolio),
        ("portfolio_list", cmd_portfolio_list),
        ("portfolio_select", cmd_portfolio_select),
        ("holdings", cmd_holdings),
        ("exposure", cmd_exposure),
        ("trades", cmd_trades),
        ("alerts", cmd_alerts),
        ("risk", cmd_risk),
        ("digest", cmd_digest),
        ("events", cmd_events),
        ("collect", cmd_collect),
        ("analyze", cmd_analyze),
        ("classify", cmd_classify),
        ("audit", cmd_audit),
        ("status", cmd_status),
        ("report", cmd_report),
        ("reset", cmd_reset),
    ]

    for name, handler in commands:
        _bot_app.add_handler(CommandHandler(name, handler))

    # File upload handler (must be before text handler)
    _bot_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Free-text message handler (must be last)
    _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set bot commands for Telegram menu
    await _bot_app.bot.set_my_commands([
        BotCommand("portfolio", "Portfolio summary & top holdings"),
        BotCommand("portfolio_list", "List portfolios & show active pin"),
        BotCommand("portfolio_select", "Switch this chat's active portfolio"),
        BotCommand("holdings", "Full holdings list"),
        BotCommand("exposure", "Sector/geo/currency breakdown"),
        BotCommand("trades", "Recent trade history"),
        BotCommand("alerts", "Active alerts"),
        BotCommand("risk", "Run risk assessment"),
        BotCommand("digest", "Latest intelligence digest"),
        BotCommand("events", "Recent events"),
        BotCommand("collect", "Trigger news collection"),
        BotCommand("analyze", "Run event analysis"),
        BotCommand("classify", "Classify holdings"),
        BotCommand("report", "Download reports as files"),
        BotCommand("audit", "Recent audit trail"),
        BotCommand("status", "System health & agents"),
        BotCommand("reset", "Clear all data and start fresh"),
        BotCommand("help", "Show all commands"),
    ])

    # Initialize and start polling
    await _bot_app.initialize()
    await _bot_app.start()
    await _bot_app.updater.start_polling(drop_pending_updates=True)

    logger.info(
        "Telegram bot started. Authorized chats: %s",
        _authorized_chats or "ALL (no restriction)",
    )
    return _bot_app


async def stop_bot():
    global _bot_app, _client
    if _bot_app:
        logger.info("Stopping Telegram bot...")
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()
        _bot_app = None
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
