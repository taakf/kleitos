"""Phase 9G + Phase 12 — Portfolio intelligence routes.

Two endpoints share this router:

* ``GET /api/v1/intelligence/summary``  (Phase 9G) — premium overview
  dict used by Portfolio → Holdings.  Stable shape; never changed.
* ``GET /api/v1/intelligence/insights`` (Phase 12) — ranked list of
  ``InsightCard`` rows used by the new Insights → Overview surface.
  Deterministic-first; optional grounded-AI narration via
  ``include_ai=true``.

Both routes are thin HTTP wrappers; the aggregation lives in the
intelligence module so it stays unit-testable.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.intelligence.insights import (
    attach_notification_state,
    build_insights,
    get_last_generated_at,
    narrate_insights,
    notify_new_or_escalated,
)
from src.intelligence.summary import build_intelligence_summary

router = APIRouter(prefix="/api/v1/intelligence", tags=["intelligence"])


@router.get("/summary")
async def intelligence_summary(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return a premium intelligence overview for the portfolio.

    Response shape::

        {
          "portfolio_id":  "default",
          "portfolio_name": "Main Portfolio",
          "holding_count": 12,
          "posture": "mildly_negative",
          "posture_reason": "1 high-severity alert(s) active.",
          "top_factors": [
            {"factor": "interest_rate", "label": "Interest Rates",
             "direction": "up", "max_relevance": 0.42,
             "holdings": ["AAPL", "MSFT"]}
          ],
          "top_relationships": [
            {"ticker": "AAPL", "relationship_type": "supplier",
             "related_entity": "Taiwan Semiconductor",
             "max_relevance": 0.48, "portfolio_id": "default"}
          ],
          "alerts": {"critical": 0, "high": 1, "warning": 0, "info": 2, "total": 3},
          "holdings_under_attention": ["AAPL"],
          "recent_events_count_24h": 7,
          "freshness": {
            "last_event_fetched_at": "2026-04-05T14:22:00+00:00",
            "stale_minutes": 12,
            "is_fresh": true
          },
          "intelligence_health": {
            "factor_links": 23,
            "relationship_links": 8,
            "analysis_notes_7d": 11,
            "has_digest": true,
            "global_factor_classifications": 47
          },
          "computed_at": "2026-04-05T14:30:00+00:00"
        }

    The endpoint never raises; on any backend hiccup it returns a
    summary with ``posture: "insufficient_data"`` and a
    ``posture_reason`` describing the failure.
    """
    summary = await build_intelligence_summary(
        session, portfolio_id=portfolio_id or "default",
    )
    return summary.to_dict()


# ---------------------------------------------------------------------------
# Phase 12 — Insights overview
# ---------------------------------------------------------------------------


@router.get("/insights")
async def intelligence_insights(
    portfolio_id: str = Query("default", description="Portfolio ID"),
    limit: int = Query(12, ge=1, le=60),
    include_ai: bool = Query(False, description=(
        "If true and an AI provider is configured, narrate the deterministic "
        "cards.  Always falls back to deterministic output silently."
    )),
    category: str | None = Query(None, description=(
        "Optional category filter: news_impact | corporate_event | "
        "concentration | revenue_geography | listing_country | "
        "factor_sensitivity | relationship_chain | alert | data_gap."
    )),
    severity: str | None = Query(None, description=(
        "Optional severity filter: critical | high | medium | low | info."
    )),
    time_window_days: int | None = Query(None, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Phase 12 — return a ranked list of deterministic insight cards.

    Stable JSON shape (see :class:`InsightsResponse`).  Never raises;
    on any backend hiccup returns an empty insight list with a
    warning so the dashboard still renders.
    """
    response = await build_insights(
        session,
        portfolio_id=portfolio_id or "default",
        limit=limit, category=category, severity=severity,
        time_window_days=time_window_days,
    )
    response = await narrate_insights(response, include_ai=include_ai)
    # Phase 13 — stamp each card with its notification_state
    # (new / escalated / unchanged / first_run) from the snapshot
    # table.  Read-only here: persistence happens on
    # ``POST /insights/run`` (manual) or the scheduler interval.
    try:
        outcome = await _peek_notification_state(session, response)
        response = attach_notification_state(response, outcome)
    except Exception as exc:  # pragma: no cover — defensive
        # The notification-state stamp is purely cosmetic; a backend
        # hiccup must not break the read-only /insights surface.  Log
        # but never raise.
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "notification_state stamp skipped: %r", exc,
        )
    out = response.to_dict()
    try:
        out["last_generated_at"] = await get_last_generated_at(
            session, portfolio_id=portfolio_id or "default",
        )
    except Exception:
        out["last_generated_at"] = None
    return out


async def _peek_notification_state(session, response):
    """Build a NotifyOutcome **without** writing to insight_snapshots.

    The /insights GET endpoint must be read-only — persistence is
    reserved for /insights/run and the scheduler.  We reuse the
    notifier's classification logic by loading existing snapshots
    once and diffing locally.
    """
    from src.database.models import InsightSnapshot
    from src.intelligence.insights.fingerprint import (
        card_fingerprint, card_key, is_escalation,
    )
    from src.intelligence.insights.notifier import (
        NotifiedInsight, NotifyOutcome,
    )
    from sqlalchemy import select as _select

    portfolio_id = response.portfolio_id
    rows = (await session.execute(
        _select(InsightSnapshot).where(
            InsightSnapshot.portfolio_id == portfolio_id,
        )
    )).scalars().all()
    by_key = {r.card_key: r for r in rows}
    outcome = NotifyOutcome(portfolio_id=portfolio_id)
    outcome.is_first_run = not by_key
    outcome.snapshot_count = len(by_key)
    for card in response.insights:
        key = card_key(card)
        fp = card_fingerprint(card)
        prior = by_key.get(key)
        if prior is None:
            state = "first_run" if outcome.is_first_run else "new"
            entry = NotifiedInsight(
                card=card, state=state,
                card_key=key, fingerprint=fp,
            )
            if state == "first_run":
                outcome.unchanged.append(entry)
            else:
                outcome.new.append(entry)
            continue
        if prior.fingerprint == fp:
            outcome.unchanged.append(NotifiedInsight(
                card=card, state="unchanged",
                card_key=key, fingerprint=fp,
                previous_severity=prior.severity,
            ))
            continue
        escalated = is_escalation(
            old_severity=prior.severity, new_severity=card.severity,
        )
        entry = NotifiedInsight(
            card=card,
            state="escalated" if escalated else "new",
            card_key=key, fingerprint=fp,
            previous_severity=prior.severity,
        )
        (outcome.escalated if escalated else outcome.new).append(entry)
    return outcome


# ---------------------------------------------------------------------------
# Phase 13 — Insight notification controls
# ---------------------------------------------------------------------------


@router.post("/insights/run")
async def insights_run(
    portfolio_id: str = Query("default"),
    deliver_telegram: bool = Query(
        False,
        description=(
            "If true and Telegram is configured, deliver new + escalated "
            "cards above the severity floor.  No-op when not configured."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Phase 13 — manual trigger for an insight generation pass.

    Reuses the scheduler's runner via ``build_insights`` +
    ``notify_new_or_escalated``.  Always returns a structured
    summary; never raises.  Persists snapshots.
    """
    response = await build_insights(
        session, portfolio_id=portfolio_id or "default", limit=60,
    )
    outcome = await notify_new_or_escalated(
        session, response, deliver_telegram=deliver_telegram,
    )
    return {
        "portfolio_id": outcome.portfolio_id,
        "generated_at": outcome.generated_at,
        "new": len(outcome.new),
        "escalated": len(outcome.escalated),
        "unchanged": len(outcome.unchanged),
        "telegram_status": outcome.telegram_status,
        "telegram_delivered": outcome.telegram_delivered,
        "is_first_run": outcome.is_first_run,
    }


@router.get("/insights/last-run")
async def insights_last_run(
    portfolio_id: str = Query("default"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the most recent ``last_seen_at`` from insight_snapshots
    for the portfolio, or ``null`` if no pass has run yet."""
    ts = await get_last_generated_at(
        session, portfolio_id=portfolio_id or "default",
    )
    return {"portfolio_id": portfolio_id, "last_generated_at": ts}


# ---------------------------------------------------------------------------
# Phase 14 — Insights history deck
# ---------------------------------------------------------------------------


@router.get("/insights/history")
async def insights_history(
    portfolio_id: str = Query("default"),
    days: int = Query(7, ge=1, le=365),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    state: str | None = Query(None, description="new | escalated | unchanged"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Phase 14 — read-only history deck over ``insight_snapshots``.

    Returns ranked items + per-day counts + summary aggregates for the
    given window.  Deterministic; never regenerates AI narration; never
    inlines AI prompt bodies.  Portfolio-isolated.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from sqlalchemy import select as _select

    from src.database.models import InsightSnapshot
    from src.intelligence.insights.fingerprint import severity_rank

    now = _dt.now(_tz.utc)
    cutoff = now - _td(days=days)
    cutoff_iso = cutoff.isoformat()
    generated_at = now.isoformat()

    # ─── Build query ──────────────────────────────────────────────
    conds: list[Any] = [
        InsightSnapshot.portfolio_id == portfolio_id,
        InsightSnapshot.last_seen_at >= cutoff_iso,
    ]
    if category:
        conds.append(InsightSnapshot.category == category)
    if severity:
        conds.append(InsightSnapshot.severity == severity.lower())
    if state and state.lower() in ("new", "escalated", "unchanged"):
        conds.append(InsightSnapshot.status == state.lower())

    stmt = _select(InsightSnapshot)
    for c in conds:
        stmt = stmt.where(c)
    stmt = stmt.order_by(
        InsightSnapshot.last_seen_at.desc(),
    ).limit(limit)

    try:
        rows = (await session.execute(stmt)).scalars().all()
    except Exception:  # pragma: no cover — defensive
        rows = []

    # ─── Per-row payload + deep-link routing ──────────────────────
    items: list[dict[str, Any]] = []
    for r in rows:
        deep_link = _deep_link_for_card_key(
            r.card_key, r.category, portfolio_id,
        )
        # Rank-aware severity sort fallback when last_seen_at ties.
        items.append({
            "card_key": r.card_key,
            "category": r.category,
            "severity": r.severity,
            "severity_rank": severity_rank(r.severity),
            "title": r.title,
            "state": r.status,
            "first_seen_at": r.first_seen_at,
            "last_seen_at": r.last_seen_at,
            "notified_at": r.notified_at,
            "notified_severity": r.notified_severity,
            "deep_link": deep_link.to_dict() if deep_link is not None else None,
        })

    # ─── Daily bucket counts (always returns a row per day) ──────
    daily_counts: list[dict[str, Any]] = []
    bucket_by_date: dict[str, dict[str, int]] = {}
    for d_offset in range(days):
        day = (cutoff + _td(days=d_offset + 1)).date().isoformat()
        bucket_by_date[day] = {
            "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
        }
    # Re-query at the date granularity (cheap — same window) and stamp
    # the right bucket per row.  We aggregate **all** rows in the
    # window, not the sliced `limit` slice.
    daily_stmt = _select(
        InsightSnapshot.status,
        InsightSnapshot.last_seen_at,
    )
    for c in conds:
        daily_stmt = daily_stmt.where(c)
    try:
        daily_rows = (await session.execute(daily_stmt)).all()
    except Exception:  # pragma: no cover — defensive
        daily_rows = []
    for st, ts in daily_rows:
        try:
            day = ts[:10]
        except (TypeError, IndexError):
            continue
        if day not in bucket_by_date:
            bucket_by_date[day] = {
                "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
            }
        bucket = bucket_by_date[day]
        key = st if st in ("new", "escalated", "unchanged") else None
        if key is not None:
            bucket[key] += 1
        bucket["total"] += 1
    daily_counts = [
        {"date": day, **counts}
        for day, counts in sorted(bucket_by_date.items())
    ]

    summary = {
        "new":        sum(b["new"]        for b in bucket_by_date.values()),
        "escalated":  sum(b["escalated"]  for b in bucket_by_date.values()),
        "unchanged":  sum(b["unchanged"]  for b in bucket_by_date.values()),
        "total":      sum(b["total"]      for b in bucket_by_date.values()),
    }

    return {
        "portfolio_id": portfolio_id,
        "window_days": days,
        "generated_at": generated_at,
        "items": items,
        "daily_counts": daily_counts,
        "summary": summary,
    }


def _deep_link_for_card_key(
    card_key: str, category: str, portfolio_id: str,
):
    """Phase 14 — best-effort navigation target from a card_key.

    ``card_key`` looks like ``insight:<category>:<ref>``.  We route
    each category to the surface that explains it; unknown shapes
    fall back to Insights Overview itself.
    """
    from src.intelligence.navigation import (
        _safe_target, target_for_alert, target_for_corporate_event,
        target_for_event,
    )

    parts = card_key.split(":", 2)
    ref = parts[2] if len(parts) >= 3 else None
    sub_parts = ref.split(":", 1) if ref else []
    ref_kind = sub_parts[0] if sub_parts else None
    ref_id = sub_parts[1] if len(sub_parts) > 1 else None

    if category == "news_impact" and ref_kind == "event" and ref_id:
        return target_for_event(ref_id, portfolio_id)
    if category == "corporate_event" and ref_kind == "corporate_event" and ref_id:
        return target_for_corporate_event(ref_id, portfolio_id)
    if category == "alert" and ref_kind == "alert" and ref_id:
        return target_for_alert(ref_id, portfolio_id)
    if category == "revenue_geography":
        return _safe_target(
            surface="portfolio", portfolio_id=portfolio_id,
            subtab="exposures", label="Open Revenue geography",
        )
    if category == "listing_country":
        return _safe_target(
            surface="portfolio", portfolio_id=portfolio_id,
            subtab="exposures", label="Open Listing country",
        )
    if category == "factor_sensitivity" and ref_kind == "factor" and ref_id:
        return _safe_target(
            surface="operator", portfolio_id=portfolio_id,
            subtab="factors", filter=ref_id, label="Open factor table",
        )
    # Default — back to the Insights Overview itself.
    return _safe_target(
        surface="intelligence", portfolio_id=portfolio_id,
        subtab="overview", label="Open in Insights",
    )


# ---------------------------------------------------------------------------
# Phase 15 — Insights export (CSV + JSON)
# ---------------------------------------------------------------------------
#
# Both endpoints stitch the current ``/insights`` cards and the
# ``/insights/history`` transitions for the same window into a single
# download.  They are deterministic-first and read-only — no
# regeneration, no AI re-narration, no live prices.  Every field that
# ships is already public on one of the existing GET endpoints; nothing
# new is exposed.
#
# Privacy invariants (Phase 15E):
#   * No API keys, OAuth tokens, Telegram tokens.
#   * No AI prompt bodies.
#   * No uploaded PDF / document content.
#   * No ``.env`` contents.
#   * No live market prices, no investment advice framing.
# These are enforced structurally — only ``InsightCard`` /
# ``InsightSnapshot`` fields are ever copied into the export, and both
# models are customer-safe by construction.


_INSIGHTS_EXPORT_CSV_COLUMNS: list[str] = [
    "section",                # "current" | "history"
    "category",
    "severity",
    "state",                  # current notification_state or history state
    "title",
    "summary",
    "why_it_matters",
    "recommended_action",
    "affected_holdings",      # ; -joined ticker list
    "confidence",
    "first_seen_at",
    "last_seen_at",
    "notified_at",
    "deep_link_label",
    "deep_link_surface",
    "deep_link_subtab",
    "source_type",
]


# Substrings that must NEVER appear in an export response — they are the
# fingerprints of upstream secrets / prompts / uploaded content.  The
# scrubber is a belt-and-braces guard; export rows are built only from
# customer-safe model fields, so the substring set is intentionally
# conservative and the test gate enforces it on every export response.
_INSIGHTS_EXPORT_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "GROUNDING CONTRACT",
    "STRUCTURED DATA",
    "ANTI-HALLUCINATION",
    "PROMPT:",
    "BEGIN PDF",
    "END PDF",
    "-----BEGIN",     # PEM / OpenSSL keys
    "api_key=",
    "apikey=",
    "Bearer ",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "FINNHUB_KEY",
    "NEWSAPI_KEY",
    "TELEGRAM_BOT_TOKEN",
)


def _safe_str(value: Any) -> str:
    """Render ``value`` as a string for CSV / JSON export, stripping
    anything that looks like a leaked secret or prompt body.

    Strings that contain any forbidden substring are replaced with
    ``"[redacted]"`` — defensive only; the upstream model never produces
    them, but a future regression in the generator can't leak.
    """
    if value is None:
        return ""
    s = str(value)
    for needle in _INSIGHTS_EXPORT_FORBIDDEN_SUBSTRINGS:
        if needle in s:
            return "[redacted]"
    return s


def _flatten_insight_card(card: dict[str, Any]) -> dict[str, Any]:
    """Project an ``InsightCard.to_dict()`` payload into the flat row
    shape used by the CSV / JSON export.

    Only the fields named in ``_INSIGHTS_EXPORT_CSV_COLUMNS`` are
    emitted, plus ``section="current"``.  Notification state is read
    from ``data_gaps`` (Phase 13 stamps it there as
    ``notification:<state>``); deep-link metadata is read from the
    first entry in ``deep_links``.
    """
    state = ""
    for tag in card.get("data_gaps") or ():
        if isinstance(tag, str) and tag.startswith("notification:"):
            state = tag.split(":", 1)[1]
            break

    dls = card.get("deep_links") or []
    dl = dls[0] if dls else {}

    holdings = card.get("affected_holdings") or []
    holdings_joined = ";".join(_safe_str(h) for h in holdings if h)

    return {
        "section": "current",
        "category": _safe_str(card.get("category")),
        "severity": _safe_str(card.get("severity")),
        "state": _safe_str(state),
        "title": _safe_str(card.get("title")),
        "summary": _safe_str(card.get("summary")),
        "why_it_matters": _safe_str(card.get("why_it_matters")),
        "recommended_action": _safe_str(card.get("recommended_action")),
        "affected_holdings": holdings_joined,
        "confidence": _safe_str(card.get("confidence")),
        "first_seen_at": "",
        "last_seen_at": _safe_str(card.get("created_at")),
        "notified_at": "",
        "deep_link_label": _safe_str(dl.get("label")) if isinstance(dl, dict) else "",
        "deep_link_surface": _safe_str(dl.get("surface")) if isinstance(dl, dict) else "",
        "deep_link_subtab": _safe_str(dl.get("subtab")) if isinstance(dl, dict) else "",
        "source_type": _safe_str(card.get("source_type") or "deterministic"),
    }


def _flatten_history_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a ``/insights/history`` item into the flat export row."""
    dl = row.get("deep_link") or {}
    return {
        "section": "history",
        "category": _safe_str(row.get("category")),
        "severity": _safe_str(row.get("severity")),
        "state": _safe_str(row.get("state")),
        "title": _safe_str(row.get("title")),
        "summary": "",
        "why_it_matters": "",
        "recommended_action": "",
        "affected_holdings": "",
        "confidence": "",
        "first_seen_at": _safe_str(row.get("first_seen_at")),
        "last_seen_at": _safe_str(row.get("last_seen_at")),
        "notified_at": _safe_str(row.get("notified_at")),
        "deep_link_label": _safe_str(dl.get("label")) if isinstance(dl, dict) else "",
        "deep_link_surface": _safe_str(dl.get("surface")) if isinstance(dl, dict) else "",
        "deep_link_subtab": _safe_str(dl.get("subtab")) if isinstance(dl, dict) else "",
        "source_type": "snapshot",
    }


async def _gather_insights_export_payload(
    session: AsyncSession,
    *,
    portfolio_id: str,
    category: str | None,
    severity: str | None,
    days: int,
    history_state: str | None,
    include_ai: bool,
    limit: int,
) -> dict[str, Any]:
    """Shared loader for both the CSV and JSON Insights export
    endpoints.  Reuses the same Phase 12 + Phase 14 builders the live
    surface uses so the export is always consistent with what the
    operator just saw on screen.
    """
    response = await build_insights(
        session,
        portfolio_id=portfolio_id or "default",
        limit=limit,
        category=category,
        severity=severity,
        time_window_days=days,
    )
    response = await narrate_insights(response, include_ai=include_ai)
    # Read-only notification-state stamp (no snapshot writes).
    try:
        outcome = await _peek_notification_state(session, response)
        response = attach_notification_state(response, outcome)
    except Exception as exc:  # pragma: no cover — defensive
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "insights export: notification_state stamp skipped: %r", exc,
        )

    current_payload = response.to_dict()
    current_cards: list[dict[str, Any]] = list(
        current_payload.get("insights") or []
    )

    # Inline call to the history endpoint logic so we don't duplicate
    # the snapshot reader.  ``insights_history`` is an async function on
    # this router; importing it would be circular, so we reuse the same
    # SQLAlchemy reader directly here.
    history_block = await _read_history_for_export(
        session,
        portfolio_id=portfolio_id or "default",
        days=days,
        category=category,
        severity=severity,
        state=history_state,
        limit=200,
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    return {
        "portfolio_id": portfolio_id or "default",
        "generated_at": generated_at,
        "window_days": days,
        "filters": {
            "category": category or "",
            "severity": severity or "",
            "history_state": history_state or "",
            "include_ai": bool(include_ai),
        },
        "summary": history_block.get("summary") or {
            "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
        },
        "current_cards": current_cards,
        "history": history_block.get("items") or [],
        "daily_counts": history_block.get("daily_counts") or [],
        "grounding_status": current_payload.get("grounding_status"),
        "warnings": current_payload.get("warnings") or [],
        "coverage": current_payload.get("coverage"),
        "last_generated_at": current_payload.get("last_generated_at"),
    }


async def _read_history_for_export(
    session: AsyncSession,
    *,
    portfolio_id: str,
    days: int,
    category: str | None,
    severity: str | None,
    state: str | None,
    limit: int,
) -> dict[str, Any]:
    """Mirror of :func:`insights_history` returning the dict payload
    without going through HTTP.  Kept local so export logic doesn't
    couple to the route handler signature."""
    from datetime import timedelta as _td
    from sqlalchemy import select as _select

    from src.database.models import InsightSnapshot
    from src.intelligence.insights.fingerprint import severity_rank

    now = datetime.now(timezone.utc)
    cutoff = now - _td(days=days)
    cutoff_iso = cutoff.isoformat()

    conds: list[Any] = [
        InsightSnapshot.portfolio_id == portfolio_id,
        InsightSnapshot.last_seen_at >= cutoff_iso,
    ]
    if category:
        conds.append(InsightSnapshot.category == category)
    if severity:
        conds.append(InsightSnapshot.severity == severity.lower())
    if state and state.lower() in ("new", "escalated", "unchanged"):
        conds.append(InsightSnapshot.status == state.lower())

    stmt = _select(InsightSnapshot)
    for c in conds:
        stmt = stmt.where(c)
    stmt = stmt.order_by(InsightSnapshot.last_seen_at.desc()).limit(limit)
    try:
        rows = (await session.execute(stmt)).scalars().all()
    except Exception:  # pragma: no cover — defensive
        rows = []

    items: list[dict[str, Any]] = []
    for r in rows:
        deep_link = _deep_link_for_card_key(
            r.card_key, r.category, portfolio_id,
        )
        items.append({
            "card_key": r.card_key,
            "category": r.category,
            "severity": r.severity,
            "severity_rank": severity_rank(r.severity),
            "title": r.title,
            "state": r.status,
            "first_seen_at": r.first_seen_at,
            "last_seen_at": r.last_seen_at,
            "notified_at": r.notified_at,
            "notified_severity": r.notified_severity,
            "deep_link": deep_link.to_dict() if deep_link is not None else None,
        })

    bucket_by_date: dict[str, dict[str, int]] = {}
    for d_offset in range(days):
        day = (cutoff + _td(days=d_offset + 1)).date().isoformat()
        bucket_by_date[day] = {
            "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
        }
    daily_stmt = _select(
        InsightSnapshot.status,
        InsightSnapshot.last_seen_at,
    )
    for c in conds:
        daily_stmt = daily_stmt.where(c)
    try:
        daily_rows = (await session.execute(daily_stmt)).all()
    except Exception:  # pragma: no cover — defensive
        daily_rows = []
    for st, ts in daily_rows:
        try:
            day = ts[:10]
        except (TypeError, IndexError):
            continue
        if day not in bucket_by_date:
            bucket_by_date[day] = {
                "new": 0, "escalated": 0, "unchanged": 0, "total": 0,
            }
        bucket = bucket_by_date[day]
        k = st if st in ("new", "escalated", "unchanged") else None
        if k is not None:
            bucket[k] += 1
        bucket["total"] += 1
    daily_counts = [
        {"date": day, **counts}
        for day, counts in sorted(bucket_by_date.items())
    ]
    summary = {
        "new":        sum(b["new"]        for b in bucket_by_date.values()),
        "escalated":  sum(b["escalated"]  for b in bucket_by_date.values()),
        "unchanged":  sum(b["unchanged"]  for b in bucket_by_date.values()),
        "total":      sum(b["total"]      for b in bucket_by_date.values()),
    }
    return {
        "items": items,
        "daily_counts": daily_counts,
        "summary": summary,
    }


def _insights_export_filename(suffix: str) -> str:
    """Deterministic filename:
    ``axion-insights-overview-YYYYMMDD-HHMMSS.<suffix>``"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"axion-insights-overview-{stamp}.{suffix}"


def _insights_export_to_csv(payload: dict[str, Any]) -> str:
    """Serialise the merged current + history payload into CSV text.

    Order: a single header row (``_INSIGHTS_EXPORT_CSV_COLUMNS``), then
    every current card, then every history transition.  The CSV is
    self-describing — no extra metadata rows so downstream tools (Excel,
    pandas) can ingest it without skip-rows.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=_INSIGHTS_EXPORT_CSV_COLUMNS,
        extrasaction="ignore", lineterminator="\n",
    )
    writer.writeheader()
    for card in payload.get("current_cards") or ():
        writer.writerow(_flatten_insight_card(card))
    for row in payload.get("history") or ():
        writer.writerow(_flatten_history_row(row))
    return buf.getvalue()


@router.post("/insights/export")
async def insights_export_csv(
    portfolio_id: str = Query("default"),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    history_state: str | None = Query(
        None, description="all | new | escalated | unchanged",
    ),
    include_ai: bool = Query(False),
    limit: int = Query(60, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Phase 15 — CSV download merging current Insights cards and recent
    history transitions for the same portfolio.

    The endpoint is POST so it shows up cleanly in browser DevTools as a
    deliberate user action (some browsers cache GET-driven downloads
    aggressively); the underlying read is still read-only — no rows are
    written.
    """
    payload = await _gather_insights_export_payload(
        session,
        portfolio_id=portfolio_id or "default",
        category=category,
        severity=severity,
        days=days,
        history_state=history_state,
        include_ai=include_ai,
        limit=limit,
    )
    csv_text = _insights_export_to_csv(payload)
    filename = _insights_export_filename("csv")
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Axion-Export-Type": "insights-overview-csv",
        },
    )


@router.get("/insights/export.json")
async def insights_export_json(
    portfolio_id: str = Query("default"),
    category: str | None = Query(None),
    severity: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    history_state: str | None = Query(None),
    include_ai: bool = Query(False),
    limit: int = Query(60, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Phase 15 — JSON twin of the CSV export.

    Stable, self-describing payload with::

        {
          "portfolio_id":  ...,
          "generated_at":  ISO-8601 UTC,
          "window_days":   <int>,
          "filters":       {category, severity, history_state, include_ai},
          "summary":       {new, escalated, unchanged, total},
          "current_cards": [<InsightCard.to_dict()>, ...],
          "history":       [<history-row>, ...],
          "daily_counts":  [{date, new, escalated, unchanged, total}, ...],
          "grounding_status": ...,
          "warnings":      [...],
          "coverage":      <InsightsCoverage>,
          "last_generated_at": ISO-8601 | null,
        }
    """
    return await _gather_insights_export_payload(
        session,
        portfolio_id=portfolio_id or "default",
        category=category,
        severity=severity,
        days=days,
        history_state=history_state,
        include_ai=include_ai,
        limit=limit,
    )
