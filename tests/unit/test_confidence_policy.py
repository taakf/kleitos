"""Tests for the Phase 9C confidence policy layer."""

from __future__ import annotations

import pytest

from src.intelligence.policy import (
    BASELINE_POLICY,
    ConfidencePolicy,
    get_active_policy,
    reset_active_policy,
    set_active_policy,
    tuned,
)


# ---------------------------------------------------------------------------
# Baseline identity
# ---------------------------------------------------------------------------


class TestBaselinePolicy:
    def test_baseline_matches_phase9a_numbers(self):
        """The baseline policy must reproduce the Phase 9A numbers
        exactly — this test is the anchor that prevents silent
        drift into a looser policy."""
        p = BASELINE_POLICY
        assert p.classifier_min_confidence == 0.35
        assert p.propagator_min_abs_sensitivity == 0.25
        assert p.macro_factor_link_min == 0.25
        assert p.propagator_p_holding_min == 0.05
        assert p.propagator_p_holding_max == 0.85
        assert p.analysis_min_relevance == 0.5

    def test_baseline_has_name_and_version(self):
        p = BASELINE_POLICY
        assert isinstance(p.name, str) and p.name
        assert isinstance(p.version, int) and p.version >= 1

    def test_baseline_source_weights(self):
        p = BASELINE_POLICY
        assert p.source_weights["default"] == 0.55
        assert p.source_weights["ai_inferred"] == 0.70
        assert p.source_weights["manual"] == 0.90

    def test_baseline_magnitude_weights(self):
        p = BASELINE_POLICY
        assert p.magnitude_weights["minor"] == 0.40
        assert p.magnitude_weights["moderate"] == 0.55
        assert p.magnitude_weights["major"] == 0.70
        assert p.magnitude_weights["extreme"] == 0.90
        assert p.magnitude_weights["unknown"] == 0.50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestPolicyHelpers:
    def test_source_weight_fallback(self):
        assert BASELINE_POLICY.source_weight("default") == 0.55
        assert BASELINE_POLICY.source_weight("mystery_source") == 0.55

    def test_magnitude_weight_fallback(self):
        assert BASELINE_POLICY.magnitude_weight("major") == 0.70
        assert BASELINE_POLICY.magnitude_weight("mystery_mag") == 0.50

    def test_factor_override_empty_by_default(self):
        assert BASELINE_POLICY.factor_override("interest_rate", "classifier_min_confidence") is None

    def test_factor_override_applied_when_present(self):
        p = tuned(per_factor_overrides={
            "interest_rate": {"classifier_min_confidence": 0.40},
        })
        assert p.factor_override("interest_rate", "classifier_min_confidence") == 0.40
        assert p.factor_override("oil_energy", "classifier_min_confidence") is None

    def test_describe_returns_json_safe_dict(self):
        d = BASELINE_POLICY.describe()
        assert isinstance(d, dict)
        assert d["name"] == BASELINE_POLICY.name
        assert d["classifier_min_confidence"] == 0.35
        # Every top-level value must be JSON-serializable
        import json
        json.dumps(d)  # should not raise


# ---------------------------------------------------------------------------
# Active policy switching
# ---------------------------------------------------------------------------


class TestActivePolicySwitching:
    def teardown_method(self):
        reset_active_policy()

    def test_get_returns_baseline_by_default(self):
        reset_active_policy()
        assert get_active_policy() is BASELINE_POLICY

    def test_set_returns_previous_and_restore_works(self):
        reset_active_policy()
        stricter = tuned(classifier_min_confidence=0.50, name="stricter_v1")
        prev = set_active_policy(stricter)
        assert prev is BASELINE_POLICY
        assert get_active_policy().classifier_min_confidence == 0.50
        set_active_policy(prev)
        assert get_active_policy() is BASELINE_POLICY


# ---------------------------------------------------------------------------
# Tuning helper
# ---------------------------------------------------------------------------


class TestTunedPolicy:
    def test_tuned_returns_new_instance(self):
        p = tuned(classifier_min_confidence=0.50)
        assert p is not BASELINE_POLICY
        assert p.classifier_min_confidence == 0.50
        # Other fields unchanged
        assert p.propagator_min_abs_sensitivity == 0.25

    def test_baseline_is_frozen(self):
        """Defensive: the baseline instance must not be mutable —
        a tuning sweep that accidentally mutated the global would
        be a silent calibration bug."""
        with pytest.raises(Exception):
            BASELINE_POLICY.classifier_min_confidence = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Policy wiring through live runtime objects
# ---------------------------------------------------------------------------


class TestPolicyWiring:
    """Prove that ``get_active_policy`` actually governs runtime
    behavior — if a caller swaps the policy, the classifier and
    propagator pick it up through their constructors."""

    def teardown_method(self):
        reset_active_policy()

    def test_classifier_reads_active_policy_min_confidence(self):
        from src.intelligence.factors.classifier import FactorClassifier

        # Very loose policy — barely any classification dropped.
        loose = tuned(classifier_min_confidence=0.05, name="loose_test")
        set_active_policy(loose)
        c_loose = FactorClassifier()
        assert c_loose._policy.classifier_min_confidence == 0.05

        # Very strict policy — almost everything dropped.
        strict = tuned(classifier_min_confidence=0.99, name="strict_test")
        set_active_policy(strict)
        c_strict = FactorClassifier()
        assert c_strict._policy.classifier_min_confidence == 0.99

        # Strict classifier should drop a moderately-scored event.
        results_strict = c_strict.classify(
            title="CPI rises slightly", summary="Core inflation inched up."
        )
        assert results_strict == [], "strict policy should drop weak classifications"

    def test_propagator_reads_active_policy_weights(self):
        from src.intelligence.factors.classifier import FactorClassification
        from src.intelligence.factors.propagation import FactorPropagator
        from src.intelligence.factors.sensitivity import SensitivityResolver

        # Bump the default-source weight so default priors can
        # cross higher thresholds — proves source_weights is read
        # from the active policy, not a module constant.
        boosted = tuned(
            source_weights={
                "default": 0.90, "ai_inferred": 0.90,
                "manual": 0.90, "zero": 0.0,
            },
            name="boosted_test",
        )
        resolver = SensitivityResolver()
        cls = FactorClassification(
            factor="interest_rate", direction="up", magnitude="major",
            confidence=0.95, rationale=["test"],
        )
        holding = {"id": "h1", "ticker": "AAPL", "portfolio_id": "default",
                   "sector": "technology"}

        baseline_prop = FactorPropagator(resolver, policy=BASELINE_POLICY)
        boosted_prop = FactorPropagator(resolver, policy=boosted)

        base_imp = baseline_prop.propagate([cls], [holding])[0]
        boost_imp = boosted_prop.propagate([cls], [holding])[0]

        assert boost_imp.holding_confidence > base_imp.holding_confidence, (
            "boosted source weight should lift p_holding"
        )
