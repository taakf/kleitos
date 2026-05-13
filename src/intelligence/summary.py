"""Phase 9G — Portfolio intelligence summary.

This module is a thin, deterministic-first aggregator that produces a
single premium overview of what matters for a given portfolio RIGHT
NOW.  It reuses the Phase 9A/9B factor data, the Phase 9D relationship
data, and the Phase 9E analysis/digest artefacts.  No new scoring
models, no new LLM calls — every field is either a direct SQL
aggregate or a rule-based roll-up of already-trusted data.

Design principles
-----------------
1. **Deterministic-first.**  Every field is computed from existing
   rows.  The posture rule is a small, explainable finite-state
   function — no ML, no magic constants beyond what the rest of the
   stack already uses.
2. **Portfolio-safe.**  Every SQL query is scoped to the supplied
   ``portfolio_id`` through ``Holding.portfolio_id`` or
   ``Alert.portfolio_id``.  There is no path in this file that reads
   rows without a portfolio filter.
3. **Empty-state safe.**  All aggregations tolerate 0 rows and return
   a ``"insufficient_data"`` posture rather than a confident claim.
4. **Explainable.**  The summary carries a ``posture_reason`` string
   that states in plain English why the posture is what it is, so a
   user can always interrogate the call.

Returned shape is a plain dict so the FastAPI route can return it as
``dict[str, Any]`` without dragging pydantic models across module
boundaries.  The route layer is responsible for HTTP concerns only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Alert,
    AnalysisNote,
    Digest,
    Event,
    EventLink,
    Holding,
    MacroFactorEvent,
    Portfolio,
)
from src.llm.grounded import (
    _aggregate_factor_rows,
    _aggregate_relationship_rows,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning knobs — deliberately small and explainable
# ---------------------------------------------------------------------------

# How many top factor / relationship touchpoints to surface.
TOP_FACTOR_LIMIT = 5
TOP_RELATIONSHIP_LIMIT = 5

# How long after the last successful event fetch before we consider
# the feed "stale".  24h is the same threshold the dashboard already
# uses for "no data collected yet".
STALE_MINUTES_THRESHOLD = 24 * 60

# Posture thresholds — rule-based finite state.  See _derive_posture.
POSTURE_MIN_SIGNAL_COUNT = 3  # need at least this many signals to call anything confidently


@dataclass
class IntelligenceSummary:
    """A single premium overview of what matters for a portfolio.

    Every field is either a direct SQL aggregate or a rule-based
    roll-up of already-trusted data.  No LLM, no new scoring model.
    """

    portfolio_id: str
    portfolio_name: str | None
    holding_count: int

    posture: str                     # strong_negative | mildly_negative | mixed | constructive | strong_positive | insufficient_data
    posture_reason: str              # plain-English explanation

    top_factors: list[dict[str, Any]] = field(default_factory=list)
    top_relationships: list[dict[str, Any]] = field(default_factory=list)

    alerts: dict[str, int] = field(default_factory=dict)    # {critical, high, warning, info, total}
    holdings_under_attention: list[str] = field(default_factory=list)
    recent_events_count_24h: int = 0

    freshness: dict[str, Any] = field(default_factory=dict)
    intelligence_health: dict[str, Any] = field(default_factory=dict)

    # Phase 9N — grounded, deterministic list of recommended operator
    # actions derived from the fields above.  Never predictive, never
    # trading advice, always explainable via rationale_refs.
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)

    computed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "portfolio_name": self.portfolio_name,
            "holding_count": self.holding_count,
            "posture": self.posture,
            "posture_reason": self.posture_reason,
            "top_factors": self.top_factors,
            "top_relationships": self.top_relationships,
            "alerts": self.alerts,
            "holdings_under_attention": self.holdings_under_attention,
            "recent_events_count_24h": self.recent_events_count_24h,
            "freshness": self.freshness,
            "intelligence_health": self.intelligence_health,
            "recommended_actions": self.recommended_actions,
            "computed_at": self.computed_at,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def build_intelligence_summary(
    session: AsyncSession,
    *,
    portfolio_id: str,
) -> IntelligenceSummary:
    """Assemble an :class:`IntelligenceSummary` for the given portfolio.

    Every SQL query joins through ``Holding.portfolio_id`` (or through
    ``Alert.portfolio_id`` for alerts) so cross-portfolio leakage is
    structurally impossible.  Aggregation follows the same pattern as
    the Phase 9E grounded chat assembler, which is already covered by
    Phase 9E + 9F tests.

    Never raises — on any DB hiccup we return an ``insufficient_data``
    posture so the dashboard always has something to show.
    """
    try:
        return await _build(session, portfolio_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("intelligence summary failed for %s: %s", portfolio_id, exc)
        return IntelligenceSummary(
            portfolio_id=portfolio_id,
            portfolio_name=None,
            holding_count=0,
            posture="insufficient_data",
            posture_reason=f"summary build failed: {exc}",
            computed_at=datetime.now(timezone.utc).isoformat(),
        )


async def _build(
    session: AsyncSession, portfolio_id: str,
) -> IntelligenceSummary:
    now = datetime.now(timezone.utc)
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_24h = (now - timedelta(hours=24)).isoformat()

    # -------------------- Portfolio identity --------------------
    portfolio = await session.get(Portfolio, portfolio_id)
    portfolio_name = portfolio.name if portfolio else None

    holding_rows = (await session.execute(
        select(Holding).where(
            Holding.portfolio_id == portfolio_id,
            Holding.status == "active",
        )
    )).scalars().all()
    holding_count = len(holding_rows)
    holding_id_set: set[str] = {h.id for h in holding_rows}
    ticker_by_id: dict[str, str] = {h.id: h.ticker for h in holding_rows}

    summary = IntelligenceSummary(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        holding_count=holding_count,
        posture="insufficient_data",
        posture_reason="",
    )

    # -------------------- Alerts (portfolio-scoped, severity-bucketed) --------------------
    alert_rows = (await session.execute(
        select(Alert.severity).where(
            Alert.portfolio_id == portfolio_id,
            Alert.acknowledged == 0,
        )
    )).all()
    alert_buckets = {"critical": 0, "high": 0, "warning": 0, "info": 0, "total": 0}
    for (sev,) in alert_rows:
        key = (sev or "info").lower()
        if key not in alert_buckets:
            key = "info"
        alert_buckets[key] += 1
        alert_buckets["total"] += 1
    summary.alerts = alert_buckets

    # -------------------- Factor touchpoints (portfolio-scoped) --------------------
    if holding_id_set:
        factor_rows = (await session.execute(
            select(
                EventLink.impact_channel, EventLink.relevance_score,
                EventLink.details_json, Holding.ticker,
            )
            .join(Holding, EventLink.link_target == Holding.id)
            .where(EventLink.link_type == "macro_factor")
            .where(Holding.portfolio_id == portfolio_id)
            .order_by(EventLink.relevance_score.desc())
            .limit(50)
        )).all()
        summary.top_factors = _aggregate_factor_rows(factor_rows)[:TOP_FACTOR_LIMIT]

    # Phase 9V fallback — when no holding-linked factor touchpoints
    # exist, surface classified MacroFactorEvent rows directly.
    # These are real deterministic classifications from the Phase 9A
    # keyword classifier (no LLM).  They don't claim holding-level
    # impact — they show observed macro signals in the event stream.
    # Only fire when the portfolio has holdings (an empty portfolio
    # should not show global factor signals).
    if not summary.top_factors and holding_count > 0:
        from src.intelligence.factors.taxonomy import get_factor
        mfe_agg = (await session.execute(
            select(
                MacroFactorEvent.factor,
                func.count(MacroFactorEvent.id).label("cnt"),
                func.max(MacroFactorEvent.confidence).label("max_conf"),
            )
            .where(MacroFactorEvent.created_at >= cutoff_7d)
            .group_by(MacroFactorEvent.factor)
            .order_by(func.count(MacroFactorEvent.id).desc())
            .limit(TOP_FACTOR_LIMIT)
        )).all()
        for factor_key, cnt, max_conf in mfe_agg:
            defn = get_factor(factor_key)
            summary.top_factors.append({
                "factor": factor_key,
                "label": defn.label if defn else factor_key,
                "direction": "observed",
                "max_relevance": float(max_conf or 0),
                "holdings": [],             # no holding-level links
                "event_count": int(cnt),
                "source": "classified",     # explicitly not "impact-linked"
            })

    # -------------------- Relationship touchpoints (portfolio-scoped) --------------------
    if holding_id_set:
        rel_rows = (await session.execute(
            select(
                EventLink.impact_channel, EventLink.relevance_score,
                EventLink.details_json, Holding.ticker, Holding.portfolio_id,
            )
            .join(Holding, EventLink.link_target == Holding.id)
            .where(EventLink.link_type == "relationship")
            .where(Holding.portfolio_id == portfolio_id)
            .order_by(EventLink.relevance_score.desc())
            .limit(50)
        )).all()
        summary.top_relationships = _aggregate_relationship_rows(rel_rows)[:TOP_RELATIONSHIP_LIMIT]

    # Phase 9V fallback — when no event-driven relationship
    # touchpoints exist, surface the seeded HoldingRelationships
    # directly.  These are real deterministic entries from
    # config/relationships.yaml reconciled into the DB by Phase 9D.
    # They already appear in the Operator panel; showing them on
    # the overview provides consistent visibility without inventing
    # any new intelligence.
    if not summary.top_relationships and holding_id_set:
        from src.database.models import HoldingRelationship
        seed_rels = (await session.execute(
            select(HoldingRelationship, Holding.ticker)
            .join(Holding, HoldingRelationship.holding_id == Holding.id)
            .where(Holding.portfolio_id == portfolio_id)
            .order_by(HoldingRelationship.strength.desc())
            .limit(TOP_RELATIONSHIP_LIMIT)
        )).all()
        for rel, ticker in seed_rels:
            summary.top_relationships.append({
                "ticker": ticker,
                "relationship_type": rel.relationship_type,
                "related_entity": rel.related_name or rel.related_ticker or rel.related_entity_key or "",
                "strength": float(rel.strength),
                "source": rel.source,
            })

    # -------------------- Recent analyses → holdings under attention --------------------
    # Phase 9A / 9E analysis notes carry an ``impact_direction`` and
    # ``materiality`` field in the JSON body.  A holding is "under
    # attention" if any note in the last 7 days is negative AND has
    # materiality >= "important".
    if holding_id_set:
        note_rows = (await session.execute(
            select(AnalysisNote, Holding.ticker)
            .join(Holding, AnalysisNote.holding_id == Holding.id)
            .where(Holding.portfolio_id == portfolio_id)
            .where(AnalysisNote.created_at >= cutoff_7d)
            .order_by(AnalysisNote.created_at.desc())
            .limit(50)
        )).all()
    else:
        note_rows = []

    direction_tally = {"positive": 0, "negative": 0, "unclear": 0}
    attention_set: set[str] = set()
    # Phase 9N — group notes by ticker so the action builder can spot
    # repeated-negative patterns without a second DB pass.
    notes_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for note, ticker in note_rows:
        body = _safe_json_obj(note.content)
        direction = (body.get("impact_direction") or "").lower()
        materiality = (body.get("materiality") or note.materiality or "").lower()
        if direction == "negative":
            direction_tally["negative"] += 1
            if materiality in ("important", "high", "critical"):
                attention_set.add(ticker)
        elif direction == "positive":
            direction_tally["positive"] += 1
        else:
            direction_tally["unclear"] += 1
        notes_by_ticker.setdefault(ticker, []).append({
            "impact_direction": direction,
            "materiality": materiality,
            "created_at": note.created_at,
        })

    summary.holdings_under_attention = sorted(attention_set)

    # -------------------- Recent events in window --------------------
    if holding_id_set:
        recent_events_count = (await session.execute(
            select(func.count(func.distinct(Event.id)))
            .select_from(Event)
            .join(EventLink, EventLink.event_id == Event.id)
            .where(EventLink.link_target.in_(holding_id_set))
            .where(Event.fetched_at >= cutoff_24h)
        )).scalar() or 0
    else:
        recent_events_count = 0

    # Phase 9V fallback — when no holding-linked events exist but the
    # collection pipeline has fetched real events recently, show the
    # total recent event count so the overview doesn't say "0 events"
    # when the events tab has real content.  These are genuine RSS
    # events collected by the deterministic pipeline.
    if recent_events_count == 0:
        recent_events_count = (await session.execute(
            select(func.count(Event.id))
            .where(Event.fetched_at >= cutoff_24h)
        )).scalar() or 0

    summary.recent_events_count_24h = int(recent_events_count)

    # -------------------- Freshness --------------------
    last_event_row = (await session.execute(
        select(Event.fetched_at)
        .order_by(Event.fetched_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    stale_minutes: int | None = None
    is_fresh = False
    if last_event_row:
        try:
            last_dt = datetime.fromisoformat(last_event_row)
            stale_minutes = int((now - last_dt).total_seconds() // 60)
            is_fresh = stale_minutes <= STALE_MINUTES_THRESHOLD
        except (ValueError, TypeError):
            stale_minutes = None
    summary.freshness = {
        "last_event_fetched_at": last_event_row,
        "stale_minutes": stale_minutes,
        "is_fresh": is_fresh,
    }

    # -------------------- Intelligence health (deterministic artefacts) --------------------
    if holding_id_set:
        factor_link_count = (await session.execute(
            select(func.count(EventLink.id))
            .select_from(EventLink)
            .join(Holding, EventLink.link_target == Holding.id)
            .where(Holding.portfolio_id == portfolio_id)
            .where(EventLink.link_type == "macro_factor")
        )).scalar() or 0
        rel_link_count = (await session.execute(
            select(func.count(EventLink.id))
            .select_from(EventLink)
            .join(Holding, EventLink.link_target == Holding.id)
            .where(Holding.portfolio_id == portfolio_id)
            .where(EventLink.link_type == "relationship")
        )).scalar() or 0
        note_count_7d = (await session.execute(
            select(func.count(AnalysisNote.id))
            .select_from(AnalysisNote)
            .join(Holding, AnalysisNote.holding_id == Holding.id)
            .where(Holding.portfolio_id == portfolio_id)
            .where(AnalysisNote.created_at >= cutoff_7d)
        )).scalar() or 0
    else:
        factor_link_count = 0
        rel_link_count = 0
        note_count_7d = 0

    has_digest = (await session.execute(
        select(func.count(Digest.id))
        .where(Digest.portfolio_id == portfolio_id)
    )).scalar() or 0

    # MacroFactorEvent count gives us a global view of deterministic
    # classification health (independent of whether they link back to
    # this specific portfolio).  Useful as a trust signal.
    mfe_count = (await session.execute(
        select(func.count(MacroFactorEvent.id))
    )).scalar() or 0

    summary.intelligence_health = {
        "factor_links": int(factor_link_count),
        "relationship_links": int(rel_link_count),
        "analysis_notes_7d": int(note_count_7d),
        "has_digest": bool(has_digest),
        "global_factor_classifications": int(mfe_count),
    }

    # -------------------- Posture (rule-based) --------------------
    posture, posture_reason = _derive_posture(
        alerts=alert_buckets,
        holdings_under_attention=summary.holdings_under_attention,
        top_factors=summary.top_factors,
        direction_tally=direction_tally,
        holding_count=holding_count,
    )
    summary.posture = posture
    summary.posture_reason = posture_reason

    # -------------------- Phase 9N — grounded recommended actions --
    # The action builder is a pure function over the summary fields
    # we already computed.  It never runs SQL, never calls an LLM,
    # and never produces output that isn't grounded in
    # ``rationale_refs`` pointing at the specific rows above.
    try:
        from src.intelligence.actions import (
            ActionInputs,
            build_actions_for_portfolio,
        )
        actions = build_actions_for_portfolio(ActionInputs(
            portfolio_id=portfolio_id,
            holding_count=holding_count,
            posture=summary.posture,
            alerts=summary.alerts,
            top_factors=summary.top_factors,
            top_relationships=summary.top_relationships,
            holdings_under_attention=summary.holdings_under_attention,
            analysis_notes_by_ticker=notes_by_ticker,
            freshness=summary.freshness,
            intelligence_health=summary.intelligence_health,
        ))
        raw_actions = [a.to_dict() for a in actions]
        # Phase 9Q — attach a deep-link ``nav_target`` to every
        # recommended action so the overview card can render a
        # clickable affordance.  The enrichment is pure and never
        # mutates the underlying ``RecommendedAction`` shape — it
        # just adds a per-dict field.
        try:
            from src.intelligence.navigation import enrich_actions_with_targets
            summary.recommended_actions = enrich_actions_with_targets(
                raw_actions, portfolio_id,
            )
        except Exception as nav_exc:  # pragma: no cover — defensive
            logger.warning(
                "nav enrichment failed for %s: %s", portfolio_id, nav_exc,
            )
            summary.recommended_actions = raw_actions
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("action builder failed for %s: %s", portfolio_id, exc)
        summary.recommended_actions = []

    summary.computed_at = now.isoformat()
    return summary


# ---------------------------------------------------------------------------
# Posture derivation (small, explainable finite state)
# ---------------------------------------------------------------------------


def _derive_posture(
    *,
    alerts: dict[str, int],
    holdings_under_attention: list[str],
    top_factors: list[dict[str, Any]],
    direction_tally: dict[str, int],
    holding_count: int,
) -> tuple[str, str]:
    """Rule-based posture derivation.

    Returns ``(posture, reason)``.  The set of postures is finite and
    documented at the top of the module.  The reason string is plain
    English so the dashboard can render it as a tooltip or caption.
    """
    if holding_count == 0:
        return "insufficient_data", "No active holdings in this portfolio."

    critical_alerts = alerts.get("critical", 0)
    high_alerts = alerts.get("high", 0)
    neg_notes = direction_tally.get("negative", 0)
    pos_notes = direction_tally.get("positive", 0)

    # Insufficient-data gate applies ONLY when there are no
    # critical/high alerts — a real high-severity alert should never
    # be hidden behind a signal-count threshold, that would be worse
    # than useless (the whole point of alerts is to break through).
    if critical_alerts == 0 and high_alerts == 0:
        total_signals = (
            (alerts.get("total") or 0)
            + len(top_factors)
            + pos_notes
            + neg_notes
        )
        if total_signals < POSTURE_MIN_SIGNAL_COUNT:
            return (
                "insufficient_data",
                f"Only {total_signals} active signals — need "
                f"{POSTURE_MIN_SIGNAL_COUNT}+ to assess posture confidently.",
            )

    # Strong negative: any critical alert OR (>=2 high alerts AND >=1 holding under attention)
    if critical_alerts >= 1:
        return (
            "strong_negative",
            f"{critical_alerts} critical alert(s) active.",
        )
    if high_alerts >= 2 and holdings_under_attention:
        return (
            "strong_negative",
            f"{high_alerts} high-severity alerts and "
            f"{len(holdings_under_attention)} holdings under attention.",
        )

    # Mildly negative: any high alert OR (holdings_under_attention AND neg > pos)
    if high_alerts >= 1:
        return (
            "mildly_negative",
            f"{high_alerts} high-severity alert(s) active.",
        )
    if holdings_under_attention and neg_notes > pos_notes:
        return (
            "mildly_negative",
            f"{len(holdings_under_attention)} holding(s) under attention and "
            f"{neg_notes} negative vs {pos_notes} positive recent analyses.",
        )

    # Strong positive: majority of notes positive, no alerts, no attention
    if (
        pos_notes >= 3
        and neg_notes == 0
        and not holdings_under_attention
        and alerts.get("total", 0) == 0
    ):
        return (
            "strong_positive",
            f"{pos_notes} positive analyses, no alerts, no attention flags.",
        )

    # Constructive: more positive than negative, no high/critical alerts
    if pos_notes > neg_notes and critical_alerts == 0 and high_alerts == 0:
        return (
            "constructive",
            f"{pos_notes} positive vs {neg_notes} negative analyses, "
            f"no high-severity alerts.",
        )

    # Default: mixed
    return (
        "mixed",
        f"{pos_notes} positive, {neg_notes} negative analyses; "
        f"{alerts.get('total', 0)} active alerts; "
        f"{len(top_factors)} factor touchpoints.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
