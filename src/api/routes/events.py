"""Event routes for Axion API.

Phase 9B: the event detail endpoint is the primary intelligence
inspection surface.  It returns, for a single event:

* core event fields
* every linked holding (with ticker + portfolio_id + weight)
* every link's normalized causal chain (see
  ``src/intelligence/chains``)
* deterministic factor tags from ``MacroFactorEvent`` rows
* related analysis notes that reference this event
* related alerts whose ``related_events`` JSON list contains this
  event's ID

Portfolio-safety: because events are global but holdings/alerts are
portfolio-scoped, the detail response preserves that distinction —
every affected holding and every related alert carries its own
``portfolio_id`` so the frontend can render honest scoping without
collapsing the two.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.database.models import (
    Alert as AlertModel,
    AnalysisNote as AnalysisNoteModel,
    Event as EventModel,
    EventLink as EventLinkModel,
    Holding as HoldingModel,
    MacroFactorEvent as MacroFactorEventModel,
    Source,
)
from src.intelligence.chains import NormalizedChain, build_chain_for_link
from src.intelligence.factors.taxonomy import get_factor as get_factor_definition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class FactorTag(BaseModel):
    """Compact factor badge attached to an event.

    Produced from ``MacroFactorEvent`` rows — one tag per classified
    factor, independent of whether any holding-level link survived
    the propagator's emission gate.
    """

    key: str
    label: str
    direction: str              # up | down | unknown
    magnitude: str              # minor | moderate | major | extreme | unknown
    confidence: float


class EventLinkResponse(BaseModel):
    """Link attached to an event.

    Phase 9A: ``channel`` and ``details_json`` are populated for
    ``link_type="macro_factor"`` rows to carry the factor key and the
    structured deterministic causal chain respectively.

    Phase 9B: ``chain`` is the normalized shared chain shape that any
    frontend can render without reparsing ``details_json``.
    """

    id: str
    link_type: str
    link_target: str
    relevance_score: float | None = None
    impact_channel: str | None = None
    link_source: str | None = None
    channel: str | None = None
    details_json: str | None = None
    chain: dict[str, Any] | None = None


class AffectedHolding(BaseModel):
    """A holding touched by this event, with enough context for the UI.

    ``portfolio_id`` is always present so the UI can label which
    portfolio this holding belongs to — multi-portfolio users see an
    honest breakdown instead of a collapsed list.
    """

    holding_id: str
    ticker: str
    portfolio_id: str
    weight_pct: float | None = None
    sector: str | None = None
    link_types: list[str] = Field(default_factory=list)
    max_relevance: float | None = None


class RelatedAnalysisNote(BaseModel):
    """Minimal analysis-note reference for the event detail panel."""

    id: str
    note_type: str
    holding_id: str | None = None
    ticker: str | None = None
    materiality: str | None = None
    confidence: str | None = None
    summary: str
    created_at: str


class RelatedAlert(BaseModel):
    """Minimal alert reference for the event detail panel."""

    id: str
    alert_type: str
    severity: str
    title: str
    portfolio_id: str | None = None
    acknowledged: bool
    created_at: str


class EventResponse(BaseModel):
    """Portfolio-relevant event (list-row shape)."""

    id: str
    title: str
    summary: str | None = None
    url: str | None = None
    event_type: str | None = None
    materiality: str
    confidence: str
    scope: str | None = None
    direction: str | None = None
    horizon: str | None = None
    source_name: str | None = None
    published_at: str | None = None
    fetched_at: str
    # Phase 9B additions
    factor_tags: list[FactorTag] = Field(default_factory=list)
    linked_ticker_count: int = 0


class EventDetailResponse(EventResponse):
    """Event with full content, links, chains, analyses, alerts.

    Phase 9B — this is the single backend source of truth for the
    dashboard's event inspection modal and any external caller.  No
    split-brain: everything the UI renders is in this payload.

    Phase 9N — adds a compact grounded explanation block:
      * ``why_it_matters``  — one-sentence plain-English narration
                               built from the factor tag + affected
                               holdings + relationship chain origin.
      * ``suggested_action`` — one-line operator-facing next step,
                               derived from the factor family and
                               affected holdings.  Null when the
                               evidence is too thin.
      * ``explanation_grounded_in`` — list of short tags describing
                               the specific inputs the explanation
                               came from (factor key, ticker list,
                               relationship channel).

    The block is a pure function of the already-computed
    ``factor_tags`` / ``links`` / ``affected_holdings`` fields in this
    payload — it never reads extra rows, never calls an LLM, and
    never invents reasoning.
    """

    content: str | None = None
    external_id: str | None = None
    links: list[EventLinkResponse] = Field(default_factory=list)
    linked_tickers: list[str] = Field(default_factory=list)
    affected_holdings: list[AffectedHolding] = Field(default_factory=list)
    related_analyses: list[RelatedAnalysisNote] = Field(default_factory=list)
    related_alerts: list[RelatedAlert] = Field(default_factory=list)

    # Phase 9N — grounded explanation + suggested action
    why_it_matters: str | None = None
    suggested_action: str | None = None
    explanation_grounded_in: list[str] = Field(default_factory=list)
    # Phase 9Q — per-ref navigation targets (parallel to
    # ``explanation_grounded_in``).  Each entry is
    # ``{"ref": "<prefix>:<value>", "nav_target": {...} | None}``.
    # Refs without a navigable destination carry ``nav_target: null``
    # so the frontend can render them as plain chips.
    explanation_grounded_in_targets: list[dict[str, Any]] = Field(
        default_factory=list,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_event(
    ev: EventModel,
    source_name: str | None,
    factor_tags: list[FactorTag] | None = None,
    linked_ticker_count: int = 0,
) -> EventResponse:
    return EventResponse(
        id=ev.id,
        title=ev.title,
        summary=ev.summary,
        url=ev.url,
        event_type=ev.event_type,
        materiality=ev.materiality,
        confidence=ev.confidence,
        scope=ev.scope,
        direction=ev.direction,
        horizon=ev.horizon,
        source_name=source_name,
        published_at=ev.published_at,
        fetched_at=ev.fetched_at,
        factor_tags=factor_tags or [],
        linked_ticker_count=linked_ticker_count,
    )


def _mfe_to_tag(mfe: MacroFactorEventModel) -> FactorTag:
    defn = get_factor_definition(mfe.factor)
    return FactorTag(
        key=mfe.factor,
        label=defn.label if defn else mfe.factor,
        direction=mfe.direction or "unknown",
        magnitude=mfe.magnitude or "unknown",
        confidence=float(mfe.confidence or 0.0),
    )


async def _load_factor_tags_for_events(
    session: AsyncSession, event_ids: list[str],
) -> dict[str, list[FactorTag]]:
    """Bulk-load factor tags keyed by event_id."""
    if not event_ids:
        return {}
    stmt = select(MacroFactorEventModel).where(
        MacroFactorEventModel.event_id.in_(event_ids)
    )
    rows = (await session.execute(stmt)).scalars().all()
    result: dict[str, list[FactorTag]] = {}
    for mfe in rows:
        result.setdefault(mfe.event_id, []).append(_mfe_to_tag(mfe))
    # Sort tags deterministically: confidence desc, then key asc.
    for eid in result:
        result[eid].sort(key=lambda t: (-(t.confidence or 0.0), t.key))
    return result


async def _load_linked_ticker_counts(
    session: AsyncSession, event_ids: list[str],
) -> dict[str, int]:
    """Return the unique ticker count per event id for the list shape.

    Events are global; this count covers every portfolio.  Callers
    that want portfolio-scoped counts should call the detail endpoint
    for the full ``affected_holdings`` list.
    """
    if not event_ids:
        return {}
    stmt = (
        select(EventLinkModel.event_id, HoldingModel.ticker)
        .join(HoldingModel, EventLinkModel.link_target == HoldingModel.id)
        .where(EventLinkModel.event_id.in_(event_ids))
    )
    rows = (await session.execute(stmt)).all()
    counts: dict[str, set[str]] = {}
    for event_id, ticker in rows:
        counts.setdefault(event_id, set()).add(ticker)
    return {k: len(v) for k, v in counts.items()}


def _summarize_note(note: AnalysisNoteModel) -> str:
    """Extract a short human summary from an analysis note's JSON content."""
    if not note.content:
        return note.note_type.replace("_", " ").title()
    try:
        data = json.loads(note.content)
    except (json.JSONDecodeError, TypeError):
        return (note.content or "")[:160]
    if not isinstance(data, dict):
        return (note.content or "")[:160]
    # Prefer short_term_outlook, then synthesis, then impact_direction/magnitude.
    for key in ("short_term_outlook", "synthesis", "summary"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:220]
    direction = data.get("impact_direction")
    magnitude = data.get("impact_magnitude")
    if direction:
        parts = [direction]
        if magnitude:
            parts.append(magnitude)
        return " / ".join(parts)
    return note.note_type.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[EventResponse])
async def list_events(
    ticker: str | None = Query(None, description="Filter by ticker (via event links)"),
    event_type: str | None = Query(None, description="Filter by event type"),
    materiality: str | None = Query(None, description="Filter by materiality level"),
    date_from: datetime | None = Query(None, description="Start of date range (published_at)"),
    date_to: datetime | None = Query(None, description="End of date range (published_at)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """List events with optional filters.

    Phase 9B adds ``factor_tags`` and ``linked_ticker_count`` to each
    row so the dashboard can render factor badges without a second
    request per row.
    """
    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
    )

    if ticker:
        ticker_subq = (
            select(EventLinkModel.event_id)
            .join(HoldingModel, EventLinkModel.link_target == HoldingModel.id)
            .where(HoldingModel.ticker == ticker.upper())
        )
        stmt = stmt.where(EventModel.id.in_(ticker_subq))

    if event_type:
        stmt = stmt.where(EventModel.event_type == event_type)
    if materiality:
        stmt = stmt.where(EventModel.materiality == materiality)
    if date_from:
        stmt = stmt.where(EventModel.published_at >= date_from.isoformat())
    if date_to:
        stmt = stmt.where(EventModel.published_at <= date_to.isoformat())

    stmt = stmt.order_by(EventModel.fetched_at.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    events = [(ev, src_name) for ev, src_name in rows]
    event_ids = [ev.id for ev, _ in events]

    factor_tags_map = await _load_factor_tags_for_events(session, event_ids)
    ticker_counts = await _load_linked_ticker_counts(session, event_ids)

    return [
        _row_to_event(
            ev,
            src_name,
            factor_tags=factor_tags_map.get(ev.id, []),
            linked_ticker_count=ticker_counts.get(ev.id, 0),
        )
        for ev, src_name in events
    ]


@router.get("/recent", response_model=list[EventResponse])
async def recent_events(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    """Return the most recent events from the last 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
        .where(EventModel.fetched_at >= cutoff)
        .order_by(EventModel.fetched_at.desc())
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    events = [(ev, src_name) for ev, src_name in rows]
    event_ids = [ev.id for ev, _ in events]

    factor_tags_map = await _load_factor_tags_for_events(session, event_ids)
    ticker_counts = await _load_linked_ticker_counts(session, event_ids)

    return [
        _row_to_event(
            ev,
            src_name,
            factor_tags=factor_tags_map.get(ev.id, []),
            linked_ticker_count=ticker_counts.get(ev.id, 0),
        )
        for ev, src_name in events
    ]


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: str,
    portfolio_id: str | None = Query(
        None,
        description=(
            "Optional portfolio context used by the Phase 9Q "
            "navigation enrichment.  Does not filter the event body "
            "itself (events are global); it only affects the target "
            "portfolio attached to each navigable evidence ref."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> EventDetailResponse:
    """Get full event detail including links, chains, analyses, and alerts.

    This is the Phase 9B intelligence inspection surface — the single
    backend source of truth that the dashboard event detail modal
    consumes.
    """
    stmt = (
        select(EventModel, Source.name.label("source_name"))
        .outerjoin(Source, EventModel.source_id == Source.id)
        .where(EventModel.id == event_id)
    )
    row = (await session.execute(stmt)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    ev, source_name = row

    # ----- Links -------------------------------------------------------
    links_stmt = select(EventLinkModel).where(EventLinkModel.event_id == event_id)
    links = list((await session.execute(links_stmt)).scalars().all())

    # Bulk-resolve link targets (holding UUIDs → holding metadata).
    holding_ids = list({lnk.link_target for lnk in links if lnk.link_target})
    holding_map: dict[str, HoldingModel] = {}
    if holding_ids:
        h_stmt = select(HoldingModel).where(HoldingModel.id.in_(holding_ids))
        for h in (await session.execute(h_stmt)).scalars().all():
            holding_map[h.id] = h

    # Build EventLinkResponse (with normalized chain) per row.
    link_responses: list[EventLinkResponse] = []
    for lnk in links:
        holding = holding_map.get(lnk.link_target)
        chain = build_chain_for_link(
            link_id=lnk.id,
            link_type=lnk.link_type,
            link_target=lnk.link_target,
            relevance_score=lnk.relevance_score,
            impact_channel=lnk.impact_channel,
            link_source=lnk.link_source,
            channel=lnk.channel,
            details_json=lnk.details_json,
            holding_ticker=holding.ticker if holding else None,
            holding_portfolio_id=holding.portfolio_id if holding else None,
            event_title=ev.title,
        )
        link_responses.append(EventLinkResponse(
            id=lnk.id,
            link_type=lnk.link_type,
            link_target=lnk.link_target,
            relevance_score=lnk.relevance_score,
            impact_channel=lnk.impact_channel,
            link_source=lnk.link_source,
            channel=lnk.channel,
            details_json=lnk.details_json,
            chain=dict(chain),
        ))

    # ----- Affected holdings (aggregated per holding) ------------------
    per_holding: dict[str, dict[str, Any]] = {}
    for lnk in links:
        h = holding_map.get(lnk.link_target)
        if h is None:
            continue
        bucket = per_holding.setdefault(
            h.id,
            {
                "holding": h,
                "link_types": [],
                "max_relevance": None,
            },
        )
        if lnk.link_type not in bucket["link_types"]:
            bucket["link_types"].append(lnk.link_type)
        if lnk.relevance_score is not None:
            prev = bucket["max_relevance"]
            if prev is None or lnk.relevance_score > prev:
                bucket["max_relevance"] = lnk.relevance_score

    # Resolve sector via a single Security join-by-ticker if needed.
    affected_holdings: list[AffectedHolding] = []
    tickers_needing_sector = {
        b["holding"].ticker for b in per_holding.values()
    }
    sector_map: dict[str, str | None] = {}
    if tickers_needing_sector:
        from src.database.models import Security as SecurityModel
        sec_stmt = select(SecurityModel.ticker, SecurityModel.sector).where(
            SecurityModel.ticker.in_(list(tickers_needing_sector))
        )
        for t, s in (await session.execute(sec_stmt)).all():
            sector_map[t] = s

    for hid, bucket in per_holding.items():
        h: HoldingModel = bucket["holding"]
        affected_holdings.append(AffectedHolding(
            holding_id=h.id,
            ticker=h.ticker,
            portfolio_id=h.portfolio_id,
            weight_pct=h.weight_pct,
            sector=sector_map.get(h.ticker),
            link_types=bucket["link_types"],
            max_relevance=bucket["max_relevance"],
        ))
    # Deterministic order: highest relevance first, then ticker.
    affected_holdings.sort(
        key=lambda a: (-(a.max_relevance or 0.0), a.ticker)
    )

    # Linked tickers (unique, for backward compat with existing callers)
    linked_tickers = sorted({h.ticker for h in holding_map.values()})

    # ----- Factor tags (one per classified MacroFactorEvent row) -------
    tag_stmt = select(MacroFactorEventModel).where(
        MacroFactorEventModel.event_id == event_id
    )
    factor_tag_rows = (await session.execute(tag_stmt)).scalars().all()
    factor_tags = [_mfe_to_tag(mfe) for mfe in factor_tag_rows]
    factor_tags.sort(key=lambda t: (-(t.confidence or 0.0), t.key))

    # ----- Related analysis notes --------------------------------------
    notes_stmt = (
        select(AnalysisNoteModel, HoldingModel.ticker)
        .outerjoin(HoldingModel, AnalysisNoteModel.holding_id == HoldingModel.id)
        .where(AnalysisNoteModel.event_id == event_id)
        .order_by(AnalysisNoteModel.created_at.desc())
    )
    note_rows = (await session.execute(notes_stmt)).all()
    related_analyses = [
        RelatedAnalysisNote(
            id=n.id,
            note_type=n.note_type,
            holding_id=n.holding_id,
            ticker=tkr,
            materiality=n.materiality,
            confidence=n.confidence,
            summary=_summarize_note(n),
            created_at=n.created_at,
        )
        for n, tkr in note_rows
    ]

    # ----- Related alerts (referenced via related_events JSON) ---------
    # SQLite doesn't index JSON columns, so filter server-side by the
    # presence of the event_id substring first to shrink the result
    # set, then verify in Python.  This is safe: related_events is
    # authored by the alert writers and always stores UUIDs.
    alert_stmt = (
        select(AlertModel)
        .where(AlertModel.related_events.is_not(None))
        .where(AlertModel.related_events.like(f'%{event_id}%'))
        .order_by(AlertModel.created_at.desc())
    )
    candidate_alerts = (await session.execute(alert_stmt)).scalars().all()
    related_alerts: list[RelatedAlert] = []
    for a in candidate_alerts:
        try:
            ref = json.loads(a.related_events or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(ref, list):
            continue
        if event_id not in ref:
            continue
        related_alerts.append(RelatedAlert(
            id=a.id,
            alert_type=a.alert_type,
            severity=a.severity,
            title=a.title,
            portfolio_id=a.portfolio_id,
            acknowledged=bool(a.acknowledged),
            created_at=a.created_at,
        ))

    # Phase 9N — grounded explanation block.  Pure function of the
    # already-computed fields; never reads extra rows.  Quietly falls
    # back to an empty block on any error so the modal still renders.
    why_it_matters: str | None = None
    suggested_action: str | None = None
    explanation_grounded_in: list[str] = []
    try:
        from src.intelligence.actions import explain_event
        chains_payload = [
            lr.chain.model_dump() if (lr.chain and hasattr(lr.chain, "model_dump")) else (lr.chain or {})
            for lr in link_responses
            if lr.chain is not None
        ]
        affected_payload = [
            ah.model_dump() if hasattr(ah, "model_dump") else dict(ah)
            for ah in affected_holdings
        ]
        tags_payload = [
            t.model_dump() if hasattr(t, "model_dump") else dict(t)
            for t in factor_tags
        ]
        explanation = explain_event(
            event_title=ev.title or "",
            factor_tags=tags_payload,
            chains=chains_payload,
            affected_holdings=affected_payload,
        )
        why_it_matters = explanation.get("why_it_matters")
        suggested_action = explanation.get("suggested_action")
        explanation_grounded_in = list(explanation.get("grounded_in") or [])
    except Exception as exc:  # pragma: no cover — defensive
        import logging
        logging.getLogger(__name__).debug(
            "event-detail explain dropped: %s", exc,
        )

    # Phase 9Q — build a parallel list of structured nav targets for
    # each grounded ref.  Falls back to an empty list if the
    # enrichment helper or the caller's portfolio context is missing.
    explanation_grounded_in_targets: list[dict] = []
    try:
        from src.intelligence.navigation import enrich_evidence_refs
        pid = portfolio_id or "default"
        explanation_grounded_in_targets = enrich_evidence_refs(
            explanation_grounded_in, pid,
        )
    except Exception as exc:  # pragma: no cover — defensive
        import logging
        logging.getLogger(__name__).debug(
            "event-detail nav enrichment dropped: %s", exc,
        )

    return EventDetailResponse(
        id=ev.id,
        title=ev.title,
        summary=ev.summary,
        url=ev.url,
        event_type=ev.event_type,
        materiality=ev.materiality,
        confidence=ev.confidence,
        scope=ev.scope,
        direction=ev.direction,
        horizon=ev.horizon,
        source_name=source_name,
        published_at=ev.published_at,
        fetched_at=ev.fetched_at,
        factor_tags=factor_tags,
        linked_ticker_count=len(linked_tickers),
        content=ev.content,
        external_id=ev.external_id,
        links=link_responses,
        linked_tickers=linked_tickers,
        affected_holdings=affected_holdings,
        related_analyses=related_analyses,
        related_alerts=related_alerts,
        why_it_matters=why_it_matters,
        suggested_action=suggested_action,
        explanation_grounded_in=explanation_grounded_in,
        explanation_grounded_in_targets=explanation_grounded_in_targets,
    )
