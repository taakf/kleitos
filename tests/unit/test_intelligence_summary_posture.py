"""Phase 9G unit tests for the deterministic posture rule.

The posture function is a pure finite-state derivation over five
already-trusted inputs (alerts bucket, holdings under attention, top
factors, direction tally, holding count).  No DB, no LLM, no network
— this test file is intentionally tiny and fast.

Every test asserts both the posture label AND the reason string
contents, so regressions can't silently change the explanation.
"""

from __future__ import annotations

from src.intelligence.summary import _derive_posture


def _alerts(critical=0, high=0, warning=0, info=0):
    total = critical + high + warning + info
    return {
        "critical": critical, "high": high, "warning": warning,
        "info": info, "total": total,
    }


def _tally(positive=0, negative=0, unclear=0):
    return {"positive": positive, "negative": negative, "unclear": unclear}


# ---------------------------------------------------------------------------
# Empty / insufficient-data cases
# ---------------------------------------------------------------------------


class TestInsufficientData:
    def test_no_holdings_is_insufficient(self):
        p, r = _derive_posture(
            alerts=_alerts(),
            holdings_under_attention=[],
            top_factors=[],
            direction_tally=_tally(),
            holding_count=0,
        )
        assert p == "insufficient_data"
        assert "No active holdings" in r

    def test_thin_signal_is_insufficient(self):
        """A single touchpoint with no analyses and no alerts is not
        enough to assess posture."""
        p, r = _derive_posture(
            alerts=_alerts(),
            holdings_under_attention=[],
            top_factors=[{"factor": "interest_rate"}],
            direction_tally=_tally(),
            holding_count=5,
        )
        assert p == "insufficient_data"
        assert "signals" in r.lower()


# ---------------------------------------------------------------------------
# Negative postures
# ---------------------------------------------------------------------------


class TestNegativePostures:
    def test_critical_alert_forces_strong_negative(self):
        """Even a single critical alert overrides everything else."""
        p, r = _derive_posture(
            alerts=_alerts(critical=1),
            holdings_under_attention=[],
            top_factors=[],
            direction_tally=_tally(positive=10),  # irrelevant
            holding_count=5,
        )
        assert p == "strong_negative"
        assert "critical" in r

    def test_two_high_alerts_plus_attention_is_strong_negative(self):
        p, r = _derive_posture(
            alerts=_alerts(high=2),
            holdings_under_attention=["AAPL", "MSFT"],
            top_factors=[],
            direction_tally=_tally(),
            holding_count=5,
        )
        assert p == "strong_negative"
        assert "high-severity" in r
        assert "2" in r and "AAPL" not in r  # tickers are not leaked into reason text

    def test_single_high_alert_is_mildly_negative(self):
        p, r = _derive_posture(
            alerts=_alerts(high=1),
            holdings_under_attention=[],
            top_factors=[{"factor": "x"}],
            direction_tally=_tally(),
            holding_count=5,
        )
        assert p == "mildly_negative"
        assert "high-severity" in r

    def test_attention_with_net_negative_notes_is_mildly_negative(self):
        p, r = _derive_posture(
            alerts=_alerts(),
            holdings_under_attention=["AAPL"],
            top_factors=[{"factor": "x"}],
            direction_tally=_tally(positive=1, negative=3),
            holding_count=5,
        )
        assert p == "mildly_negative"
        assert "1 holding" in r
        assert "3 negative" in r


# ---------------------------------------------------------------------------
# Positive / constructive postures
# ---------------------------------------------------------------------------


class TestPositivePostures:
    def test_many_positive_notes_no_friction_is_strong_positive(self):
        p, r = _derive_posture(
            alerts=_alerts(),
            holdings_under_attention=[],
            top_factors=[],
            direction_tally=_tally(positive=4),
            holding_count=5,
        )
        assert p == "strong_positive"
        assert "4 positive" in r

    def test_net_positive_with_low_severity_alerts_is_constructive(self):
        p, r = _derive_posture(
            alerts=_alerts(warning=1, info=2),   # low severity only
            holdings_under_attention=[],
            top_factors=[{"factor": "x"}],
            direction_tally=_tally(positive=3, negative=1),
            holding_count=5,
        )
        assert p == "constructive"
        assert "positive" in r

    def test_constructive_requires_no_high_or_critical(self):
        """Even with 5 positive notes, a single high alert blocks
        constructive — the high alert wins."""
        p, _ = _derive_posture(
            alerts=_alerts(high=1),
            holdings_under_attention=[],
            top_factors=[{"factor": "x"}],
            direction_tally=_tally(positive=5),
            holding_count=5,
        )
        assert p == "mildly_negative"


# ---------------------------------------------------------------------------
# Mixed fallback
# ---------------------------------------------------------------------------


class TestMixedPosture:
    def test_balanced_signals_return_mixed(self):
        p, r = _derive_posture(
            alerts=_alerts(warning=1),
            holdings_under_attention=[],
            top_factors=[{"factor": "x"}],
            direction_tally=_tally(positive=1, negative=1),
            holding_count=5,
        )
        assert p == "mixed"
        assert "1 positive" in r and "1 negative" in r
