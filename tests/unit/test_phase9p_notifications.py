"""Phase 9P unit tests — notification inbox builder.

Covers the three public responsibilities of
:mod:`src.intelligence.notifications`:

1. Source-specific shaping (alerts, digests, operator entries,
   recommended actions).
2. Prioritisation + ordering (unread > read, high > medium > low,
   newest within priority).
3. Dedupe + cap enforcement.

Every test is pure — no DB, no ORM, no fixtures.  The builder takes
simple dicts that mirror the shapes produced by the upstream API
layers (alerts route, digest reader, Phase 9O traceability, Phase
9N action builder).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.intelligence.notifications import (
    MAX_INBOX_ITEMS,
    InboxInputs,
    NotificationItem,
    _ALERT_SEVERITY_PRIORITY,
    _OPERATOR_ENTITY_PRIORITY,
    build_inbox,
    summarise_inbox,
    within_window,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alert(
    id_="a1", sev="critical", title="t", body="b", created="2026-04-06T14:00:00+00:00",
    acknowledged=False, related_holdings=None, related_events=None, alert_type="risk",
):
    return {
        "id": id_,
        "severity": sev,
        "title": title,
        "body": body,
        "created_at": created,
        "acknowledged": acknowledged,
        "related_holdings": related_holdings or [],
        "related_events": related_events or [],
        "alert_type": alert_type,
    }


def _digest(id_="d1", created="2026-04-06T12:00:00+00:00", content=None):
    import json
    if content is None:
        content = {"headline": "Daily", "portfolio_assessment": "ok"}
    return {
        "id": id_,
        "digest_type": "daily",
        "content": json.dumps(content) if isinstance(content, dict) else content,
        "event_count": 3,
        "alert_count": 2,
        "holding_count": 1,
        "created_at": created,
    }


def _op(
    id_, title, summary, entity_type, created="2026-04-06T13:00:00+00:00",
    failed=0, actor="operator",
):
    entry = {
        "id": id_,
        "title": title,
        "summary": summary,
        "entity_type": entity_type,
        "timestamp": created,
        "actor": actor,
        "evidence_refs": [],
    }
    if entity_type == "intelligence_backfill":
        entry["new_highlights"] = {"events_failed": failed}
    return entry


def _action(key, title, priority="high", refs=None, tickers=None):
    return {
        "key": key,
        "title": title,
        "description": f"desc:{key}",
        "priority": priority,
        "rationale_refs": refs or [],
        "related_tickers": tickers or [],
    }


# ---------------------------------------------------------------------------
# 1) Source shaping — alerts
# ---------------------------------------------------------------------------


class TestAlertShaping:
    def test_critical_alert_becomes_high_priority_unread(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[_alert(id_="a1", sev="critical", title="Rate shock")],
        )
        items = build_inbox(inputs)
        assert len(items) == 1
        item = items[0]
        assert item.source_type == "alert"
        assert item.source_id == "a1"
        assert item.key == "alert:a1"
        assert item.priority == "high"
        assert item.unread is True
        assert item.portfolio_id == "pA"
        # Phase 9Q — action_target is a structured dict produced by
        # the navigation builder, not a legacy string literal.
        assert isinstance(item.action_target, dict)
        assert item.action_target["surface"] == "alerts"
        assert item.action_target["portfolio_id"] == "pA"
        assert item.action_target["entity_type"] == "alert"
        assert item.action_target["entity_id"] == "a1"
        assert item.title == "Rate shock"

    def test_acknowledged_alert_is_read(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[_alert(id_="a1", sev="critical", acknowledged=True)],
        )
        items = build_inbox(inputs)
        assert len(items) == 1
        assert items[0].unread is False
        # Still carries the metadata for trust
        assert items[0].metadata["acknowledged"] is True

    def test_severity_priority_mapping_is_stable(self):
        # Critical and high → high; warning/medium → medium;
        # info/low → low.  Lock this in so ordering changes can't
        # sneak in via a config edit.
        assert _ALERT_SEVERITY_PRIORITY["critical"] == "high"
        assert _ALERT_SEVERITY_PRIORITY["high"] == "high"
        assert _ALERT_SEVERITY_PRIORITY["warning"] == "medium"
        assert _ALERT_SEVERITY_PRIORITY["medium"] == "medium"
        assert _ALERT_SEVERITY_PRIORITY["info"] == "low"
        assert _ALERT_SEVERITY_PRIORITY["low"] == "low"

    def test_alert_evidence_refs_pulled_from_related_fields(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[_alert(
                id_="a1", sev="high",
                related_holdings=["h_aapl"],
                related_events=["evt_fed"],
            )],
        )
        item = build_inbox(inputs)[0]
        refs = item.evidence_refs
        assert "event:evt_fed" in refs
        assert "holding:h_aapl" in refs

    def test_alert_without_id_is_silently_dropped(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[{"severity": "critical", "title": "Ghost"}],
        )
        items = build_inbox(inputs)
        assert items == []

    def test_message_field_is_accepted_as_alias_for_body(self):
        # The alerts API exposes the body as ``message`` — make sure
        # the shaper accepts either.
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[{
                "id": "a1", "severity": "high", "title": "t",
                "message": "body via message field",
                "acknowledged": False,
                "related_holdings": [],
                "related_events": [],
                "created_at": "2026-04-06T14:00:00+00:00",
            }],
        )
        item = build_inbox(inputs)[0]
        assert item.body == "body via message field"


# ---------------------------------------------------------------------------
# 2) Source shaping — digest
# ---------------------------------------------------------------------------


class TestDigestShaping:
    def test_digest_becomes_one_medium_item(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            digests=[_digest(id_="dg1", content={
                "headline": "Mildly negative on rate shock",
                "portfolio_assessment": "Two AAPL negatives",
                "risk_flags": ["a", "b"],
            })],
        )
        items = build_inbox(inputs)
        assert len(items) == 1
        d = items[0]
        assert d.key == "digest:dg1"
        assert d.source_type == "digest"
        assert d.priority == "medium"
        assert d.title == "Mildly negative on rate shock"
        assert "AAPL negatives" in d.body
        # Phase 9Q — structured target dict instead of string
        assert isinstance(d.action_target, dict)
        assert d.action_target["surface"] == "digest"
        assert d.action_target["subtab"] == "digest"
        assert d.action_target["portfolio_id"] == "pA"

    def test_digest_without_headline_falls_back_to_safe_title(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            digests=[_digest(id_="dg1", content={})],
        )
        d = build_inbox(inputs)[0]
        assert d.title  # non-empty fallback
        assert d.priority == "medium"

    def test_digest_long_body_is_truncated(self):
        body = "x" * 500
        inputs = InboxInputs(
            portfolio_id="pA",
            digests=[_digest(content={
                "headline": "h", "portfolio_assessment": body,
            })],
        )
        d = build_inbox(inputs)[0]
        assert len(d.body) <= 240
        assert d.body.endswith("…")

    def test_digest_evidence_refs_include_counts(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            digests=[_digest(content={
                "headline": "h",
                "risk_flags": ["x", "y", "z"],
            })],
        )
        refs = build_inbox(inputs)[0].evidence_refs
        assert any(r.startswith("risk_flags:") for r in refs)
        assert any(r.startswith("events:") for r in refs)

    def test_read_digest_is_marked_read(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            digests=[_digest(id_="dg1")],
            read_keys=frozenset({"digest:dg1"}),
        )
        items = build_inbox(inputs)
        assert len(items) == 1
        assert items[0].unread is False


# ---------------------------------------------------------------------------
# 3) Source shaping — operator audit entries
# ---------------------------------------------------------------------------


class TestOperatorShaping:
    def test_operator_factor_override_is_medium_priority(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            operator_entries=[_op(
                id_="o1",
                title="Updated override · AAPL / interest_rate",
                summary="AAPL · interest_rate: -0.60 → -0.90",
                entity_type="holding_factor_sensitivity",
            )],
        )
        item = build_inbox(inputs)[0]
        assert item.source_type == "operator"
        assert item.priority == "medium"
        # Phase 9Q — entity-type-aware deep link into the factor table
        assert isinstance(item.action_target, dict)
        assert item.action_target["surface"] == "operator"
        assert item.action_target["subtab"] == "factors"
        assert item.action_target["portfolio_id"] == "pA"
        assert item.metadata["entity_type"] == "holding_factor_sensitivity"

    def test_backfill_success_is_low_priority(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            operator_entries=[_op(
                id_="o2", title="Backfill complete", summary="scanned 47",
                entity_type="intelligence_backfill", failed=0,
            )],
        )
        assert build_inbox(inputs)[0].priority == "low"

    def test_backfill_with_failures_is_escalated_to_high(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            operator_entries=[_op(
                id_="o3", title="Backfill complete", summary="3 failed",
                entity_type="intelligence_backfill", failed=3,
            )],
        )
        assert build_inbox(inputs)[0].priority == "high"

    def test_reconcile_is_low_priority_by_default(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            operator_entries=[_op(
                id_="o4", title="Reconciled seeds", summary="created 2",
                entity_type="holding_relationships",
            )],
        )
        assert build_inbox(inputs)[0].priority == "low"

    def test_operator_entity_priority_mapping_is_stable(self):
        assert _OPERATOR_ENTITY_PRIORITY["holding_factor_sensitivity"] == "medium"
        assert _OPERATOR_ENTITY_PRIORITY["holding_relationship"] == "medium"
        assert _OPERATOR_ENTITY_PRIORITY["holding_relationships"] == "low"
        assert _OPERATOR_ENTITY_PRIORITY["intelligence_backfill"] == "low"


# ---------------------------------------------------------------------------
# 4) Source shaping — recommended actions
# ---------------------------------------------------------------------------


class TestActionShaping:
    def test_only_high_priority_actions_land_in_inbox(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            recommended_actions=[
                _action("alerts.critical_present", "Review critical alerts", priority="high"),
                _action("factors.broad_pressure", "Broad pressure", priority="medium"),
                _action("freshness.stale_feed", "Stale feed", priority="low"),
            ],
        )
        items = build_inbox(inputs)
        # Only the high-priority action should appear
        assert len(items) == 1
        assert items[0].source_type == "action"
        assert items[0].key == "action:alerts.critical_present"
        assert items[0].priority == "high"

    def test_action_rationale_refs_pass_through(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            recommended_actions=[_action(
                "x.y", "t", priority="high",
                refs=("alerts.critical=1", "holdings:2"),
            )],
        )
        item = build_inbox(inputs)[0]
        assert set(item.evidence_refs) == {"alerts.critical=1", "holdings:2"}


# ---------------------------------------------------------------------------
# 5) Prioritisation + ordering
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_unread_before_read(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[
                _alert(id_="read_high", sev="critical", title="Read high",
                       created="2026-04-06T12:00:00+00:00"),
                _alert(id_="unread_low", sev="info", title="Unread low",
                       created="2026-04-06T10:00:00+00:00"),
            ],
            read_keys=frozenset({"alert:read_high"}),
        )
        items = build_inbox(inputs)
        assert items[0].key == "alert:unread_low"
        assert items[1].key == "alert:read_high"

    def test_within_unread_high_beats_medium_beats_low(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[
                _alert(id_="hi", sev="critical", title="High",
                       created="2026-04-06T10:00:00+00:00"),
                _alert(id_="md", sev="warning", title="Medium",
                       created="2026-04-06T14:00:00+00:00"),
                _alert(id_="lo", sev="info", title="Low",
                       created="2026-04-06T13:00:00+00:00"),
            ],
        )
        items = build_inbox(inputs)
        assert [i.priority for i in items] == ["high", "medium", "low"]

    def test_within_same_priority_newest_first(self):
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[
                _alert(id_="old", sev="high", title="Old",
                       created="2026-04-06T10:00:00+00:00"),
                _alert(id_="new", sev="high", title="New",
                       created="2026-04-06T14:00:00+00:00"),
                _alert(id_="mid", sev="high", title="Mid",
                       created="2026-04-06T12:00:00+00:00"),
            ],
        )
        items = build_inbox(inputs)
        assert [i.source_id for i in items] == ["new", "mid", "old"]


# ---------------------------------------------------------------------------
# 6) Dedupe + cap
# ---------------------------------------------------------------------------


class TestDedupeCap:
    def test_dedupe_keeps_first_occurrence(self):
        # Fabricate two alerts with the same id (shouldn't happen in
        # practice but the builder must be defensive).
        inputs = InboxInputs(
            portfolio_id="pA",
            alerts=[
                _alert(id_="dup", sev="critical", title="First",
                       created="2026-04-06T14:00:00+00:00"),
                _alert(id_="dup", sev="high", title="Second",
                       created="2026-04-06T13:00:00+00:00"),
            ],
        )
        items = build_inbox(inputs)
        assert len(items) == 1

    def test_cap_is_enforced(self):
        alerts = [
            _alert(id_=f"a{i}", sev="info", title=f"t{i}",
                   created=f"2026-04-06T10:{i:02d}:00+00:00")
            for i in range(MAX_INBOX_ITEMS + 10)
        ]
        items = build_inbox(InboxInputs(portfolio_id="pA", alerts=alerts))
        assert len(items) == MAX_INBOX_ITEMS

    def test_empty_inputs_yield_empty_list(self):
        assert build_inbox(InboxInputs(portfolio_id="pA")) == []


# ---------------------------------------------------------------------------
# 7) summarise_inbox
# ---------------------------------------------------------------------------


class TestSummarise:
    def test_summary_counts_match(self):
        items = [
            NotificationItem(key="alert:a1", source_type="alert", source_id="a1",
                             portfolio_id="pA", priority="high", title="t", body="",
                             timestamp="2026-04-06T14:00:00+00:00", unread=True),
            NotificationItem(key="alert:a2", source_type="alert", source_id="a2",
                             portfolio_id="pA", priority="low", title="t", body="",
                             timestamp="2026-04-06T13:00:00+00:00", unread=False),
            NotificationItem(key="digest:d1", source_type="digest", source_id="d1",
                             portfolio_id="pA", priority="medium", title="t", body="",
                             timestamp="2026-04-06T12:00:00+00:00", unread=True),
        ]
        s = summarise_inbox(items)
        assert s["total"] == 3
        assert s["unread"] == 2
        assert s["by_source"] == {"alert": 2, "digest": 1}
        assert s["by_priority"] == {"high": 1, "low": 1, "medium": 1}

    def test_empty_summary(self):
        s = summarise_inbox([])
        assert s["total"] == 0
        assert s["unread"] == 0
        assert s["by_source"] == {}


# ---------------------------------------------------------------------------
# 8) NotificationItem.to_dict
# ---------------------------------------------------------------------------


class TestItemSerialisation:
    def test_to_dict_is_json_safe(self):
        import json
        # Phase 9Q — action_target must be a dict (the navigation
        # builders produce dicts; the dataclass stores it as a
        # Mapping and to_dict() copies it shallowly).
        item = NotificationItem(
            key="alert:a1", source_type="alert", source_id="a1",
            portfolio_id="pA", priority="high", title="t", body="b",
            timestamp="2026-04-06T14:00:00+00:00", unread=True,
            evidence_refs=("factor:interest_rate",),
            action_label="Open",
            action_target={
                "surface": "alerts", "portfolio_id": "pA",
                "entity_type": "alert", "entity_id": "a1",
                "subtab": None, "filter": None, "open_modal": False,
                "highlight_key": "alert:a1", "label": "Open",
            },
            metadata={"severity": "critical"},
        )
        d = item.to_dict()
        payload = json.dumps(d)
        assert "factor:interest_rate" in payload
        assert d["evidence_refs"] == ["factor:interest_rate"]
        assert d["action_target"]["surface"] == "alerts"
        assert d["action_target"]["entity_id"] == "a1"
        assert d["metadata"]["severity"] == "critical"


# ---------------------------------------------------------------------------
# 9) within_window utility
# ---------------------------------------------------------------------------


class TestWithinWindow:
    def test_recent_timestamp_is_in_window(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(hours=1)).isoformat()
        assert within_window(ts, hours=24) is True

    def test_old_timestamp_is_out_of_window(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(hours=100)).isoformat()
        assert within_window(ts, hours=24) is False

    def test_blank_or_invalid_returns_false(self):
        assert within_window(None, hours=24) is False
        assert within_window("", hours=24) is False
        assert within_window("not a date", hours=24) is False
