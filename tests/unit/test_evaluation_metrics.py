"""Unit tests for the Phase 9C evaluation metrics module.

These are pure math / dataclass tests — no DB, no classifier.
They pin the metric contracts so future changes can't silently
alter calibration output shape.
"""

from __future__ import annotations

import pytest

from src.intelligence.evaluation.metrics import (
    FactorObservation,
    ImpactObservation,
    compute_brier,
    compute_factor_metrics,
    compute_propagation_metrics,
    compute_reliability_bins,
)


# ---------------------------------------------------------------------------
# Brier
# ---------------------------------------------------------------------------


class TestBrier:
    def test_empty_returns_none(self):
        assert compute_brier([]) is None

    def test_perfect_predictions_score_zero(self):
        b = compute_brier([(0.5, 0.5), (0.9, 0.9), (0.1, 0.1)])
        assert b is not None
        assert b.score == pytest.approx(0.0)
        assert b.n == 3
        assert b.is_synthetic is True

    def test_worst_case_scores_one(self):
        b = compute_brier([(0.0, 1.0), (1.0, 0.0)])
        assert b.score == pytest.approx(1.0)

    def test_mixed_case(self):
        # (0.8 - 1.0)^2 + (0.3 - 0.0)^2 = 0.04 + 0.09 = 0.13 / 2 = 0.065
        b = compute_brier([(0.8, 1.0), (0.3, 0.0)])
        assert b.score == pytest.approx(0.065)
        assert b.n == 2

    def test_synthetic_flag_always_true(self):
        b = compute_brier([(0.5, 0.5)])
        assert b.is_synthetic is True


# ---------------------------------------------------------------------------
# Reliability bins
# ---------------------------------------------------------------------------


class TestReliabilityBins:
    def test_empty_input_returns_empty(self):
        assert compute_reliability_bins([]) == []

    def test_single_bucket_aggregates(self):
        bins = compute_reliability_bins([
            (0.45, 0.50), (0.55, 0.52), (0.50, 0.48),
        ])
        assert len(bins) == 1
        b = bins[0]
        assert b.lower == 0.4
        assert b.upper == 0.6
        assert b.count == 3
        assert b.mean_predicted == pytest.approx(0.5)
        assert b.mean_expected == pytest.approx(0.5)

    def test_multiple_buckets(self):
        bins = compute_reliability_bins([
            (0.1, 0.15),
            (0.45, 0.55),
            (0.75, 0.80),
            (0.95, 0.90),
        ])
        assert len(bins) == 4
        # Buckets are deterministic
        assert [b.count for b in bins] == [1, 1, 1, 1]

    def test_last_bucket_is_closed_on_right(self):
        bins = compute_reliability_bins([(1.0, 1.0)])
        assert len(bins) == 1
        assert bins[0].upper == 1.0
        assert bins[0].count == 1


# ---------------------------------------------------------------------------
# Factor metrics aggregation
# ---------------------------------------------------------------------------


def _fo(
    expected: bool, predicted: bool,
    expected_dir: str | None = None, predicted_dir: str | None = None,
    known: bool = False,
    target_conf: float | None = None,
    predicted_conf: float | None = None,
) -> FactorObservation:
    return FactorObservation(
        scenario_id="s",
        factor="interest_rate",
        expected=expected,
        predicted=predicted,
        expected_direction=expected_dir,
        predicted_direction=predicted_dir,
        target_confidence=target_conf,
        predicted_confidence=predicted_conf,
        known_weakness=known,
    )


class TestFactorMetrics:
    def test_perfect_precision_recall(self):
        obs = [_fo(True, True), _fo(True, True), _fo(False, False)]
        m = compute_factor_metrics(obs)
        assert m.true_positives == 2
        assert m.true_negatives == 1
        assert m.false_positives == 0
        assert m.false_negatives == 0
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.false_positive_rate == 0.0

    def test_false_positive_and_negative(self):
        obs = [
            _fo(True, True),
            _fo(True, False),       # missed — false negative
            _fo(False, True),       # noise — false positive
            _fo(False, False),
        ]
        m = compute_factor_metrics(obs)
        assert m.true_positives == 1
        assert m.false_negatives == 1
        assert m.false_positives == 1
        assert m.true_negatives == 1
        assert m.precision == pytest.approx(0.5)
        assert m.recall == pytest.approx(0.5)
        assert m.f1 == pytest.approx(0.5)

    def test_direction_accuracy(self):
        obs = [
            _fo(True, True, expected_dir="up", predicted_dir="up"),
            _fo(True, True, expected_dir="down", predicted_dir="up"),
            _fo(True, True, expected_dir="up", predicted_dir="up"),
        ]
        m = compute_factor_metrics(obs)
        assert m.direction_total == 3
        assert m.direction_correct == 2
        assert m.direction_accuracy == pytest.approx(2 / 3)

    def test_known_weakness_excluded_from_direction_denom(self):
        """Marked-known observations must NOT count toward direction
        accuracy — the baseline stays stable across those cases."""
        obs = [
            _fo(True, True, expected_dir="up", predicted_dir="up"),
            _fo(True, True, expected_dir="down", predicted_dir="up", known=True),
        ]
        m = compute_factor_metrics(obs)
        assert m.direction_total == 1      # only the non-known obs counted
        assert m.direction_correct == 1
        assert m.direction_accuracy == 1.0

    def test_brier_aggregation(self):
        obs = [
            _fo(True, True, target_conf=0.9, predicted_conf=0.9),
            _fo(True, True, target_conf=0.5, predicted_conf=0.7),
        ]
        m = compute_factor_metrics(obs)
        assert m.brier is not None
        assert m.brier.n == 2
        # (0.9 - 0.9)^2 + (0.7 - 0.5)^2 = 0 + 0.04 = 0.04 / 2 = 0.02
        assert m.brier.score == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Propagation metrics aggregation
# ---------------------------------------------------------------------------


def _io(
    expected_link: bool, predicted_link: bool,
    exp_effect: str | None = None, pred_effect: str | None = None,
    portfolio_id: str = "default",
) -> ImpactObservation:
    return ImpactObservation(
        scenario_id="s",
        factor="interest_rate",
        ticker="AAPL",
        portfolio_id=portfolio_id,
        expected_link=expected_link,
        predicted_link=predicted_link,
        expected_effect=exp_effect,
        predicted_effect=pred_effect,
    )


class TestPropagationMetrics:
    def test_perfect_links(self):
        obs = [
            _io(True, True, "negative", "negative"),
            _io(False, False),
        ]
        m = compute_propagation_metrics(obs)
        assert m.true_links == 1
        assert m.correctly_suppressed == 1
        assert m.missed_links == 0
        assert m.extra_links == 0
        assert m.emission_precision == 1.0
        assert m.emission_recall == 1.0
        assert m.sign_accuracy == 1.0

    def test_missed_and_extra(self):
        obs = [
            _io(True, False),       # missed
            _io(False, True),       # extra
        ]
        m = compute_propagation_metrics(obs)
        assert m.missed_links == 1
        assert m.extra_links == 1
        assert m.emission_precision == 0.0
        assert m.emission_recall == 0.0

    def test_sign_accuracy(self):
        obs = [
            _io(True, True, "negative", "negative"),
            _io(True, True, "positive", "negative"),
        ]
        m = compute_propagation_metrics(obs)
        assert m.sign_total == 2
        assert m.sign_correct == 1
        assert m.sign_accuracy == 0.5
