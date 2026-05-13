"""Phase 9S unit tests — history-aware navigation + exact holding targets.

Covers the Phase 9S additions to :mod:`src.intelligence.navigation`:

1. ``target_for_holding`` — the new builder that emits exact
   holding-detail landing targets (``open_modal=True``).
2. Maintenance anchor highlight keys — reconcile and backfill
   operator entries carry audit-level keys that the frontend maps
   to ``[data-maintenance-action]`` DOM anchors.
3. Hash encode/decode of the new holding-detail and maintenance
   targets (round-trip fidelity).
"""

from __future__ import annotations

import pytest

from src.intelligence.navigation import (
    NavigationTarget,
    _safe_target,
    decode_nav_hash,
    encode_nav_hash,
    target_for_holding,
    target_for_evidence_ref,
    target_for_operator_entry,
)


# ---------------------------------------------------------------------------
# 1) target_for_holding — exact detail landing
# ---------------------------------------------------------------------------


class TestTargetForHolding:
    def test_holding_with_open_detail_true(self):
        t = target_for_holding("h_aapl_pA", "pA", open_detail=True)
        assert t is not None
        assert t.surface == "portfolio"
        assert t.portfolio_id == "pA"
        assert t.entity_type == "holding"
        assert t.entity_id == "h_aapl_pA"
        assert t.open_modal is True
        assert t.highlight_key == "holding:h_aapl_pA"
        assert "detail" in t.label.lower()

    def test_holding_with_open_detail_false(self):
        t = target_for_holding("h_aapl_pA", "pA", open_detail=False)
        assert t is not None
        assert t.open_modal is False
        assert "detail" not in t.label.lower()

    def test_holding_without_id_falls_back_to_tab(self):
        t = target_for_holding("", "pA")
        assert t is not None
        assert t.surface == "portfolio"
        assert t.entity_id is None
        assert t.open_modal is False

    def test_custom_label(self):
        t = target_for_holding("h1", "pA", label="Inspect AAPL")
        assert t.label == "Inspect AAPL"


# ---------------------------------------------------------------------------
# 2) Evidence ref holding targets include highlight_key
# ---------------------------------------------------------------------------


class TestEvidenceRefHolding:
    def test_holding_ref_has_highlight_and_no_modal_by_default(self):
        t = target_for_evidence_ref("holding:h_aapl", "pA")
        assert t is not None
        assert t.highlight_key == "holding:h_aapl"
        assert t.open_modal is False  # lightweight ref

    def test_ticker_ref_has_highlight_and_no_modal(self):
        t = target_for_evidence_ref("ticker:AAPL", "pA")
        assert t is not None
        assert t.highlight_key == "ticker:AAPL"
        assert t.open_modal is False


# ---------------------------------------------------------------------------
# 3) Maintenance anchor keys
# ---------------------------------------------------------------------------


class TestMaintenanceAnchorKeys:
    def test_reconcile_entry_carries_audit_key(self):
        entry = {
            "id": "rc1",
            "entity_type": "holding_relationships",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.subtab == "maintenance"
        assert t.entity_type == "holding_relationships"
        # The audit-level key is used by _applyHighlight; the
        # maintenance block is scrolled by _applyMaintenanceAnchor
        # which keys off entity_type.
        assert t.highlight_key == "audit:rc1"

    def test_backfill_entry_carries_audit_key(self):
        entry = {
            "id": "bf1",
            "entity_type": "intelligence_backfill",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        assert t is not None
        assert t.subtab == "maintenance"
        assert t.entity_type == "intelligence_backfill"
        assert t.highlight_key == "audit:bf1"


# ---------------------------------------------------------------------------
# 4) Hash round-trip of holding-detail target
# ---------------------------------------------------------------------------


class TestHoldingDetailHashRoundTrip:
    def test_holding_detail_target_round_trips(self):
        t = target_for_holding("h_aapl_pA", "pA", open_detail=True)
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "portfolio"
        assert d["entity_type"] == "holding"
        assert d["entity_id"] == "h_aapl_pA"
        assert d["open_modal"] is True
        assert d["highlight_key"] == "holding:h_aapl_pA"
        assert d["portfolio_id"] == "pA"

    def test_holding_row_only_target_round_trips(self):
        t = target_for_holding("h_msft", "pB", open_detail=False)
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["open_modal"] is False
        assert d["entity_id"] == "h_msft"

    def test_maintenance_target_round_trips(self):
        entry = {
            "id": "bf1",
            "entity_type": "intelligence_backfill",
            "evidence_refs": [],
        }
        t = target_for_operator_entry(entry, "pA")
        h = encode_nav_hash(t)
        d = decode_nav_hash(h)
        assert d is not None
        assert d["surface"] == "operator"
        assert d["entity_type"] == "intelligence_backfill"
