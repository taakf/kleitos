"""Unit / integration tests for the Phase 9C evaluation harness.

These run the live ``FactorClassifier`` + ``FactorPropagator``
against the gold benchmark with the baseline policy, so any
regression in either Phase 9A module shows up here immediately.
"""

from __future__ import annotations

import json

import pytest

from src.intelligence.evaluation import (
    BENCHMARK_VERSION,
    HARNESS_VERSION,
    EvaluationScenario,
    ExpectedFactor,
    GOLD_SCENARIOS,
    load_scenarios,
    run_evaluation,
)
from src.intelligence.evaluation.run import format_text_report, main
from src.intelligence.policy import BASELINE_POLICY, tuned


# ---------------------------------------------------------------------------
# Scenario loader
# ---------------------------------------------------------------------------


class TestScenarioLoader:
    def test_loader_excludes_skipped_by_default(self):
        active = load_scenarios()
        for s in active:
            assert not s.skip

    def test_loader_includes_skipped_when_asked(self):
        # Today there are no skipped scenarios, but the API must work.
        full = load_scenarios(include_skipped=True)
        assert len(full) >= len(load_scenarios())

    def test_every_scenario_has_unique_id(self):
        ids = [s.id for s in GOLD_SCENARIOS]
        assert len(ids) == len(set(ids)), "scenario ids must be unique"

    def test_every_scenario_has_family(self):
        for s in GOLD_SCENARIOS:
            assert s.family, f"scenario {s.id} missing family"

    def test_every_expected_factor_has_known_factor_key(self):
        from src.intelligence.factors.taxonomy import FACTOR_KEYS
        valid = set(FACTOR_KEYS)
        for s in GOLD_SCENARIOS:
            for ef in s.expected_factors:
                assert ef.factor in valid, (
                    f"scenario {s.id}: unknown factor key {ef.factor!r}"
                )

    def test_benchmark_covers_every_factor_family(self):
        from src.intelligence.factors.taxonomy import FACTOR_KEYS
        covered = {
            ef.factor for s in GOLD_SCENARIOS for ef in s.expected_factors
            if ef.should_fire
        }
        missing = set(FACTOR_KEYS) - covered
        assert not missing, f"benchmark missing families: {sorted(missing)}"


# ---------------------------------------------------------------------------
# End-to-end baseline evaluation
# ---------------------------------------------------------------------------


class TestBaselineEvaluation:
    """The Phase 9C baseline — these numbers pin the current
    classifier + propagator behavior.  Any regression in Phase 9A/9B
    shifts them and these tests fail loudly."""

    @pytest.fixture(scope="class")
    def report(self):
        return run_evaluation()

    def test_versions_present(self, report):
        assert report.benchmark_version == BENCHMARK_VERSION
        assert report.harness_version == HARNESS_VERSION
        assert report.policy_name == BASELINE_POLICY.name
        assert report.policy_version == BASELINE_POLICY.version

    def test_scenarios_run(self, report):
        assert report.scenarios_run >= 15
        assert report.scenarios_skipped == 0

    def test_classifier_precision_recall(self, report):
        fm = report.factor_metrics
        # Baseline MUST be clean on classification — no silent drift.
        assert fm.precision == 1.0
        assert fm.recall == 1.0
        assert fm.false_positive_rate == 0.0

    def test_classifier_direction_accuracy(self, report):
        # Post-Phase-9C corrective pass: there are NO documented
        # known-weakness exclusions in the benchmark, so this
        # number reflects the real classifier's direction output
        # on every expected-factor row in the dataset.  Any
        # classifier regression immediately drops it below 1.0.
        assert report.factor_metrics.direction_accuracy == 1.0

    def test_classifier_brier_is_synthetic(self, report):
        b = report.factor_metrics.brier
        assert b is not None
        assert b.is_synthetic is True
        # The synthetic Brier must be low — the classifier's outputs
        # track our design-time targets reasonably closely.
        assert b.score < 0.10

    def test_propagation_sign_accuracy(self, report):
        assert report.propagation_metrics.sign_accuracy == 1.0

    def test_propagation_portfolio_isolation_pass(self, report):
        pm = report.propagation_metrics
        assert pm.portfolio_isolation_pass is True
        assert pm.portfolio_isolation_violations == 0
        assert pm.portfolio_isolation_checks >= 1

    def test_no_known_weaknesses_in_baseline(self, report):
        """Post-Phase-9C corrective pass: the two previously-documented
        classifier weaknesses (``oil.opec_output_cut`` and
        ``trade.sanctions_lifted``) were fixed in the classifier and
        their scenarios are now regular passing cases.  The baseline
        must therefore carry an empty ``known_weaknesses`` list — any
        regression that re-opens those weaknesses fails this test.

        Future documented weaknesses can be added to the benchmark by
        setting ``known_weakness=True`` on the relevant
        ``ExpectedFactor``; this test should then be tightened to
        assert the expected set of IDs rather than flipped back."""
        assert report.known_weaknesses == [], (
            f"unexpected known_weaknesses in baseline: "
            f"{[(kw.scenario_id, kw.aspect, kw.reason) for kw in report.known_weaknesses]}"
        )

    def test_no_unexpected_confusing_cases(self, report):
        """The baseline must have zero confusing cases — any non-
        documented failure should fail this test."""
        assert report.confusing_cases == [], (
            f"unexpected confusing cases: "
            f"{[(c.scenario_id, c.reason) for c in report.confusing_cases]}"
        )

    def test_report_is_json_serializable(self, report):
        d = report.summary_dict()
        # Must not raise
        json.dumps(d)
        assert d["benchmark_version"] == BENCHMARK_VERSION
        assert d["is_synthetic_benchmark"] is True
        assert "classifier" in d and "propagation" in d
        assert "known_weaknesses" in d

    def test_report_preserves_synthetic_flag(self, report):
        """The synthetic-benchmark flag must propagate into both the
        top-level report and the per-metric Brier summaries.  This
        is the honesty invariant: downstream consumers can never
        mistake these numbers for real calibration."""
        assert report.is_synthetic_benchmark is True
        assert report.factor_metrics.brier.is_synthetic is True
        assert report.propagation_metrics.brier.is_synthetic is True


# ---------------------------------------------------------------------------
# Policy sensitivity
# ---------------------------------------------------------------------------


class TestPolicySensitivity:
    def test_strict_policy_drops_classifications(self):
        strict = tuned(
            classifier_min_confidence=0.99,
            name="strict_sweep_v1",
        )
        report = run_evaluation(policy=strict)
        fm = report.factor_metrics
        # Strict policy should push recall below 1.0 — this proves
        # the policy layer is actually influencing the classifier
        # through the harness.
        assert fm.recall < 1.0

    def test_loose_policy_accepts_more(self):
        # A very loose link-emission threshold should let more
        # propagator impacts through AND not break invariants.
        loose = tuned(
            classifier_min_confidence=0.30,
            macro_factor_link_min=0.15,
            name="loose_sweep_v1",
        )
        report = run_evaluation(policy=loose)
        # Baseline precision is 1.0; loose policy must not regress
        # precision below a clear floor — pinning 0.9 gives a
        # margin without over-constraining the sweep.
        assert report.factor_metrics.precision >= 0.9
        assert report.propagation_metrics.portfolio_isolation_pass is True


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


class TestRunnerCLI:
    def test_format_text_report_contains_key_sections(self):
        report = run_evaluation()
        text = format_text_report(report)
        assert "Axion Intelligence Evaluation" in text
        assert "Classifier" in text
        assert "Propagation" in text
        assert "synthetic benchmark" in text.lower()
        assert f"benchmark       : {BENCHMARK_VERSION}" in text

    def test_main_writes_json_and_returns_zero(self, tmp_path):
        out = tmp_path / "report.json"
        rc = main(["--json", str(out), "--quiet"])
        assert rc == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["benchmark_version"] == BENCHMARK_VERSION
        assert data["is_synthetic_benchmark"] is True
        assert data["propagation"]["portfolio_isolation_pass"] is True

    def test_main_quiet_mode(self, capsys, tmp_path):
        main(["--json", str(tmp_path / "r.json"), "--quiet"])
        captured = capsys.readouterr()
        # Quiet mode suppresses the text report on stdout
        assert "Axion Intelligence Evaluation" not in captured.out


# ---------------------------------------------------------------------------
# Custom scenario end-to-end
# ---------------------------------------------------------------------------


class TestCustomScenario:
    """The harness must accept caller-supplied scenarios so evaluation
    sweeps and ad-hoc probes don't need to modify the gold dataset."""

    def test_run_with_custom_scenarios(self):
        custom = (
            EvaluationScenario(
                id="custom.trivial_fed",
                family="interest_rate",
                title="Federal Reserve raises interest rates by 50 bps",
                summary="FOMC cited persistent inflation.",
                expected_factors=(
                    ExpectedFactor(
                        "interest_rate", should_fire=True,
                        direction="up", target_confidence=0.90,
                    ),
                ),
            ),
        )
        report = run_evaluation(scenarios=custom)
        assert report.scenarios_run == 1
        assert report.factor_metrics.true_positives == 1
        assert report.factor_metrics.false_positives == 0
