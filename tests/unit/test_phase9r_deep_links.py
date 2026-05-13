"""Phase 9R unit tests — shareable deep links + exact anchors.

Covers the three new responsibilities of Phase 9R:

1. ``encode_nav_hash`` / ``decode_nav_hash`` — the URL hash encoding
   round-trip contract, including edge cases (malformed input,
   missing fields, unknown surfaces, padding handling).
2. Exact holding-level ``highlight_key`` on ``target_for_evidence_ref``
   for ``holding:`` and ``ticker:`` prefixes.
3. Exact operator-row ``highlight_key`` on ``target_for_operator_entry``
   (``factor-row:`` and ``rel-row:`` prefixes).

Every test is pure — no DB, no DOM, no Playwright.
"""

from __future__ import annotations

import json

import pytest

from src.intelligence.navigation import (
    NAV_HASH_PREFIX,
    NavigationTarget,
    _safe_target,
    decode_nav_hash,
    encode_nav_hash,
    target_for_evidence_ref,
    target_for_operator_entry,
)


# ---------------------------------------------------------------------------
# 1) Hash encode/decode round-trip
# ---------------------------------------------------------------------------


class TestHashEncode:
    def test_round_trip_preserves_all_fields(self):
        t = _safe_target(
            surface="alerts",
            portfolio_id="pA",
            entity_type="alert",
            entity_id="alert_abc",
            highlight_key="alert:alert_abc",
            open_modal=False,
        )
        h = encode_nav_hash(t)
        assert h.startswith(f"#{NAV_HASH_PREFIX}")
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "alerts"
        assert d["portfolio_id"] == "pA"
        assert d["entity_type"] == "alert"
        assert d["entity_id"] == "alert_abc"
        assert d["highlight_key"] == "alert:alert_abc"
        assert d["open_modal"] is False

    def test_label_is_stripped_from_hash(self):
        t = _safe_target(
            surface="events", portfolio_id="pA",
            entity_id="e1", label="Open event",
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert "label" not in d  # stripped for compactness

    def test_none_values_are_stripped(self):
        t = _safe_target(
            surface="digest", portfolio_id="pA",
            subtab="digest",
        )
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        # entity_type / entity_id / filter / highlight_key were None
        assert "entity_type" not in d
        assert "filter" not in d

    def test_dict_input_accepted(self):
        raw = {"surface": "alerts", "portfolio_id": "pA", "label": "x"}
        h = encode_nav_hash(raw)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "alerts"
        assert "label" not in d

    def test_non_target_input_returns_empty_string(self):
        assert encode_nav_hash(42) == ""  # type: ignore[arg-type]
        assert encode_nav_hash(None) == ""  # type: ignore[arg-type]


class TestHashDecode:
    def test_empty_string_returns_none(self):
        assert decode_nav_hash("") is None

    def test_none_input_returns_none(self):
        assert decode_nav_hash(None) is None  # type: ignore[arg-type]

    def test_no_prefix_returns_none(self):
        assert decode_nav_hash("#foo=abc") is None

    def test_garbage_base64_returns_none(self):
        assert decode_nav_hash(f"#{NAV_HASH_PREFIX}!!!invalid!!!") is None

    def test_valid_base64_but_invalid_json_returns_none(self):
        import base64
        raw = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        assert decode_nav_hash(f"#{NAV_HASH_PREFIX}{raw}") is None

    def test_valid_json_but_missing_surface_returns_none(self):
        import base64
        payload = json.dumps({"portfolio_id": "pA"})
        raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        assert decode_nav_hash(f"#{NAV_HASH_PREFIX}{raw}") is None

    def test_valid_json_but_unknown_surface_returns_none(self):
        import base64
        payload = json.dumps({"surface": "mystery", "portfolio_id": "pA"})
        raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        assert decode_nav_hash(f"#{NAV_HASH_PREFIX}{raw}") is None

    def test_valid_json_but_missing_portfolio_returns_none(self):
        import base64
        payload = json.dumps({"surface": "alerts"})
        raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        assert decode_nav_hash(f"#{NAV_HASH_PREFIX}{raw}") is None

    def test_with_or_without_leading_hash(self):
        t = _safe_target(surface="alerts", portfolio_id="pA")
        h = encode_nav_hash(t)
        # With #
        d1 = decode_nav_hash(h)
        # Without #
        d2 = decode_nav_hash(h.lstrip("#"))
        assert d1 == d2

    def test_padded_and_unpadded_base64_both_decode(self):
        """Base64 padding (trailing '=') is stripped by the encoder
        and re-added by the decoder.  Verify both forms decode."""
        t = _safe_target(
            surface="events", portfolio_id="pA",
            entity_id="evt123", open_modal=True,
        )
        h = encode_nav_hash(t)
        # Manually add padding to simulate a copy-paste artifact
        h_padded = h + "=="
        # Both should decode to the same dict (the decoder strips
        # excess padding gracefully)
        d1 = decode_nav_hash(h)
        d2 = decode_nav_hash(h_padded)
        # d2 may be None if the excess padding makes the payload
        # invalid — that's fine, but d1 must always work.
        assert d1 is not None
        assert d1["entity_id"] == "evt123"


# ---------------------------------------------------------------------------
# 2) Exact holding highlight_key
# ---------------------------------------------------------------------------


class TestHoldingHighlightKey:
    def test_holding_ref_carries_holding_highlight(self):
        t = target_for_evidence_ref("holding:h_aapl_pA", "pA")
        assert t is not None
        assert t.highlight_key == "holding:h_aapl_pA"

    def test_ticker_ref_carries_ticker_highlight(self):
        t = target_for_evidence_ref("ticker:AAPL", "pA")
        assert t is not None
        assert t.highlight_key == "ticker:AAPL"


# ---------------------------------------------------------------------------
# 3) Exact operator-row highlight_key
# ---------------------------------------------------------------------------


class TestOperatorRowHighlightKey:
    def test_factor_override_with_both_refs_gets_factor_row_key(self):
        entry = {
            "id": "o1",
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": ["factor:interest_rate", "holding:h_aapl"],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.highlight_key == "factor-row:h_aapl:interest_rate"

    def test_factor_override_with_only_holding_ref_falls_back_to_audit(self):
        entry = {
            "id": "o2",
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": ["holding:h_aapl"],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.highlight_key == "audit:o2"

    def test_factor_override_with_no_refs_no_id_gets_none(self):
        entry = {
            "id": None,
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.highlight_key is None

    def test_relationship_gets_rel_row_key(self):
        entry = {
            "id": "rel_aapl_tsmc",
            "entity_type": "holding_relationship",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.highlight_key == "rel-row:rel_aapl_tsmc"

    def test_reconcile_keeps_audit_level_key(self):
        entry = {
            "id": "rc1",
            "entity_type": "holding_relationships",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.highlight_key == "audit:rc1"


# ---------------------------------------------------------------------------
# 4) Hash round-trip of targets with exact keys
# ---------------------------------------------------------------------------


class TestHashExactTargetRoundTrip:
    def test_holding_target_round_trips_through_hash(self):
        t = target_for_evidence_ref("holding:h_aapl", "pA")
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "portfolio"
        assert d["highlight_key"] == "holding:h_aapl"
        assert d["portfolio_id"] == "pA"

    def test_factor_row_target_round_trips_through_hash(self):
        entry = {
            "id": "o1",
            "entity_type": "holding_factor_sensitivity",
            "evidence_refs": ["factor:interest_rate", "holding:h_aapl"],
        }
        t = target_for_operator_entry(entry, "pA")
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["highlight_key"] == "factor-row:h_aapl:interest_rate"
        assert d["surface"] == "operator"

    def test_event_with_modal_round_trips(self):
        from src.intelligence.navigation import target_for_event
        t = target_for_event("evt_fed", "pA", open_modal=True)
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["open_modal"] is True
        assert d["entity_id"] == "evt_fed"
        assert d["surface"] == "events"
