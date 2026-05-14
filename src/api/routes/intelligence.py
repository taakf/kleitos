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
from src.intelligence.insights import build_insights, narrate_insights
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
    return response.to_dict()
