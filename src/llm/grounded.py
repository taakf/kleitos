"""Grounded AI synthesis layer (Phase 9E).

This module is the single place where Axion assembles LLM prompts
and context blocks that are **grounded** in the deterministic
intelligence produced by Phase 9A–9D.  It is NOT a replacement for
the LLM client — it is a layer of prompt builders + context
assemblers that the live agents, routes, and chat surface call
into.

Design principles
-----------------
1. **Deterministic data is the source of truth.**  Every prompt
   carries an explicit block describing what Axion already knows
   from the rule-based pipeline (factor classifications, factor
   tags, relationship chains, causal-chain summaries, confidence
   scores).  The LLM's job is to *narrate* that data, not
   *generate* new data.
2. **No invention.**  Every prompt carries a short, strict
   "grounding contract" at the top that forbids new factors,
   relationships, holdings, or mechanisms and tells the model to
   say "insufficient data" when a claim is unsupported.
3. **Honest uncertainty.**  When the deterministic chain records
   ``effect_direction == "unclear"``, the prompt contract tells
   the model to NOT force a directional claim.
4. **Fallback-friendly.**  Every builder returns pure Python
   data structures (prompt strings, context dicts).  When the
   LLM is unavailable, callers can use the same data structures
   to render a deterministic summary via
   :func:`render_deterministic_explanation` / related helpers.
5. **Portfolio-safe.**  Every context assembler takes a
   ``portfolio_id`` and every SQL query is scoped to it through
   the existing ``Holding.portfolio_id`` FK.  No query in this
   module reads holdings, alerts, or analysis notes without
   portfolio scoping.

Nothing in this file calls the LLM itself — it only BUILDS the
inputs.  Agents and routes own the call site.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    Alert,
    AnalysisNote,
    Digest,
    Event,
    EventLink,
    Holding,
    MacroFactorEvent,
    Security,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The grounding contract.  Prepended to every AI prompt this module
# builds.  Short on purpose — long lists of rules dilute signal.
# ---------------------------------------------------------------------------

GROUNDING_CONTRACT: str = (
    "GROUNDING CONTRACT (strict):\n"
    "- You are explaining Axion's already-computed deterministic analysis.\n"
    "- The STRUCTURED DATA block below is the only ground truth.\n"
    "- Do NOT introduce new factors, relationships, tickers, or causal mechanisms "
    "that are not present in that block.\n"
    "- Do NOT contradict the deterministic direction, magnitude, or confidence "
    "values; you may narrate or soften them in prose, never override them.\n"
    "- If a field is marked 'unclear' or missing, say so honestly; do NOT "
    "force a positive or negative claim.\n"
    "- If the data is insufficient to answer part of the question, say 'insufficient "
    "data' for that part.\n"
    "- Stay inside the active portfolio; do not mention holdings not listed in the data.\n"
)


# ---------------------------------------------------------------------------
# Grounded context dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GroundedFactorTag:
    """Minimal factor tag as the AI layer sees it.  Mirrors the
    Phase 9B event detail factor tag shape."""

    key: str
    label: str
    direction: str
    magnitude: str
    confidence: float


@dataclass
class GroundedChain:
    """A flattened, AI-safe view of a ``NormalizedChain``.

    Only the fields the model needs to narrate are carried through;
    internal bookkeeping (link ids, rationale byproducts) stays out
    of the prompt to keep tokens tight and the surface narrow.
    """

    origin: str                     # deterministic_factor | direct_match | relationship | ...
    link_type: str
    channel: str | None             # factor key, rel type, etc.
    channel_label: str | None
    holding_ticker: str | None
    effect_direction: str | None    # positive | negative | unclear | None
    effect_confidence: float | None
    rationale_summary: str          # human-readable one-liner
    factor_direction: str | None = None
    factor_magnitude: str | None = None
    related_entity: str | None = None


@dataclass
class GroundedEventContext:
    """Full grounded view of a single event + affected holding.

    The per-event LLM analysis prompt takes this dataclass as its
    only input.  Everything else is derived from it inside the
    builder — no live DB queries happen during prompt construction.
    """

    # Event
    event_id: str
    event_title: str
    event_summary: str | None = None
    event_type: str | None = None
    event_url: str | None = None
    event_published_at: str | None = None

    # Holding (portfolio-scoped)
    holding_id: str | None = None
    holding_ticker: str | None = None
    holding_portfolio_id: str | None = None
    holding_sector: str | None = None
    # ``holding_listing_country`` is the ISIN-prefix / venue-derived
    # country.  Phase 10 renames the public name (legacy alias
    # ``holding_geography`` is still set to the same value for back-
    # compat with prompts / consumers that haven't migrated).
    holding_listing_country: str | None = None
    holding_geography: str | None = None
    # Phase 10 — typed revenue-geography availability flag.
    # Never inferred from listing country.
    #   ``missing``    — no rows uploaded for this holding
    #   ``partial``    — rows exist but allocations sum <95% (or some
    #                    fiscal year is gappy)
    #   ``available``  — full upload for this holding
    # Surfaced to the LLM prompt so the model can say
    # "revenue geography is not available" instead of guessing.
    holding_revenue_geography_status: str = "missing"
    holding_revenue_breakdown: list[dict[str, Any]] = field(default_factory=list)
    holding_themes: list[str] = field(default_factory=list)

    # Deterministic intelligence already computed for this event
    factor_tags: list[GroundedFactorTag] = field(default_factory=list)
    chains: list[GroundedChain] = field(default_factory=list)


@dataclass
class GroundedDigestContext:
    """Grounded view of a digest window for the LLM digest prompt.

    Carries both the per-note summaries AND the factor touchpoints
    already computed by ``AnalysisAgent._fetch_macro_factor_touchpoints``.
    """

    period: str
    portfolio_id: str
    notes: list[dict[str, Any]] = field(default_factory=list)
    factor_touchpoints: list[dict[str, Any]] = field(default_factory=list)
    active_alerts: list[dict[str, Any]] = field(default_factory=list)
    holdings_snapshot: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GroundedChatContext:
    """Portfolio-scoped grounded context for the assistant / chat path.

    Replaces the Phase 9B ``AxionContext`` when called through the
    Phase 9E chat endpoint.  Every query in the assembler joins
    through ``Holding.portfolio_id``, so cross-portfolio leakage is
    structurally impossible.
    """

    portfolio_id: str
    holding_count: int = 0
    total_value: float = 0.0
    sector_count: int = 0
    currency_count: int = 0

    holdings: list[dict[str, Any]] = field(default_factory=list)
    active_alerts: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    analysis_highlights: list[dict[str, Any]] = field(default_factory=list)
    factor_touchpoints: list[dict[str, Any]] = field(default_factory=list)
    relationship_touchpoints: list[dict[str, Any]] = field(default_factory=list)
    latest_digest_headline: str | None = None

    llm_available: bool = False
    provider: str | None = None

    def summary_line(self) -> str:
        return (
            f"portfolio={self.portfolio_id}, "
            f"{self.holding_count} holdings, "
            f"{len(self.active_alerts)} active alerts, "
            f"{len(self.recent_events)} recent events, "
            f"{len(self.factor_touchpoints)} factor touchpoints, "
            f"{len(self.relationship_touchpoints)} relationship touchpoints"
        )


# ---------------------------------------------------------------------------
# Per-event context assembly (for the analysis agent)
# ---------------------------------------------------------------------------


async def assemble_event_context(
    session: AsyncSession,
    *,
    event_id: str,
    holding_id: str,
) -> GroundedEventContext:
    """Build a :class:`GroundedEventContext` for a single (event, holding) pair.

    Scopes all reads through the holding's own portfolio_id so the
    returned context cannot accidentally leak data from another
    portfolio.  Used by ``AnalysisAgent._call_analysis_llm``.
    """
    ev = await session.get(Event, event_id)
    if ev is None:
        raise ValueError(f"event {event_id!r} not found")

    holding = await session.get(Holding, holding_id)
    if holding is None:
        raise ValueError(f"holding {holding_id!r} not found")

    sector = None
    geography = None
    themes: list[str] = []
    sec = (await session.execute(
        select(Security).where(Security.ticker == holding.ticker)
    )).scalars().first()
    if sec:
        sector = sec.sector
        geography = sec.geography
        if sec.themes:
            try:
                parsed = json.loads(sec.themes)
                if isinstance(parsed, list):
                    themes = [str(t) for t in parsed if t]
            except (json.JSONDecodeError, TypeError):
                pass

    # Phase 10 — read revenue-geography rows for this holding.  We
    # NEVER fall back to ``geography`` (listing country) if no rows
    # exist; the typed status flag tells the prompt builder so the
    # LLM can honestly say "revenue geography has not been uploaded".
    from src.database.models import RevenueGeography
    rg_rows = (await session.execute(
        select(RevenueGeography).where(
            RevenueGeography.portfolio_id == holding.portfolio_id,
            RevenueGeography.holding_id == holding.id,
        )
    )).scalars().all()
    revenue_breakdown: list[dict[str, Any]] = []
    revenue_status: str = "missing"
    if rg_rows:
        # Pick the latest fiscal year present.
        latest_fy = max(
            (r.fiscal_year for r in rg_rows if r.fiscal_year is not None),
            default=None,
        )
        scoped = [r for r in rg_rows if (r.fiscal_year or None) == latest_fy] \
                 if latest_fy is not None else list(rg_rows)
        total_share = sum(float(r.revenue_share or 0.0) for r in scoped)
        # > 95% counts as fully allocated; below = partial.
        revenue_status = "available" if total_share >= 0.95 else "partial"
        revenue_breakdown = [
            {
                "region": r.region,
                "country": r.country,
                "revenue_share": float(r.revenue_share or 0.0),
                "fiscal_year": r.fiscal_year,
                "period": r.period,
                "source_type": r.source_type,
            }
            for r in scoped
        ]

    # Factor tags from MacroFactorEvent rows
    mfe_rows = (await session.execute(
        select(MacroFactorEvent).where(MacroFactorEvent.event_id == event_id)
    )).scalars().all()
    from src.intelligence.factors.taxonomy import get_factor as _get_factor

    factor_tags: list[GroundedFactorTag] = []
    for mfe in mfe_rows:
        defn = _get_factor(mfe.factor)
        factor_tags.append(GroundedFactorTag(
            key=mfe.factor,
            label=defn.label if defn else mfe.factor,
            direction=mfe.direction or "unknown",
            magnitude=mfe.magnitude or "unknown",
            confidence=float(mfe.confidence or 0.0),
        ))

    # Causal chains for THIS holding only — we filter at the SQL
    # layer so a wide event with many affected holdings doesn't
    # leak chains from a neighbour.
    link_rows = (await session.execute(
        select(EventLink).where(
            EventLink.event_id == event_id,
            EventLink.link_target == holding_id,
        )
    )).scalars().all()

    chains: list[GroundedChain] = []
    for lnk in link_rows:
        chains.append(_link_to_grounded_chain(lnk, holding.ticker))

    return GroundedEventContext(
        event_id=ev.id,
        event_title=ev.title or "",
        event_summary=(ev.summary or None),
        event_type=ev.event_type,
        event_url=ev.url,
        event_published_at=ev.published_at,
        holding_id=holding.id,
        holding_ticker=holding.ticker,
        holding_portfolio_id=holding.portfolio_id,
        holding_sector=sector,
        holding_listing_country=geography,
        holding_geography=geography,
        holding_revenue_geography_status=revenue_status,
        holding_revenue_breakdown=revenue_breakdown,
        holding_themes=themes,
        factor_tags=factor_tags,
        chains=chains,
    )


def _link_to_grounded_chain(
    link: EventLink, holding_ticker: str,
) -> GroundedChain:
    """Convert an ``EventLink`` row into a flat ``GroundedChain``.

    Mirrors the Phase 9B normalizer but lives here so the AI layer
    can operate on a narrow, stable subset without depending on
    internal normalizer fields.
    """
    details: dict[str, Any] = {}
    if link.details_json:
        try:
            parsed = json.loads(link.details_json)
            if isinstance(parsed, dict):
                details = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    origin = "direct_match"
    if link.link_type == "macro_factor":
        origin = "deterministic_factor"
    elif link.link_type == "relationship":
        origin = "relationship"
    elif link.link_type == "macro_screen":
        origin = "llm_screen"

    channel = link.channel or link.impact_channel
    effect = None
    confidence = None
    factor_direction = None
    factor_magnitude = None
    related_entity = None
    rationale = ""

    if details:
        effect_block = details.get("expected_effect") or {}
        if isinstance(effect_block, dict):
            effect = effect_block.get("direction")
            try:
                confidence = float(effect_block.get("confidence")) if effect_block.get("confidence") is not None else None
            except (ValueError, TypeError):
                confidence = None

        if origin == "deterministic_factor":
            f = details.get("factor") or {}
            if isinstance(f, dict):
                factor_direction = f.get("direction")
                factor_magnitude = f.get("magnitude")
                rationale_list = f.get("rationale")
                if isinstance(rationale_list, list) and rationale_list:
                    rationale = " · ".join(str(r) for r in rationale_list[:3])

        if origin == "relationship":
            ent = details.get("related_entity") or {}
            if isinstance(ent, dict):
                related_entity = (
                    ent.get("name") or ent.get("ticker") or ent.get("key")
                )
            rat_list = details.get("rationale")
            if isinstance(rat_list, list) and rat_list:
                rationale = " · ".join(str(r) for r in rat_list[:3])

    if confidence is None and link.relevance_score is not None:
        confidence = float(link.relevance_score)

    if not rationale:
        rationale = _fallback_rationale(origin, channel, holding_ticker, related_entity)

    return GroundedChain(
        origin=origin,
        link_type=link.link_type,
        channel=channel,
        channel_label=_channel_label(origin, channel),
        holding_ticker=holding_ticker,
        effect_direction=effect,
        effect_confidence=confidence,
        rationale_summary=rationale,
        factor_direction=factor_direction,
        factor_magnitude=factor_magnitude,
        related_entity=related_entity,
    )


def _channel_label(origin: str, channel: str | None) -> str | None:
    if not channel:
        return None
    if origin == "deterministic_factor":
        from src.intelligence.factors.taxonomy import get_factor as _get_factor
        defn = _get_factor(channel)
        return defn.label if defn else channel
    if origin == "relationship":
        return {
            "supplier":   "Supplier relationship",
            "customer":   "Customer relationship",
            "competitor": "Competitor",
            "regulator":  "Regulator",
            "parent":     "Parent company",
            "subsidiary": "Subsidiary",
        }.get(channel, channel.replace("_", " ").title())
    if origin == "direct_match":
        return channel.replace("_", " ").title()
    return channel


def _fallback_rationale(
    origin: str,
    channel: str | None,
    holding_ticker: str,
    related_entity: str | None,
) -> str:
    if origin == "deterministic_factor":
        return f"Deterministic {channel or 'factor'} touchpoint on {holding_ticker}"
    if origin == "relationship":
        if related_entity:
            return f"{related_entity} linked to {holding_ticker} via {channel or 'relationship'}"
        return f"{channel or 'relationship'} link to {holding_ticker}"
    if origin == "direct_match":
        return f"Direct mention of {holding_ticker}"
    return f"{origin} link to {holding_ticker}"


# ---------------------------------------------------------------------------
# Portfolio-scoped chat context assembly (replaces legacy assemble_context)
# ---------------------------------------------------------------------------


async def assemble_chat_context(
    session: AsyncSession,
    *,
    portfolio_id: str,
    max_events: int = 15,
    max_analyses: int = 10,
    max_alerts: int = 10,
) -> GroundedChatContext:
    """Assemble a portfolio-scoped chat context.

    Every list in the returned ``GroundedChatContext`` is filtered
    through the supplied ``portfolio_id``:

    * ``holdings``, ``holdings_snapshot``, ``total_value`` —
      ``Holding.portfolio_id == portfolio_id``
    * ``active_alerts`` — ``Alert.portfolio_id == portfolio_id``
    * ``analysis_highlights`` — joined via
      ``AnalysisNote.holding_id → Holding.portfolio_id``
    * ``recent_events`` / ``factor_touchpoints`` /
      ``relationship_touchpoints`` — joined via
      ``EventLink.link_target → Holding.portfolio_id`` (events are
      global; we only surface ones that actually link to THIS
      portfolio's holdings).

    This is the only way to build a chat context in Phase 9E.
    The legacy ``assemble_context`` is now a thin wrapper that
    delegates here with a resolved default portfolio.
    """
    ctx = GroundedChatContext(portfolio_id=portfolio_id)

    # LLM availability + provider (both optional, never crash the build)
    try:
        from src.llm.client import is_llm_available
        ctx.llm_available = is_llm_available()
        if ctx.llm_available:
            from src.config import get_settings
            ctx.provider = get_settings().llm.provider
    except Exception:
        ctx.llm_available = False

    # --- Holdings (portfolio-scoped) ------------------------------------
    holdings = (await session.execute(
        select(Holding)
        .where(
            Holding.status == "active",
            Holding.portfolio_id == portfolio_id,
        )
        .order_by(Holding.weight_pct.desc().nullslast())
    )).scalars().all()

    tickers = [h.ticker for h in holdings]
    sector_map: dict[str, str | None] = {}
    if tickers:
        sec_rows = (await session.execute(
            select(Security.ticker, Security.sector).where(
                Security.ticker.in_(tickers)
            )
        )).all()
        sector_map = {t: s for t, s in sec_rows}

    sectors: set[str] = set()
    currencies: set[str] = set()
    ctx.holding_count = len(holdings)
    for h in holdings:
        mv = float((h.current_price or 0) * (h.quantity or 0))
        ctx.total_value += mv
        if h.currency:
            currencies.add(h.currency)
        sec = sector_map.get(h.ticker) or "Unknown"
        if sec != "Unknown":
            sectors.add(sec)
        ctx.holdings.append({
            "id": h.id,
            "ticker": h.ticker,
            "sector": sec,
            "weight_pct": h.weight_pct or 0.0,
            "market_value": mv,
            "currency": h.currency,
        })
    ctx.sector_count = len(sectors)
    ctx.currency_count = len(currencies)

    holding_id_set: set[str] = {h["id"] for h in ctx.holdings}

    # --- Active alerts (portfolio-scoped) -------------------------------
    alert_rows = (await session.execute(
        select(Alert)
        .where(
            Alert.acknowledged == 0,
            Alert.portfolio_id == portfolio_id,
        )
        .order_by(Alert.created_at.desc())
        .limit(max_alerts)
    )).scalars().all()
    ctx.active_alerts = [
        {
            "id": a.id,
            "severity": a.severity,
            "alert_type": a.alert_type,
            "title": a.title,
        }
        for a in alert_rows
    ]

    if not holding_id_set:
        return ctx

    # --- Recent events linked to THIS portfolio's holdings --------------
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    evt_stmt = (
        select(
            Event.id, Event.title, Event.event_type, Event.materiality,
            Event.fetched_at, Event.url,
        )
        .join(EventLink, EventLink.event_id == Event.id)
        .where(EventLink.link_target.in_(holding_id_set))
        .where(Event.fetched_at >= cutoff)
        .distinct()
        .order_by(Event.fetched_at.desc())
        .limit(max_events)
    )
    ctx.recent_events = [
        {
            "id": row[0],
            "title": row[1],
            "event_type": row[2],
            "materiality": row[3],
            "fetched_at": row[4],
            "url": row[5],
        }
        for row in (await session.execute(evt_stmt)).all()
    ]

    # --- Analysis highlights (portfolio-scoped via holding) -------------
    note_rows = (await session.execute(
        select(AnalysisNote, Holding.ticker)
        .join(Holding, AnalysisNote.holding_id == Holding.id)
        .where(Holding.portfolio_id == portfolio_id)
        .order_by(AnalysisNote.created_at.desc())
        .limit(max_analyses)
    )).all()
    for note, ticker in note_rows:
        try:
            body = json.loads(note.content) if note.content else {}
        except (json.JSONDecodeError, TypeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        ctx.analysis_highlights.append({
            "id": note.id,
            "ticker": ticker,
            "note_type": note.note_type,
            "materiality": note.materiality or body.get("materiality"),
            "direction": body.get("impact_direction"),
            "short_term_outlook": body.get("short_term_outlook"),
        })

    # --- Factor touchpoints (aggregate per factor, scoped) --------------
    factor_rows = (await session.execute(
        select(
            EventLink.impact_channel, EventLink.relevance_score,
            EventLink.details_json, Holding.ticker,
        )
        .join(Holding, EventLink.link_target == Holding.id)
        .where(EventLink.link_type == "macro_factor")
        .where(Holding.portfolio_id == portfolio_id)
        .order_by(EventLink.relevance_score.desc())
        .limit(30)
    )).all()
    ctx.factor_touchpoints = _aggregate_factor_rows(factor_rows)

    # --- Relationship touchpoints (aggregate per rel type, scoped) ------
    rel_rows = (await session.execute(
        select(
            EventLink.impact_channel, EventLink.relevance_score,
            EventLink.details_json, Holding.ticker, Holding.portfolio_id,
        )
        .join(Holding, EventLink.link_target == Holding.id)
        .where(EventLink.link_type == "relationship")
        .where(Holding.portfolio_id == portfolio_id)
        .order_by(EventLink.relevance_score.desc())
        .limit(30)
    )).all()
    ctx.relationship_touchpoints = _aggregate_relationship_rows(rel_rows)

    # --- Latest digest headline (portfolio-scoped) ----------------------
    digest_row = (await session.execute(
        select(Digest)
        .where(Digest.portfolio_id == portfolio_id)
        .order_by(Digest.created_at.desc())
        .limit(1)
    )).scalars().first()
    if digest_row and digest_row.content:
        try:
            body = json.loads(digest_row.content)
            if isinstance(body, dict):
                ctx.latest_digest_headline = body.get("headline")
        except (json.JSONDecodeError, TypeError):
            pass

    return ctx


def _aggregate_factor_rows(rows: list) -> list[dict[str, Any]]:
    """Group raw factor EventLink rows by factor key."""
    from src.intelligence.factors.taxonomy import get_factor as _get_factor

    per_factor: dict[str, dict[str, Any]] = {}
    for channel, score, details, ticker in rows:
        if not channel:
            continue
        bucket = per_factor.setdefault(channel, {
            "factor": channel,
            "label": (_get_factor(channel).label if _get_factor(channel) else channel),
            "max_relevance": 0.0,
            "holdings": set(),
            "direction": None,
        })
        if score is not None and score > bucket["max_relevance"]:
            bucket["max_relevance"] = float(score)
        if ticker:
            bucket["holdings"].add(ticker)
        if details and bucket["direction"] is None:
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    factor_block = parsed.get("factor") or {}
                    if isinstance(factor_block, dict):
                        bucket["direction"] = factor_block.get("direction")
            except (json.JSONDecodeError, TypeError):
                pass

    out: list[dict[str, Any]] = []
    for b in per_factor.values():
        out.append({
            "factor": b["factor"],
            "label": b["label"],
            "direction": b["direction"] or "unknown",
            "max_relevance": round(b["max_relevance"], 4),
            "holdings": sorted(b["holdings"]),
        })
    out.sort(key=lambda r: (-r["max_relevance"], r["factor"]))
    return out


def _aggregate_relationship_rows(rows: list) -> list[dict[str, Any]]:
    """Group raw relationship EventLink rows by (holding, rel_type)."""
    per_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for channel, score, details, ticker, portfolio_id in rows:
        if not channel or not ticker:
            continue
        key = (ticker, channel)
        bucket = per_pair.setdefault(key, {
            "ticker": ticker,
            "relationship_type": channel,
            "portfolio_id": portfolio_id,
            "max_relevance": 0.0,
            "related_entity": None,
        })
        if score is not None and score > bucket["max_relevance"]:
            bucket["max_relevance"] = float(score)
        if details and bucket["related_entity"] is None:
            try:
                parsed = json.loads(details)
                if isinstance(parsed, dict):
                    ent = parsed.get("related_entity") or {}
                    if isinstance(ent, dict):
                        bucket["related_entity"] = (
                            ent.get("name") or ent.get("ticker") or ent.get("key")
                        )
            except (json.JSONDecodeError, TypeError):
                pass

    out = list(per_pair.values())
    for b in out:
        b["max_relevance"] = round(b["max_relevance"], 4)
    out.sort(key=lambda b: (-b["max_relevance"], b["ticker"], b["relationship_type"]))
    return out


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def build_event_analysis_prompt(ctx: GroundedEventContext) -> str:
    """Build a grounded per-event impact-analysis prompt.

    The prompt is a deterministic function of the
    ``GroundedEventContext`` — same input, same output.  The LLM
    is asked to return the same JSON shape the Phase 9A/9B
    ``AnalysisAgent._call_analysis_llm`` already produces.
    """
    data_lines: list[str] = []
    data_lines.append("STRUCTURED DATA (deterministic, from Axion):")
    data_lines.append(
        f"event.title          : {_trunc(ctx.event_title, 200)}"
    )
    if ctx.event_type:
        data_lines.append(f"event.type           : {ctx.event_type}")
    if ctx.event_published_at:
        data_lines.append(f"event.published_at   : {ctx.event_published_at}")
    if ctx.event_summary:
        data_lines.append(f"event.summary        : {_trunc(ctx.event_summary, 400)}")

    data_lines.append(
        f"holding.ticker       : {ctx.holding_ticker or '?'}"
    )
    data_lines.append(
        f"holding.portfolio_id : {ctx.holding_portfolio_id or '?'}"
    )
    data_lines.append(
        f"holding.sector       : {ctx.holding_sector or 'Unknown'}"
    )
    data_lines.append(
        f"holding.listing_country : {ctx.holding_listing_country or ctx.holding_geography or 'Unknown'}"
    )
    # Phase 10 — surface revenue-geography availability honestly.
    rg_status = ctx.holding_revenue_geography_status or "missing"
    if rg_status == "available" and ctx.holding_revenue_breakdown:
        regions_short = ", ".join(
            f"{r['region']}={int(round(r['revenue_share'] * 100))}%"
            for r in ctx.holding_revenue_breakdown[:5]
        )
        data_lines.append(
            f"holding.revenue_geography : {regions_short}"
        )
    elif rg_status == "partial" and ctx.holding_revenue_breakdown:
        regions_short = ", ".join(
            f"{r['region']}={int(round(r['revenue_share'] * 100))}%"
            for r in ctx.holding_revenue_breakdown[:5]
        )
        data_lines.append(
            f"holding.revenue_geography : partial — {regions_short}"
        )
    else:
        data_lines.append(
            "holding.revenue_geography : (not uploaded — "
            "do not infer from listing country)"
        )
    if ctx.holding_themes:
        data_lines.append(
            f"holding.themes       : {', '.join(ctx.holding_themes[:6])}"
        )

    if ctx.factor_tags:
        data_lines.append("deterministic_factors:")
        for tag in ctx.factor_tags[:8]:
            data_lines.append(
                f"  - {tag.key} ({tag.label}): direction={tag.direction}, "
                f"magnitude={tag.magnitude}, confidence={tag.confidence:.2f}"
            )
    else:
        data_lines.append(
            "deterministic_factors: (none — classifier found no macro factor)"
        )

    if ctx.chains:
        data_lines.append("deterministic_chains:")
        for ch in ctx.chains[:6]:
            eff = ch.effect_direction or "unclear"
            conf = (
                f"{ch.effect_confidence:.2f}" if ch.effect_confidence is not None else "—"
            )
            data_lines.append(
                f"  - origin={ch.origin}, channel={ch.channel or '?'}, "
                f"effect={eff}, confidence={conf}"
            )
            data_lines.append(f"      rationale: {_trunc(ch.rationale_summary, 160)}")
    else:
        data_lines.append(
            "deterministic_chains: (none — no link for this holding)"
        )

    schema = (
        "Return ONLY a JSON object with these fields:\n"
        "{\n"
        '  "impact_direction": "positive|negative|neutral|mixed|unclear",\n'
        '  "impact_magnitude": "high|medium|low",\n'
        '  "materiality": "noise|watch|important|critical",\n'
        '  "thesis_impact": "none|low|medium|high",\n'
        '  "earnings_impact": "none|low|medium|high",\n'
        '  "valuation_impact": "none|low|medium|high",\n'
        '  "risk_impact": "none|low|medium|high",\n'
        '  "short_term_outlook": "<1-2 sentences grounded in the data block>",\n'
        '  "long_term_outlook": "<1-2 sentences grounded in the data block>",\n'
        '  "key_factors": ["<factor_key_or_chain_channel>", ...],\n'
        '  "uncertainty_note": "<what is unclear or missing from the data>",\n'
        '  "confidence": <0.0-1.0>\n'
        "}\n"
    )
    constraint = (
        "Additional rules:\n"
        "- 'key_factors' must come ONLY from deterministic_factors.key or "
        "deterministic_chains.channel above; do NOT invent new ones.\n"
        "- If deterministic_factors is empty AND every deterministic_chains "
        "entry has effect='unclear', set impact_direction='unclear'.\n"
        "- Most events are 'noise' or 'watch' — be honest, don't over-dramatise.\n"
        "- Do not name any ticker other than the holding ticker above.\n"
    )
    return (
        GROUNDING_CONTRACT
        + "\n"
        + "\n".join(data_lines)
        + "\n\n"
        + schema
        + "\n"
        + constraint
    )


def build_digest_prompt(ctx: GroundedDigestContext) -> str:
    """Build a grounded digest prompt that carries the Phase 9A
    factor touchpoints alongside the per-note summaries."""
    lines: list[str] = []
    lines.append(
        f"STRUCTURED DATA for {ctx.period} digest (portfolio={ctx.portfolio_id}):"
    )

    if ctx.notes:
        lines.append("per_holding_notes:")
        for n in ctx.notes[:40]:
            lines.append(
                f"  - [{n.get('ticker', '?')}] "
                f"{n.get('impact_direction', '?')}/{n.get('impact_magnitude', '?')} "
                f"materiality={n.get('materiality', 'watch')} "
                f"thesis={n.get('thesis_impact', '?')} "
                f"earnings={n.get('earnings_impact', '?')} "
                f"risk={n.get('risk_impact', '?')} | "
                f"{_trunc(n.get('short_term_outlook', ''), 140)}"
            )
    else:
        lines.append("per_holding_notes: (none in this window)")

    if ctx.factor_touchpoints:
        lines.append("deterministic_factor_touchpoints:")
        for t in ctx.factor_touchpoints[:10]:
            tickers = ", ".join((t.get("affected_tickers") or [])[:6])
            lines.append(
                f"  - {t.get('factor', '?')} "
                f"({t.get('label', '')}): "
                f"direction={t.get('factor_direction', 'unknown')}, "
                f"max_magnitude={t.get('max_magnitude', 'unknown')}, "
                f"tickers=[{tickers}], "
                f"max_link_relevance={t.get('max_link_relevance', 0):.2f}"
            )
    else:
        lines.append(
            "deterministic_factor_touchpoints: (none touched this window)"
        )

    if ctx.active_alerts:
        lines.append("active_alerts:")
        for a in ctx.active_alerts[:8]:
            lines.append(
                f"  - [{a.get('severity', '?')}] {a.get('title', '?')}"
            )

    schema = (
        "Return ONLY a JSON object with these fields:\n"
        "{\n"
        '  "headline": "<one-line portfolio-level headline grounded in the data>",\n'
        '  "portfolio_assessment": "<2-3 sentences tying notes + factor touchpoints>",\n'
        '  "sector_patterns": [{"sector": "...", "signal": "positive|negative|mixed|neutral|unclear", "summary": "..."}],\n'
        '  "key_developments": ["<dev1>", "..."],\n'
        '  "risk_flags": ["<flag1>"],\n'
        '  "action_items": ["<item1>"],\n'
        '  "market_context": "<brief market backdrop grounded in data>",\n'
        '  "holdings_requiring_attention": ["<ticker1>", "..."]\n'
        "}\n"
    )
    constraint = (
        "Additional rules:\n"
        "- Every ticker you mention must appear in per_holding_notes or as a "
        "ticker listed under a factor touchpoint above.\n"
        "- Every factor you mention must appear in deterministic_factor_touchpoints.\n"
        "- If the data is thin, keep the digest short and honest; do NOT pad.\n"
    )
    return (
        GROUNDING_CONTRACT
        + "\n"
        + "\n".join(lines)
        + "\n\n"
        + schema
        + "\n"
        + constraint
    )


def build_chat_system_prompt(ctx: GroundedChatContext) -> str:
    """Build the system prompt for the assistant / chat path.

    Carries a portfolio-scoped data block — the LLM sees ONLY the
    holdings/alerts/events/factors/relationships for the active
    portfolio.  The grounding contract forbids it from mentioning
    anything else.
    """
    lines: list[str] = []
    lines.append(
        f"Active portfolio: {ctx.portfolio_id} — "
        f"{ctx.holding_count} holdings, "
        f"total value ${ctx.total_value:,.0f}, "
        f"{ctx.sector_count} sectors, {ctx.currency_count} currencies."
    )

    if ctx.holdings:
        top = ", ".join(
            f"{h['ticker']} ({h.get('weight_pct', 0):.1f}%)"
            for h in ctx.holdings[:12]
        )
        lines.append(f"Top holdings: {top}")

    if ctx.active_alerts:
        lines.append(
            "Active alerts: "
            + "; ".join(
                f"[{a.get('severity', '?')}] {a.get('title', '?')}"
                for a in ctx.active_alerts[:5]
            )
        )
    else:
        lines.append("Active alerts: none")

    if ctx.recent_events:
        lines.append("Recent events (last 7d):")
        for e in ctx.recent_events[:6]:
            mat = e.get("materiality") or "unscored"
            lines.append(f"  - [{mat}] {_trunc(e.get('title', ''), 120)}")

    if ctx.factor_touchpoints:
        lines.append("Deterministic factor touchpoints:")
        for t in ctx.factor_touchpoints[:6]:
            tickers = ", ".join(t.get("holdings") or [])
            lines.append(
                f"  - {t.get('label', t.get('factor'))} ({t.get('direction', 'unknown')})"
                f" → {tickers} [rel={t.get('max_relevance', 0):.2f}]"
            )

    if ctx.relationship_touchpoints:
        lines.append("Deterministic relationship touchpoints:")
        for r in ctx.relationship_touchpoints[:6]:
            lines.append(
                f"  - {r.get('ticker')} via {r.get('relationship_type')}"
                + (f" ({r['related_entity']})" if r.get("related_entity") else "")
                + f" [rel={r.get('max_relevance', 0):.2f}]"
            )

    if ctx.analysis_highlights:
        lines.append("Recent analysis highlights:")
        for n in ctx.analysis_highlights[:5]:
            lines.append(
                f"  - {n.get('ticker', '?')}: "
                f"{n.get('direction') or 'unclear'} / "
                f"{n.get('materiality') or 'watch'}"
            )

    if ctx.latest_digest_headline:
        lines.append(f"Latest digest: {ctx.latest_digest_headline}")

    system_rules = (
        "You are Axion, a portfolio intelligence assistant.\n"
        "\n"
        "Rules:\n"
        "- Answer using ONLY the structured data block below.\n"
        "- Never recommend buying or selling securities.\n"
        "- If the user asks about a ticker not listed above, say "
        "'that ticker is not in the active portfolio' and stop.\n"
        "- Do not invent factors, relationships, or analysis notes.\n"
        "- Quote deterministic confidence scores as given — do not round them into "
        "stronger or weaker claims.\n"
        "- Keep responses under 400 words.\n"
        "- If deterministic effect is 'unclear', say so — do not force a direction.\n"
    )

    return (
        system_rules
        + "\n"
        + GROUNDING_CONTRACT
        + "\n"
        + "STRUCTURED DATA:\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# Deterministic fallback renderers
# ---------------------------------------------------------------------------


def render_deterministic_explanation(ctx: GroundedEventContext) -> dict[str, Any]:
    """Produce a Phase 9A-shaped analysis dict purely from the
    deterministic chains + factor tags in the context.

    Used when the LLM is unavailable or has returned an error.  The
    output matches the shape of the JSON ``AnalysisAgent._call_analysis_llm``
    would have returned so downstream persistence code is unchanged.
    """
    directions: list[str] = []
    magnitudes: list[str] = []
    key_factors: list[str] = []
    rationale_bits: list[str] = []
    confidences: list[float] = []

    for tag in ctx.factor_tags:
        key_factors.append(tag.key)
        if tag.magnitude and tag.magnitude != "unknown":
            magnitudes.append(tag.magnitude)

    for ch in ctx.chains:
        if ch.channel:
            key_factors.append(ch.channel)
        if ch.effect_direction and ch.effect_direction != "unclear":
            directions.append(ch.effect_direction)
        if ch.effect_confidence is not None:
            confidences.append(ch.effect_confidence)
        if ch.rationale_summary:
            rationale_bits.append(ch.rationale_summary)

    direction = _dominant(directions) if directions else "unclear"
    magnitude = _max_magnitude(magnitudes)
    materiality = "watch"
    if direction == "negative" and magnitude in ("major", "extreme"):
        materiality = "important"
    if magnitude == "extreme":
        materiality = "important"

    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    ticker = ctx.holding_ticker or "this holding"
    if direction == "positive":
        short = f"Deterministic chains point to a positive effect on {ticker}."
    elif direction == "negative":
        short = f"Deterministic chains point to a negative effect on {ticker}."
    else:
        short = f"Deterministic chains do not yield a clear direction for {ticker}."
    if rationale_bits:
        short += " " + rationale_bits[0][:200]

    return {
        "impact_direction": direction,
        "impact_magnitude": _magnitude_to_level(magnitude),
        "materiality": materiality,
        "thesis_impact": "low",
        "earnings_impact": "low",
        "valuation_impact": "low",
        "risk_impact": "medium" if direction == "negative" else "low",
        "short_term_outlook": short,
        "long_term_outlook": (
            "No long-term projection without LLM; defer to standing thesis."
        ),
        "key_factors": list(dict.fromkeys(key_factors))[:6],
        "uncertainty_note": (
            "Deterministic fallback — narrative was not produced by an LLM; "
            "this is a structured summary of the chains."
        ),
        "confidence": round(mean_conf, 4),
    }


def render_deterministic_digest(ctx: GroundedDigestContext) -> dict[str, Any]:
    """Deterministic digest fallback grounded in factor touchpoints
    and per-note summaries.  Returned shape matches the Phase 9A LLM
    digest schema so downstream persistence is unchanged."""
    notes = ctx.notes or []
    touchpoints = ctx.factor_touchpoints or []

    direction_counts: dict[str, int] = {}
    tickers_neg: list[str] = []
    tickers_pos: list[str] = []
    for n in notes:
        d = (n.get("impact_direction") or "unclear").lower()
        direction_counts[d] = direction_counts.get(d, 0) + 1
        if d == "negative":
            tickers_neg.append(n.get("ticker", "?"))
        elif d == "positive":
            tickers_pos.append(n.get("ticker", "?"))

    total = len(notes)
    pos = direction_counts.get("positive", 0)
    neg = direction_counts.get("negative", 0)

    if pos > neg * 2:
        sentiment = "predominantly positive"
    elif neg > pos * 2:
        sentiment = "predominantly negative"
    elif pos > neg:
        sentiment = "slightly positive"
    elif neg > pos:
        sentiment = "slightly negative"
    else:
        sentiment = "mixed"

    # Phase 9V — richer headline when alerts are present but no
    # per-holding notes/touchpoints exist (common without LLM).
    # Referencing active alerts is honest: they are real deterministic
    # artefacts, not invented analysis.
    alert_count = len(ctx.active_alerts) if ctx.active_alerts else 0
    critical_count = sum(
        1 for a in (ctx.active_alerts or [])
        if (a.get("severity") or "").lower() == "critical"
    )
    high_count = sum(
        1 for a in (ctx.active_alerts or [])
        if (a.get("severity") or "").lower() == "high"
    )

    if total == 0 and touchpoints and alert_count > 0:
        sev_parts = []
        if critical_count:
            sev_parts.append(f"{critical_count} critical")
        if high_count:
            sev_parts.append(f"{high_count} high")
        sev_str = ", ".join(sev_parts) if sev_parts else f"{alert_count} active"
        headline = (
            f"{ctx.period.capitalize()} digest — "
            f"{sev_str} alert{'s' if alert_count != 1 else ''}, "
            f"{len(touchpoints)} macro signal{'s' if len(touchpoints) != 1 else ''} observed"
        )
    elif total == 0 and touchpoints:
        headline = (
            f"{ctx.period.capitalize()} digest — "
            f"{len(touchpoints)} macro signal{'s' if len(touchpoints) != 1 else ''} observed"
        )
    elif total == 0 and alert_count > 0:
        sev_parts = []
        if critical_count:
            sev_parts.append(f"{critical_count} critical")
        if high_count:
            sev_parts.append(f"{high_count} high")
        sev_str = ", ".join(sev_parts) if sev_parts else f"{alert_count} active"
        headline = (
            f"{ctx.period.capitalize()} digest — "
            f"{sev_str} alert{'s' if alert_count != 1 else ''} require attention"
        )
    elif total == 0:
        headline = f"{ctx.period.capitalize()} digest — no activity in window"
    else:
        headline = (
            f"{ctx.period.capitalize()} digest: {total} notes across "
            f"{len({n.get('ticker', '') for n in notes})} holdings — {sentiment} outlook"
        )

    key_developments: list[str] = [
        f"{pos} positive, {neg} negative, {direction_counts.get('neutral', 0)} neutral signals",
    ]

    # Add alert-based key developments when no notes/touchpoints exist
    if total == 0 and alert_count > 0:
        for a in (ctx.active_alerts or [])[:3]:
            key_developments.append(
                f"[{(a.get('severity') or 'info').upper()}] "
                f"{a.get('title', 'Alert')}"
            )
    if touchpoints:
        for t in touchpoints[:3]:
            key_developments.append(
                f"Factor touchpoint: {t.get('label', t.get('factor'))} "
                f"{t.get('factor_direction', 'unknown')} "
                f"on {', '.join((t.get('affected_tickers') or [])[:4])}"
            )

    risk_flags: list[str] = []
    if neg >= 3:
        risk_flags.append(f"Cluster of {neg} negative signals this window")
    # Phase 9V — alert-derived risk flags when no notes exist
    if not risk_flags and critical_count:
        risk_flags.append(
            f"{critical_count} critical alert{'s' if critical_count != 1 else ''} "
            f"active — review before market open"
        )
    if not risk_flags and high_count >= 2:
        risk_flags.append(
            f"{high_count} high-severity alerts active simultaneously"
        )
    if any(
        (t.get("factor_direction") == "up" and t.get("factor") == "interest_rate")
        for t in touchpoints
    ):
        risk_flags.append("Interest rates trending up — duration risk")

    # Phase 9N — populate ``action_items`` via the shared grounded
    # action builder.  The digest context already carries every input
    # the builder needs; we remap the field names to match the Phase
    # 9G intelligence summary shape so the same rule families produce
    # the same keys whether the caller is the dashboard overview or
    # the digest.
    action_items: list[str] = list(risk_flags)  # start from risk flags
    try:
        from src.intelligence.actions import (
            ActionInputs,
            build_actions_for_portfolio,
        )
        # Map digest factor touchpoints to the summary/top_factors shape.
        digest_top_factors = []
        for t in touchpoints:
            digest_top_factors.append({
                "factor": t.get("factor"),
                "label": t.get("label"),
                "direction": t.get("factor_direction"),
                "holdings": t.get("affected_tickers") or [],
            })
        # Build a per-ticker note dict from the digest notes.
        notes_by_ticker: dict[str, list[dict[str, Any]]] = {}
        for n in notes:
            t = n.get("ticker") or "?"
            notes_by_ticker.setdefault(t, []).append({
                "impact_direction": n.get("impact_direction"),
                "materiality": n.get("materiality"),
            })
        recs = build_actions_for_portfolio(ActionInputs(
            portfolio_id=ctx.portfolio_id,
            holding_count=len({n.get("ticker") for n in notes if n.get("ticker")}),
            posture="mixed",  # digest doesn't carry a posture; rule families don't depend on this directly
            alerts={},  # digest doesn't expose alerts; keep quiet to avoid double-counting the dashboard
            top_factors=digest_top_factors,
            top_relationships=[],
            holdings_under_attention=tickers_neg[:5],
            analysis_notes_by_ticker=notes_by_ticker,
        ))
        # We keep action_items as plain strings for backward compat
        # with the Phase 9E digest schema and the Phase 9G dashboard
        # renderer — the detailed structured list lives in the
        # intelligence summary surface instead.
        for r in recs:
            # Dedup against risk_flags we already surfaced
            if r.title in action_items:
                continue
            action_items.append(r.title)
    except Exception as exc:  # pragma: no cover — defensive
        import logging
        logging.getLogger(__name__).debug(
            "digest action builder failed: %s", exc,
        )

    # Build client-facing portfolio assessment text
    if total > 0:
        assessment = (
            f"{sentiment.capitalize()} tone across {total} holding-level notes and "
            f"{len(touchpoints)} macro signal{'s' if len(touchpoints) != 1 else ''}."
        )
    elif touchpoints:
        assessment = (
            f"{len(touchpoints)} macro signal{'s' if len(touchpoints) != 1 else ''} "
            f"classified from recent events."
        )
    elif alert_count > 0:
        assessment = f"{alert_count} active alert{'s' if alert_count != 1 else ''} flagged for review."
    else:
        assessment = "No significant signals in the current window."

    # Build client-facing market context
    if touchpoints:
        factor_names = [t.get("label", t.get("factor", "macro")) for t in touchpoints[:3]]
        market_ctx = f"Active signal areas: {', '.join(factor_names)}."
    else:
        market_ctx = "Monitoring active — no macro signals in the current window."

    return {
        "headline": headline,
        "portfolio_assessment": assessment,
        "sector_patterns": [],
        "key_developments": key_developments,
        "risk_flags": risk_flags,
        "action_items": action_items,
        "market_context": market_ctx,
        "holdings_requiring_attention": tickers_neg[:5],
    }


def render_deterministic_chat_answer(
    ctx: GroundedChatContext, query: str,
) -> str:
    """Rule-based chat fallback.  Returns a structured Markdown
    answer built from the grounded context.  Never invents anything."""
    parts: list[str] = []

    parts.append(
        f"**Portfolio {ctx.portfolio_id}:** {ctx.holding_count} holdings, "
        f"${ctx.total_value:,.0f} total value"
    )

    if ctx.active_alerts:
        parts.append(f"\n**Active Alerts ({len(ctx.active_alerts)}):**")
        for a in ctx.active_alerts[:5]:
            parts.append(f"  - [{a.get('severity', '?')}] {a.get('title', '?')}")

    if ctx.factor_touchpoints:
        parts.append(f"\n**Factor Touchpoints ({len(ctx.factor_touchpoints)}):**")
        for t in ctx.factor_touchpoints[:5]:
            parts.append(
                f"  - {t.get('label', t.get('factor'))} "
                f"({t.get('direction', 'unknown')}) → "
                f"{', '.join(t.get('holdings') or [])}"
            )

    if ctx.relationship_touchpoints:
        parts.append(
            f"\n**Relationship Touchpoints ({len(ctx.relationship_touchpoints)}):**"
        )
        for r in ctx.relationship_touchpoints[:5]:
            parts.append(
                f"  - {r.get('ticker')} via {r.get('relationship_type')}"
                + (f" with {r['related_entity']}" if r.get("related_entity") else "")
            )

    if ctx.recent_events:
        parts.append(f"\n**Recent Events ({len(ctx.recent_events)}):**")
        for e in ctx.recent_events[:5]:
            parts.append(f"  - {_trunc(e.get('title', ''), 100)}")

    if not ctx.llm_available:
        parts.append(
            "\n_Running in rule-based mode.  Configure an Anthropic API key "
            "for full AI responses._"
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _trunc(s: str | None, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _dominant(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return "unclear"
    best = max(counts.values())
    winners = [k for k, c in counts.items() if c == best]
    if len(winners) == 1:
        return winners[0]
    return "mixed"


_MAGNITUDE_ORDER = ("unknown", "minor", "moderate", "major", "extreme")


def _max_magnitude(values: list[str]) -> str:
    if not values:
        return "unknown"
    idx = max(
        _MAGNITUDE_ORDER.index(v) if v in _MAGNITUDE_ORDER else 0
        for v in values
    )
    return _MAGNITUDE_ORDER[idx]


def _magnitude_to_level(magnitude: str) -> str:
    return {
        "extreme": "high",
        "major": "high",
        "moderate": "medium",
        "minor": "low",
        "unknown": "low",
    }.get(magnitude, "low")
