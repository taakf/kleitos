"""Tests for the deterministic macro factor classifier (Phase 9A)."""

from __future__ import annotations

import pytest

from src.intelligence.factors.classifier import (
    FactorClassification,
    FactorClassifier,
)
from src.intelligence.factors.taxonomy import FACTOR_KEYS


@pytest.fixture
def classifier() -> FactorClassifier:
    return FactorClassifier()


def _results_by_factor(
    results: list[FactorClassification],
) -> dict[str, FactorClassification]:
    return {r.factor: r for r in results}


# ---------------------------------------------------------------------------
# Direct cases from the Phase 9A brief
# ---------------------------------------------------------------------------


class TestClassifierBriefCases:
    """Each case is a scenario lifted verbatim from the Phase 9A brief."""

    def test_fed_raises_rates_50_bps(self, classifier: FactorClassifier):
        """Fed raises rates 50 bps → interest_rate up major, p_factor ≥ 0.75."""
        results = classifier.classify(
            title="Federal Reserve raises interest rates by 50 bps",
            summary="The FOMC voted to raise the federal funds rate by 50 basis points citing sticky inflation.",
        )
        by_factor = _results_by_factor(results)

        assert "interest_rate" in by_factor, f"expected interest_rate, got {list(by_factor)}"
        r = by_factor["interest_rate"]
        assert r.direction == "up"
        assert r.magnitude in ("major", "extreme"), f"expected major/extreme, got {r.magnitude}"
        assert r.confidence >= 0.75, f"expected >= 0.75, got {r.confidence}"
        # Rationale should cite the bps parse
        assert any("50" in s for s in r.rationale), r.rationale

    def test_cpi_rises_mom(self, classifier: FactorClassifier):
        """CPI rises 0.8% m/m → inflation up major (optional secondary interest_rate)."""
        results = classifier.classify(
            title="CPI rises 0.8% month-on-month, hotter than expected",
            summary="Core CPI also accelerated, adding to price pressures and wage growth concerns.",
        )
        by_factor = _results_by_factor(results)

        assert "inflation" in by_factor
        r = by_factor["inflation"]
        assert r.direction == "up"
        assert r.magnitude in ("major", "extreme")
        assert r.confidence >= 0.55

    def test_pipeline_attack_brent_jumps(self, classifier: FactorClassifier):
        """Pipeline attack + Brent jumps 12% → oil_energy up major AND geopolitical_risk up."""
        results = classifier.classify(
            title="Pipeline attack in the Strait of Hormuz sends Brent crude oil jumping 12%",
            summary="A drone strike on a key refinery caused an immediate supply disruption.",
        )
        by_factor = _results_by_factor(results)

        assert "oil_energy" in by_factor, f"expected oil_energy, got {list(by_factor)}"
        oil = by_factor["oil_energy"]
        assert oil.direction == "up"
        assert oil.magnitude in ("major", "extreme")

        assert "geopolitical_risk" in by_factor
        geo = by_factor["geopolitical_risk"]
        assert geo.direction == "up"

    def test_new_tariffs_announced(self, classifier: FactorClassifier):
        """New tariffs announced → trade_policy up."""
        results = classifier.classify(
            title="US announces new tariffs and export controls on chip equipment",
            summary="The administration cited national security concerns.",
        )
        by_factor = _results_by_factor(results)

        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "up"

    def test_missile_strike_escalates(self, classifier: FactorClassifier):
        """Missile strike escalates conflict → geopolitical_risk up."""
        results = classifier.classify(
            title="Missile strike escalates conflict between regional powers",
            summary="A mobilization was announced in response to the airstrike.",
        )
        by_factor = _results_by_factor(results)

        assert "geopolitical_risk" in by_factor
        assert by_factor["geopolitical_risk"].direction == "up"

    def test_apple_orchard_false_positive(self, classifier: FactorClassifier):
        """Apple orchard destroyed by frost → NO macro factors, NO contamination.

        This is the canonical false-positive guard: the word "Apple"
        must never produce a macro tag, and there are no factor-specific
        keywords in the text.
        """
        results = classifier.classify(
            title="Apple orchard destroyed by frost in upstate New York",
            summary="Local growers face losses after an unseasonably cold night wiped out their harvest.",
        )
        # We allow zero results, OR at most a single non-macro result
        # — but nothing matching our factor taxonomy should fire here.
        assert results == [], (
            f"Expected no macro classifications, got: "
            f"{[(r.factor, r.confidence) for r in results]}"
        )


# ---------------------------------------------------------------------------
# Confidence + taxonomy sanity
# ---------------------------------------------------------------------------


class TestClassifierInvariants:
    def test_empty_text_returns_empty(self, classifier: FactorClassifier):
        assert classifier.classify("", "") == []
        assert classifier.classify(None, None) == []  # type: ignore[arg-type]

    def test_confidence_is_bounded(self, classifier: FactorClassifier):
        """Regardless of input, confidence stays in [0.05, 0.95]."""
        samples = [
            "Federal Reserve signals a 75 basis point rate hike",
            "CPI plunges to record lows after deflation shock",
            "OPEC+ announces extreme production cut, WTI surges 15%",
            "Dollar index DXY soars to unprecedented historic levels",
        ]
        for text in samples:
            for r in classifier.classify(title=text):
                assert 0.05 <= r.confidence <= 0.95, (text, r)

    def test_all_returned_factors_are_in_taxonomy(self, classifier: FactorClassifier):
        """Classifier may only emit known factor keys."""
        results = classifier.classify(
            title="Federal Reserve hikes rates; OPEC cuts output; yuan weakens against dollar",
            summary="Tariffs widen; CPI accelerates; AI cycle restrictions tighten.",
        )
        for r in results:
            assert r.factor in FACTOR_KEYS, f"unknown factor emitted: {r.factor}"

    def test_single_weak_support_does_not_classify(self, classifier: FactorClassifier):
        """A single weak support-only phrase should not reach the floor.

        "consumer" alone is a support-tier phrase for consumer_demand —
        it must NOT classify without a core match.
        """
        results = classifier.classify(
            title="Consumer confusion over new packaging rollout",
            summary="Shoppers asked about the new look.",
        )
        factors = {r.factor for r in results}
        assert "consumer_demand" not in factors

    def test_ceasefire_infers_down_direction_geopolitical(
        self, classifier: FactorClassifier
    ):
        results = classifier.classify(
            title="Ceasefire reached in regional conflict, de-escalation confirmed",
        )
        by_factor = _results_by_factor(results)
        if "geopolitical_risk" in by_factor:
            assert by_factor["geopolitical_risk"].direction == "down"


# ---------------------------------------------------------------------------
# Phase 9C corrective pass — factor-specific direction rules
# ---------------------------------------------------------------------------


class TestOilEnergyDirectionRules:
    """OPEC / supply-cut language must map to oil_energy=up, and
    output increases must map to oil_energy=down, regardless of the
    fact that the global up/down cue dictionary has "cut" on the
    down side."""

    def setup_method(self):
        self.c = FactorClassifier()

    def test_opec_production_cut_is_up(self):
        results = self.c.classify(
            title="OPEC+ announces unexpected crude oil production cut",
            summary="Brent crude prices jumped 5% after the output cut.",
        )
        by_factor = _results_by_factor(results)
        assert "oil_energy" in by_factor
        assert by_factor["oil_energy"].direction == "up"

    def test_opec_output_cut_is_up(self):
        results = self.c.classify(
            title="OPEC announces surprise output cut",
            summary="Saudi-led OPEC+ curbs supply.",
        )
        by_factor = _results_by_factor(results)
        assert "oil_energy" in by_factor
        assert by_factor["oil_energy"].direction == "up"

    def test_supply_cut_language_is_up(self):
        results = self.c.classify(
            title="OPEC supply cuts extended into next quarter",
            summary="Crude oil curbs remain in place.",
        )
        by_factor = _results_by_factor(results)
        assert "oil_energy" in by_factor
        assert by_factor["oil_energy"].direction == "up"

    def test_production_increase_is_down(self):
        """New: the symmetric rule.  OPEC boosting output implies
        oil_energy down (more supply, lower prices)."""
        results = self.c.classify(
            title="OPEC boosts production, output increase announced",
            summary="Saudi Arabia lifts supply limits.",
        )
        by_factor = _results_by_factor(results)
        assert "oil_energy" in by_factor
        assert by_factor["oil_energy"].direction == "down"

    def test_pipeline_attack_still_fires_up(self):
        """Preservation: the pre-corrective-pass supply-disruption
        case must still produce oil_energy up."""
        results = self.c.classify(
            title="Pipeline attack in Strait of Hormuz sends Brent crude oil surging",
            summary="A drone strike escalated regional tensions; WTI jumped 12%.",
        )
        by_factor = _results_by_factor(results)
        assert "oil_energy" in by_factor
        assert by_factor["oil_energy"].direction == "up"

    def test_apple_orchard_still_no_oil_false_positive(self):
        """Preservation: the canonical false-positive guard must
        still exclude non-oil news."""
        results = self.c.classify(
            title="Apple orchard destroyed by frost in upstate New York",
            summary="Local growers report severe losses.",
        )
        assert results == []


class TestTradePolicyDirectionRules:
    """Trade-policy easing language must map to trade_policy=down
    (easing branch is checked before the restrictive keyword branch),
    and restrictive language must still map to trade_policy=up."""

    def setup_method(self):
        self.c = FactorClassifier()

    def test_sanctions_lifted_is_down(self):
        results = self.c.classify(
            title="Sanctions lifted on regional bank",
            summary="Trade restrictions eased following diplomatic talks.",
        )
        by_factor = _results_by_factor(results)
        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "down"

    def test_tariff_relief_is_down(self):
        results = self.c.classify(
            title="Administration announces tariff relief",
            summary="Duties reduced on select imports.",
        )
        by_factor = _results_by_factor(results)
        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "down"

    def test_trade_restrictions_eased_is_down(self):
        results = self.c.classify(
            title="Trade restrictions eased",
            summary="Tariff rollback announced as part of trade deal.",
        )
        by_factor = _results_by_factor(results)
        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "down"

    def test_tariff_imposed_still_up(self):
        """Preservation: restrictive trade policy must still fire up."""
        results = self.c.classify(
            title="US announces new tariffs and export controls on chip equipment",
            summary="The administration cited national security concerns.",
        )
        by_factor = _results_by_factor(results)
        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "up"

    def test_sanctions_imposed_still_up(self):
        """Preservation: the restrictive 'sanctions' path (without
        the 'lifted' trigger) must still map to up."""
        results = self.c.classify(
            title="New sanctions imposed on regional bank",
            summary="Embargo announced on all trade with the sanctioned entity.",
        )
        by_factor = _results_by_factor(results)
        assert "trade_policy" in by_factor
        assert by_factor["trade_policy"].direction == "up"
