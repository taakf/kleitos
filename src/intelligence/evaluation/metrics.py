"""Metrics for the Phase 9C evaluation harness.

Small, readable, testable.  No numpy / sklearn dependency — the
numbers are simple enough that adding a dependency for them would
be overbuilding.  Every function here is a pure transform on the
harness's run-time observations; nothing touches the database.

Honest labeling
---------------
Every metric that could be mistaken for real calibration is
explicitly labeled as *synthetic-benchmark* in its docstring and
its output.  The harness reports pass this label through to the
top-level report so there is never any ambiguity about whether a
number is from a real-world calibration run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ---------------------------------------------------------------------------
# Raw observations
# ---------------------------------------------------------------------------


@dataclass
class FactorObservation:
    """A single classifier prediction against a gold target.

    Populated by the harness for every (scenario, expected factor)
    pair and every unexpected factor that actually fired.
    """

    scenario_id: str
    factor: str
    expected: bool              # True if the factor should fire
    predicted: bool             # True if the factor actually fired
    expected_direction: str | None = None
    predicted_direction: str | None = None
    expected_magnitude: str | None = None
    predicted_magnitude: str | None = None
    target_confidence: float | None = None
    predicted_confidence: float | None = None
    #: Phase 9C honesty flag — marked-known gold expectations are
    #: excluded from direction/magnitude accuracy denominators and
    #: reported in a separate section of the evaluation output.
    known_weakness: bool = False
    known_weakness_reason: str = ""


@dataclass
class ImpactObservation:
    """A single propagator prediction against a gold expected impact."""

    scenario_id: str
    factor: str
    ticker: str
    portfolio_id: str
    expected_link: bool
    predicted_link: bool
    expected_effect: str | None = None
    predicted_effect: str | None = None
    target_confidence: float | None = None
    predicted_confidence: float | None = None


@dataclass
class RelationshipObservation:
    """A single relationship-propagation prediction (Phase 9D)."""

    scenario_id: str
    ticker: str
    portfolio_id: str
    relationship_type: str
    related_entity_key: str
    expected_link: bool
    predicted_link: bool
    target_confidence: float | None = None
    predicted_confidence: float | None = None


# ---------------------------------------------------------------------------
# Aggregated metric shapes
# ---------------------------------------------------------------------------


@dataclass
class BrierSummary:
    """Brier score summary with honesty flag.

    Brier score on a synthetic benchmark measures how closely the
    classifier's confidences track our design-time expectations.
    It is NOT a calibrated real-world probability metric.  The
    ``is_synthetic`` flag is always True in Phase 9C and is passed
    through the report so downstream consumers can never mistake
    it for something it isn't.
    """

    score: float               # 0 is perfect, 1 is worst
    n: int
    is_synthetic: bool = True


@dataclass
class ReliabilityBin:
    """One bucket of the synthetic-calibration reliability diagram."""

    lower: float
    upper: float
    count: int
    mean_predicted: float
    mean_expected: float       # For synthetic data this is mean(target)


@dataclass
class FactorClassifierMetrics:
    """Aggregate classifier metrics across a scenario run."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    direction_correct: int = 0
    direction_total: int = 0
    magnitude_correct: int = 0
    magnitude_total: int = 0
    brier: BrierSummary | None = None
    reliability_bins: list[ReliabilityBin] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    @property
    def false_positive_rate(self) -> float:
        denom = self.false_positives + self.true_negatives
        return self.false_positives / denom if denom else 0.0

    @property
    def direction_accuracy(self) -> float:
        return self.direction_correct / self.direction_total if self.direction_total else 1.0

    @property
    def magnitude_accuracy(self) -> float:
        return self.magnitude_correct / self.magnitude_total if self.magnitude_total else 1.0


@dataclass
class PropagationMetrics:
    """Aggregate propagation metrics across a scenario run."""

    true_links: int = 0          # should emit AND emitted
    missed_links: int = 0        # should emit AND not emitted
    extra_links: int = 0         # should not emit but emitted
    correctly_suppressed: int = 0  # should not emit AND not emitted
    sign_correct: int = 0
    sign_total: int = 0
    portfolio_isolation_violations: int = 0
    portfolio_isolation_checks: int = 0
    brier: BrierSummary | None = None
    reliability_bins: list[ReliabilityBin] = field(default_factory=list)

    @property
    def emission_precision(self) -> float:
        denom = self.true_links + self.extra_links
        return self.true_links / denom if denom else 1.0

    @property
    def emission_recall(self) -> float:
        denom = self.true_links + self.missed_links
        return self.true_links / denom if denom else 1.0

    @property
    def sign_accuracy(self) -> float:
        return self.sign_correct / self.sign_total if self.sign_total else 1.0

    @property
    def portfolio_isolation_pass(self) -> bool:
        return self.portfolio_isolation_violations == 0


@dataclass
class RelationshipMetrics:
    """Aggregate relationship-propagation metrics (Phase 9D)."""

    true_links: int = 0
    missed_links: int = 0
    extra_links: int = 0
    correctly_suppressed: int = 0
    brier: BrierSummary | None = None
    portfolio_isolation_violations: int = 0
    portfolio_isolation_checks: int = 0
    #: Highest predicted confidence across all observed links.  Used
    #: to enforce the Phase 9D invariant that relationship confidence
    #: stays below the direct-match ceiling.
    max_predicted_confidence: float = 0.0

    @property
    def emission_precision(self) -> float:
        denom = self.true_links + self.extra_links
        return self.true_links / denom if denom else 1.0

    @property
    def emission_recall(self) -> float:
        denom = self.true_links + self.missed_links
        return self.true_links / denom if denom else 1.0

    @property
    def portfolio_isolation_pass(self) -> bool:
        return self.portfolio_isolation_violations == 0


@dataclass
class ConfusingCase:
    """A scenario that triggered a metric failure — surfaced in reports."""

    scenario_id: str
    family: str
    reason: str


@dataclass
class KnownWeakness:
    """A gold expectation deliberately marked as a known-classifier
    weakness.  These show up in a separate evaluation section so the
    baseline is honest: expected failure vs. surprise failure."""

    scenario_id: str
    factor: str
    aspect: str                 # "direction" | "magnitude" | "fires" | ...
    reason: str


@dataclass
class EvaluationReport:
    """Top-level evaluation report — the thing the harness returns."""

    benchmark_version: str
    policy_name: str
    policy_version: int
    harness_version: str
    scenarios_run: int
    scenarios_skipped: int
    factor_metrics: FactorClassifierMetrics
    propagation_metrics: PropagationMetrics
    relationship_metrics: RelationshipMetrics = field(
        default_factory=lambda: RelationshipMetrics()
    )
    confusing_cases: list[ConfusingCase] = field(default_factory=list)
    known_weaknesses: list[KnownWeakness] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    is_synthetic_benchmark: bool = True  # do NOT change

    def summary_dict(self) -> dict:
        """JSON-safe summary used by the CLI runner and health endpoint."""
        fm = self.factor_metrics
        pm = self.propagation_metrics
        return {
            "benchmark_version": self.benchmark_version,
            "harness_version": self.harness_version,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "scenarios_run": self.scenarios_run,
            "scenarios_skipped": self.scenarios_skipped,
            "is_synthetic_benchmark": self.is_synthetic_benchmark,
            "classifier": {
                "true_positives": fm.true_positives,
                "false_positives": fm.false_positives,
                "false_negatives": fm.false_negatives,
                "true_negatives": fm.true_negatives,
                "precision": round(fm.precision, 4),
                "recall": round(fm.recall, 4),
                "f1": round(fm.f1, 4),
                "false_positive_rate": round(fm.false_positive_rate, 4),
                "direction_accuracy": round(fm.direction_accuracy, 4),
                "magnitude_accuracy": round(fm.magnitude_accuracy, 4),
                "brier": _brier_dict(fm.brier),
                "reliability_bins": [_bin_dict(b) for b in fm.reliability_bins],
            },
            "propagation": {
                "true_links": pm.true_links,
                "missed_links": pm.missed_links,
                "extra_links": pm.extra_links,
                "correctly_suppressed": pm.correctly_suppressed,
                "emission_precision": round(pm.emission_precision, 4),
                "emission_recall": round(pm.emission_recall, 4),
                "sign_accuracy": round(pm.sign_accuracy, 4),
                "portfolio_isolation_violations": pm.portfolio_isolation_violations,
                "portfolio_isolation_checks": pm.portfolio_isolation_checks,
                "portfolio_isolation_pass": pm.portfolio_isolation_pass,
                "brier": _brier_dict(pm.brier),
                "reliability_bins": [_bin_dict(b) for b in pm.reliability_bins],
            },
            "relationships": {
                "true_links": self.relationship_metrics.true_links,
                "missed_links": self.relationship_metrics.missed_links,
                "extra_links": self.relationship_metrics.extra_links,
                "correctly_suppressed": self.relationship_metrics.correctly_suppressed,
                "emission_precision": round(self.relationship_metrics.emission_precision, 4),
                "emission_recall": round(self.relationship_metrics.emission_recall, 4),
                "portfolio_isolation_violations": self.relationship_metrics.portfolio_isolation_violations,
                "portfolio_isolation_checks": self.relationship_metrics.portfolio_isolation_checks,
                "portfolio_isolation_pass": self.relationship_metrics.portfolio_isolation_pass,
                "max_predicted_confidence": round(
                    self.relationship_metrics.max_predicted_confidence, 4,
                ),
                "brier": _brier_dict(self.relationship_metrics.brier),
            },
            "confusing_cases": [
                {"scenario_id": c.scenario_id, "family": c.family, "reason": c.reason}
                for c in self.confusing_cases
            ],
            "known_weaknesses": [
                {
                    "scenario_id": kw.scenario_id,
                    "factor": kw.factor,
                    "aspect": kw.aspect,
                    "reason": kw.reason,
                }
                for kw in self.known_weaknesses
            ],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Pure metric computations
# ---------------------------------------------------------------------------


def _brier_dict(b: BrierSummary | None) -> dict | None:
    if b is None:
        return None
    return {"score": round(b.score, 4), "n": b.n, "is_synthetic": b.is_synthetic}


def _bin_dict(b: ReliabilityBin) -> dict:
    return {
        "lower": round(b.lower, 2),
        "upper": round(b.upper, 2),
        "count": b.count,
        "mean_predicted": round(b.mean_predicted, 4),
        "mean_expected": round(b.mean_expected, 4),
    }


def compute_brier(
    pairs: Iterable[tuple[float, float]],
) -> BrierSummary | None:
    """Compute Brier score for a list of (predicted, target) pairs.

    Returns None if the list is empty.  Explicitly flagged as
    ``is_synthetic=True`` so the report can never present this as
    a real-world calibration number.
    """
    items = list(pairs)
    if not items:
        return None
    total = 0.0
    for pred, target in items:
        total += (pred - target) ** 2
    return BrierSummary(score=total / len(items), n=len(items), is_synthetic=True)


def compute_reliability_bins(
    pairs: Iterable[tuple[float, float]],
    bin_edges: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
) -> list[ReliabilityBin]:
    """Bucket (predicted, target) pairs into reliability bins.

    Synthetic-calibration ONLY.  Since the "target" here is the
    design-time probability (not an observed outcome frequency),
    ``mean_expected`` is the mean of the targets, not an empirical
    rate.  The bucket structure is identical to a real reliability
    diagram so downstream calibration code can reuse the shape.
    """
    items = list(pairs)
    bins: list[ReliabilityBin] = []
    for i in range(len(bin_edges) - 1):
        lower = bin_edges[i]
        upper = bin_edges[i + 1]
        # Closed-open on the left, closed on the right for the last bucket.
        if i == len(bin_edges) - 2:
            bucket = [(p, t) for p, t in items if lower <= p <= upper]
        else:
            bucket = [(p, t) for p, t in items if lower <= p < upper]
        if not bucket:
            continue
        mean_pred = sum(p for p, _ in bucket) / len(bucket)
        mean_exp = sum(t for _, t in bucket) / len(bucket)
        bins.append(ReliabilityBin(
            lower=lower,
            upper=upper,
            count=len(bucket),
            mean_predicted=mean_pred,
            mean_expected=mean_exp,
        ))
    return bins


def compute_factor_metrics(
    observations: Iterable[FactorObservation],
) -> FactorClassifierMetrics:
    """Aggregate classifier observations into a metrics summary.

    Known-weakness observations (``obs.known_weakness=True``) are
    excluded from direction / magnitude accuracy denominators so
    the baseline is stable — but they're NOT excluded from
    precision/recall, because a known-weakness observation whose
    factor suddenly stops firing is still a regression worth
    catching.
    """
    m = FactorClassifierMetrics()
    brier_pairs: list[tuple[float, float]] = []
    for obs in observations:
        if obs.expected and obs.predicted:
            m.true_positives += 1
            if (
                obs.expected_direction and obs.predicted_direction
                and not obs.known_weakness
            ):
                m.direction_total += 1
                if obs.expected_direction == obs.predicted_direction:
                    m.direction_correct += 1
            if (
                obs.expected_magnitude and obs.predicted_magnitude
                and not obs.known_weakness
            ):
                m.magnitude_total += 1
                if obs.expected_magnitude == obs.predicted_magnitude:
                    m.magnitude_correct += 1
            if obs.target_confidence is not None and obs.predicted_confidence is not None:
                brier_pairs.append((obs.predicted_confidence, obs.target_confidence))
        elif obs.expected and not obs.predicted:
            m.false_negatives += 1
        elif (not obs.expected) and obs.predicted:
            m.false_positives += 1
        else:  # expected False and predicted False
            m.true_negatives += 1

    m.brier = compute_brier(brier_pairs)
    m.reliability_bins = compute_reliability_bins(brier_pairs)
    return m


def compute_propagation_metrics(
    observations: Iterable[ImpactObservation],
) -> PropagationMetrics:
    """Aggregate propagation observations into a metrics summary."""
    m = PropagationMetrics()
    brier_pairs: list[tuple[float, float]] = []
    for obs in observations:
        if obs.expected_link and obs.predicted_link:
            m.true_links += 1
            if obs.expected_effect and obs.predicted_effect:
                m.sign_total += 1
                if obs.expected_effect == obs.predicted_effect:
                    m.sign_correct += 1
            if obs.target_confidence is not None and obs.predicted_confidence is not None:
                brier_pairs.append((obs.predicted_confidence, obs.target_confidence))
        elif obs.expected_link and not obs.predicted_link:
            m.missed_links += 1
        elif (not obs.expected_link) and obs.predicted_link:
            m.extra_links += 1
        else:
            m.correctly_suppressed += 1

    m.brier = compute_brier(brier_pairs)
    m.reliability_bins = compute_reliability_bins(brier_pairs)
    return m


def compute_relationship_metrics(
    observations: Iterable[RelationshipObservation],
) -> RelationshipMetrics:
    """Aggregate relationship observations into a metrics summary."""
    m = RelationshipMetrics()
    brier_pairs: list[tuple[float, float]] = []
    for obs in observations:
        if obs.expected_link and obs.predicted_link:
            m.true_links += 1
            if (
                obs.target_confidence is not None
                and obs.predicted_confidence is not None
            ):
                brier_pairs.append((obs.predicted_confidence, obs.target_confidence))
        elif obs.expected_link and not obs.predicted_link:
            m.missed_links += 1
        elif (not obs.expected_link) and obs.predicted_link:
            m.extra_links += 1
        else:
            m.correctly_suppressed += 1
        if obs.predicted_confidence is not None:
            if obs.predicted_confidence > m.max_predicted_confidence:
                m.max_predicted_confidence = obs.predicted_confidence

    m.brier = compute_brier(brier_pairs)
    return m
