"""Confidence policy layer (Phase 9C).

Single source of truth for every threshold the deterministic
intelligence pipeline uses at runtime.  Previously these lived as
module-level constants scattered across ``factors/classifier.py``,
``factors/propagation.py``, ``agents/collection.py``, and
``agents/analysis.py`` — a classic "magic number" situation that
made tuning dangerous and evaluation hard.

Phase 9C centralises them into a single named, versioned
``ConfidencePolicy`` that the live runtime reads from one place.
Nothing is numerically different from the Phase 9A/9B baseline —
this is a pure refactor plus an explicit surface for evaluation
and future calibration.

Why a dataclass, not a YAML config?
-----------------------------------
* We want determinism and type-safety first.
* Swappable policy instances make evaluation (see
  ``src.intelligence.evaluation``) trivial — just hand the runner
  a different policy and every threshold moves consistently.
* Future calibration can serialize a policy to disk if needed;
  today that would be premature.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Final


# ---------------------------------------------------------------------------
# Immutable defaults — the Phase 9A/9B baseline
# ---------------------------------------------------------------------------


#: Default source-weight table used by the propagator.
#: ``"zero"`` is kept so callers can look up a sentinel for skipped
#: resolutions; it is never actually multiplied through the formula.
_DEFAULT_SOURCE_WEIGHTS: Final[dict[str, float]] = {
    "default": 0.55,
    "ai_inferred": 0.70,
    "manual": 0.90,
    "zero": 0.0,
}

#: Default magnitude-weight table used by the propagator.
_DEFAULT_MAGNITUDE_WEIGHTS: Final[dict[str, float]] = {
    "minor": 0.40,
    "moderate": 0.55,
    "major": 0.70,
    "extreme": 0.90,
    "unknown": 0.50,
}


# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidencePolicy:
    """A single named, versioned set of intelligence-pipeline thresholds.

    Every numeric threshold the live runtime relies on lives here.
    Callers that need a threshold MUST read it from a policy instance
    (normally ``get_active_policy()``) rather than hard-coding a value.
    """

    # --- Identity ----------------------------------------------------
    #: Short stable name used in evaluation reports, health output,
    #: and audit trails.
    name: str = "phase9a_baseline_v1"
    #: Integer version; bump on every tuning change so reports can
    #: distinguish runs.  NOT the Axion app version.
    version: int = 1
    #: One-line description printed in evaluation output.
    description: str = (
        "Conservative Phase 9A baseline: honest sector-prior floor, "
        "type-aware analysis exclusion, no production calibration yet."
    )

    # --- Classifier gates --------------------------------------------
    #: Minimum ``p_factor`` below which a classification is dropped
    #: entirely (``FactorClassifier.classify`` floor).
    classifier_min_confidence: float = 0.35

    # --- Propagator gates --------------------------------------------
    #: Absolute sensitivity below which a (factor, holding) pair is
    #: skipped at propagation time.  A holding whose resolved
    #: sensitivity is below this number is considered neutral to
    #: the factor.
    propagator_min_abs_sensitivity: float = 0.25

    #: Minimum ``p_holding`` required to persist a
    #: ``EventLink(link_type="macro_factor")`` row.  Below this the
    #: propagator still emits the structured impact, but the link is
    #: dropped before it reaches the database.  Matches
    #: ``propagator_min_abs_sensitivity`` by design — see the Phase 9A
    #: corrective-pass rationale in ``factors/propagation.py``.
    macro_factor_link_min: float = 0.25

    #: ``p_holding`` bounds — post-corrective-pass, honest caps.
    propagator_p_holding_min: float = 0.05
    propagator_p_holding_max: float = 0.85

    # --- Analysis agent gates ----------------------------------------
    #: Minimum ``relevance_score`` on an ``EventLink`` for the
    #: ``AnalysisAgent`` to consider it for per-event LLM analysis.
    #: Factor links are ALSO excluded by type regardless of this gate
    #: (see ``AnalysisAgent._ANALYSIS_EXCLUDED_LINK_TYPES``) — the
    #: number below only governs non-factor link types.
    analysis_min_relevance: float = 0.5

    # --- Weight tables -----------------------------------------------
    source_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_SOURCE_WEIGHTS)
    )
    magnitude_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MAGNITUDE_WEIGHTS)
    )

    # --- Optional per-factor overrides (reserved for calibration) ----
    #: Factor-key → override dict.  A future phase can specialise a
    #: single factor's gates without forking the whole policy.  Kept
    #: empty in the baseline but wired through so calibration work
    #: doesn't need a schema change.
    per_factor_overrides: dict[str, dict[str, float]] = field(
        default_factory=dict
    )

    # --- Derived helpers ---------------------------------------------

    def source_weight(self, source: str) -> float:
        """Return the weight for a given sensitivity source, with a
        conservative fallback to ``default`` if the source is unknown.
        """
        if source in self.source_weights:
            return self.source_weights[source]
        return self.source_weights.get("default", 0.55)

    def magnitude_weight(self, magnitude: str) -> float:
        """Return the weight for a given factor magnitude, with a
        conservative fallback to ``unknown`` if the label is unknown.
        """
        if magnitude in self.magnitude_weights:
            return self.magnitude_weights[magnitude]
        return self.magnitude_weights.get("unknown", 0.50)

    def factor_override(self, factor_key: str, attr: str) -> float | None:
        """Return a per-factor override for ``attr`` or ``None`` if
        the factor uses the policy defaults."""
        block = self.per_factor_overrides.get(factor_key)
        if not block:
            return None
        return block.get(attr)

    def describe(self) -> dict[str, object]:
        """Return a JSON-safe summary for health / evaluation output."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "classifier_min_confidence": self.classifier_min_confidence,
            "propagator_min_abs_sensitivity": self.propagator_min_abs_sensitivity,
            "macro_factor_link_min": self.macro_factor_link_min,
            "propagator_p_holding_min": self.propagator_p_holding_min,
            "propagator_p_holding_max": self.propagator_p_holding_max,
            "analysis_min_relevance": self.analysis_min_relevance,
            "source_weights": dict(self.source_weights),
            "magnitude_weights": dict(self.magnitude_weights),
            "per_factor_overrides": dict(self.per_factor_overrides),
        }


# ---------------------------------------------------------------------------
# Singleton-like access
# ---------------------------------------------------------------------------


#: Baseline policy — the Phase 9A/9B honest floor.  Do NOT mutate.
BASELINE_POLICY: Final[ConfidencePolicy] = ConfidencePolicy()


_active_policy: ConfidencePolicy = BASELINE_POLICY


def get_active_policy() -> ConfidencePolicy:
    """Return the policy currently in force across the live runtime.

    Tests and the evaluation harness may override this via
    ``set_active_policy`` — always restore the previous value in
    teardown.
    """
    return _active_policy


def set_active_policy(policy: ConfidencePolicy) -> ConfidencePolicy:
    """Swap the active policy; returns the previously-active policy
    so the caller can restore it (test-friendly pattern)."""
    global _active_policy
    previous = _active_policy
    _active_policy = policy
    return previous


def reset_active_policy() -> None:
    """Restore the baseline policy.  Safe to call repeatedly."""
    global _active_policy
    _active_policy = BASELINE_POLICY


# ---------------------------------------------------------------------------
# Tuning helper (read-only)
# ---------------------------------------------------------------------------


def tuned(
    base: ConfidencePolicy = BASELINE_POLICY,
    **overrides,
) -> ConfidencePolicy:
    """Return a new ``ConfidencePolicy`` with selected fields replaced.

    Used by the evaluation harness to run sensitivity sweeps without
    mutating the baseline.  Just a ``dataclasses.replace`` wrapper.
    """
    return replace(base, **overrides)
