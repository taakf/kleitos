"""Phase 9T unit tests — action state lifecycle.

Covers:
1. ``compute_action_fingerprint`` — deterministic, stable, sensitive
   to material changes (priority, rationale_refs, related_tickers).
2. ``filter_actions_by_state`` — the visibility split rule:
   same key + same fingerprint → hidden; different fingerprint → visible.
3. Edge cases: missing fields, empty inputs, unknown keys.
"""

from __future__ import annotations

import pytest

from src.intelligence.actions import (
    compute_action_fingerprint,
    filter_actions_by_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action(key, priority="high", refs=None, tickers=None):
    return {
        "key": key,
        "priority": priority,
        "rationale_refs": refs or [],
        "related_tickers": tickers or [],
    }


# ---------------------------------------------------------------------------
# 1) Fingerprinting
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_same_inputs_produce_same_fingerprint(self):
        a = _action("x.y", "high", ["a=1"], ["AAPL"])
        assert compute_action_fingerprint(a) == compute_action_fingerprint(a)

    def test_different_priority_changes_fingerprint(self):
        a = _action("x.y", "high", ["a=1"], ["AAPL"])
        b = _action("x.y", "medium", ["a=1"], ["AAPL"])
        assert compute_action_fingerprint(a) != compute_action_fingerprint(b)

    def test_different_rationale_refs_changes_fingerprint(self):
        a = _action("x.y", "high", ["alerts.critical=1"], ["AAPL"])
        b = _action("x.y", "high", ["alerts.critical=2"], ["AAPL"])
        assert compute_action_fingerprint(a) != compute_action_fingerprint(b)

    def test_different_tickers_changes_fingerprint(self):
        a = _action("x.y", "high", ["a=1"], ["AAPL"])
        b = _action("x.y", "high", ["a=1"], ["MSFT"])
        assert compute_action_fingerprint(a) != compute_action_fingerprint(b)

    def test_additional_ticker_changes_fingerprint(self):
        a = _action("x.y", "high", ["a=1"], ["AAPL"])
        b = _action("x.y", "high", ["a=1"], ["AAPL", "MSFT"])
        assert compute_action_fingerprint(a) != compute_action_fingerprint(b)

    def test_order_of_refs_does_not_matter(self):
        """rationale_refs and tickers are sorted before hashing so
        the fingerprint is stable regardless of input order."""
        a = _action("x.y", "high", ["b", "a"], ["MSFT", "AAPL"])
        b = _action("x.y", "high", ["a", "b"], ["AAPL", "MSFT"])
        assert compute_action_fingerprint(a) == compute_action_fingerprint(b)

    def test_empty_inputs_produce_stable_fingerprint(self):
        a = _action("x.y", "", [], [])
        b = _action("x.y", "", [], [])
        assert compute_action_fingerprint(a) == compute_action_fingerprint(b)

    def test_missing_fields_produce_stable_fingerprint(self):
        a = {"key": "x.y"}
        b = {"key": "x.y"}
        assert compute_action_fingerprint(a) == compute_action_fingerprint(b)

    def test_fingerprint_is_16_hex_chars(self):
        fp = compute_action_fingerprint(_action("x.y", "high", ["a"], ["AAPL"]))
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# 2) Filter — visibility split
# ---------------------------------------------------------------------------


class TestFilterByState:
    def test_no_state_all_visible(self):
        actions = [_action("a.x"), _action("b.y")]
        visible, hidden = filter_actions_by_state(actions, {})
        assert len(visible) == 2
        assert len(hidden) == 0

    def test_same_fingerprint_hides_action(self):
        a = _action("a.x", "high", ["ref1"], ["AAPL"])
        fp = compute_action_fingerprint(a)
        visible, hidden = filter_actions_by_state(
            [a], {"a.x": ("dismissed", fp)},
        )
        assert len(visible) == 0
        assert len(hidden) == 1
        assert hidden[0]["action_state"] == "dismissed"

    def test_different_fingerprint_reappears(self):
        a = _action("a.x", "high", ["alerts.critical=2"], ["AAPL"])
        visible, hidden = filter_actions_by_state(
            [a], {"a.x": ("dismissed", "old_fingerprint")},
        )
        assert len(visible) == 1
        assert len(hidden) == 0
        assert visible[0]["action_state"] is None

    def test_read_state_also_hides(self):
        a = _action("a.x", "high", ["ref1"], ["AAPL"])
        fp = compute_action_fingerprint(a)
        visible, hidden = filter_actions_by_state(
            [a], {"a.x": ("read", fp)},
        )
        assert len(visible) == 0
        assert len(hidden) == 1
        assert hidden[0]["action_state"] == "read"

    def test_mixed_visible_and_hidden(self):
        a = _action("a.x", "high", ["ref1"], ["AAPL"])
        b = _action("b.y", "medium", ["ref2"], ["MSFT"])
        fp_a = compute_action_fingerprint(a)
        visible, hidden = filter_actions_by_state(
            [a, b], {"a.x": ("dismissed", fp_a)},
        )
        assert len(visible) == 1
        assert len(hidden) == 1
        assert visible[0]["key"] == "b.y"
        assert hidden[0]["key"] == "a.x"

    def test_unknown_handled_key_does_not_crash(self):
        a = _action("a.x")
        visible, hidden = filter_actions_by_state(
            [a], {"unknown.key": ("dismissed", "fp")},
        )
        assert len(visible) == 1
        assert len(hidden) == 0

    def test_empty_actions_returns_empty(self):
        visible, hidden = filter_actions_by_state(
            [], {"a.x": ("dismissed", "fp")},
        )
        assert visible == []
        assert hidden == []

    def test_fingerprint_attached_to_output(self):
        a = _action("a.x", "high", ["ref1"], ["AAPL"])
        visible, _ = filter_actions_by_state([a], {})
        assert "fingerprint" in visible[0]
        assert len(visible[0]["fingerprint"]) == 16

    def test_action_state_attached_to_visible_as_none(self):
        a = _action("a.x")
        visible, _ = filter_actions_by_state([a], {})
        assert visible[0]["action_state"] is None

    def test_action_state_attached_to_hidden_as_state_value(self):
        a = _action("a.x", "high", ["r"], ["T"])
        fp = compute_action_fingerprint(a)
        _, hidden = filter_actions_by_state(
            [a], {"a.x": ("dismissed", fp)},
        )
        assert hidden[0]["action_state"] == "dismissed"
