"""Phase 9W unit tests — server-backed alerts filtering + saved-view quality.

Covers:
1. ``validate_filters`` with the new ``ack`` key
2. ``describe_view`` with combined severity + ack filters
3. Hash round-trip for combined alert filters
4. Backward compat with pre-9W payloads (no ``ack`` key)
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
# 1) ack filter validation
# ---------------------------------------------------------------------------


class TestAckFilterValidation:
    def test_ack_key_approved_for_alerts(self):
        r = validate_filters("alerts", None, {"ack": "open"})
        assert r == {"ack": "open"}

    def test_severity_and_ack_both_approved(self):
        r = validate_filters("alerts", None, {"severity": "critical_high", "ack": "ack"})
        assert r == {"severity": "critical_high", "ack": "ack"}

    def test_unknown_key_stripped_with_ack(self):
        r = validate_filters("alerts", None, {"ack": "open", "bogus": "x"})
        assert r == {"ack": "open"}


# ---------------------------------------------------------------------------
# 2) describe_view with ack filter
# ---------------------------------------------------------------------------


class TestDescribeViewAck:
    def test_open_only_label(self):
        r = describe_view({"surface": "alerts", "filters": {"ack": "open"}})
        assert r == "Alerts · Open only"

    def test_acknowledged_only_label(self):
        r = describe_view({"surface": "alerts", "filters": {"ack": "ack"}})
        assert r == "Alerts · Acknowledged only"

    def test_combined_severity_and_ack(self):
        r = describe_view({"surface": "alerts", "filters": {"severity": "critical_high", "ack": "open"}})
        assert "Critical & High" in r
        assert "Open only" in r

    def test_all_ack_label(self):
        r = describe_view({"surface": "alerts", "filters": {"ack": "all"}})
        assert "Open & acknowledged" in r

    def test_empty_ack_is_omitted_from_description(self):
        r = describe_view({"surface": "alerts", "filters": {"ack": ""}})
        # Empty ack value → treated as "no filter" → not shown
        assert r == "Alerts"


# ---------------------------------------------------------------------------
# 3) Hash round-trip with ack filter
# ---------------------------------------------------------------------------


class TestAckHashRoundTrip:
    def test_ack_filter_round_trips(self):
        t = _safe_target(
            surface="alerts", portfolio_id="pA",
            filters={"ack": "open"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"ack": "open"}

    def test_combined_filters_round_trip(self):
        t = _safe_target(
            surface="alerts", portfolio_id="pA",
            filters={"severity": "critical_high", "ack": "ack"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"]["severity"] == "critical_high"
        assert d["filters"]["ack"] == "ack"


# ---------------------------------------------------------------------------
# 4) Backward compat
# ---------------------------------------------------------------------------


class TestBackwardCompat9W:
    def test_pre_9w_hash_without_ack_still_decodes(self):
        """A 9V-era hash with only ``severity`` and no ``ack`` should
        still decode cleanly."""
        t = _safe_target(
            surface="alerts", portfolio_id="pA",
            filters={"severity": "critical"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"severity": "critical"}
        assert "ack" not in d["filters"]

    def test_describe_view_handles_pre_9w_payload(self):
        # A pre-9W saved view with only severity, no ack
        r = describe_view({"surface": "alerts", "filters": {"severity": "critical_high"}})
        assert r == "Alerts · Critical & High"
