"""Deterministic factor → holding propagation engine (Phase 9A).

Given:
  * a list of ``FactorClassification`` objects for an event, and
  * a list of holding dicts (with at least ``id``, ``ticker``,
    ``portfolio_id``, and ``sector``),
  * a :class:`SensitivityResolver`,

the propagator produces a list of :class:`FactorImpact` records —
one per (factor, holding) combination that survives the sensitivity
and relevance gates.

This module is pure Python, stateless, and has zero database
coupling; it is called from the live collection path with pre-loaded
data so the SQL side stays localised to the agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from src.intelligence.factors.classifier import FactorClassification
from src.intelligence.factors.sensitivity import (
    ResolvedSensitivity,
    SensitivityResolver,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Weight tables (from the Phase 9A brief)
# ---------------------------------------------------------------------------

_SOURCE_WEIGHTS: dict[str, float] = {
    "default": 0.55,
    "ai_inferred": 0.70,
    "manual": 0.90,
    "zero": 0.0,  # never reached; skipped earlier
}

_MAGNITUDE_WEIGHTS: dict[str, float] = {
    "minor": 0.40,
    "moderate": 0.55,
    "major": 0.70,
    "extreme": 0.90,
    "unknown": 0.50,
}


# Minimum absolute sensitivity required to emit an impact.  Below
# this the holding is considered neutral to the factor and the
# propagator emits nothing — this is the primary portfolio-safety
# gate on the left side of the pipe.
MIN_ABS_SENSITIVITY = 0.25


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class FactorImpact:
    """A single factor-driven impact hypothesis on a holding.

    Attributes
    ----------
    factor:
        Factor key (stable taxonomy identifier).
    holding_id:
        Holding UUID.
    ticker:
        Holding ticker (for readability in logs / audit trails).
    portfolio_id:
        Scope tag so callers can filter.
    sensitivity:
        Resolved sensitivity record (value + source + sector).
    factor_direction:
        ``"up"`` / ``"down"`` / ``"unknown"`` — direction of the
        factor move itself.
    effect_direction:
        ``"positive"`` / ``"negative"`` / ``"unclear"`` — direction of
        the effect on the holding (sign of sensitivity × direction).
    magnitude:
        Pass-through from the classifier.
    factor_confidence:
        ``p_factor`` from the classifier, 0–1.
    holding_confidence:
        ``p_holding`` after propagation — this becomes the
        ``relevance_score`` on the EventLink row.
    rationale:
        Copied from the classification result for traceability.
    """

    factor: str
    holding_id: str
    ticker: str
    portfolio_id: str
    sensitivity: ResolvedSensitivity
    factor_direction: str
    effect_direction: str
    magnitude: str
    factor_confidence: float
    holding_confidence: float
    rationale: list[str] = field(default_factory=list)

    def to_details_json(self, event_id: str, event_title: str) -> dict:
        """Return the structured causal chain for storage.

        The shape is exactly the one specified by the Phase 9A brief.
        """
        return {
            "event": {
                "id": event_id,
                "title": (event_title or "")[:200],
            },
            "factor": {
                "key": self.factor,
                "direction": self.factor_direction,
                "magnitude": self.magnitude,
                "confidence": round(self.factor_confidence, 4),
                "rationale": list(self.rationale),
            },
            "holding": {
                "id": self.holding_id,
                "ticker": self.ticker,
                "portfolio_id": self.portfolio_id,
            },
            "sensitivity": {
                "value": round(self.sensitivity.value, 4),
                "source": self.sensitivity.source,
                "sector": self.sensitivity.sector,
            },
            "expected_effect": {
                "direction": self.effect_direction,
                "confidence": round(self.holding_confidence, 4),
            },
        }


# ---------------------------------------------------------------------------
# The propagator
# ---------------------------------------------------------------------------


class FactorPropagator:
    """Stateless deterministic factor → holding propagator."""

    # Persistence floor for ``EventLink(link_type="macro_factor")`` rows.
    #
    # Phase 9A design note (post-corrective-pass):
    # The original value matched the AnalysisAgent's generic
    # ``_MIN_ANALYSIS_RELEVANCE = 0.5`` gate so factor links would
    # either reach per-event LLM analysis or be dropped at write time.
    # That was too aggressive: under the brief's formula, default
    # sector priors can never produce ``p_holding >= 0.5`` (max ≈ 0.47),
    # so the entire factor pipeline produced zero operational output
    # for normal holdings without manual overrides.
    #
    # The fix is type-aware gating, NOT threshold inflation.  We now:
    #   1. Emit factor links at their honest computed ``p_holding`` as
    #      long as it clears this floor, which mirrors the propagator's
    #      own ``MIN_ABS_SENSITIVITY = 0.25`` and therefore represents
    #      "meaningful sector-level exposure, deterministically reasoned".
    #   2. Rely on ``AnalysisAgent._get_linked_holdings`` to EXCLUDE
    #      ``link_type == "macro_factor"`` from per-event LLM analysis
    #      regardless of score — factor links already carry a full
    #      deterministic causal chain in ``details_json`` and don't need
    #      to be re-narrated by the LLM.
    #   3. Surface factor links through a dedicated digest touchpoint
    #      path in ``AnalysisAgent.generate_digest``.
    #
    # This keeps the system conservative (floor matches |sens| gate),
    # honest (scores are the real computed ``p_holding``), and
    # operational out of the box.
    MACRO_FACTOR_LINK_MIN = 0.25

    # Retained as a backward-compatible alias so older imports
    # keep working if anyone depends on ``LINK_EMIT_THRESHOLD``.
    LINK_EMIT_THRESHOLD = MACRO_FACTOR_LINK_MIN

    def __init__(self, resolver: SensitivityResolver, policy=None) -> None:
        # Lazy import avoids a circular at package import time.
        from src.intelligence.policy import get_active_policy

        self._resolver = resolver
        self._policy = policy if policy is not None else get_active_policy()

    def propagate(
        self,
        classifications: Iterable[FactorClassification],
        holdings: Iterable[dict],
    ) -> list[FactorImpact]:
        """Produce holding-level impacts for a set of classified factors.

        Parameters
        ----------
        classifications:
            Factor classifications produced by the classifier.
        holdings:
            Holding dicts with at least ``id``, ``ticker``,
            ``portfolio_id``, and ``sector`` keys (``sector`` may be
            missing / None).
        """
        impacts: list[FactorImpact] = []
        holdings_list = list(holdings)
        for cls in classifications:
            for h in holdings_list:
                impact = self._propagate_one(cls, h)
                if impact is not None:
                    impacts.append(impact)
        return impacts

    # ------------------------------------------------------------------
    # One (factor, holding) pair
    # ------------------------------------------------------------------

    def _propagate_one(
        self,
        cls: FactorClassification,
        holding: dict,
    ) -> FactorImpact | None:
        holding_id = holding.get("id")
        ticker = holding.get("ticker") or ""
        portfolio_id = holding.get("portfolio_id") or ""
        sector = holding.get("sector")

        if not holding_id or not ticker:
            return None

        sens = self._resolver.resolve(
            holding_id=holding_id,
            factor=cls.factor,
            sector=sector,
        )

        # Gate 1: no sensitivity data at all → skip.
        if sens.source == "zero":
            return None

        # Gate 2: too-small absolute sensitivity → skip.
        # Policy value, with the module-level constant as a hard floor
        # so a loose policy can't drop below the Phase 9A baseline.
        min_abs_sens = max(
            self._policy.propagator_min_abs_sensitivity,
            MIN_ABS_SENSITIVITY,
        )
        if abs(sens.value) < min_abs_sens:
            return None

        # --- p_holding formula -------------------------------------
        # p_holding = clamp(
        #     p_factor * w_source * (0.5 + 0.5*abs(sensitivity)) * w_mag,
        #     p_holding_min, p_holding_max,
        # )
        w_source = self._policy.source_weight(sens.source)
        w_mag = self._policy.magnitude_weight(cls.magnitude)
        abs_sens = abs(sens.value)

        p_holding = (
            cls.confidence
            * w_source
            * (0.5 + 0.5 * abs_sens)
            * w_mag
        )
        p_holding = max(
            self._policy.propagator_p_holding_min,
            min(self._policy.propagator_p_holding_max, round(p_holding, 4)),
        )

        # --- Effect direction --------------------------------------
        effect_direction = self._infer_effect_direction(cls.direction, sens.value)

        return FactorImpact(
            factor=cls.factor,
            holding_id=holding_id,
            ticker=ticker,
            portfolio_id=portfolio_id,
            sensitivity=sens,
            factor_direction=cls.direction,
            effect_direction=effect_direction,
            magnitude=cls.magnitude,
            factor_confidence=cls.confidence,
            holding_confidence=p_holding,
            rationale=list(cls.rationale),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_effect_direction(factor_direction: str, sensitivity: float) -> str:
        """Sign-product of (direction, sensitivity).

        - up   * positive sensitivity → positive
        - up   * negative sensitivity → negative
        - down * positive sensitivity → negative
        - down * negative sensitivity → positive
        - unknown direction           → unclear
        """
        if factor_direction == "unknown":
            return "unclear"
        if sensitivity == 0:
            return "unclear"
        if factor_direction == "up":
            return "positive" if sensitivity > 0 else "negative"
        if factor_direction == "down":
            return "negative" if sensitivity > 0 else "positive"
        return "unclear"
