"""Phase 12 — Insights model.

Pydantic models for the deterministic + grounded-AI Insights surface.
Stable JSON shape so the API + dashboard + future external callers
can consume them without surprises.

Design rules:

* **No claim without evidence.**  Every :class:`InsightCard` carries
  at least one :class:`InsightEvidence` row identifying its origin
  (news, corporate event, alert, holding, revenue-geography row,
  relationship, factor, source).  Generators raise if the list is
  empty; AI narrators must preserve it verbatim.
* **Deterministic-first.**  Cards default to
  ``source_type="deterministic"``.  Only the AI narrator may flip
  individual cards to ``"ai_narrative"`` and only after preserving
  the original deterministic evidence + title.
* **Customer-safe deep links.**  The :class:`InsightDeepLink` is the
  same shape the Phase 9Q navigation engine consumes — surface
  enum, optional subtab, optional entity id, optional filters.
* **No live-price or live-data claims**, ever.  The category list is
  explicit; an LLM-narrated card cannot add a new category.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────


InsightSeverity = Literal["critical", "high", "medium", "low", "info"]


InsightCategory = Literal[
    "news_impact",
    "corporate_event",
    "concentration",
    "revenue_geography",
    "listing_country",
    "factor_sensitivity",
    "relationship_chain",
    "alert",
    "data_gap",
]


#: ``deterministic`` — the card was built entirely from stored facts.
#: ``ai_narrative`` — the card's title/summary was rewritten by the
#: grounded AI narrator; ``evidence`` and ``deep_links`` are unchanged
#: from the deterministic original.
InsightSourceType = Literal["deterministic", "ai_narrative"]


#: Grounding status surfaced at the **response** level — never at the
#: per-card level — so callers know whether AI was even consulted.
#:
#: * ``deterministic_only`` — AI narrator was not invoked
#:   (``include_ai=false`` or no provider).
#: * ``ai_grounded``        — AI narrated at least one card without
#:   adding facts.
#: * ``ai_unavailable``     — AI was requested but no provider/key.
#: * ``ai_failed``          — AI was requested but raised; cards are
#:   the deterministic originals.
GroundingStatus = Literal[
    "deterministic_only",
    "ai_grounded",
    "ai_unavailable",
    "ai_failed",
]


# ─────────────────────────────────────────────────────────────────────
# Building blocks
# ─────────────────────────────────────────────────────────────────────


class InsightEvidence(BaseModel):
    """A single piece of evidence backing one :class:`InsightCard`.

    The evidence is *structured*, not free text — the dashboard can
    render it as a chip, and the AI narrator can be inspected against
    it to verify nothing new was claimed.
    """

    kind: Literal[
        "news",
        "corporate_event",
        "alert",
        "holding",
        "revenue_geography",
        "listing",
        "relationship",
        "factor",
        "source",
        "config",
    ]
    ref: str                # e.g. "event:evt_123", "holding:h_aapl", "factor:interest_rate"
    label: str              # short customer-facing label
    detail: str | None = None

    model_config = {"frozen": True}


class InsightDeepLink(BaseModel):
    """A compact pointer to the customer-facing surface that explains
    this card.  Matches the Phase 9Q ``NavigationTarget`` envelope.

    Marked frozen so the dashboard renderer can rely on stable
    references across re-renders.
    """

    surface: Literal[
        "alerts", "digest", "events", "operator", "portfolio",
        "corporate-events", "settings",
    ]
    subtab: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    label: str
    filters: dict[str, str] | None = None

    model_config = {"frozen": True}


# ─────────────────────────────────────────────────────────────────────
# Core insight card
# ─────────────────────────────────────────────────────────────────────


class InsightCard(BaseModel):
    """One professional, evidence-backed insight card.

    Stable JSON shape.  Every field has a defined type and a sensible
    default so the dashboard can round-trip without nullability
    surprises.  The card is **never persisted** — it's computed on
    demand from stored facts and returned to the API.
    """

    id: str
    portfolio_id: str
    severity: InsightSeverity
    category: InsightCategory
    title: str
    summary: str
    why_it_matters: str | None = None
    affected_holdings: list[str] = Field(default_factory=list)
    evidence: list[InsightEvidence]
    recommended_action: str | None = None
    confidence: float | None = None
    source_type: InsightSourceType = "deterministic"
    data_gaps: list[str] = Field(default_factory=list)
    deep_links: list[InsightDeepLink] = Field(default_factory=list)
    created_at: str = ""
    # Phase 12 — internal sort key (rank ascending = more important).
    # Kept on the model so generators and tests can assert ordering
    # without depending on insertion order.
    rank: int = 100

    model_config = {"frozen": True}


# ─────────────────────────────────────────────────────────────────────
# Coverage / data-availability summary
# ─────────────────────────────────────────────────────────────────────


class InsightsCoverage(BaseModel):
    """Side-panel "data coverage" snapshot.

    Reports the inputs the generator had — used by the dashboard to
    render the "Holdings: 12 / News: 47 / Revenue geography:
    partial" coverage strip.  Never claims data that isn't there.
    """

    holding_count: int = 0
    news_count_7d: int = 0
    corporate_event_count_30d: int = 0
    active_alert_count: int = 0
    revenue_geography_status: str = "missing"   # missing | partial | available
    revenue_geography_uploaded_holdings: int = 0
    source_health: dict[str, int] = Field(default_factory=dict)
    ai_provider_available: bool = False
    last_news_fetched_at: str | None = None


class InsightsResponse(BaseModel):
    """The wire-level Insights payload.

    ``insights`` is the ranked list of cards.  ``coverage`` reports
    the inputs.  ``grounding_status`` tells the operator whether AI
    was consulted.  ``warnings`` carries non-blocking notes (e.g. "AI
    narrator failed; deterministic output shown").
    """

    portfolio_id: str
    portfolio_name: str | None = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    grounding_status: GroundingStatus = "deterministic_only"
    insights: list[InsightCard]
    coverage: InsightsCoverage
    warnings: list[str] = Field(default_factory=list)
    total: int = 0
    limit: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
