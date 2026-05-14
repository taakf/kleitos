"""Phase 9Q unit tests — navigation target builders.

Covers the six public responsibilities of
:mod:`src.intelligence.navigation`:

1. Surface/portfolio safety validation (``_safe_target``).
2. Per-source target builders (alert, event, digest, operator,
   action, evidence ref).
3. Operator entity-type dispatch (factors vs relationships vs
   maintenance).
4. Action family dispatch (alerts / factors / holdings /
   relationships / freshness / maintenance).
5. Evidence ref prefix dispatch (event: alert: holding: ticker:
   factor: rel:).
6. Enrichment helpers for action lists and evidence ref lists.

Every test is pure — no DB, no ORM, no fixtures.  Unknown inputs
must return ``None`` (not raise) so the frontend can render a
disabled button instead of a broken link.
"""

from __future__ import annotations

import pytest

from src.intelligence.navigation import (
    NavigationTarget,
    _KNOWN_SURFACES,
    _OPERATOR_SUBTABS,
    _safe_target,
    enrich_actions_with_targets,
    enrich_evidence_refs,
    target_for_action,
    target_for_alert,
    target_for_digest,
    target_for_event,
    target_for_evidence_ref,
    target_for_operator_entry,
)


# ---------------------------------------------------------------------------
# 1) _safe_target + surface registry
# ---------------------------------------------------------------------------


class TestSafeTarget:
    def test_known_surfaces_are_stable(self):
        # Lock in the public surface vocabulary so any future phase
        # that adds a new surface must update the tests explicitly.
        # Phase 12 added the top-level "corporate-events" surface
        # (Phase 9 tab) and "settings" so Insights cards can deep-link
        # to them — both additive.
        assert _KNOWN_SURFACES == frozenset({
            "alerts", "digest", "events", "operator", "portfolio",
            "corporate-events", "settings",
        })

    def test_known_operator_subtabs_are_stable(self):
        assert _OPERATOR_SUBTABS == frozenset({
            "factors", "relationships", "maintenance", "recent-actions",
        })

    def test_valid_inputs_build_target(self):
        t = _safe_target(surface="alerts", portfolio_id="pA",
                         entity_type="alert", entity_id="a1")
        assert t is not None
        assert t.surface == "alerts"
        assert t.portfolio_id == "pA"
        assert t.entity_id == "a1"

    def test_unknown_surface_returns_none(self):
        assert _safe_target(surface="mystery", portfolio_id="pA") is None

    def test_missing_portfolio_returns_none(self):
        assert _safe_target(surface="alerts", portfolio_id="") is None
        assert _safe_target(surface="alerts", portfolio_id=None) is None  # type: ignore[arg-type]

    def test_non_string_portfolio_returns_none(self):
        assert _safe_target(surface="alerts", portfolio_id=123) is None  # type: ignore[arg-type]

    def test_unknown_operator_subtab_is_dropped_but_target_still_builds(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="mystery",
        )
        assert t is not None
        assert t.subtab is None  # dropped defensively
        assert t.surface == "operator"


# ---------------------------------------------------------------------------
# 2) target_for_alert
# ---------------------------------------------------------------------------


class TestAlertTarget:
    def test_valid_alert_carries_highlight_key(self):
        t = target_for_alert("alert_abc", "pA")
        assert t is not None
        assert t.surface == "alerts"
        assert t.entity_id == "alert_abc"
        assert t.highlight_key == "alert:alert_abc"
        assert t.portfolio_id == "pA"

    def test_missing_alert_id_falls_back_to_tab(self):
        t = target_for_alert("", "pA")
        assert t is not None
        assert t.surface == "alerts"
        assert t.entity_id is None
        assert t.highlight_key is None

    def test_custom_label(self):
        t = target_for_alert("a1", "pA", label="Review critical")
        assert t is not None
        assert t.label == "Review critical"


# ---------------------------------------------------------------------------
# 3) target_for_event
# ---------------------------------------------------------------------------


class TestEventTarget:
    def test_event_target_opens_modal_by_default(self):
        t = target_for_event("evt_fed", "pA")
        assert t is not None
        assert t.surface == "events"
        assert t.subtab == "events"
        assert t.entity_id == "evt_fed"
        assert t.open_modal is True
        assert t.highlight_key == "event:evt_fed"

    def test_event_target_without_modal(self):
        t = target_for_event("evt_fed", "pA", open_modal=False)
        assert t is not None
        assert t.open_modal is False

    def test_missing_event_id_lands_on_events_subtab(self):
        t = target_for_event("", "pA")
        assert t is not None
        assert t.surface == "events"
        assert t.entity_id is None
        assert t.open_modal is False  # no id, no modal


# ---------------------------------------------------------------------------
# 4) target_for_digest
# ---------------------------------------------------------------------------


class TestDigestTarget:
    def test_digest_target_has_subtab(self):
        t = target_for_digest("pA")
        assert t is not None
        assert t.surface == "digest"
        assert t.subtab == "digest"
        assert t.portfolio_id == "pA"


# ---------------------------------------------------------------------------
# 5) target_for_operator_entry — entity_type dispatch
# ---------------------------------------------------------------------------


class TestOperatorEntryTarget:
    def test_factor_override_routes_to_factors_table(self):
        entry = {
            "id": "o1",
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": ["factor:interest_rate", "holding:h_aapl"],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "factors"
        assert t.entity_type == "factor_override"
        assert t.entity_id == "o1"
        # The factor filter is extracted from the evidence refs
        assert t.filter == "interest_rate"
        # Phase 9R — exact factor-row anchor when both holding_id
        # and factor are available from the evidence refs.
        assert t.highlight_key == "factor-row:h_aapl:interest_rate"

    def test_factor_override_without_factor_ref_has_no_filter(self):
        entry = {
            "id": "o1",
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": ["holding:h_aapl"],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.filter is None
        # Without both holding + factor, falls back to audit-level key
        assert t.highlight_key == "audit:o1"

    def test_relationship_routes_to_relationships_table(self):
        entry = {
            "id": "r1",
            "entity_type": "holding_relationship",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "relationships"
        assert t.entity_type == "relationship"

    def test_reconcile_routes_to_maintenance(self):
        entry = {
            "id": "rc1",
            "entity_type": "holding_relationships",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "maintenance"

    def test_backfill_routes_to_maintenance(self):
        entry = {
            "id": "bf1",
            "entity_type": "intelligence_backfill",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "maintenance"

    def test_unknown_entity_type_lands_on_recent_actions(self):
        entry = {"id": "x", "entity_type": "custom_type", "evidence_refs": []}
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "recent-actions"


# ---------------------------------------------------------------------------
# 6) target_for_action — Phase 9N key family dispatch
# ---------------------------------------------------------------------------


class TestActionTarget:
    def test_alerts_family_targets_alerts_tab(self):
        t = target_for_action(
            {"key": "alerts.critical_present", "rationale_refs": []},
            "pA",
        )
        assert t is not None
        assert t.surface == "alerts"

    def test_holdings_family_targets_portfolio_tab(self):
        t = target_for_action(
            {"key": "holdings.under_attention", "rationale_refs": []},
            "pA",
        )
        assert t is not None
        assert t.surface == "portfolio"

    def test_factors_family_extracts_factor_filter(self):
        t = target_for_action(
            {
                "key": "factors.strong_rate_pressure",
                "rationale_refs": ["factor:interest_rate", "holdings:2"],
            },
            "pA",
        )
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "factors"
        assert t.filter == "interest_rate"

    def test_factors_family_without_factor_ref_has_no_filter(self):
        t = target_for_action(
            {"key": "factors.broad_pressure", "rationale_refs": ["distinct_factors=4"]},
            "pA",
        )
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "factors"
        assert t.filter is None

    def test_relationships_family_targets_relationships_table(self):
        t = target_for_action(
            {"key": "relationships.supplier_dependency", "rationale_refs": []},
            "pA",
        )
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "relationships"

    def test_freshness_family_targets_maintenance(self):
        t = target_for_action(
            {"key": "freshness.stale_feed", "rationale_refs": []},
            "pA",
        )
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "maintenance"

    def test_unknown_family_returns_none(self):
        t = target_for_action(
            {"key": "mystery.whatever", "rationale_refs": []},
            "pA",
        )
        assert t is None

    def test_missing_key_returns_none(self):
        assert target_for_action({}, "pA") is None
        assert target_for_action({"key": ""}, "pA") is None


# ---------------------------------------------------------------------------
# 7) target_for_evidence_ref — prefix dispatch
# ---------------------------------------------------------------------------


class TestEvidenceRefTarget:
    def test_event_prefix_opens_modal(self):
        t = target_for_evidence_ref("event:evt_abc", "pA")
        assert t is not None
        assert t.surface == "events"
        assert t.entity_id == "evt_abc"
        assert t.open_modal is True

    def test_alert_prefix_highlights_alert(self):
        t = target_for_evidence_ref("alert:a1", "pA")
        assert t is not None
        assert t.surface == "alerts"
        assert t.highlight_key == "alert:a1"

    def test_holding_prefix_targets_portfolio(self):
        t = target_for_evidence_ref("holding:h_aapl", "pA")
        assert t is not None
        assert t.surface == "portfolio"
        assert t.entity_id == "h_aapl"
        # Phase 9R — exact holding highlight
        assert t.highlight_key == "holding:h_aapl"

    def test_ticker_prefix_targets_portfolio(self):
        t = target_for_evidence_ref("ticker:AAPL", "pA")
        assert t is not None
        assert t.surface == "portfolio"
        assert t.entity_id == "AAPL"
        # Phase 9R — exact ticker highlight
        assert t.highlight_key == "ticker:AAPL"

    def test_factor_prefix_targets_operator_factors(self):
        t = target_for_evidence_ref("factor:interest_rate", "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "factors"
        assert t.filter == "interest_rate"

    def test_rel_prefix_targets_operator_relationships(self):
        t = target_for_evidence_ref("rel:supplier", "pA")
        assert t is not None
        assert t.surface == "operator"
        assert t.subtab == "relationships"

    def test_unknown_prefix_returns_none(self):
        assert target_for_evidence_ref("mystery:x", "pA") is None
        assert target_for_evidence_ref("note:n1", "pA") is None

    def test_missing_value_returns_none(self):
        assert target_for_evidence_ref("event:", "pA") is None
        assert target_for_evidence_ref(":value", "pA") is None
        assert target_for_evidence_ref("", "pA") is None
        assert target_for_evidence_ref(None, "pA") is None  # type: ignore[arg-type]

    def test_invalid_input_types_return_none(self):
        assert target_for_evidence_ref(42, "pA") is None  # type: ignore[arg-type]
        assert target_for_evidence_ref(["event:x"], "pA") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 8) Enrichment helpers
# ---------------------------------------------------------------------------


class TestEnrichActions:
    def test_enrich_adds_nav_target_per_action(self):
        out = enrich_actions_with_targets(
            [
                {"key": "alerts.critical_present", "rationale_refs": []},
                {"key": "mystery.x", "rationale_refs": []},
            ],
            "pA",
        )
        assert len(out) == 2
        # Known family → non-null target
        assert out[0]["nav_target"] is not None
        assert out[0]["nav_target"]["surface"] == "alerts"
        # Unknown family → null target
        assert out[1]["nav_target"] is None

    def test_enrich_preserves_original_fields(self):
        out = enrich_actions_with_targets(
            [{
                "key": "factors.strong_rate_pressure",
                "title": "Review rate-sensitive exposure",
                "rationale_refs": ["factor:interest_rate"],
                "related_tickers": ["AAPL", "MSFT"],
            }],
            "pA",
        )
        assert out[0]["title"] == "Review rate-sensitive exposure"
        assert out[0]["related_tickers"] == ["AAPL", "MSFT"]
        assert out[0]["nav_target"]["filter"] == "interest_rate"

    def test_enrich_skips_non_dict_entries(self):
        out = enrich_actions_with_targets([None, "str", 42, {"key": "alerts.x"}], "pA")  # type: ignore[list-item]
        assert len(out) == 1
        assert out[0]["nav_target"]["surface"] == "alerts"


class TestEnrichEvidenceRefs:
    def test_enrich_produces_parallel_list(self):
        out = enrich_evidence_refs(
            ["event:e1", "factor:interest_rate", "mystery:x"],
            "pA",
        )
        assert len(out) == 3
        assert out[0] == {
            "ref": "event:e1",
            "nav_target": {
                "surface": "events", "portfolio_id": "pA",
                "entity_type": "event", "entity_id": "e1",
                "subtab": "events", "filter": None, "open_modal": True,
                "highlight_key": "event:e1", "label": "Open event",
            },
        }
        assert out[1]["nav_target"]["surface"] == "operator"
        assert out[1]["nav_target"]["filter"] == "interest_rate"
        # Unknown → null target
        assert out[2]["nav_target"] is None

    def test_enrich_drops_blank_and_non_string(self):
        out = enrich_evidence_refs(["", None, 42, "event:e1"], "pA")  # type: ignore[list-item]
        assert len(out) == 1
        assert out[0]["ref"] == "event:e1"


# ---------------------------------------------------------------------------
# 9) Portfolio safety — every target must carry portfolio_id
# ---------------------------------------------------------------------------


class TestPortfolioSafety:
    @pytest.mark.parametrize("builder,args", [
        (target_for_alert, ("a1",)),
        (target_for_event, ("e1",)),
        (target_for_digest, ()),
        (target_for_operator_entry, ({"id": "o1", "entity_type": "holding_factor_sensitivity"},)),
        (target_for_action, ({"key": "alerts.critical_present"},)),
        (target_for_evidence_ref, ("event:e1",)),
    ])
    def test_every_builder_carries_portfolio_id(self, builder, args):
        t = builder(*args, "pA")
        assert t is not None
        assert t.portfolio_id == "pA"

    def test_targets_isolate_between_portfolios(self):
        t_a = target_for_alert("a1", "pA")
        t_b = target_for_alert("a1", "pB")
        assert t_a.portfolio_id == "pA"
        assert t_b.portfolio_id == "pB"
        # Same entity id but different portfolios — the portfolio
        # safety rule means a jump from pA never lands on pB.
        assert t_a.entity_id == t_b.entity_id


# ---------------------------------------------------------------------------
# 10) NavigationTarget.to_dict — JSON-safe
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict_is_json_safe(self):
        import json
        t = target_for_event("evt_fed", "pA", label="Open Fed event")
        d = t.to_dict()
        payload = json.dumps(d)
        assert "evt_fed" in payload
        assert d["open_modal"] is True
        assert d["highlight_key"] == "event:evt_fed"
        assert d["portfolio_id"] == "pA"

    def test_dataclass_is_frozen(self):
        t = target_for_alert("a1", "pA")
        with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError/dataclasses
            t.surface = "events"  # type: ignore[misc]
