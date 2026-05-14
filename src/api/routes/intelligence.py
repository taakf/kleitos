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

from typing import Any

from fastapi import APIRouter, Depends, Query
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
