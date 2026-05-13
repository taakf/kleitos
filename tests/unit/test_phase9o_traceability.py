"""Phase 9O unit tests — traceability helpers.

Covers the three public responsibilities of
:mod:`src.intelligence.traceability`:

1. ``shape_audit_entry`` — turning a raw ``AuditLog`` row into a
   ``TraceabilityEntry`` with a human title + summary + highlights.
2. ``select_recent_operator_entries`` — the prioritisation,
   sorting, and no-op reconcile dedupe rule.
3. ``group_evidence_refs`` — the Phase 9N evidence-ref categoriser.

Every test is pure — no DB, no ORM, no fixtures.  We hand the module
``SimpleNamespace`` rows that quack like ``AuditLog`` instances.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.intelligence.traceability import (
    CATEGORY_LABELS,
    TraceabilityEntry,
    entity_type_label,
    group_evidence_refs,
    is_operator_entity_type,
    select_recent_operator_entries,
    shape_audit_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    id: str,
    entity_type: str,
    entity_id: str,
    action: str,
    created_at: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
    reason: str | None = None,
    agent_id: str | None = "operator",
    user_id: str = "operator",
) -> SimpleNamespace:
    """Build a minimal object with the ``AuditLog`` attributes the
    shaping helpers read.  Using ``SimpleNamespace`` keeps tests
    independent of the ORM session."""
    return SimpleNamespace(
        id=id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=json.dumps(old_value) if old_value is not None else None,
        new_value=json.dumps(new_value) if new_value is not None else None,
        agent_id=agent_id,
        user_id=user_id,
        reason=reason,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Registry + labels
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_operator_entity_types_are_stable(self):
        # These four entity types are the contract for this phase and
        # must never silently grow/shrink without a test update.
        assert is_operator_entity_type("holding_factor_sensitivity")
        assert is_operator_entity_type("holding_relationship")
        assert is_operator_entity_type("holding_relationships")
        assert is_operator_entity_type("intelligence_backfill")

    def test_non_operator_entity_types_are_rejected(self):
        assert not is_operator_entity_type("event")
        assert not is_operator_entity_type("alert")
        assert not is_operator_entity_type("agent_run")
        assert not is_operator_entity_type(None)
        assert not is_operator_entity_type("")

    def test_entity_type_label_known_and_unknown(self):
        assert entity_type_label("holding_factor_sensitivity") == "factor override"
        assert entity_type_label("holding_relationship") == "relationship"
        assert entity_type_label("holding_relationships") == "reconcile"
        assert entity_type_label("intelligence_backfill") == "backfill"
        # Unknown → fallback to raw value
        assert entity_type_label("custom_type") == "custom_type"
        assert entity_type_label(None) == "unknown"


# ---------------------------------------------------------------------------
# shape_audit_entry — factor sensitivity
# ---------------------------------------------------------------------------


class TestShapeFactorSensitivity:
    def test_update_builds_old_new_summary(self):
        row = _row(
            id="a1",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_abc",
            action="update",
            created_at="2026-04-05T14:22:00+00:00",
            old_value={
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.60, "holding_id": "h_aapl_pA",
            },
            new_value={
                "ticker": "AAPL", "factor": "interest_rate",
                "sensitivity": -0.90, "holding_id": "h_aapl_pA",
            },
            reason="tune beta after Mar print",
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "AAPL" in entry.title and "interest_rate" in entry.title
        assert "Updated override" in entry.title
        assert "-0.60" in entry.summary and "-0.90" in entry.summary
        assert entry.old_highlights == {"sensitivity": -0.60}
        assert entry.new_highlights == {"sensitivity": -0.90}
        assert entry.entity_type == "holding_factor_sensitivity"
        assert entry.reason == "tune beta after Mar print"
        # Evidence refs include the holding_id + factor key
        assert "holding:h_aapl_pA" in entry.evidence_refs
        assert "factor:interest_rate" in entry.evidence_refs

    def test_create_upsert_has_no_old_highlights(self):
        row = _row(
            id="a2",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_new",
            action="upsert",
            created_at="2026-04-05T14:22:00+00:00",
            old_value=None,
            new_value={
                "ticker": "MSFT", "factor": "oil_energy",
                "sensitivity": 0.20, "holding_id": "h_msft",
            },
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Created override" in entry.title
        assert "MSFT" in entry.title and "oil_energy" in entry.title
        assert "0.20" in entry.summary
        assert entry.old_highlights is None
        assert entry.new_highlights == {"sensitivity": 0.20}

    def test_delete_carries_old_value_only(self):
        row = _row(
            id="a3",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_del",
            action="delete",
            created_at="2026-04-05T14:22:00+00:00",
            old_value={
                "ticker": "NVDA", "factor": "supply_chain",
                "sensitivity": -0.35, "holding_id": "h_nvda",
            },
            new_value=None,
            reason="revert",
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Deleted override" in entry.title
        assert "NVDA" in entry.title
        assert "-0.35" in entry.summary
        assert entry.old_highlights == {"sensitivity": -0.35}
        assert entry.new_highlights is None


# ---------------------------------------------------------------------------
# shape_audit_entry — relationship (singular)
# ---------------------------------------------------------------------------


class TestShapeRelationship:
    def test_create_relationship(self):
        row = _row(
            id="a4",
            entity_type="holding_relationship",
            entity_id="rel_1",
            action="create",
            created_at="2026-04-05T14:22:00+00:00",
            old_value=None,
            new_value={
                "holding_id": "h_aapl",
                "relationship_type": "supplier",
                "related_ticker": "TSM",
                "related_entity_key": None,
                "strength": 0.85,
                "source": "manual",
            },
            reason="TSMC is primary foundry",
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Created relationship" in entry.title
        assert "supplier" in entry.title and "TSM" in entry.title
        assert "0.85" in entry.summary
        assert entry.new_highlights == {"strength": 0.85}
        assert "rel:supplier" in entry.evidence_refs
        assert "holding:h_aapl" in entry.evidence_refs
        assert "related:TSM" in entry.evidence_refs

    def test_update_relationship_shows_strength_delta(self):
        row = _row(
            id="a5",
            entity_type="holding_relationship",
            entity_id="rel_1",
            action="update",
            created_at="2026-04-05T14:22:00+00:00",
            old_value={
                "holding_id": "h_aapl",
                "relationship_type": "supplier",
                "related_ticker": "TSM",
                "strength": 0.85,
            },
            new_value={
                "holding_id": "h_aapl",
                "relationship_type": "supplier",
                "related_ticker": "TSM",
                "strength": 0.70,
            },
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Updated relationship" in entry.title
        assert "0.85" in entry.summary and "0.70" in entry.summary

    def test_delete_relationship(self):
        row = _row(
            id="a6",
            entity_type="holding_relationship",
            entity_id="rel_1",
            action="delete",
            created_at="2026-04-05T14:22:00+00:00",
            old_value={
                "holding_id": "h_aapl",
                "relationship_type": "competitor",
                "related_ticker": "SMSG",
                "strength": 0.40,
            },
            new_value=None,
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Deleted relationship" in entry.title
        assert "competitor" in entry.title
        assert entry.old_highlights == {"strength": 0.40}


# ---------------------------------------------------------------------------
# shape_audit_entry — reconcile (plural)
# ---------------------------------------------------------------------------


class TestShapeReconcile:
    def test_reconcile_with_changes(self):
        row = _row(
            id="a7",
            entity_type="holding_relationships",
            entity_id="seed_reconcile",
            action="reconcile",
            created_at="2026-04-05T14:25:00+00:00",
            new_value={
                "created": 2, "updated": 1, "unchanged": 12,
                "pruned": 0, "skipped_no_holding": 0,
            },
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "Reconciled" in entry.title
        assert "no-op" not in entry.title
        assert "created 2" in entry.summary
        assert "updated 1" in entry.summary
        assert "12 unchanged" in entry.summary
        assert entry.new_highlights["created"] == 2
        assert entry.new_highlights["pruned"] == 0

    def test_reconcile_noop_is_explicitly_labelled(self):
        row = _row(
            id="a8",
            entity_type="holding_relationships",
            entity_id="seed_reconcile",
            action="reconcile",
            created_at="2026-04-05T14:25:00+00:00",
            new_value={
                "created": 0, "updated": 0, "unchanged": 15,
                "pruned": 0, "skipped_no_holding": 0,
            },
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "no-op" in entry.title.lower()
        assert "no changes" in entry.summary.lower()


# ---------------------------------------------------------------------------
# shape_audit_entry — backfill
# ---------------------------------------------------------------------------


class TestShapeBackfill:
    def test_backfill_summary_includes_stats(self):
        row = _row(
            id="a9",
            entity_type="intelligence_backfill",
            entity_id="window_7d",
            action="backfill",
            created_at="2026-04-05T14:27:00+00:00",
            agent_id="operator_backfill",
            new_value={
                "window_days": 7,
                "events_scanned": 47,
                "events_replayed": 47,
                "links_added": 12,
                "mfe_added": 3,
                "events_failed": 0,
            },
            reason="ui backfill",
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "7d window" in entry.title
        assert "scanned 47" in entry.summary
        assert "+12 links" in entry.summary
        assert "+3 factor rows" in entry.summary
        assert entry.new_highlights["links_added"] == 12
        assert entry.new_highlights["events_failed"] == 0
        assert entry.actor == "operator_backfill"
        assert entry.reason == "ui backfill"

    def test_backfill_with_failures(self):
        row = _row(
            id="a10",
            entity_type="intelligence_backfill",
            entity_id="window_30d",
            action="backfill",
            created_at="2026-04-05T14:27:00+00:00",
            new_value={
                "window_days": 30,
                "events_scanned": 500,
                "events_replayed": 495,
                "links_added": 0,
                "mfe_added": 0,
                "events_failed": 5,
            },
        )
        entry = shape_audit_entry(row)
        assert entry is not None
        assert "5 failed" in entry.summary


# ---------------------------------------------------------------------------
# shape_audit_entry — non-operator rows are rejected
# ---------------------------------------------------------------------------


class TestShapeRejection:
    def test_non_operator_entity_type_returns_none(self):
        row = _row(
            id="b1",
            entity_type="alert",
            entity_id="alert_abc",
            action="acknowledge",
            created_at="2026-04-05T14:22:00+00:00",
        )
        assert shape_audit_entry(row) is None

    def test_missing_entity_type_returns_none(self):
        row = SimpleNamespace(
            id="b2",
            entity_type=None,
            entity_id="",
            action="",
            old_value=None, new_value=None,
            agent_id=None, user_id=None, reason=None,
            created_at="2026-04-05T14:22:00+00:00",
        )
        assert shape_audit_entry(row) is None

    def test_malformed_json_does_not_raise(self):
        row = SimpleNamespace(
            id="b3",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_x",
            action="update",
            old_value="{not valid json",
            new_value="also not valid",
            agent_id="operator", user_id="operator", reason=None,
            created_at="2026-04-05T14:22:00+00:00",
        )
        # shape_audit_entry must still return an entry (with None
        # highlights) rather than crash
        entry = shape_audit_entry(row)
        assert entry is not None
        assert entry.entity_type == "holding_factor_sensitivity"


# ---------------------------------------------------------------------------
# select_recent_operator_entries — prioritisation + dedupe
# ---------------------------------------------------------------------------


class TestSelectRecentEntries:
    def test_newest_first_across_mixed_entity_types(self):
        rows = [
            _row(id="r1", entity_type="intelligence_backfill", entity_id="window_7d",
                 action="backfill", created_at="2026-04-05T10:00:00+00:00",
                 new_value={"window_days": 7, "events_scanned": 10, "events_replayed": 10}),
            _row(id="r2", entity_type="holding_factor_sensitivity", entity_id="ovr",
                 action="update", created_at="2026-04-05T15:00:00+00:00",
                 old_value={"ticker": "AAPL", "factor": "interest_rate", "sensitivity": -0.5},
                 new_value={"ticker": "AAPL", "factor": "interest_rate", "sensitivity": -0.7}),
            _row(id="r3", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T12:00:00+00:00",
                 new_value={"created": 1, "updated": 0, "unchanged": 5, "pruned": 0}),
        ]
        entries = select_recent_operator_entries(rows, limit=10)
        assert [e.id for e in entries] == ["r2", "r3", "r1"]

    def test_non_operator_rows_are_filtered_out(self):
        rows = [
            _row(id="r1", entity_type="event", entity_id="evt", action="ingest",
                 created_at="2026-04-05T15:00:00+00:00"),
            _row(id="r2", entity_type="holding_relationship", entity_id="rel",
                 action="create", created_at="2026-04-05T14:00:00+00:00",
                 new_value={"holding_id": "h", "relationship_type": "supplier",
                            "related_ticker": "TSM", "strength": 0.7}),
        ]
        entries = select_recent_operator_entries(rows, limit=10)
        assert [e.id for e in entries] == ["r2"]

    def test_consecutive_noop_reconciles_are_collapsed(self):
        # Three consecutive no-op reconciles should collapse into one.
        rows = [
            _row(id="n1", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T15:00:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
            _row(id="n2", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T14:59:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
            _row(id="n3", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T14:58:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
        ]
        entries = select_recent_operator_entries(rows, limit=10)
        assert len(entries) == 1
        assert entries[0].id == "n1"

    def test_noop_reconcile_does_not_collapse_through_a_real_action(self):
        # A real action between two no-op reconciles must break the
        # collapse chain — both no-ops survive because they are no
        # longer "consecutive".
        rows = [
            _row(id="n1", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T15:00:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
            _row(id="mid", entity_type="holding_factor_sensitivity", entity_id="ovr",
                 action="update", created_at="2026-04-05T14:50:00+00:00",
                 new_value={"ticker": "AAPL", "factor": "interest_rate", "sensitivity": -0.7}),
            _row(id="n2", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T14:40:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
        ]
        entries = select_recent_operator_entries(rows, limit=10)
        ids = [e.id for e in entries]
        assert ids == ["n1", "mid", "n2"]

    def test_real_reconcile_is_never_collapsed(self):
        # A real reconcile with created>0 is never collapsed, even
        # when it's immediately after a no-op.
        rows = [
            _row(id="real", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T15:00:00+00:00",
                 new_value={"created": 2, "updated": 0, "unchanged": 10, "pruned": 0}),
            _row(id="noop", entity_type="holding_relationships", entity_id="seed_reconcile",
                 action="reconcile", created_at="2026-04-05T14:59:00+00:00",
                 new_value={"created": 0, "updated": 0, "unchanged": 10, "pruned": 0}),
        ]
        entries = select_recent_operator_entries(rows, limit=10)
        assert [e.id for e in entries] == ["real", "noop"]

    def test_limit_is_enforced(self):
        rows = [
            _row(id=f"r{i}", entity_type="holding_factor_sensitivity", entity_id=f"o{i}",
                 action="update", created_at=f"2026-04-05T15:{i:02d}:00+00:00",
                 new_value={"ticker": "AAPL", "factor": "interest_rate", "sensitivity": -0.5})
            for i in range(20)
        ]
        entries = select_recent_operator_entries(rows, limit=5)
        assert len(entries) == 5

    def test_limit_floor_is_one(self):
        row = _row(id="r", entity_type="holding_relationships", entity_id="seed_reconcile",
                   action="reconcile", created_at="2026-04-05T15:00:00+00:00",
                   new_value={"created": 1, "updated": 0, "unchanged": 0, "pruned": 0})
        entries = select_recent_operator_entries([row], limit=0)
        assert len(entries) == 1  # floors to 1

    def test_empty_input_returns_empty(self):
        assert select_recent_operator_entries([], limit=10) == []


# ---------------------------------------------------------------------------
# group_evidence_refs
# ---------------------------------------------------------------------------


class TestGroupEvidenceRefs:
    def test_basic_categorisation(self):
        groups = group_evidence_refs([
            "factor:interest_rate",
            "alert:alert_abc",
            "holding:h_aapl",
            "ticker:AAPL",
            "rel:supplier",
        ])
        assert groups["factors"] == ["factor:interest_rate"]
        assert groups["alerts"] == ["alert:alert_abc"]
        assert groups["holdings"] == ["holding:h_aapl"]
        assert groups["tickers"] == ["ticker:AAPL"]
        assert groups["relationships"] == ["rel:supplier"]

    def test_unknown_prefix_goes_to_other(self):
        groups = group_evidence_refs(["mystery:x", "random"])
        assert "other" in groups
        assert set(groups["other"]) == {"mystery:x", "random"}

    def test_counts_category_collects_multiple_prefixes(self):
        groups = group_evidence_refs([
            "holdings:3",
            "distinct_factors=4",
        ])
        assert set(groups["counts"]) == {"holdings:3", "distinct_factors=4"}

    def test_maintenance_category(self):
        groups = group_evidence_refs([
            "reconcile.created=3",
            "backfill.links_added=12",
            "manual_edit",
        ])
        assert set(groups["maintenance"]) == {
            "reconcile.created=3",
            "backfill.links_added=12",
            "manual_edit",
        }

    def test_empty_and_invalid_input(self):
        assert group_evidence_refs([]) == {}
        assert group_evidence_refs([None, "", 42]) == {}

    def test_preserves_input_order(self):
        refs = ["factor:a", "factor:b", "factor:c"]
        groups = group_evidence_refs(refs)
        assert groups["factors"] == refs

    def test_category_labels_cover_all_categories(self):
        # Every category key used by the grouper must be in the
        # labels map so the UI can always resolve a human label.
        groups = group_evidence_refs([
            "factor:x", "alert:x", "rel:x", "holding:x", "ticker:x",
            "related:x", "note:x", "attention:x", "repeat_neg:x",
            "holdings:x", "distinct_factors=x", "stale_minutes=x",
            "reconcile.x", "backfill.x", "manual_edit",
            "totally_unknown",
        ])
        for cat in groups:
            assert cat in CATEGORY_LABELS, f"category {cat!r} missing label"


# ---------------------------------------------------------------------------
# TraceabilityEntry JSON round-trip
# ---------------------------------------------------------------------------


class TestTraceabilityEntryDict:
    def test_to_dict_round_trips(self):
        entry = TraceabilityEntry(
            id="a1",
            title="Updated override · AAPL",
            timestamp="2026-04-05T14:22:00+00:00",
            actor="operator",
            entity_type="holding_factor_sensitivity",
            entity_id="ovr_abc",
            action="update",
            summary="AAPL · interest_rate: -0.60 → -0.90",
            old_highlights={"sensitivity": -0.60},
            new_highlights={"sensitivity": -0.90},
            evidence_refs=("holding:h_aapl", "factor:interest_rate"),
            reason="tune beta",
        )
        d = entry.to_dict()
        # Must be JSON-safe
        payload = json.dumps(d)
        assert "AAPL" in payload
        assert d["entity_type_label"] == "factor override"
        assert d["evidence_refs"] == ["holding:h_aapl", "factor:interest_rate"]
        assert d["old_highlights"] == {"sensitivity": -0.60}
        assert d["portfolio_id"] is None
