"""Phase 9C intelligence evaluation package.

A lightweight, deterministic evaluation harness for Axion's
intelligence pipeline.  Lets us measure classifier precision /
recall, propagation sign accuracy, portfolio-isolation safety,
and confidence calibration on a synthetic benchmark.

This is explicitly NOT real-world production calibration.  The
benchmark is hand-authored gold data; every probability target
here is a design-time expectation, not a probability elicited
from humans or markets.  Phase 9C's job is to give us:

* a repeatable way to detect regressions
* a surface for tuning the confidence policy
* a clean basis for later calibration against real data

Anything that looks like "calibration" in this module is
explicitly labeled as synthetic-calibration to avoid ever being
confused with production numbers.
"""

from src.intelligence.evaluation.scenarios import (
    BENCHMARK_VERSION,
    EvaluationScenario,
    ExpectedFactor,
    ExpectedImpact,
    GOLD_SCENARIOS,
    load_scenarios,
)
from src.intelligence.evaluation.metrics import (
    BrierSummary,
    EvaluationReport,
    FactorClassifierMetrics,
    PropagationMetrics,
    ReliabilityBin,
    compute_factor_metrics,
    compute_propagation_metrics,
)
from src.intelligence.evaluation.harness import (
    HARNESS_VERSION,
    EvaluationRunResult,
    run_evaluation,
)

__all__ = [
    "BENCHMARK_VERSION",
    "HARNESS_VERSION",
    "EvaluationScenario",
    "ExpectedFactor",
    "ExpectedImpact",
    "GOLD_SCENARIOS",
    "load_scenarios",
    "BrierSummary",
    "EvaluationReport",
    "FactorClassifierMetrics",
    "PropagationMetrics",
    "ReliabilityBin",
    "compute_factor_metrics",
    "compute_propagation_metrics",
    "EvaluationRunResult",
    "run_evaluation",
]
