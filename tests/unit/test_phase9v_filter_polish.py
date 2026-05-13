"""Phase 9V unit tests — alerts filter + saved-view readability.

Covers:
1. ``describe_view`` — the unified labeling function
2. Alerts severity filter validation via ``validate_filters``
3. Backward compat with pre-9V payloads
"""

from __future__ import annotations

import pytest

from src.intelligence.navigation import (
    _safe_target,
    describe_view,
    encode_nav_hash,
    decode_nav_hash,
    validate_filters,
)


# ---------------------------------------------------------------------------
# 1) describe_view
# ---------------------------------------------------------------------------


class TestDescribeView:
    def test_alerts_with_severity_filter(self):
        assert describe_view({"surface": "alerts", "filters": {"severity": "critical_high"}}) == "Alerts · Critical & High"

    def test_alerts_critical_only(self):
        assert describe_view({"surface": "alerts", "filters": {"severity": "critical"}}) == "Alerts · Critical only"

    def test_alerts_no_filter(self):
        assert describe_view({"surface": "alerts"}) == "Alerts"

    def test_operator_relationships_with_source(self):
        r = describe_view({"surface": "operator", "subtab": "relationships", "filters": {"source": "manual"}})
        assert r == "Operator · Relationships · Source: manual"

    def test_events_with_search(self):
        assert describe_view({"surface": "events", "subtab": "events", "filters": {"search": "fed"}}) == "Events · Search: fed"

    def test_portfolio_plain(self):
        assert describe_view({"surface": "portfolio"}) == "Portfolio"

    def test_holding_detail(self):
        r = describe_view({"surface": "portfolio", "entity_type": "holding", "open_modal": True})
        assert "Holding detail" in r

    def test_event_detail(self):
        r = describe_view({"surface": "events", "entity_type": "event", "open_modal": True})
        assert "Event detail" in r

    def test_operator_factors_with_factor(self):
        r = describe_view({"surface": "operator", "subtab": "factors", "filters": {"factor": "interest_rate"}})
        assert "Factor: interest_rate" in r

    def test_empty_payload(self):
        assert describe_view({}) == "Unknown view"
        assert describe_view(None) == "Unknown view"

    def test_unknown_severity_value_falls_back_to_raw(self):
        r = describe_view({"surface": "alerts", "filters": {"severity": "mystery"}})
        assert "mystery" in r

    def test_subtab_same_as_surface_not_duplicated(self):
        r = describe_view({"surface": "events", "subtab": "events"})
        # "Events" should appear only once, not "Events · Events"
        assert r == "Events"

    def test_digest_surface(self):
        assert describe_view({"surface": "digest", "subtab": "digest"}) == "Digest"


# ---------------------------------------------------------------------------
# 2) Alerts severity filter validation
# ---------------------------------------------------------------------------


class TestAlertsSeverityFilter:
    def test_severity_key_approved(self):
        r = validate_filters("alerts", None, {"severity": "critical_high"})
        assert r == {"severity": "critical_high"}

    def test_unknown_alert_filter_key_stripped(self):
        r = validate_filters("alerts", None, {"severity": "critical", "bogus": "x"})
        assert r == {"severity": "critical"}

    def test_severity_round_trips_through_hash(self):
        t = _safe_target(
            surface="alerts", portfolio_id="pA",
            filters={"severity": "critical_high"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"severity": "critical_high"}


# ---------------------------------------------------------------------------
# 3) Backward compat with pre-9V payloads
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_pre_9v_hash_without_filters_still_decodes(self):
        t = _safe_target(surface="alerts", portfolio_id="pA")
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "alerts"
        assert d.get("filters") is None

    def test_describe_view_handles_pre_9v_payload(self):
        # A pre-9V saved view with no filters field
        assert describe_view({"surface": "alerts"}) == "Alerts"
        assert describe_view({"surface": "operator", "subtab": "factors"}) == "Operator · Factors"
