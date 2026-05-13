"""Analysis agent -- uses LLM to assess event impact on holdings.

Produces structured analysis notes with source traces and can generate
periodic digest summaries.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from sqlalchemy import func, select

from src.database.models import (
    AnalysisNote,
    CoverageReport,
    Digest,
    Event,
    EventLink,
    Holding,
    MacroFactorEvent,
    Security,
)
from src.intelligence.factors.taxonomy import get_factor as get_factor_definition

from .base import BaseAgent

logger = logging.getLogger(__name__)


def _get_analysis_prompt(fallback: str) -> str:
    from src.llm.prompts import get_prompt
    return get_prompt("analysis", fallback=fallback)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
ANALYSIS_PROMPT = """\
You are a senior portfolio analyst.  Given the event below and the
affected holding, produce a structured impact analysis.

Distinguish clearly between different types of impact:
- THESIS impact: does this change the fundamental investment thesis?
- EARNINGS impact: does this affect near-term revenue, margins, or EPS?
- VALUATION impact: does this change how the market should value the company?
- RISK impact: does this introduce, increase, or reduce material risk?

Event
-----
Title     : {event_title}
Type      : {event_type}
Summary   : {event_summary}
Published : {published_at}
URL       : {event_url}

Holding
-------
Ticker    : {ticker}
Sector    : {sector}
Geography : {geography}
Themes    : {themes}

Return a JSON object:
{{
    "impact_direction": "positive|negative|neutral|mixed",
    "impact_magnitude": "high|medium|low",
    "materiality": "noise|watch|important|critical",
    "thesis_impact": "none|low|medium|high",
    "earnings_impact": "none|low|medium|high",
    "valuation_impact": "none|low|medium|high",
    "risk_impact": "none|low|medium|high",
    "short_term_outlook": "<1-2 sentences>",
    "long_term_outlook": "<1-2 sentences>",
    "key_factors": ["<factor1>", "<factor2>"],
    "recommended_actions": ["<action1>"],
    "confidence": <0.0-1.0>
}}

Guidelines:
- "noise" = not material, no action needed
- "watch" = worth monitoring, could become material
- "important" = material, affects investment view
- "critical" = requires immediate attention, thesis-changing
- Most events are "noise" or "watch" — be honest, don't over-dramatise

Return ONLY valid JSON.
"""

DIGEST_PROMPT = """\
You are a portfolio intelligence assistant.  Summarise the following
analysis notes into a concise {period} digest for a portfolio manager.

Think at THREE levels:
1. HOLDING level — what happened to individual positions
2. SECTOR level — are there patterns across holdings in the same sector?
   (e.g. "3 of 4 tech holdings received negative signals — sector under pressure")
3. PORTFOLIO level — what's the net effect on the overall portfolio?
   (e.g. "portfolio tilted more defensive this week as tech weakened")

Notes
-----
{notes_text}

Produce a JSON object:
{{
    "headline": "<one-line portfolio-level headline>",
    "portfolio_assessment": "<2-3 sentences on overall portfolio health and direction>",
    "sector_patterns": [
        {{
            "sector": "<sector name>",
            "signal": "positive|negative|mixed|neutral",
            "summary": "<1 sentence>"
        }}
    ],
    "key_developments": ["<dev1>", "<dev2>"],
    "risk_flags": ["<flag1>"],
    "action_items": ["<item1>"],
    "market_context": "<brief market backdrop>",
    "holdings_requiring_attention": ["<ticker1>", "<ticker2>"]
}}

Return ONLY valid JSON.
"""

SECTOR_ANALYSIS_PROMPT = """\
You are a senior portfolio strategist.  Multiple holdings in the same sector
received signals during this analysis cycle.  Synthesise them into a
sector-level assessment.

Sector: {sector}

Individual holding signals
--------------------------
{holding_signals}

Portfolio context
-----------------
Total holdings in this sector: {sector_holding_count}
Holdings with signals this cycle: {signal_count}

Produce a JSON object:
{{
    "sector": "{sector}",
    "sector_signal": "positive|negative|mixed|neutral",
    "signal_strength": "strong|moderate|weak",
    "materiality": "noise|watch|important|critical",
    "thesis_impact": "none|low|medium|high",
    "synthesis": "<2-3 sentences explaining the sector-level picture>",
    "pattern_detected": "<one-line description of any pattern, or 'none'>",
    "affected_tickers": ["{tickers_placeholder}"],
    "recommended_actions": ["<action1>"],
    "confidence": <0.0-1.0>
}}

Guidelines:
- Look for PATTERNS: are multiple holdings moving the same way for the same reason?
- Distinguish sector-wide headwinds/tailwinds from idiosyncratic single-stock events
- "strong" signal = 3+ holdings aligned; "moderate" = 2 holdings or mixed; "weak" = divergent
- Be honest — if the signals are unrelated, say so

Return ONLY valid JSON.
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class AnalysisAgent(BaseAgent):
    """Analyses event impact on holdings and generates digests."""

    agent_name: ClassVar[str] = "analysis"
    read_permissions: ClassVar[list[str]] = [
        "holdings",
        "securities",
        "events",
        "event_links",
        "coverage_reports",
        "analysis_notes",
        "macro_factor_events",
    ]
    write_permissions: ClassVar[list[str]] = [
        "analysis_notes",
        "digests",
        "agent_runs",
    ]

    _portfolio_id: str = "default"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """Entry point -- dispatches to analyse or digest."""
        self._portfolio_id = kwargs.get("portfolio_id", "default")
        if kwargs.get("digest"):
            period: str = kwargs.get("period", "daily")
            return await self.generate_digest(period=period)
        event_ids: list[str] | None = kwargs.get("event_ids")
        return await self.analyze_events(event_ids=event_ids)

    # -- event analysis ----------------------------------------------------

    async def analyze_events(
        self,
        event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Analyse the impact of events on linked holdings.

        Parameters
        ----------
        event_ids:
            Specific event IDs.  When ``None``, analyses all unanalysed
            events.

        Returns
        -------
        dict
            Summary with ``analysed``, ``skipped``, ``errors``.
        """
        await self._log_run_start(parameters={"event_ids": event_ids})
        analysed: list[dict[str, Any]] = []
        skipped: int = 0
        errors: list[str] = []

        try:
            events = await self._fetch_events(event_ids)

            for event in events:
                linked_holdings = await self._get_linked_holdings(event["id"])
                if not linked_holdings:
                    skipped += 1
                    continue

                for holding in linked_holdings:
                    try:
                        note = await self._analyse_single(event, holding)
                        analysed.append(note)
                    except Exception as exc:
                        msg = f"Analysis failed for event {event['id']} x holding {holding['id']}: {exc}"
                        logger.error(msg, exc_info=True)
                        errors.append(msg)

            # --- Sector-level analysis ---
            # If 2+ holdings in the same sector were analysed this cycle,
            # produce a sector-level synthesis note.
            sector_notes = 0
            try:
                sector_notes = await self._analyse_sector_patterns(analysed)
            except Exception as exc:
                logger.warning("Sector analysis failed (non-fatal): %s", exc)

            summary = {
                "analysed": len(analysed),
                "sector_notes": sector_notes,
                "skipped": skipped,
                "errors": len(errors),
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    async def _fetch_events(
        self, event_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        """Return events to analyse."""
        self._check_permission("events", "read")
        async with self._get_db() as session:
            if event_ids:
                stmt = select(Event).where(Event.id.in_(event_ids))
            else:
                # Only fetch events that don't already have analysis notes,
                # preventing duplicate analysis on repeated runs.
                already_analysed = (
                    select(AnalysisNote.event_id)
                    .where(AnalysisNote.event_id.isnot(None))
                    .distinct()
                )
                stmt = (
                    select(Event)
                    .where(Event.id.notin_(already_analysed))
                    .order_by(Event.created_at.desc())
                    .limit(50)
                )

            rows = (await session.execute(stmt)).scalars().all()

        return [
            {
                "id": e.id,
                "title": e.title,
                "event_type": e.event_type,
                "summary": e.summary,
                "url": e.url,
                "published_at": e.published_at,
            }
            for e in rows
        ]

    # Minimum link confidence for full per-holding analysis.
    # Lower-confidence links (sector/geo at 0.4, market-wide at 0.3) are
    # preserved in the event_links table for digest/watch context but do
    # NOT trigger individual impact analysis notes.
    _MIN_ANALYSIS_RELEVANCE = 0.5

    # Phase 9A corrective pass: ``macro_factor`` links are deterministic
    # factor-driven impact hypotheses that already carry a full causal
    # chain in ``EventLink.details_json``.  They MUST be excluded from
    # the per-event LLM analysis pass regardless of their relevance
    # score, because:
    #   1. Re-narrating them via LLM is redundant with the stored
    #      rationale.
    #   2. At the honest emission floor (0.25), default-sector-prior
    #      links would otherwise flood analysis if they ever crossed
    #      the generic 0.5 gate (they don't today, but a future
    #      manual-override row or formula change could).
    #   3. Factor signals are surfaced via a dedicated deterministic
    #      touchpoint path in ``generate_digest`` below, so analysts
    #      still see them — just not as LLM-generated per-event notes.
    _ANALYSIS_EXCLUDED_LINK_TYPES: tuple[str, ...] = ("macro_factor",)

    async def _get_linked_holdings(self, event_id: str) -> list[dict[str, Any]]:
        """Return holdings linked to *event_id* with sufficient relevance for analysis.

        Type-aware: deterministic factor links are excluded here and
        handled by the digest factor-touchpoint path instead, so they
        do not consume LLM per-event analysis budget or produce
        redundant narratives.
        """
        self._check_permission("event_links", "read")
        self._check_permission("holdings", "read")

        async with self._get_db() as session:
            stmt = (
                select(Holding)
                .join(EventLink, EventLink.link_target == Holding.id)
                .where(EventLink.event_id == event_id)
                .where(EventLink.relevance_score >= self._MIN_ANALYSIS_RELEVANCE)
                .where(EventLink.link_type.notin_(self._ANALYSIS_EXCLUDED_LINK_TYPES))
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [{"id": h.id, "ticker": h.ticker} for h in rows]

    async def _get_security_info(self, ticker: str) -> dict[str, Any]:
        """Fetch classification metadata for a ticker."""
        self._check_permission("securities", "read")
        async with self._get_db() as session:
            stmt = select(Security).where(Security.ticker == ticker)
            sec = (await session.execute(stmt)).scalars().first()

        if sec is None:
            return {"sector": "Unknown", "geography": "Unknown", "themes": []}

        # themes is stored as a JSON string (e.g. '["AI","cloud"]') — parse it
        themes_raw = getattr(sec, "themes", None)
        if isinstance(themes_raw, str):
            try:
                themes = json.loads(themes_raw)
            except (json.JSONDecodeError, TypeError):
                themes = []
        elif isinstance(themes_raw, list):
            themes = themes_raw
        else:
            themes = []

        return {
            "sector": sec.sector or "Unknown",
            "geography": sec.geography or "Unknown",
            "themes": themes,
        }

    async def _analyse_single(
        self,
        event: dict[str, Any],
        holding: dict[str, Any],
    ) -> dict[str, Any]:
        """Run LLM analysis (or deterministic fallback) for one event-holding
        pair and persist the note.

        Phase 9E: the LLM path is now grounded in the deterministic causal
        chains + factor tags computed upstream.  The fallback path is
        upgraded from a keyword-based heuristic to a chain-grounded
        deterministic renderer so a missing LLM never drops us to a
        weaker answer than the factor pipeline already produced.
        """
        sec_info = await self._get_security_info(holding["ticker"])

        # Build the grounded context ONCE — it is the shared input for
        # both the LLM call and the deterministic fallback.  If it
        # cannot be assembled (missing holding/event), fall through
        # to the legacy rule-based path so the agent still produces
        # a note.
        from src.llm.client import is_llm_available
        from src.llm.grounded import (
            GroundedEventContext,
            assemble_event_context,
            build_event_analysis_prompt,
            render_deterministic_explanation,
        )

        grounded_ctx: GroundedEventContext | None = None
        try:
            async with self._get_db() as session:
                grounded_ctx = await assemble_event_context(
                    session, event_id=event["id"], holding_id=holding["id"],
                )
        except Exception as exc:
            logger.warning(
                "Failed to assemble grounded context for event=%s holding=%s: %s",
                event.get("id"), holding.get("id"), exc,
            )

        if grounded_ctx is not None and is_llm_available():
            analysis = await self._call_analysis_llm(
                event, holding, sec_info, grounded_ctx=grounded_ctx,
            )
            prompt_hash = getattr(self, "_last_prompt_hash", None)
        elif grounded_ctx is not None:
            analysis = render_deterministic_explanation(grounded_ctx)
            prompt_hash = "deterministic_fallback"
            logger.info(
                "Used deterministic chain-grounded fallback for %s "
                "(LLM unavailable)",
                holding["ticker"],
            )
        else:
            from src.agents.fallbacks import rule_based_analysis
            analysis = rule_based_analysis(event, holding)
            prompt_hash = "rule_based"
            logger.info(
                "Used legacy rule-based analysis fallback for %s "
                "(grounded context unavailable)",
                holding["ticker"],
            )

        # Persist
        self._check_permission("analysis_notes", "write")
        note_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        note = AnalysisNote(
            id=note_id,
            event_id=event["id"],
            holding_id=holding["id"],
            note_type="impact_analysis",
            content=json.dumps({
                "ticker": holding["ticker"],
                "impact_direction": analysis.get("impact_direction"),
                "impact_magnitude": analysis.get("impact_magnitude"),
                "materiality": analysis.get("materiality", "watch"),
                "thesis_impact": analysis.get("thesis_impact", "none"),
                "earnings_impact": analysis.get("earnings_impact", "none"),
                "valuation_impact": analysis.get("valuation_impact", "none"),
                "risk_impact": analysis.get("risk_impact", "none"),
                "short_term_outlook": analysis.get("short_term_outlook"),
                "long_term_outlook": analysis.get("long_term_outlook"),
                "key_factors": analysis.get("key_factors", []),
                "recommended_actions": analysis.get("recommended_actions", []),
                # Phase 9E: persist the uncertainty note so event detail
                # can surface it verbatim.  Only present on grounded
                # LLM and deterministic-fallback paths.
                "uncertainty_note": analysis.get("uncertainty_note"),
                "source_event_url": event.get("url"),
            }),
            materiality=analysis.get("materiality", "watch"),
            confidence=str(analysis.get("confidence", 0.0)),
            agent_id=self.agent_name,
            prompt_hash=prompt_hash,
            created_at=now,
        )

        async with self._get_db() as session:
            session.add(note)
            await session.commit()

        # Write materiality back to the parent Event so digest/filter pipelines
        # can query events by materiality without joining analysis_notes.
        mat = analysis.get("materiality", "watch")
        try:
            async with self._get_db() as session:
                evt = (await session.execute(
                    select(Event).where(Event.id == event["id"])
                )).scalars().first()
                if evt and (evt.materiality is None or evt.materiality == "unscored"):
                    evt.materiality = mat
                    await session.commit()
                    logger.debug("Event %s materiality set to %s", event["id"], mat)
        except Exception as exc:
            logger.warning("Failed to write materiality to event %s: %s", event["id"], exc)

        await self._audit_log(
            action="analysis_created",
            entity_type="analysis_note",
            entity_id=note_id,
            details={
                "event_id": event["id"],
                "ticker": holding["ticker"],
                "impact": analysis.get("impact_direction"),
            },
        )

        logger.info(
            "Analysis note created  id=%s  event=%s  ticker=%s  impact=%s",
            note_id,
            event["id"],
            holding["ticker"],
            analysis.get("impact_direction"),
        )
        return {"note_id": note_id, "ticker": holding["ticker"], **analysis}

    async def _call_analysis_llm(
        self,
        event: dict[str, Any],
        holding: dict[str, Any],
        sec_info: dict[str, Any],
        *,
        grounded_ctx=None,
    ) -> dict[str, Any]:
        """Send the analysis prompt to the LLM and return parsed JSON.

        Phase 9E: when a :class:`GroundedEventContext` is supplied the
        prompt is built via ``build_event_analysis_prompt`` so the
        LLM sees the deterministic factor tags + causal chains as
        ground truth.  On LLM failure the method returns the
        deterministic-chain explanation rather than the old empty
        stub, so downstream persistence still gets a meaningful
        answer.
        """
        import hashlib
        from src.llm.client import call_llm_json
        from src.llm.grounded import (
            build_event_analysis_prompt,
            render_deterministic_explanation,
        )

        if grounded_ctx is not None:
            prompt = build_event_analysis_prompt(grounded_ctx)
        else:
            # Legacy path: no grounded context was built.  Preserved for
            # defensive fallback — exercised only if the pre-fetch in
            # _analyse_single raised an exception.
            prompt = _get_analysis_prompt(ANALYSIS_PROMPT).format(
                event_title=event.get("title", ""),
                event_type=event.get("event_type", ""),
                event_summary=event.get("summary", ""),
                published_at=event.get("published_at", ""),
                event_url=event.get("url", ""),
                ticker=holding["ticker"],
                sector=sec_info["sector"],
                geography=sec_info["geography"],
                themes=", ".join(sec_info["themes"]) if sec_info["themes"] else "N/A",
            )

        # Store prompt hash for reproducibility auditing
        self._last_prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        try:
            result = await call_llm_json(prompt)
            logger.info("LLM analysis completed for %s", holding["ticker"])
            return result
        except Exception as exc:
            logger.error("LLM analysis failed for %s: %s", holding["ticker"], exc)
            # Phase 9E: prefer the deterministic explanation over the
            # old empty stub so the note persists a real summary even
            # on LLM error.
            if grounded_ctx is not None:
                return render_deterministic_explanation(grounded_ctx)
            return {
                "impact_direction": "unclear",
                "impact_magnitude": "low",
                "short_term_outlook": f"LLM analysis unavailable: {exc}",
                "long_term_outlook": "Retry later.",
                "key_factors": [],
                "recommended_actions": [],
                "confidence": 0.0,
            }

    # -- digest generation -------------------------------------------------

    async def generate_digest(self, period: str = "daily") -> dict[str, Any]:
        """Generate a periodic digest from recent analysis notes.

        Parameters
        ----------
        period:
            ``"daily"``, ``"weekly"``, or ``"monthly"``.

        Phase 9A corrective pass: this path now ALSO fetches
        deterministic macro-factor touchpoints (from
        ``MacroFactorEvent`` and ``EventLink(link_type="macro_factor")``)
        and injects them into the digest content under
        ``macro_factor_touchpoints``.  This is how default-sector-prior
        factor intelligence reaches the user without ever passing
        through the LLM per-event analysis pipeline.
        """
        await self._log_run_start(parameters={"period": period})

        try:
            notes = await self._fetch_recent_notes(period)
            factor_touchpoints = await self._fetch_macro_factor_touchpoints(period)

            # Permit factor-only digests: if notes is empty but factor
            # touchpoints exist, still produce a digest so downstream
            # consumers see the deterministic signal.
            if not notes and not factor_touchpoints:
                summary = {
                    "period": period,
                    "digest_id": None,
                    "note_count": 0,
                    "factor_touchpoints": 0,
                }
                await self._log_run_complete(result_summary=summary)
                return summary

            # Phase 9E: build the grounded digest context once and
            # use it for both the LLM prompt and the deterministic
            # fallback.  The context carries factor touchpoints as
            # first-class ground truth so the LLM cannot invent
            # factors and the fallback is a real structured summary
            # rather than an empty stub.
            from src.llm.client import is_llm_available
            from src.llm.grounded import (
                GroundedDigestContext,
                render_deterministic_digest,
            )

            grounded_digest = GroundedDigestContext(
                period=period,
                portfolio_id=self._portfolio_id,
                notes=notes,
                factor_touchpoints=factor_touchpoints,
            )

            if notes and is_llm_available():
                digest_content = await self._call_digest_llm(
                    notes, period, grounded_ctx=grounded_digest,
                )
            elif notes or factor_touchpoints:
                digest_content = render_deterministic_digest(grounded_digest)
                logger.info(
                    "Used deterministic grounded digest fallback "
                    "(period=%s notes=%d touchpoints=%d)",
                    period, len(notes), len(factor_touchpoints),
                )
            else:
                # Should never reach here because of the early return
                # above when both are empty, but keep a safe default
                # for belt-and-suspenders.
                digest_content = render_deterministic_digest(grounded_digest)

            # Always attach the deterministic touchpoints as a
            # first-class field so consumers (UI, chat, export) can
            # render them without re-querying.
            digest_content["macro_factor_touchpoints"] = factor_touchpoints

            digest_id = await self._persist_digest(period, notes, digest_content)

            summary = {
                "period": period,
                "digest_id": digest_id,
                "note_count": len(notes),
                "factor_touchpoints": len(factor_touchpoints),
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    async def _fetch_macro_factor_touchpoints(
        self, period: str,
    ) -> list[dict[str, Any]]:
        """Fetch deterministic macro-factor touchpoints for the digest.

        Returns a per-factor summary of:
          * factor key + label (from the taxonomy)
          * dominant direction (up / down / unknown / mixed)
          * highest magnitude seen
          * number of events and holdings touched IN THIS PORTFOLIO
          * aggregate maximum link relevance (the strongest deterministic
            signal observed for this portfolio)
          * up to 3 example events and affected tickers

        Everything is scoped to ``self._portfolio_id`` via the join
        through ``Holding``, so two portfolios sharing the same event
        get disjoint touchpoint lists.
        """
        self._check_permission("event_links", "read")
        self._check_permission("macro_factor_events", "read")
        self._check_permission("events", "read")
        self._check_permission("holdings", "read")

        days_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = days_map.get(period, 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self._get_db() as session:
            # Join:
            #   EventLink (macro_factor) -> Event (time window)
            #                            -> Holding (portfolio scope)
            #                            -> MacroFactorEvent (direction/
            #                               magnitude/confidence pulled
            #                               from the deterministic row)
            link_stmt = (
                select(
                    EventLink.id,
                    EventLink.impact_channel,
                    EventLink.relevance_score,
                    EventLink.details_json,
                    Event.id,
                    Event.title,
                    Holding.id,
                    Holding.ticker,
                )
                .join(Event, EventLink.event_id == Event.id)
                .join(Holding, EventLink.link_target == Holding.id)
                .where(
                    EventLink.link_type == "macro_factor",
                    Event.fetched_at >= cutoff,
                    Holding.portfolio_id == self._portfolio_id,
                )
                .order_by(EventLink.relevance_score.desc())
            )
            rows = (await session.execute(link_stmt)).all()

            # Pull the corresponding MacroFactorEvent rows so we get
            # authoritative direction / magnitude / factor-level
            # confidence (the EventLink score is the holding-level
            # p_holding, which already folds sensitivity in).
            event_ids = {row[4] for row in rows}
            mfe_map: dict[tuple[str, str], MacroFactorEvent] = {}
            if event_ids:
                mfe_stmt = select(MacroFactorEvent).where(
                    MacroFactorEvent.event_id.in_(event_ids)
                )
                for mfe in (await session.execute(mfe_stmt)).scalars().all():
                    mfe_map[(mfe.event_id, mfe.factor)] = mfe

        # Aggregate per factor.
        per_factor: dict[str, dict[str, Any]] = {}
        for (
            link_id,
            factor_key,
            link_score,
            details_raw,
            event_id,
            event_title,
            holding_id,
            ticker,
        ) in rows:
            if not factor_key:
                continue
            bucket = per_factor.setdefault(
                factor_key,
                {
                    "factor": factor_key,
                    "label": (
                        get_factor_definition(factor_key).label
                        if get_factor_definition(factor_key)
                        else factor_key
                    ),
                    "directions": [],
                    "magnitudes": [],
                    "max_link_relevance": 0.0,
                    "max_factor_confidence": 0.0,
                    "event_ids": set(),
                    "holding_ids": set(),
                    "example_events": [],
                    "example_tickers": [],
                    "effect_directions": [],
                },
            )
            mfe = mfe_map.get((event_id, factor_key))
            if mfe:
                bucket["directions"].append(mfe.direction)
                bucket["magnitudes"].append(mfe.magnitude)
                if mfe.confidence and mfe.confidence > bucket["max_factor_confidence"]:
                    bucket["max_factor_confidence"] = float(mfe.confidence)
            if link_score is not None and link_score > bucket["max_link_relevance"]:
                bucket["max_link_relevance"] = float(link_score)
            bucket["event_ids"].add(event_id)
            bucket["holding_ids"].add(holding_id)
            if len(bucket["example_events"]) < 3 and event_title:
                bucket["example_events"].append({
                    "id": event_id,
                    "title": (event_title or "")[:200],
                })
            if ticker and ticker not in bucket["example_tickers"]:
                bucket["example_tickers"].append(ticker)
            # Pull the expected effect direction from details_json if
            # present — this is the propagator's sign(sens)×direction.
            if details_raw:
                try:
                    details = json.loads(details_raw)
                    eff = (
                        details.get("expected_effect", {}).get("direction")
                        if isinstance(details, dict) else None
                    )
                    if eff:
                        bucket["effect_directions"].append(eff)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Finalise: collapse lists to a deterministic summary.
        def _dominant(values: list[str]) -> str:
            if not values:
                return "unknown"
            counts: dict[str, int] = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            best = max(counts.values())
            winners = [k for k, c in counts.items() if c == best]
            if len(winners) == 1:
                return winners[0]
            return "mixed"

        _MAGNITUDE_ORDER = ("unknown", "minor", "moderate", "major", "extreme")

        def _max_magnitude(values: list[str]) -> str:
            if not values:
                return "unknown"
            idx = max(_MAGNITUDE_ORDER.index(v) for v in values if v in _MAGNITUDE_ORDER)
            return _MAGNITUDE_ORDER[idx]

        result: list[dict[str, Any]] = []
        for b in per_factor.values():
            result.append({
                "factor": b["factor"],
                "label": b["label"],
                "factor_direction": _dominant(b["directions"]),
                "expected_effect": _dominant(b["effect_directions"]),
                "max_magnitude": _max_magnitude(b["magnitudes"]),
                "max_factor_confidence": round(b["max_factor_confidence"], 4),
                "max_link_relevance": round(b["max_link_relevance"], 4),
                "event_count": len(b["event_ids"]),
                "holding_count": len(b["holding_ids"]),
                "affected_tickers": sorted(b["example_tickers"])[:10],
                "example_events": b["example_events"],
            })

        # Deterministic ordering: strongest link relevance first, then
        # alphabetical factor key for stability.
        result.sort(key=lambda r: (-r["max_link_relevance"], r["factor"]))
        return result

    async def _fetch_recent_notes(self, period: str) -> list[dict[str, Any]]:
        """Fetch analysis notes within the given period window, scoped to portfolio."""
        self._check_permission("analysis_notes", "read")

        days_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = days_map.get(period, 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self._get_db() as session:
            stmt = (
                select(AnalysisNote)
                .join(Holding, AnalysisNote.holding_id == Holding.id)
                .where(
                    AnalysisNote.created_at >= cutoff,
                    Holding.portfolio_id == self._portfolio_id,
                )
                .order_by(AnalysisNote.created_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()

        results = []
        for n in rows:
            try:
                data = json.loads(n.content) if n.content else {}
            except json.JSONDecodeError:
                data = {}
            results.append({
                "id": n.id,
                "ticker": data.get("ticker", ""),
                "impact_direction": data.get("impact_direction", ""),
                "impact_magnitude": data.get("impact_magnitude", ""),
                "materiality": data.get("materiality", "watch"),
                "thesis_impact": data.get("thesis_impact", "none"),
                "earnings_impact": data.get("earnings_impact", "none"),
                "valuation_impact": data.get("valuation_impact", "none"),
                "risk_impact": data.get("risk_impact", "none"),
                "short_term_outlook": data.get("short_term_outlook", ""),
                "key_factors": data.get("key_factors", []),
            })
        return results

    async def _call_digest_llm(
        self,
        notes: list[dict[str, Any]],
        period: str,
        *,
        grounded_ctx=None,
    ) -> dict[str, Any]:
        """Generate the digest via LLM.

        Phase 9E: when a ``GroundedDigestContext`` is supplied the
        prompt is built via ``build_digest_prompt`` so the LLM sees
        the deterministic factor touchpoints as part of the ground
        truth.  On LLM failure the method returns the deterministic
        grounded digest rather than an empty error stub.
        """
        from src.llm.client import call_llm_json
        from src.llm.grounded import (
            GroundedDigestContext,
            build_digest_prompt,
            render_deterministic_digest,
        )

        if grounded_ctx is None:
            # Defensive fallback for any future caller that hasn't
            # migrated to the grounded path yet.
            grounded_ctx = GroundedDigestContext(
                period=period,
                portfolio_id=self._portfolio_id,
                notes=notes,
                factor_touchpoints=[],
            )

        prompt = build_digest_prompt(grounded_ctx)

        try:
            result = await call_llm_json(prompt)
            logger.info("LLM digest generated for period=%s", period)
            return result
        except Exception as exc:
            logger.error("LLM digest failed for period=%s: %s", period, exc)
            return render_deterministic_digest(grounded_ctx)

    async def _persist_digest(
        self,
        period: str,
        notes: list[dict[str, Any]],
        content: dict[str, Any],
    ) -> str:
        """Save the digest to the database."""
        self._check_permission("digests", "write")
        digest_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        digest = Digest(
            id=digest_id,
            portfolio_id=self._portfolio_id,
            digest_type=period,
            period_start=(datetime.now(timezone.utc) - timedelta(days={"daily": 1, "weekly": 7, "monthly": 30}.get(period, 1))).isoformat(),
            period_end=now,
            content=json.dumps(content),
            event_count=0,
            alert_count=0,
            holding_count=len(notes),
            created_at=now,
        )

        async with self._get_db() as session:
            session.add(digest)
            await session.commit()

        await self._audit_log(
            action="digest_generated",
            entity_type="digest",
            entity_id=digest_id,
            details={"period": period, "note_count": len(notes)},
        )

        logger.info("Digest generated  id=%s  period=%s  notes=%d", digest_id, period, len(notes))
        return digest_id

    # -- sector-level analysis -----------------------------------------------

    async def _analyse_sector_patterns(
        self,
        analysed_notes: list[dict[str, Any]],
    ) -> int:
        """Group analysed notes by sector and synthesise patterns.

        For every sector with 2+ holdings analysed in this cycle, sends a
        sector-level prompt to Claude (or skips if LLM unavailable) and
        persists an ``AnalysisNote`` with ``note_type="sector_impact"``.

        Parameters
        ----------
        analysed_notes:
            The list of note dicts returned by ``_analyse_single()`` during
            this cycle.  Each has at minimum ``ticker`` and impact fields.

        Returns
        -------
        int
            Number of sector-level notes created.
        """
        if not analysed_notes:
            return 0

        # --- 1. Look up sector for each ticker --------------------------------
        tickers = {n["ticker"] for n in analysed_notes}
        ticker_sector: dict[str, str] = {}

        for ticker in tickers:
            sec_info = await self._get_security_info(ticker)
            ticker_sector[ticker] = sec_info.get("sector", "Unknown")

        # --- 2. Group notes by sector -----------------------------------------
        sector_notes: dict[str, list[dict[str, Any]]] = {}
        for note in analysed_notes:
            sector = ticker_sector.get(note["ticker"], "Unknown")
            if sector == "Unknown":
                continue
            sector_notes.setdefault(sector, []).append(note)

        # --- 3. Only process sectors with 2+ holdings -------------------------
        multi_holding_sectors = {
            sector: notes
            for sector, notes in sector_notes.items()
            if len({n["ticker"] for n in notes}) >= 2
        }

        if not multi_holding_sectors:
            logger.debug("No sectors with 2+ holdings analysed this cycle — skipping sector analysis")
            return 0

        # --- 4. Count total holdings per sector (for context) -----------------
        sector_holding_counts: dict[str, int] = {}
        self._check_permission("holdings", "read")
        self._check_permission("securities", "read")

        async with self._get_db() as session:
            stmt = (
                select(Security.sector, func.count())
                .select_from(Holding)
                .join(Security, Security.ticker == Holding.ticker)
                .where(Security.sector.in_(list(multi_holding_sectors.keys())))
                .group_by(Security.sector)
            )
            rows = (await session.execute(stmt)).all()
            sector_holding_counts = {row[0]: row[1] for row in rows}

        # --- 5. Analyse each sector -------------------------------------------
        from src.llm.client import is_llm_available

        created = 0

        for sector, notes in multi_holding_sectors.items():
            unique_tickers = sorted({n["ticker"] for n in notes})
            signal_count = len(unique_tickers)
            sector_total = sector_holding_counts.get(sector, signal_count)

            try:
                if is_llm_available():
                    result = await self._call_sector_llm(
                        sector, notes, unique_tickers, sector_total,
                    )
                else:
                    result = self._rule_based_sector_analysis(
                        sector, notes, unique_tickers, sector_total,
                    )

                # Persist sector note
                note_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc).isoformat()

                sector_note = AnalysisNote(
                    id=note_id,
                    event_id=None,  # sector-level, not tied to one event
                    holding_id=None,  # sector-level, not tied to one holding
                    note_type="sector_impact",
                    content=json.dumps({
                        "sector": sector,
                        "affected_tickers": unique_tickers,
                        "sector_signal": result.get("sector_signal", "neutral"),
                        "signal_strength": result.get("signal_strength", "weak"),
                        "materiality": result.get("materiality", "watch"),
                        "thesis_impact": result.get("thesis_impact", "none"),
                        "synthesis": result.get("synthesis", ""),
                        "pattern_detected": result.get("pattern_detected", "none"),
                        "recommended_actions": result.get("recommended_actions", []),
                    }),
                    materiality=result.get("materiality", "watch"),
                    confidence=str(result.get("confidence", 0.0)),
                    agent_id=self.agent_name,
                    prompt_hash=getattr(self, "_last_prompt_hash", None),
                    created_at=now,
                )

                self._check_permission("analysis_notes", "write")
                async with self._get_db() as session:
                    session.add(sector_note)
                    await session.commit()

                await self._audit_log(
                    action="sector_analysis_created",
                    entity_type="analysis_note",
                    entity_id=note_id,
                    details={
                        "sector": sector,
                        "tickers": unique_tickers,
                        "signal": result.get("sector_signal"),
                    },
                )

                logger.info(
                    "Sector analysis created  id=%s  sector=%s  tickers=%s  signal=%s",
                    note_id, sector, unique_tickers, result.get("sector_signal"),
                )
                created += 1

            except Exception as exc:
                logger.warning(
                    "Sector analysis failed for %s (non-fatal): %s", sector, exc,
                )

        return created

    async def _call_sector_llm(
        self,
        sector: str,
        notes: list[dict[str, Any]],
        tickers: list[str],
        sector_total: int,
    ) -> dict[str, Any]:
        """Send the sector synthesis prompt to the LLM."""
        import hashlib
        from src.llm.client import call_llm_json

        # Build the per-holding signal lines
        signal_lines = []
        for n in notes:
            line = (
                f"- [{n.get('ticker', '?')}] "
                f"impact={n.get('impact_direction', '?')} / {n.get('impact_magnitude', '?')} "
                f"(materiality: {n.get('materiality', '?')}) | "
                f"thesis={n.get('thesis_impact', '?')}, "
                f"earnings={n.get('earnings_impact', '?')}, "
                f"valuation={n.get('valuation_impact', '?')}, "
                f"risk={n.get('risk_impact', '?')}"
            )
            outlook = n.get("short_term_outlook") or n.get("key_factors", [])
            if isinstance(outlook, list) and outlook:
                line += f"  Factors: {', '.join(outlook[:3])}"
            elif isinstance(outlook, str):
                line += f"  Outlook: {outlook}"
            signal_lines.append(line)

        prompt = SECTOR_ANALYSIS_PROMPT.format(
            sector=sector,
            holding_signals="\n".join(signal_lines),
            sector_holding_count=sector_total,
            signal_count=len(tickers),
            tickers_placeholder='", "'.join(tickers),
        )

        self._last_prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        try:
            result = await call_llm_json(prompt)
            logger.info("LLM sector analysis completed for sector=%s", sector)
            return result
        except Exception as exc:
            logger.error("LLM sector analysis failed for %s: %s", sector, exc)
            # Fall back to rule-based
            return self._rule_based_sector_analysis(
                sector, notes, tickers, sector_total,
            )

    @staticmethod
    def _rule_based_sector_analysis(
        sector: str,
        notes: list[dict[str, Any]],
        tickers: list[str],
        sector_total: int,
    ) -> dict[str, Any]:
        """Produce a sector synthesis without the LLM.

        Uses simple vote-counting: majority direction wins, strength
        depends on alignment.
        """
        direction_counts: dict[str, int] = {}
        materialities: list[str] = []
        for n in notes:
            d = n.get("impact_direction", "neutral")
            direction_counts[d] = direction_counts.get(d, 0) + 1
            materialities.append(n.get("materiality", "watch"))

        # Determine dominant direction
        dominant = max(direction_counts, key=direction_counts.get)  # type: ignore[arg-type]
        dominant_pct = direction_counts[dominant] / len(notes)

        # Check if there's a genuine majority vs an even split
        num_directions = len(direction_counts)

        if dominant_pct >= 0.75:
            sector_signal = dominant
            strength = "strong" if len(tickers) >= 3 else "moderate"
        elif dominant_pct > 0.5 or (dominant_pct == 0.5 and num_directions == 1):
            sector_signal = dominant
            strength = "moderate"
        else:
            # True split — no clear winner
            sector_signal = "mixed"
            strength = "weak"

        # Highest materiality seen
        mat_order = {"noise": 0, "watch": 1, "important": 2, "critical": 3}
        best_mat = max(materialities, key=lambda m: mat_order.get(m, 0))

        tickers_str = ", ".join(tickers)
        synthesis = (
            f"{len(tickers)} of {sector_total} {sector} holdings received signals. "
            f"Dominant direction: {dominant} ({direction_counts[dominant]}/{len(notes)} signals). "
            f"Highest materiality: {best_mat}."
        )

        pattern = "none"
        if dominant_pct >= 0.75 and len(tickers) >= 2:
            pattern = f"Aligned {dominant} pressure across {tickers_str}"

        return {
            "sector": sector,
            "sector_signal": sector_signal,
            "signal_strength": strength,
            "materiality": best_mat,
            "thesis_impact": "low" if best_mat in ("noise", "watch") else "medium",
            "synthesis": synthesis,
            "pattern_detected": pattern,
            "affected_tickers": tickers,
            "recommended_actions": [
                f"Review {sector} sector exposure"
            ] if sector_signal in ("negative", "mixed") else [],
            "confidence": round(dominant_pct * 0.5, 2),  # rule-based → capped at 0.5
        }
