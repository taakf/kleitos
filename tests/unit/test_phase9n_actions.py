"""Phase 9N unit tests for the deterministic action builder.

All tests in this file are pure — no DB, no HTTP, no LLM.  They lock
down the rule families, the priority ordering, the maintenance hint
builder, the per-event explainer, and the per-alert next-step helper.
Every grounded rule needs a test, every "no evidence" path needs a
silence test.
"""

from __future__ import annotations

import pytest

from src.intelligence.actions import (
    ActionInputs,
    MaintenanceInputs,
    RecommendedAction,
    build_actions_for_portfolio,
    build_operator_maintenance_action,
    explain_event,
    suggest_next_step_for_alert,
    MAX_ACTIONS_PER_CALL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inputs(**overrides) -> ActionInputs:
    base = dict(
        portfolio_id="pA",
        holding_count=3,
        posture="mixed",
        alerts={"critical": 0, "high": 0, "warning": 0, "info": 0, "total": 0},
        top_factors=[],
        top_relationships=[],
        holdings_under_attention=[],
        analysis_notes_by_ticker={},
        freshness={},
        intelligence_health={},
    )
    base.update(overrides)
    return ActionInputs(**base)


def _keys(actions) -> list[str]:
    return [a.key for a in actions]


# ---------------------------------------------------------------------------
# 1) Empty input → zero actions
# ---------------------------------------------------------------------------


class TestEmptyInputs:
    def test_no_data_produces_no_actions(self):
        assert build_actions_for_portfolio(_inputs()) == []

    def test_missing_portfolio_id_produces_no_actions(self):
        bad = _inputs(portfolio_id="")
        assert build_actions_for_portfolio(bad) == []

    def test_none_input_produces_no_actions(self):
        assert build_actions_for_portfolio(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2) Alert families
# ---------------------------------------------------------------------------


class TestAlertFamilies:
    def test_critical_alert_produces_high_priority_action(self):
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 1, "high": 0, "warning": 0, "info": 0, "total": 1},
        ))
        assert "alerts.critical_present" in _keys(actions)
        critical_action = next(a for a in actions if a.key == "alerts.critical_present")
        assert critical_action.priority == "high"
        assert "critical" in critical_action.description.lower()
        # Rationale must reference the concrete count
        assert any("critical=1" in r for r in critical_action.rationale_refs)

    def test_two_high_alerts_produce_cluster_action(self):
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 0, "high": 2, "warning": 0, "info": 0, "total": 2},
        ))
        assert "alerts.high_cluster" in _keys(actions)
        cluster = next(a for a in actions if a.key == "alerts.high_cluster")
        assert cluster.priority == "high"

    def test_high_cluster_with_critical_is_medium_priority(self):
        """When a critical is already flagged, the high-cluster drops to
        medium so the critical stays visually dominant."""
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 1, "high": 2, "warning": 0, "info": 0, "total": 3},
        ))
        cluster = next(a for a in actions if a.key == "alerts.high_cluster")
        assert cluster.priority == "medium"

    def test_single_high_alert_produces_medium_action(self):
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 0, "high": 1, "warning": 0, "info": 0, "total": 1},
        ))
        assert "alerts.high_single" in _keys(actions)
        high = next(a for a in actions if a.key == "alerts.high_single")
        assert high.priority == "medium"

    def test_only_warnings_produce_no_alert_action(self):
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 0, "high": 0, "warning": 5, "info": 0, "total": 5},
        ))
        alert_keys = [k for k in _keys(actions) if k.startswith("alerts.")]
        assert alert_keys == []


# ---------------------------------------------------------------------------
# 3) Attention + repeated-negative families
# ---------------------------------------------------------------------------


class TestAttentionAndRepeatedNegative:
    def test_attention_single_ticker_is_medium_priority(self):
        actions = build_actions_for_portfolio(_inputs(
            holdings_under_attention=["AAPL"],
        ))
        assert "holdings.under_attention" in _keys(actions)
        att = next(a for a in actions if a.key == "holdings.under_attention")
        assert att.priority == "medium"
        assert "AAPL" in att.related_tickers

    def test_attention_many_tickers_escalates_to_high(self):
        actions = build_actions_for_portfolio(_inputs(
            holdings_under_attention=["AAPL", "MSFT", "NVDA", "GOOG"],
        ))
        att = next(a for a in actions if a.key == "holdings.under_attention")
        assert att.priority == "high"
        # Description includes a preview
        assert "AAPL" in att.description
        assert "MSFT" in att.description

    def test_repeated_negative_requires_two_notes(self):
        notes = {
            "AAPL": [
                {"impact_direction": "negative", "materiality": "important"},
                {"impact_direction": "negative", "materiality": "watch"},
            ],
            "MSFT": [
                {"impact_direction": "negative", "materiality": "important"},
                # Only one negative on MSFT → doesn't qualify
            ],
        }
        actions = build_actions_for_portfolio(_inputs(
            analysis_notes_by_ticker=notes,
        ))
        repeat = next(a for a in actions if a.key == "holdings.repeated_negative")
        assert "AAPL" in repeat.related_tickers
        assert "MSFT" not in repeat.related_tickers

    def test_repeated_negative_needs_real_repetition(self):
        notes = {
            "AAPL": [
                {"impact_direction": "negative", "materiality": "important"},
                {"impact_direction": "positive", "materiality": "watch"},
            ],
        }
        actions = build_actions_for_portfolio(_inputs(
            analysis_notes_by_ticker=notes,
        ))
        keys = _keys(actions)
        assert "holdings.repeated_negative" not in keys


# ---------------------------------------------------------------------------
# 4) Factor families
# ---------------------------------------------------------------------------


class TestFactorFamilies:
    def test_rate_pressure_requires_multiple_holdings(self):
        """A rate touchpoint on a single holding does NOT emit the
        broad rate-pressure action — we want breadth, not per-ticker
        noise."""
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "up", "holdings": ["AAPL"],
            }],
        ))
        assert "factors.strong_rate_pressure" not in _keys(actions)

    def test_rate_pressure_across_two_holdings_is_medium(self):
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "up", "holdings": ["AAPL", "MSFT"],
            }],
        ))
        rate = next(a for a in actions if a.key == "factors.strong_rate_pressure")
        assert rate.priority == "medium"
        assert "AAPL" in rate.related_tickers
        assert "MSFT" in rate.related_tickers

    def test_rate_pressure_across_four_holdings_is_high(self):
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "up",
                "holdings": ["AAPL", "MSFT", "NVDA", "GOOG"],
            }],
        ))
        rate = next(a for a in actions if a.key == "factors.strong_rate_pressure")
        assert rate.priority == "high"

    def test_rate_pressure_down_direction_is_silent(self):
        """Rate pressure rule only fires on direction='up' —
        a rates-easing touchpoint shouldn't trigger a duration-risk
        recommendation."""
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[{
                "factor": "interest_rate", "label": "Interest Rates",
                "direction": "down", "holdings": ["AAPL", "MSFT"],
            }],
        ))
        assert "factors.strong_rate_pressure" not in _keys(actions)

    def test_energy_pressure_family_fires_on_any_direction(self):
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[{
                "factor": "oil_energy", "label": "Oil & Energy",
                "direction": "up", "holdings": ["XOM", "CVX"],
            }],
        ))
        energy = next(a for a in actions if a.key == "factors.strong_energy_pressure")
        assert energy.priority == "medium"

    def test_broad_pressure_family_needs_three_factors(self):
        actions = build_actions_for_portfolio(_inputs(
            top_factors=[
                {"factor": "interest_rate", "direction": "up", "holdings": ["AAPL"]},
                {"factor": "inflation", "direction": "up", "holdings": ["AAPL"]},
                {"factor": "trade_policy", "direction": "up", "holdings": ["AAPL"]},
            ],
        ))
        assert "factors.broad_pressure" in _keys(actions)
        breadth = next(a for a in actions if a.key == "factors.broad_pressure")
        assert breadth.priority == "low"


# ---------------------------------------------------------------------------
# 5) Relationship families
# ---------------------------------------------------------------------------


class TestRelationshipFamilies:
    def test_supplier_dependency_emits_action(self):
        actions = build_actions_for_portfolio(_inputs(
            top_relationships=[{
                "ticker": "AAPL",
                "relationship_type": "supplier",
                "related_entity": "Taiwan Semiconductor",
            }],
        ))
        rel = next(a for a in actions if a.key == "relationships.supplier_dependency")
        assert "AAPL" in rel.related_tickers
        assert "Taiwan Semiconductor" in rel.title

    def test_competitor_relationship_is_silent(self):
        """Competitor/customer/regulator are informational only in
        Phase 9D — we don't turn them into standalone actions."""
        actions = build_actions_for_portfolio(_inputs(
            top_relationships=[{
                "ticker": "AAPL",
                "relationship_type": "competitor",
                "related_entity": "Samsung",
            }],
        ))
        assert not any(k.startswith("relationships.") for k in _keys(actions))


# ---------------------------------------------------------------------------
# 6) Freshness family
# ---------------------------------------------------------------------------


class TestFreshnessFamily:
    def test_stale_feed_emits_low_priority_action(self):
        actions = build_actions_for_portfolio(_inputs(
            freshness={"is_fresh": False, "stale_minutes": 180},
        ))
        stale = next(a for a in actions if a.key == "freshness.stale_feed")
        assert stale.priority == "low"
        assert "3h" in stale.description or "180" in stale.description

    def test_fresh_feed_is_silent(self):
        actions = build_actions_for_portfolio(_inputs(
            freshness={"is_fresh": True, "stale_minutes": 10},
        ))
        assert "freshness.stale_feed" not in _keys(actions)


# ---------------------------------------------------------------------------
# 7) Prioritization + cap
# ---------------------------------------------------------------------------


class TestPrioritizationAndCap:
    def test_output_is_sorted_high_to_low(self):
        """With multiple rule families active, high-priority actions
        land before medium, and medium before low."""
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 1, "high": 0, "warning": 0, "info": 0, "total": 1},
            top_factors=[
                {"factor": "interest_rate", "direction": "up",
                 "holdings": ["AAPL", "MSFT"]},
                {"factor": "inflation", "direction": "up", "holdings": ["AAPL"]},
                {"factor": "trade_policy", "direction": "up", "holdings": ["AAPL"]},
            ],
            freshness={"is_fresh": False, "stale_minutes": 300},
        ))
        priorities = [a.priority for a in actions]
        # Must be a non-decreasing prefix of high → medium → low
        rank = {"high": 0, "medium": 1, "low": 2}
        for i in range(len(priorities) - 1):
            assert rank[priorities[i]] <= rank[priorities[i + 1]], (
                f"action list not sorted by priority: {priorities}"
            )

    def test_output_is_capped_at_max(self):
        # Pile every rule family on top of each other — must still
        # return at most MAX_ACTIONS_PER_CALL
        actions = build_actions_for_portfolio(_inputs(
            alerts={"critical": 1, "high": 2, "warning": 0, "info": 0, "total": 3},
            holdings_under_attention=["AAPL", "MSFT", "NVDA"],
            top_factors=[
                {"factor": "interest_rate", "direction": "up",
                 "holdings": ["AAPL", "MSFT", "NVDA"]},
                {"factor": "oil_energy", "direction": "up",
                 "holdings": ["XOM", "CVX"]},
                {"factor": "trade_policy", "direction": "up", "holdings": ["AAPL"]},
            ],
            top_relationships=[{
                "ticker": "AAPL", "relationship_type": "supplier",
                "related_entity": "TSMC",
            }],
            freshness={"is_fresh": False, "stale_minutes": 500},
        ))
        assert len(actions) <= MAX_ACTIONS_PER_CALL


# ---------------------------------------------------------------------------
# 8) Maintenance hint builder
# ---------------------------------------------------------------------------


class TestMaintenanceHint:
    def test_reconcile_with_changes_suggests_backfill(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="reconcile",
            stats={"created": 3, "updated": 0, "pruned": 1},
        ))
        assert action is not None
        assert action.key == "maintenance.backfill_after_reconcile"
        assert "backfill" in action.description.lower()

    def test_reconcile_with_no_changes_returns_none(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="reconcile",
            stats={"created": 0, "updated": 0, "pruned": 0},
        ))
        assert action is None

    def test_backfill_with_new_links_is_low_priority(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="backfill",
            stats={"links_added": 7, "mfe_added": 2, "events_failed": 0},
        ))
        assert action is not None
        assert action.key == "maintenance.backfill_applied"
        assert action.priority == "low"
        assert "7" in action.description

    def test_backfill_noop_returns_noop_action(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="backfill",
            stats={"links_added": 0, "mfe_added": 0, "events_failed": 0},
        ))
        assert action is not None
        assert action.key == "maintenance.backfill_no_op"
        assert action.priority == "low"

    def test_backfill_with_failures_is_medium_priority(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="backfill",
            stats={"links_added": 3, "mfe_added": 1, "events_failed": 2},
        ))
        assert action is not None
        assert action.key == "maintenance.backfill_partial"
        assert action.priority == "medium"

    def test_manual_edit_suggests_backfill(self):
        for kind in ("manual_relationship", "manual_factor"):
            action = build_operator_maintenance_action(MaintenanceInputs(
                action=kind, stats={},
            ))
            assert action is not None
            assert action.key == "maintenance.backfill_after_edit"

    def test_unknown_action_returns_none(self):
        action = build_operator_maintenance_action(MaintenanceInputs(
            action="nonsense", stats={},
        ))
        assert action is None


# ---------------------------------------------------------------------------
# 9) Per-event explanation helper
# ---------------------------------------------------------------------------


class TestExplainEvent:
    def test_rate_event_with_affected_holdings_produces_grounded_explanation(self):
        result = explain_event(
            event_title="Federal Reserve raises rates by 50 bps",
            factor_tags=[{
                "key": "interest_rate",
                "label": "Interest Rates",
                "direction": "up",
                "magnitude": "major",
            }],
            chains=[{
                "origin": "deterministic_factor",
                "channel": "interest_rate",
            }],
            affected_holdings=[
                {"ticker": "AAPL"},
                {"ticker": "MSFT"},
            ],
        )
        assert result["why_it_matters"] is not None
        assert "Interest Rates" in result["why_it_matters"]
        assert "AAPL" in result["why_it_matters"]
        assert result["suggested_action"] is not None
        assert "AAPL" in result["suggested_action"]
        assert "duration" in result["suggested_action"].lower()
        # Grounding refs name the specific inputs
        assert any("factor:interest_rate" in r for r in result["grounded_in"])

    def test_relationship_chain_is_called_out(self):
        result = explain_event(
            event_title="TSMC capacity cut",
            factor_tags=[],
            chains=[{
                "origin": "relationship",
                "channel": "supplier",
                "related_entity": "Taiwan Semiconductor",
            }],
            affected_holdings=[{"ticker": "AAPL"}],
        )
        assert result["why_it_matters"] is not None
        assert "supplier" in result["why_it_matters"]
        assert "Taiwan Semiconductor" in result["why_it_matters"]
        assert any("relationship:supplier" in r for r in result["grounded_in"])

    def test_no_evidence_returns_silent_block(self):
        result = explain_event(
            event_title="Some random news",
            factor_tags=[],
            chains=[],
            affected_holdings=[],
        )
        assert result["why_it_matters"] is None
        assert result["suggested_action"] is None
        assert result["grounded_in"] == []

    def test_energy_event_suggests_energy_exposure_check(self):
        result = explain_event(
            event_title="OPEC production cut",
            factor_tags=[{
                "key": "oil_energy",
                "label": "Oil & Energy",
                "direction": "up",
                "magnitude": "moderate",
            }],
            chains=[],
            affected_holdings=[{"ticker": "XOM"}, {"ticker": "CVX"}],
        )
        assert result["suggested_action"] is not None
        assert "energy" in result["suggested_action"].lower()
        assert "XOM" in result["suggested_action"]


# ---------------------------------------------------------------------------
# 10) Per-alert next-step helper
# ---------------------------------------------------------------------------


class TestSuggestNextStepForAlert:
    def test_critical_with_holdings_suggests_inspect(self):
        result = suggest_next_step_for_alert({
            "severity": "critical",
            "alert_type": "macro_factor",
            "related_holdings": ["h_aapl"],
        })
        assert result is not None
        assert "review" in result.lower()

    def test_critical_without_holdings_is_still_review(self):
        result = suggest_next_step_for_alert({
            "severity": "critical",
            "alert_type": "system",
            "related_holdings": [],
        })
        assert result is not None
        assert "review" in result.lower()

    def test_high_macro_factor_suggests_inspect_chain(self):
        result = suggest_next_step_for_alert({
            "severity": "high",
            "alert_type": "macro_factor",
            "related_holdings": ["h_aapl"],
        })
        assert result is not None
        assert "chain" in result.lower() or "inspect" in result.lower()

    def test_high_drift_suggests_concentration_review(self):
        result = suggest_next_step_for_alert({
            "severity": "high",
            "alert_type": "drift",
            "related_holdings": [],
        })
        assert result is not None
        assert "concentration" in result.lower() or "balance" in result.lower()

    def test_info_is_silent(self):
        assert suggest_next_step_for_alert({
            "severity": "info",
            "alert_type": "digest_ready",
            "related_holdings": [],
        }) is None

    def test_empty_alert_is_silent(self):
        assert suggest_next_step_for_alert({}) is None
        assert suggest_next_step_for_alert(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 11) RecommendedAction serialisation contract
# ---------------------------------------------------------------------------


class TestRecommendedActionSerialisation:
    def test_to_dict_includes_every_expected_field(self):
        a = RecommendedAction(
            key="alerts.critical_present",
            title="Review critical alerts",
            description="desc",
            priority="high",
            related_tickers=("AAPL", "MSFT"),
            rationale_refs=("alerts.critical=1",),
            portfolio_id="pA",
        )
        d = a.to_dict()
        assert d == {
            "key": "alerts.critical_present",
            "title": "Review critical alerts",
            "description": "desc",
            "priority": "high",
            "related_tickers": ["AAPL", "MSFT"],
            "rationale_refs": ["alerts.critical=1"],
            "portfolio_id": "pA",
        }

    def test_to_dict_is_json_safe(self):
        import json
        a = RecommendedAction(
            key="x", title="y", description="z", priority="low",
        )
        payload = json.dumps(a.to_dict())  # must not raise
        assert "low" in payload
