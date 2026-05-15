"""Phase 12 — Deterministic insight generator.

Combines stored facts (Phase 8 News, Phase 9 Corporate Events, Phase
9D Relationships, Phase 9A Factor classifications, Phase 10–11
Revenue Geography, Phase 7 Source Health, Phase 9N Recommended
Actions, plain Alerts) into a ranked list of :class:`InsightCard`
instances.

The generator is **pure**: it takes an :class:`AsyncSession`,
performs read-only queries, returns models.  No writes, no side
effects.  Empty-state safe — when there's nothing to say it emits a
small set of helpful onboarding/data-gap cards instead of going
silent.

Ranking
-------
Cards are sorted by ``rank`` ascending.  Lower rank = more
important.  The rank is computed from severity + category so:

* ``critical`` always wins;
* direct holding impact (news / event / alert / concentration)
  beats generic data gaps at the same severity;
* a card with recent evidence beats an older card.

We never invent a severity to inflate ranking — the severity is
always derived from the source rows.

Tuning knobs
------------
A handful of small thresholds, kept named + commented so a future
operator can tune them without rereading the entire generator.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Alert,
    CorporateEvent,
    Event,
    EventLink,
    Holding,
    Portfolio,
    Source,
)
from src.intelligence.insights.models import (
    InsightCard,
    InsightDeepLink,
    InsightEvidence,
    InsightsCoverage,
    InsightsResponse,
)
from src.intelligence.listing import is_athex_listed
from src.intelligence.revenue_geography import (
    compute_portfolio_revenue_exposure,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Tuning knobs
# ─────────────────────────────────────────────────────────────────────

#: Window for "recent news" insights.
NEWS_RECENT_DAYS = 7

#: Window for upcoming corporate events.
EVENTS_LOOKAHEAD_DAYS = 30

#: A holding above this weight (%) without a revenue-geography row is
#: a notable data gap.
MISSING_REVENUE_WEIGHT_FLOOR = 5.0

#: Listing-country concentration threshold above which we emit a
#: card.  Kept above the existing risk-agent threshold so we don't
#: duplicate alerts.
LISTING_CONCENTRATION_PCT = 30.0

#: Number of corporate events to surface per portfolio.
MAX_EVENT_CARDS = 5

#: Number of news cards to surface per portfolio.
MAX_NEWS_CARDS = 5

#: Default rank floor — used as fallback for cards that don't get a
#: more specific rank from the helpers below.
RANK_FLOOR = 1000


# ─────────────────────────────────────────────────────────────────────
# Severity → rank base
# ─────────────────────────────────────────────────────────────────────

_SEVERITY_BASE: dict[str, int] = {
    "critical": 0,
    "high":     100,
    "medium":   200,
    "low":      300,
    "info":     400,
}

#: Category nudge — direct-impact cards rank ahead of data gaps at
#: the same severity.  Numbers chosen so that a data-gap "high" still
#: outranks a "medium" alert (data gaps deserve attention) but trails
#: a direct-impact "high".
_CATEGORY_NUDGE: dict[str, int] = {
    "alert":              0,
    "news_impact":        5,
    "corporate_event":    10,
    "concentration":      15,
    "factor_sensitivity": 20,
    "relationship_chain": 25,
    "revenue_geography":  30,
    "listing_country":    35,
    "data_gap":           50,
}


def _rank(severity: str, category: str, age_days: int = 0) -> int:
    base = _SEVERITY_BASE.get(severity, 500)
    nudge = _CATEGORY_NUDGE.get(category, 40)
    # Age penalty: 1 rank per day, capped at 30, so 7-day news beats
    # 14-day news.  Future-dated events (corporate calendar) use
    # negative age — soonest wins.
    return base + nudge + max(min(age_days, 90), -90)


def _new_id() -> str:
    return f"ins_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _safe_age_days(iso_str: str | None) -> int:
    if not iso_str:
        return 0
    try:
        when = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0, int((_now() - when).total_seconds() // 86400))
    except (ValueError, TypeError):
        return 0


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


async def build_insights(
    session: AsyncSession,
    *,
    portfolio_id: str,
    limit: int = 12,
    category: str | None = None,
    severity: str | None = None,
    time_window_days: int | None = None,
) -> InsightsResponse:
    """Generate a ranked, deterministic list of insight cards.

    Never raises — on any backend hiccup it returns an empty
    response with a warning so the dashboard always has something to
    render.
    """
    try:
        return await _build(
            session,
            portfolio_id=portfolio_id,
            limit=limit,
            category=category,
            severity=severity,
            time_window_days=time_window_days,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "insights build failed for %s: %r", portfolio_id, exc,
        )
        return InsightsResponse(
            portfolio_id=portfolio_id,
            portfolio_name=None,
            grounding_status="deterministic_only",
            insights=[],
            coverage=InsightsCoverage(),
            warnings=[f"insights build failed: {type(exc).__name__}"],
            total=0,
            limit=limit,
        )


async def _build(
    session: AsyncSession,
    *,
    portfolio_id: str,
    limit: int,
    category: str | None,
    severity: str | None,
    time_window_days: int | None,
) -> InsightsResponse:
    portfolio = await session.get(Portfolio, portfolio_id)
    portfolio_name = portfolio.name if portfolio else None

    now = _now()
    holdings = list((await session.execute(
        select(Holding).where(
            Holding.portfolio_id == portfolio_id,
            Holding.status == "active",
        )
    )).scalars().all())
    ticker_by_holding_id = {h.id: h.ticker for h in holdings}

    cards: list[InsightCard] = []

    # ── data-gap onboarding cards (shown low priority) ───────────────
    cards.extend(_data_gap_no_holdings(portfolio_id, holdings))

    # ── alerts → top-priority cards ──────────────────────────────────
    cards.extend(await _cards_from_alerts(session, portfolio_id=portfolio_id))

    # ── news-impact (recent linked news) ─────────────────────────────
    cards.extend(await _cards_from_news(
        session, portfolio_id=portfolio_id,
        holdings=holdings, now=now,
    ))

    # ── corporate events (upcoming) ──────────────────────────────────
    cards.extend(await _cards_from_corporate_events(
        session, portfolio_id=portfolio_id,
        holdings=holdings, now=now,
    ))

    # ── relationship / factor touchpoints ────────────────────────────
    cards.extend(await _cards_from_factors(
        session, portfolio_id=portfolio_id,
        holdings=holdings, ticker_by_holding_id=ticker_by_holding_id,
    ))

    # ── revenue geography (data + gaps) ──────────────────────────────
    rev_geo = await _cards_from_revenue_geography(
        session, portfolio_id=portfolio_id, holdings=holdings,
    )
    cards.extend(rev_geo["cards"])

    # ── listing-country concentration ────────────────────────────────
    cards.extend(_cards_from_listing(
        portfolio_id=portfolio_id, holdings=holdings,
    ))

    # ── source-health / AI-config data gaps ──────────────────────────
    source_health = await _summarise_source_health(session)
    ai_available = _is_ai_available()
    cards.extend(_data_gap_config_cards(
        portfolio_id=portfolio_id,
        source_health=source_health, ai_available=ai_available,
    ))

    # ── coverage panel (always built; never lies) ────────────────────
    coverage = await _build_coverage(
        session, portfolio_id=portfolio_id, holdings=holdings,
        rev_geo_status=rev_geo["status"],
        rev_geo_holdings_with_data=rev_geo["holdings_with_data"],
        source_health=source_health, ai_available=ai_available,
        now=now,
    )

    # ── apply filters + sort + slice ─────────────────────────────────
    filtered = cards
    if category:
        filtered = [c for c in filtered if c.category == category]
    if severity:
        filtered = [c for c in filtered if c.severity == severity]
    if time_window_days:
        cutoff_iso = _iso(now - timedelta(days=time_window_days))
        filtered = [c for c in filtered if c.created_at >= cutoff_iso]

    filtered_sorted = sorted(filtered, key=lambda c: (c.rank, c.title))
    total = len(filtered_sorted)
    sliced = filtered_sorted[: max(1, int(limit))] if limit else filtered_sorted

    return InsightsResponse(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        grounding_status="deterministic_only",
        insights=sliced,
        coverage=coverage,
        warnings=[],
        total=total,
        limit=limit,
    )


# ─────────────────────────────────────────────────────────────────────
# Card builders
# ─────────────────────────────────────────────────────────────────────


def _data_gap_no_holdings(
    portfolio_id: str, holdings: list[Holding],
) -> list[InsightCard]:
    if holdings:
        return []
    return [
        InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="info",
            category="data_gap",
            title="No holdings imported yet",
            summary=(
                "Import a portfolio CSV or add holdings manually to "
                "unlock insights, alerts, and exposure breakdowns."
            ),
            why_it_matters=(
                "Insights are computed from your portfolio's holdings. "
                "Without any holdings, every other surface stays empty."
            ),
            evidence=[
                InsightEvidence(
                    kind="holding", ref=f"portfolio:{portfolio_id}",
                    label="Portfolio is empty",
                    detail="0 active holdings",
                ),
            ],
            recommended_action="Open Portfolio → Holdings → + Add or Upload",
            data_gaps=["holdings_missing"],
            deep_links=[
                InsightDeepLink(
                    surface="portfolio", subtab="holdings",
                    label="Open Holdings",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("info", "data_gap"),
        ),
    ]


async def _cards_from_alerts(
    session: AsyncSession, *, portfolio_id: str,
) -> list[InsightCard]:
    rows = (await session.execute(
        select(Alert).where(
            Alert.portfolio_id == portfolio_id,
            Alert.acknowledged == 0,
        ).order_by(Alert.created_at.desc()).limit(20)
    )).scalars().all()
    out: list[InsightCard] = []
    for a in rows:
        sev = (a.severity or "info").lower()
        if sev not in ("critical", "high", "medium", "warning", "low", "info"):
            sev = "info"
        if sev == "warning":
            sev = "medium"
        age = _safe_age_days(a.created_at)
        body = getattr(a, "body", None)
        out.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity=sev,                          # type: ignore[arg-type]
            category="alert",
            title=a.title or f"Alert: {a.alert_type}",
            summary=body or a.title or "Open alert.",
            why_it_matters=(
                "Alerts surface issues the deterministic risk engine "
                "or operator flagged as needing review."
            ),
            evidence=[
                InsightEvidence(
                    kind="alert", ref=f"alert:{a.id}",
                    label=f"Alert · {sev}",
                    detail=a.title,
                ),
            ],
            recommended_action="Open Alerts to review or acknowledge.",
            deep_links=[
                InsightDeepLink(
                    surface="alerts", entity_type="alert", entity_id=a.id,
                    label="Open alert",
                ),
            ],
            created_at=a.created_at or _iso(_now()),
            rank=_rank(sev, "alert", age),
        ))
    return out


async def _cards_from_news(
    session: AsyncSession,
    *,
    portfolio_id: str,
    holdings: list[Holding],
    now: datetime,
) -> list[InsightCard]:
    if not holdings:
        return []
    cutoff = _iso(now - timedelta(days=NEWS_RECENT_DAYS))
    holding_id_set = {h.id for h in holdings}
    rows = (await session.execute(
        select(
            Event.id, Event.title, Event.materiality, Event.confidence,
            Event.event_type, Event.published_at, Event.source_id,
            EventLink.link_target,
        )
        .join(EventLink, EventLink.event_id == Event.id)
        .where(
            EventLink.link_target.in_(holding_id_set),
            Event.fetched_at >= cutoff,
        )
        .order_by(Event.published_at.desc().nulls_last())
        .limit(60)
    )).all()

    # Aggregate per event so an event affecting 3 holdings is one card.
    by_event: dict[str, dict[str, Any]] = {}
    ticker_by_id = {h.id: h.ticker for h in holdings}
    for ev_id, title, materiality, confidence, event_type, pub, src_id, link_target in rows:
        bucket = by_event.setdefault(ev_id, {
            "title": title,
            "materiality": (materiality or "unscored").lower(),
            "confidence": (confidence or "unscored").lower(),
            "event_type": event_type,
            "published_at": pub,
            "source_id": src_id,
            "tickers": set(),
        })
        if link_target in ticker_by_id:
            bucket["tickers"].add(ticker_by_id[link_target])

    cards: list[InsightCard] = []
    for ev_id, data in by_event.items():
        materiality = data["materiality"]
        if materiality in ("critical",):
            sev = "critical"
        elif materiality in ("high", "important"):
            sev = "high"
        elif materiality in ("watch",):
            sev = "medium"
        elif materiality in ("immaterial",):
            sev = "low"
        else:
            sev = "info"
        age = _safe_age_days(data["published_at"])
        tickers = sorted(data["tickers"])
        title = (data["title"] or "News item").strip()
        ticker_clause = (
            f" · affects {', '.join(tickers[:3])}"
            + ("…" if len(tickers) > 3 else "")
        ) if tickers else ""
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity=sev,                          # type: ignore[arg-type]
            category="news_impact",
            title=title[:140],
            summary=(
                f"Materiality: {materiality}; affects "
                f"{len(tickers) or 0} holding(s){ticker_clause}."
            ),
            why_it_matters=(
                "Axion classified this news as material to at least "
                "one of your holdings."
            ),
            affected_holdings=tickers,
            evidence=[
                InsightEvidence(
                    kind="news", ref=f"event:{ev_id}",
                    label=f"News · {materiality}",
                    detail=title[:160],
                ),
            ] + [
                InsightEvidence(
                    kind="holding", ref=f"holding-ticker:{t}",
                    label=t, detail=None,
                )
                for t in tickers[:5]
            ],
            recommended_action=(
                "Open the News item to review the deterministic factor "
                "tags, affected holdings, and causal chains."
            ),
            deep_links=[
                InsightDeepLink(
                    surface="events", subtab="events",
                    entity_type="event", entity_id=ev_id,
                    label="Open News item",
                ),
            ],
            created_at=data["published_at"] or _iso(_now()),
            rank=_rank(sev, "news_impact", age),
        ))

    cards.sort(key=lambda c: c.rank)
    return cards[:MAX_NEWS_CARDS]


async def _cards_from_corporate_events(
    session: AsyncSession,
    *,
    portfolio_id: str,
    holdings: list[Holding],
    now: datetime,
) -> list[InsightCard]:
    today = now.date().isoformat()
    end = (now + timedelta(days=EVENTS_LOOKAHEAD_DAYS)).date().isoformat()
    rows = (await session.execute(
        select(CorporateEvent)
        .where(
            CorporateEvent.portfolio_id == portfolio_id,
            CorporateEvent.event_date >= today,
            CorporateEvent.event_date <= end,
        )
        .order_by(CorporateEvent.event_date.asc())
        .limit(20)
    )).scalars().all()

    cards: list[InsightCard] = []
    unmatched: list[CorporateEvent] = []
    for ev in rows:
        # Days until event — negative age = sooner (higher importance).
        try:
            ev_dt = datetime.fromisoformat(f"{ev.event_date}T00:00:00+00:00")
            days_until = max(0, int((ev_dt - now).total_seconds() // 86400))
        except ValueError:
            days_until = 0
        if ev.holding_id is None and ev.ticker is None:
            continue
        if ev.holding_id is None:
            unmatched.append(ev)
        sev = "high" if days_until <= 3 else "medium" if days_until <= 14 else "low"
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity=sev,                          # type: ignore[arg-type]
            category="corporate_event",
            title=f"{ev.event_type.title()} — {ev.title}"[:140],
            summary=(
                f"{ev.event_type} for {ev.ticker or ev.isin or 'holding'} "
                f"on {ev.event_date}"
                f"{' · unmatched (no holding in this portfolio)' if ev.holding_id is None else ''}"
            ),
            why_it_matters=(
                "Upcoming corporate / issuer event in your calendar window."
            ),
            affected_holdings=[ev.ticker] if ev.ticker else [],
            evidence=[
                InsightEvidence(
                    kind="corporate_event",
                    ref=f"corporate_event:{ev.id}",
                    label=f"{ev.event_type} · {ev.event_date}",
                    detail=ev.title,
                ),
            ],
            recommended_action="Open the Events tab to inspect the calendar.",
            deep_links=[
                InsightDeepLink(
                    surface="corporate-events",
                    entity_type="corporate_event", entity_id=ev.id,
                    label="Open in Events",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank(sev, "corporate_event", -days_until),
        ))

    if unmatched:
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="medium",
            category="data_gap",
            title=f"{len(unmatched)} corporate event(s) unmatched to holdings",
            summary=(
                "These imported corporate events did not match any "
                "active holding by ISIN or ticker."
            ),
            why_it_matters=(
                "Unmatched events stay in the calendar but cannot "
                "drive holding-specific actions."
            ),
            evidence=[
                InsightEvidence(
                    kind="corporate_event",
                    ref=f"corporate_event:{ev.id}",
                    label=f"{ev.event_type} · {ev.event_date}",
                    detail=ev.title,
                )
                for ev in unmatched[:5]
            ],
            data_gaps=["corporate_events_unmatched"],
            deep_links=[
                InsightDeepLink(
                    surface="corporate-events",
                    label="Review unmatched events",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("medium", "data_gap"),
        ))

    cards.sort(key=lambda c: c.rank)
    return cards[:MAX_EVENT_CARDS + 1]


async def _cards_from_factors(
    session: AsyncSession,
    *,
    portfolio_id: str,
    holdings: list[Holding],
    ticker_by_holding_id: dict[str, str],
) -> list[InsightCard]:
    if not holdings:
        return []
    holding_id_set = set(ticker_by_holding_id.keys())
    factor_rows = (await session.execute(
        select(
            EventLink.event_id,
            EventLink.relevance_score,
            EventLink.channel,
            EventLink.link_target,
        )
        .where(
            EventLink.link_type == "macro_factor",
            EventLink.link_target.in_(holding_id_set),
        )
        .order_by(EventLink.relevance_score.desc().nulls_last())
        .limit(40)
    )).all()
    # Aggregate by factor channel.
    by_factor: dict[str, dict[str, Any]] = {}
    for ev_id, score, channel, link_target in factor_rows:
        if not channel:
            continue
        bucket = by_factor.setdefault(channel, {
            "tickers": set(),
            "max_score": 0.0,
            "event_id": ev_id,
        })
        if link_target in ticker_by_holding_id:
            bucket["tickers"].add(ticker_by_holding_id[link_target])
        if (score or 0) > bucket["max_score"]:
            bucket["max_score"] = float(score or 0)
            bucket["event_id"] = ev_id

    cards: list[InsightCard] = []
    for factor_key, data in sorted(
        by_factor.items(), key=lambda kv: -kv[1]["max_score"],
    )[:3]:
        max_score = data["max_score"]
        sev = "high" if max_score >= 0.5 else "medium" if max_score >= 0.3 else "low"
        tickers = sorted(data["tickers"])[:5]
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity=sev,                          # type: ignore[arg-type]
            category="factor_sensitivity",
            title=f"Macro factor touchpoint: {factor_key}",
            summary=(
                f"{len(tickers)} holding(s) touched by {factor_key}; "
                f"max relevance {max_score:.2f}."
            ),
            why_it_matters=(
                "Deterministic factor classifier linked recent news "
                "events to holdings via this factor channel."
            ),
            affected_holdings=tickers,
            evidence=[
                InsightEvidence(
                    kind="factor", ref=f"factor:{factor_key}",
                    label=f"Factor · {factor_key}",
                    detail=f"max relevance {max_score:.2f}",
                ),
            ] + [
                InsightEvidence(
                    kind="holding", ref=f"holding-ticker:{t}",
                    label=t,
                )
                for t in tickers
            ],
            deep_links=[
                InsightDeepLink(
                    surface="operator", subtab="factors",
                    entity_type="factor_override",
                    label="Open factor table",
                    filters={"factor": factor_key},
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank(sev, "factor_sensitivity"),
        ))
    return cards


async def _cards_from_revenue_geography(
    session: AsyncSession,
    *,
    portfolio_id: str,
    holdings: list[Holding],
) -> dict[str, Any]:
    """Build revenue-geography cards AND return summary for coverage panel."""
    if not holdings:
        return {"cards": [], "status": "missing", "holdings_with_data": 0}
    report = await compute_portfolio_revenue_exposure(
        session, portfolio_id=portfolio_id,
    )
    cards: list[InsightCard] = []
    if report.status == "missing":
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="medium",
            category="data_gap",
            title="No revenue geography uploaded yet",
            summary=(
                "Listing country is shown as a separate exposure; "
                "Axion never infers revenue geography from listing."
            ),
            why_it_matters=(
                "Revenue geography is the cleanest signal for "
                "where a company actually earns money."
            ),
            evidence=[
                InsightEvidence(
                    kind="config",
                    ref=f"revenue_geography:{portfolio_id}",
                    label="Status: missing",
                ),
            ],
            recommended_action=(
                "Open Portfolio → Exposures → Revenue geography → "
                "Import CSV (or use AI extraction)."
            ),
            data_gaps=["revenue_geography_missing"],
            deep_links=[
                InsightDeepLink(
                    surface="portfolio", subtab="exposures",
                    label="Open Revenue geography",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("medium", "data_gap"),
        ))
    elif report.status == "partial":
        # Surface missing-holdings as a single gap card, weighted by
        # the largest uncovered position.
        missing = report.missing_holdings
        biggest = max(missing, key=lambda m: m.get("weight_pct", 0), default=None)
        sev = "high" if biggest and biggest["weight_pct"] >= MISSING_REVENUE_WEIGHT_FLOOR else "medium"
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity=sev,                          # type: ignore[arg-type]
            category="revenue_geography",
            title=(
                f"Revenue geography partial — "
                f"{len(missing)} holding(s) missing"
            ),
            summary=(
                f"{report.holdings_with_data} of "
                f"{report.holdings_with_data + report.holdings_without_data} "
                "holdings have a revenue-geography breakdown uploaded."
            ),
            why_it_matters=(
                "Holdings without uploads flow into the "
                "'Revenue geography not uploaded' bucket on the chart."
            ),
            affected_holdings=[m["ticker"] for m in missing[:5]],
            evidence=[
                InsightEvidence(
                    kind="revenue_geography",
                    ref=f"holding:{m['holding_id']}",
                    label=f"{m['ticker']}",
                    detail=f"weight {m['weight_pct']:.1f}%",
                )
                for m in missing[:5]
            ] + [
                InsightEvidence(
                    kind="config",
                    ref=f"revenue_geography:{portfolio_id}",
                    label="Status: partial",
                ),
            ],
            recommended_action="Import or extract a CSV for the missing tickers.",
            data_gaps=["revenue_geography_partial"],
            deep_links=[
                InsightDeepLink(
                    surface="portfolio", subtab="exposures",
                    label="Open Revenue geography",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank(sev, "revenue_geography"),
        ))
    elif report.status == "available":
        # Top region card — explicitly evidence-backed, never invented.
        top_region = next(
            (b for b in report.buckets if b.region not in (
                "Revenue geography not uploaded", "Other / unallocated",
            )),
            None,
        )
        if top_region and top_region.weight_pct > 0:
            sev = "high" if top_region.weight_pct >= 50 else "medium" if top_region.weight_pct >= 30 else "low"
            cards.append(InsightCard(
                id=_new_id(),
                portfolio_id=portfolio_id,
                severity=sev,                      # type: ignore[arg-type]
                category="revenue_geography",
                title=(
                    f"Top revenue region: {top_region.region} "
                    f"({top_region.weight_pct:.1f}%)"
                ),
                summary=(
                    f"{top_region.holding_count} holding(s) drive "
                    f"{top_region.weight_pct:.1f}% of revenue exposure "
                    f"to {top_region.region}."
                ),
                why_it_matters=(
                    "Revenue concentration in one region is a separate "
                    "consideration from listing-country concentration."
                ),
                affected_holdings=list(top_region.tickers[:5]),
                evidence=[
                    InsightEvidence(
                        kind="revenue_geography",
                        ref=f"region:{top_region.region}",
                        label=top_region.region,
                        detail=f"{top_region.weight_pct:.1f}% across {top_region.holding_count} holding(s)",
                    ),
                ],
                deep_links=[
                    InsightDeepLink(
                        surface="portfolio", subtab="exposures",
                        label="Open Revenue geography",
                    ),
                ],
                created_at=_iso(_now()),
                rank=_rank(sev, "revenue_geography"),
            ))

    return {
        "cards": cards,
        "status": report.status,
        "holdings_with_data": report.holdings_with_data,
    }


def _cards_from_listing(
    *, portfolio_id: str, holdings: list[Holding],
) -> list[InsightCard]:
    if not holdings:
        return []
    by_country: dict[str, dict[str, Any]] = {}
    athex_tickers: list[str] = []
    for h in holdings:
        # Listing detector — never claims revenue.
        if is_athex_listed(h):
            athex_tickers.append(h.ticker)
        # Use the same simple aggregation the existing exposures card
        # does — but at the country level so we get a high-signal card.
        prefix = (h.isin or "")[:2].upper() if h.isin else None
        country = "Greece" if prefix == "GR" else (prefix or "Unknown")
        bucket = by_country.setdefault(country, {"weight": 0.0, "tickers": []})
        bucket["weight"] += float(h.weight_pct or 0)
        bucket["tickers"].append(h.ticker)

    cards: list[InsightCard] = []
    # Concentration card — only above the threshold.
    if by_country:
        top = max(by_country.items(), key=lambda kv: kv[1]["weight"])
        country, data = top
        if data["weight"] >= LISTING_CONCENTRATION_PCT:
            sev = "high" if data["weight"] >= 60 else "medium"
            cards.append(InsightCard(
                id=_new_id(),
                portfolio_id=portfolio_id,
                severity=sev,                      # type: ignore[arg-type]
                category="listing_country",
                title=f"Listing concentration: {country} ({data['weight']:.1f}%)",
                summary=(
                    f"{len(data['tickers'])} holdings listed in "
                    f"{country} account for {data['weight']:.1f}% of "
                    "the portfolio. This is listing exposure, not "
                    "revenue geography."
                ),
                why_it_matters=(
                    "A heavy listing footprint can correlate with "
                    "regulatory + FX exposure, but it does NOT imply "
                    "the underlying companies earn there."
                ),
                affected_holdings=sorted(data["tickers"])[:5],
                evidence=[
                    InsightEvidence(
                        kind="listing", ref=f"listing-country:{country}",
                        label=country,
                        detail=f"{data['weight']:.1f}% across {len(data['tickers'])} holding(s)",
                    ),
                ],
                deep_links=[
                    InsightDeepLink(
                        surface="portfolio", subtab="exposures",
                        label="Open Listing country",
                    ),
                ],
                created_at=_iso(_now()),
                rank=_rank(sev, "listing_country"),
            ))

    # ATHEX-listed callout (separate from concentration so even small
    # weights surface for Greek-listed holdings; the Phase 9 corporate-
    # events surface depends on this detection).
    if athex_tickers:
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="info",
            category="listing_country",
            title=f"{len(athex_tickers)} ATHEX-listed holding(s)",
            summary=(
                "Listing detector flagged these as Greek/ATHEX-listed. "
                "The Events tab can show their corporate calendar once "
                "rows are imported."
            ),
            why_it_matters=(
                "ATHEX listings unlock the Phase 9 corporate-events "
                "calendar. Revenue geography stays separate."
            ),
            affected_holdings=sorted(athex_tickers)[:5],
            evidence=[
                InsightEvidence(
                    kind="listing", ref=f"holding-ticker:{t}",
                    label=t,
                )
                for t in sorted(athex_tickers)[:5]
            ],
            deep_links=[
                InsightDeepLink(
                    surface="corporate-events",
                    label="Open Events",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("info", "listing_country"),
        ))
    return cards


async def _summarise_source_health(session: AsyncSession) -> dict[str, int]:
    """Return ``{total, active, missing_key, unsupported, …}``.

    Cheap — Phase 7 stores the last status on each Source row.  We
    aggregate without instantiating the registry.
    """
    rows = (await session.execute(select(Source.last_status))).all()
    out: dict[str, int] = {"total": 0}
    for (status,) in rows:
        out["total"] += 1
        key = (status or "unknown").lower()
        out[key] = out.get(key, 0) + 1
    return out


def _is_ai_available() -> bool:
    try:
        from src.llm.client import is_llm_available
        return bool(is_llm_available())
    except Exception:  # pragma: no cover — defensive
        return False


def _data_gap_config_cards(
    *, portfolio_id: str,
    source_health: dict[str, int], ai_available: bool,
) -> list[InsightCard]:
    cards: list[InsightCard] = []

    missing_key = source_health.get("missing_key", 0)
    if missing_key:
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="low",
            category="data_gap",
            title=f"{missing_key} optional source(s) waiting for an API key",
            summary=(
                "Optional API-key news sources (NewsAPI / Finnhub) are "
                "available but no key is configured. Public RSS feeds "
                "continue to work."
            ),
            why_it_matters=(
                "More sources = better signal coverage. Optional only — "
                "Insights does not require any of these."
            ),
            evidence=[
                InsightEvidence(
                    kind="source", ref="settings:sources",
                    label=f"{missing_key} sources · missing_key",
                ),
            ],
            recommended_action="Open Settings → News Sources to set keys.",
            data_gaps=["source_keys_missing"],
            deep_links=[
                InsightDeepLink(
                    surface="settings", subtab="sources",
                    label="Open News Sources",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("low", "data_gap"),
        ))

    if not ai_available:
        cards.append(InsightCard(
            id=_new_id(),
            portfolio_id=portfolio_id,
            severity="low",
            category="data_gap",
            title="AI narrator is optional and currently not configured",
            summary=(
                "Insights work fully without AI. Configure an AI key "
                "in Settings → AI Configuration to get AI-narrated "
                "summaries of the deterministic cards."
            ),
            why_it_matters=(
                "AI is grounded-only — it summarises the same evidence "
                "shown here. The deterministic view is always available."
            ),
            evidence=[
                InsightEvidence(
                    kind="config", ref="settings:ai",
                    label="AI provider · not configured",
                ),
            ],
            recommended_action="Open Settings → AI Configuration if desired.",
            data_gaps=["ai_provider_missing"],
            deep_links=[
                InsightDeepLink(
                    surface="settings", subtab="ai",
                    label="Open AI configuration",
                ),
            ],
            created_at=_iso(_now()),
            rank=_rank("low", "data_gap"),
        ))
    return cards


async def _build_coverage(
    session: AsyncSession,
    *,
    portfolio_id: str,
    holdings: list[Holding],
    rev_geo_status: str,
    rev_geo_holdings_with_data: int,
    source_health: dict[str, int],
    ai_available: bool,
    now: datetime,
) -> InsightsCoverage:
    cutoff_7d = _iso(now - timedelta(days=7))
    news_count = int((await session.execute(
        select(func.count(Event.id)).where(Event.fetched_at >= cutoff_7d)
    )).scalar_one() or 0)
    event_count = int((await session.execute(
        select(func.count(CorporateEvent.id)).where(
            CorporateEvent.portfolio_id == portfolio_id,
            CorporateEvent.event_date >= now.date().isoformat(),
            CorporateEvent.event_date <= (now + timedelta(days=30)).date().isoformat(),
        )
    )).scalar_one() or 0)
    alert_count = int((await session.execute(
        select(func.count(Alert.id)).where(
            Alert.portfolio_id == portfolio_id,
            Alert.acknowledged == 0,
        )
    )).scalar_one() or 0)
    last_news = (await session.execute(
        select(func.max(Event.fetched_at))
    )).scalar()

    return InsightsCoverage(
        holding_count=len(holdings),
        news_count_7d=news_count,
        corporate_event_count_30d=event_count,
        active_alert_count=alert_count,
        revenue_geography_status=rev_geo_status,
        revenue_geography_uploaded_holdings=rev_geo_holdings_with_data,
        source_health=source_health,
        ai_provider_available=ai_available,
        last_news_fetched_at=last_news,
    )
