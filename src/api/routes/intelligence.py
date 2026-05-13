"""Phase 9G — Portfolio intelligence summary route.

Provides a single thin endpoint — ``GET /api/v1/intelligence/summary``
— that returns a premium overview of what matters for a given
portfolio right now.  The route is a very thin wrapper around
:func:`src.intelligence.summary.build_intelligence_summary`; all the
aggregation + posture logic lives in the intelligence module so it
can be tested without any HTTP machinery.

Every field the endpoint returns is deterministic-first.  No LLM, no
new scoring model, no new confidence math — just a rule-based roll-up
of data that Phase 9A/9B/9C/9D/9E already produces.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
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
