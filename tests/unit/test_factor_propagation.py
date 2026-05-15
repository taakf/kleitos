"""Tests for the deterministic factor → holding propagator (Phase 9A)."""

from __future__ import annotations

import pytest

from src.intelligence.factors.classifier import FactorClassification
from src.intelligence.factors.propagation import (
    FactorImpact,
    FactorPropagator,
    MIN_ABS_SENSITIVITY,
)
from src.intelligence.factors.sensitivity import SensitivityResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cls(
    factor: str = "oil_energy",
    direction: str = "up",
    magnitude: str = "major",
    confidence: float = 0.8,
) -> FactorClassification:
    return FactorClassification(
        factor=factor,
        direction=direction,
        magnitude=magnitude,
        confidence=confidence,
        rationale=["test"],
    )


def _h(
    id: str,
    ticker: str,
    sector: str | None,
    portfolio_id: str = "default",
) -> dict:
    return {
        "id": id,
        "ticker": ticker,
        "portfolio_id": portfolio_id,
        "sector": sector,
    }


# ---------------------------------------------------------------------------
# Core propagation scenarios
# ---------------------------------------------------------------------------


class TestPropagation:
    def test_oil_up_harms_airline_industrial_negative_sensitivity(self):
        """Oil up → an industrial (w_oil=-0.2) should NOT emit (below |0.25|).

        Airlines would need an explicit manual override under Phase 9A
        since there is no dedicated "transportation" sector prior.
        Use an explicit manual sensitivity here to represent an
        operator-tuned airline.
        """
        resolver = SensitivityResolver(
            manual_overrides=[
                ("h1", "oil_energy", -0.7, "manual"),
            ],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("oil_energy", "up", "major", 0.8)],
            holdings=[_h("h1", "UAL", sector="Industrials")],
        )
        assert len(impacts) == 1
        impact = impacts[0]
        assert impact.factor_direction == "up"
        assert impact.effect_direction == "negative"
        assert impact.holding_confidence >= 0.3  # passes emission check
        assert impact.holding_confidence <= 0.85

    def test_oil_up_helps_energy_positive_sensitivity(self):
        """Oil up → energy holding (sector prior +0.8) produces positive effect."""
        resolver = SensitivityResolver()  # no overrides, sector priors only
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("oil_energy", "up", "major", 0.8)],
            holdings=[_h("h1", "XOM", sector="energy")],
        )
        assert len(impacts) == 1
        assert impacts[0].effect_direction == "positive"
        assert impacts[0].sensitivity.source == "default"
        assert impacts[0].sensitivity.value == pytest.approx(0.8)

    def test_confidence_is_bounded(self):
        """holding_confidence must stay within [0.05, 0.85] for all inputs."""
        resolver = SensitivityResolver(
            manual_overrides=[("h1", "interest_rate", 1.0, "manual")],
        )
        prop = FactorPropagator(resolver)
        # Maximal settings: manual +1, major+extreme, p_factor=0.95
        impacts = prop.propagate(
            classifications=[_cls("interest_rate", "up", "extreme", 0.95)],
            holdings=[_h("h1", "BIG", sector="financials")],
        )
        assert impacts, "expected at least one impact"
        for impact in impacts:
            assert 0.05 <= impact.holding_confidence <= 0.85, impact

    def test_low_sensitivity_emits_nothing(self):
        """|sensitivity| < 0.25 → no impact row at all."""
        resolver = SensitivityResolver(
            manual_overrides=[("h1", "oil_energy", 0.10, "manual")],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("oil_energy", "up", "major", 0.8)],
            holdings=[_h("h1", "TINY", sector="technology")],
        )
        assert impacts == []

    def test_zero_source_emits_nothing(self):
        """Unknown sector + no override → resolver returns "zero" → no impact."""
        resolver = SensitivityResolver()
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("oil_energy", "up", "major", 0.8)],
            holdings=[_h("h1", "MYST", sector=None)],
        )
        assert impacts == []

    def test_portfolio_isolation(self):
        """Impacts retain the holding's portfolio_id — no cross-portfolio leak."""
        resolver = SensitivityResolver(
            manual_overrides=[
                ("h1", "interest_rate", -0.6, "manual"),
                ("h2", "interest_rate", -0.6, "manual"),
            ],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("interest_rate", "up", "major", 0.85)],
            holdings=[
                _h("h1", "AAPL", sector="technology", portfolio_id="pA"),
                _h("h2", "MSFT", sector="technology", portfolio_id="pB"),
            ],
        )
        assert {i.portfolio_id for i in impacts} == {"pA", "pB"}
        for i in impacts:
            assert i.portfolio_id in ("pA", "pB")

    def test_details_json_shape(self):
        """Causal chain JSON must match the Phase 9A brief schema."""
        resolver = SensitivityResolver()
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("interest_rate", "up", "major", 0.8)],
            holdings=[_h("h1", "AAPL", sector="technology")],
        )
        assert impacts
        details = impacts[0].to_details_json("evt-1", "Fed raises 50 bps")
        assert set(details.keys()) == {
            "event", "factor", "holding", "sensitivity", "expected_effect",
        }
        assert details["event"]["id"] == "evt-1"
        assert details["factor"]["key"] == "interest_rate"
        assert details["factor"]["direction"] == "up"
        assert details["holding"]["ticker"] == "AAPL"
        assert details["sensitivity"]["sector"] == "technology"
        assert details["expected_effect"]["direction"] in ("positive", "negative", "unclear")

    def test_source_weight_ordering(self):
        """manual > ai_inferred > default weighting of holding_confidence.

        Identical absolute sensitivities should produce monotonically
        increasing confidence as the source gets stronger.
        """
        resolver = SensitivityResolver(
            manual_overrides=[
                ("h_m", "interest_rate", 0.6, "manual"),
                ("h_a", "interest_rate", 0.6, "ai_inferred"),
                ("h_d", "interest_rate", 0.6, "default"),
            ],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("interest_rate", "up", "major", 0.8)],
            holdings=[
                _h("h_m", "MAN", sector=None),
                _h("h_a", "AI", sector=None),
                _h("h_d", "DEF", sector=None),
            ],
        )
        by_h = {i.holding_id: i.holding_confidence for i in impacts}
        assert by_h["h_m"] > by_h["h_a"] > by_h["h_d"]

    def test_direction_product_down_negative_sensitivity_positive_effect(self):
        """down × negative sensitivity → positive effect (sign algebra)."""
        resolver = SensitivityResolver(
            manual_overrides=[("h1", "interest_rate", -0.6, "manual")],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("interest_rate", "down", "major", 0.8)],
            holdings=[_h("h1", "TECH", sector="technology")],
        )
        assert impacts[0].effect_direction == "positive"

    def test_unknown_direction_yields_unclear_effect(self):
        resolver = SensitivityResolver(
            manual_overrides=[("h1", "oil_energy", -0.7, "manual")],
        )
        prop = FactorPropagator(resolver)
        impacts = prop.propagate(
            classifications=[_cls("oil_energy", "unknown", "moderate", 0.5)],
            holdings=[_h("h1", "UAL", sector="Industrials")],
        )
        assert impacts[0].effect_direction == "unclear"


# ---------------------------------------------------------------------------
# Sensitivity resolver edge cases
# ---------------------------------------------------------------------------


class TestSensitivityResolver:
    def test_normalize_gics_information_technology(self):
        from src.intelligence.factors.sensitivity import normalize_sector

        assert normalize_sector("Information Technology") == "technology"
        assert normalize_sector("technology") == "technology"
        assert normalize_sector("Health Care") == "health care"
        assert normalize_sector(None) is None
        assert normalize_sector("asteroid mining") is None

    def test_clamp_out_of_range_override(self):
        """Sensitivities outside [-1, 1] should be clamped, not dropped."""
        r = SensitivityResolver(
            manual_overrides=[("h1", "oil_energy", 5.0, "manual")],
        )
        sens = r.resolve("h1", "oil_energy", sector="energy")
        assert sens.value == 1.0
        assert sens.source == "manual"

    def test_override_wins_over_sector_default(self):
        r = SensitivityResolver(
            manual_overrides=[("h1", "interest_rate", 0.9, "manual")],
        )
        sens = r.resolve("h1", "interest_rate", sector="technology")
        # Technology prior is -0.6; the manual override must win.
        assert sens.value == 0.9
        assert sens.source == "manual"

    def test_sector_default_applied_when_no_override(self):
        r = SensitivityResolver()
        sens = r.resolve("h1", "interest_rate", sector="technology")
        assert sens.value == pytest.approx(-0.6)
        assert sens.source == "default"

    def test_unknown_sector_returns_zero(self):
        r = SensitivityResolver()
        sens = r.resolve("h1", "interest_rate", sector="space station ops")
        assert sens.value == 0.0
        assert sens.source == "zero"


# ---------------------------------------------------------------------------
# Threshold sanity
# ---------------------------------------------------------------------------


def test_min_abs_sensitivity_constant():
    assert MIN_ABS_SENSITIVITY == 0.25


def test_macro_factor_link_min_matches_sensitivity_gate():
    """Phase 9A corrective pass: the persistence floor for factor
    links equals the propagator's own ``MIN_ABS_SENSITIVITY`` gate.

    This is the critical integration invariant: factor links only
    persist for "meaningful sector-level exposure, deterministically
    reasoned".  The AnalysisAgent does NOT use this threshold — it
    excludes ``macro_factor`` links entirely by type, because they
    already carry a complete deterministic causal chain.
    """
    assert FactorPropagator.MACRO_FACTOR_LINK_MIN == 0.25
    assert FactorPropagator.MACRO_FACTOR_LINK_MIN == MIN_ABS_SENSITIVITY
    # Backward-compat alias preserved
    assert FactorPropagator.LINK_EMIT_THRESHOLD == FactorPropagator.MACRO_FACTOR_LINK_MIN
