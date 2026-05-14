"""Phase 12 — Professional Insights quality upgrade.

This package is the single source of truth for the **Insights →
Overview** surface that aggregates News, Corporate Events, Revenue
Geography, Listing Country, Alerts, Relationships, Factor
sensitivities, and data gaps into a deterministic, evidence-backed
list of insight cards.

Public surface:

* :class:`InsightCard`, :class:`InsightEvidence`,
  :class:`InsightDeepLink`, :class:`InsightsResponse` — JSON-stable
  Pydantic models.
* :func:`build_insights` — deterministic generator that combines
  already-stored facts into ranked cards.  Works without any AI key.
* :func:`narrate_insights` — optional grounded AI rewriter.  Can
  improve wording or add a summary line, but **never** introduces
  new evidence, percentages, holdings, or claims.  When AI is
  unavailable, returns the deterministic cards unchanged.

The package never persists insights — they are computed on demand
and returned to the API/dashboard.  No new DB tables, no
side-effects.
"""

from src.intelligence.insights.models import (
    InsightCard,
    InsightCategory,
    InsightDeepLink,
    InsightEvidence,
    InsightSeverity,
    InsightSourceType,
    InsightsCoverage,
    InsightsResponse,
)
from src.intelligence.insights.generator import build_insights
from src.intelligence.insights.ai_narrator import narrate_insights

__all__ = [
    "InsightCard",
    "InsightCategory",
    "InsightDeepLink",
    "InsightEvidence",
    "InsightSeverity",
    "InsightSourceType",
    "InsightsCoverage",
    "InsightsResponse",
    "build_insights",
    "narrate_insights",
]
