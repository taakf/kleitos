"""Phase 9U unit tests — saved views + filter-state deep links.

Covers:
1. ``validate_filters`` — approved filter keys registry
2. ``NavigationTarget.filters`` field — construction, to_dict, hash round-trip
3. Backward compat — old hashes without ``filters`` still decode
"""

from __future__ import annotations

import pytest

from src.intelligence.navigation import (
    _APPROVED_FILTERS,
    _safe_target,
    NavigationTarget,
    decode_nav_hash,
    encode_nav_hash,
    validate_filters,
)


# ---------------------------------------------------------------------------
# 1) validate_filters
# ---------------------------------------------------------------------------


class TestValidateFilters:
    def test_approved_factor_key_passes(self):
        r = validate_filters("operator", "factors", {"factor": "interest_rate"})
        assert r == {"factor": "interest_rate"}

    def test_approved_source_key_passes(self):
        r = validate_filters("operator", "relationships", {"source": "seed"})
        assert r == {"source": "seed"}

    def test_unknown_key_stripped(self):
        r = validate_filters("operator", "factors", {"factor": "x", "unknown": "y"})
        assert r == {"factor": "x"}

    def test_all_unknown_returns_none(self):
        r = validate_filters("operator", "factors", {"unknown": "y"})
        assert r is None

    def test_empty_input_returns_none(self):
        assert validate_filters("operator", "factors", {}) is None
        assert validate_filters("operator", "factors", None) is None

    def test_unknown_surface_returns_none(self):
        assert validate_filters("mystery", None, {"x": "y"}) is None

    def test_events_search_filter_passes(self):
        r = validate_filters("events", "events", {"search": "fed"})
        assert r == {"search": "fed"}

    def test_alerts_severity_passes(self):
        r = validate_filters("alerts", None, {"severity": "critical"})
        assert r == {"severity": "critical"}

    def test_approved_registry_is_stable(self):
        # Lock in the registry so changes require a test update
        assert ("operator", "factors") in _APPROVED_FILTERS
        assert ("operator", "relationships") in _APPROVED_FILTERS
        assert ("events", "events") in _APPROVED_FILTERS
        assert ("alerts", None) in _APPROVED_FILTERS


# ---------------------------------------------------------------------------
# 2) NavigationTarget with filters
# ---------------------------------------------------------------------------


class TestNavigationTargetFilters:
    def test_safe_target_with_filters(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="factors",
            filters={"factor": "interest_rate"},
        )
        assert t is not None
        assert t.filters == {"factor": "interest_rate"}

    def test_safe_target_strips_unknown_filters(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="factors",
            filters={"factor": "x", "unknown": "y"},
        )
        assert t is not None
        assert t.filters == {"factor": "x"}

    def test_safe_target_without_filters(self):
        t = _safe_target(surface="alerts", portfolio_id="pA")
        assert t is not None
        assert t.filters is None

    def test_to_dict_includes_filters_when_present(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="factors",
            filters={"factor": "oil_energy"},
        )
        d = t.to_dict()
        assert d["filters"] == {"factor": "oil_energy"}

    def test_to_dict_omits_filters_when_none(self):
        t = _safe_target(surface="alerts", portfolio_id="pA")
        d = t.to_dict()
        assert "filters" not in d


# ---------------------------------------------------------------------------
# 3) Hash round-trip with filters
# ---------------------------------------------------------------------------


class TestFiltersHashRoundTrip:
    def test_filters_survive_hash_encode_decode(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="factors",
            filters={"factor": "interest_rate"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"factor": "interest_rate"}

    def test_filters_absent_in_old_hash_is_fine(self):
        t = _safe_target(surface="alerts", portfolio_id="pA", filter="test")
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d.get("filter") == "test"
        assert d.get("filters") is None

    def test_empty_filters_stripped(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="factors",
            filters={},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        # Empty filters → None (stripped during to_dict)
        assert d is not None
        assert d.get("filters") is None

    def test_relationship_source_filter_round_trips(self):
        t = _safe_target(
            surface="operator", portfolio_id="pA", subtab="relationships",
            filters={"source": "manual"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"source": "manual"}

    def test_events_search_round_trips(self):
        t = _safe_target(
            surface="events", portfolio_id="pA", subtab="events",
            filters={"search": "federal reserve"},
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["filters"] == {"search": "federal reserve"}
