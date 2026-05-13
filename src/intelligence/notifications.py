"""Phase 9P — Notification inbox helpers.

This module is the single source of truth for how Axion composes an
in-product inbox from existing deterministic rows.  It is a pure
functional library: callers pass trusted snapshots, the module
returns a sorted, deduplicated list of :class:`NotificationItem`
objects ready for JSON rendering.

Responsibilities
----------------
1. **Normalise source rows into a common contract.**  Alerts,
   digests, operator audit rows, and Phase 9N recommended actions
   all become :class:`NotificationItem` instances with the same
   minimal shape (title, body, priority, timestamp, unread, refs,
   jump target).
2. **Apply priority + ordering rules.**  The final sort is a
   stable three-key order: unread first, then priority, then newest
   first.  Source-level priority mapping is a frozen module
   constant so tests and the route share the exact same rules.
3. **Dedupe per notification key.**  The ``notification_key`` is a
   stable string that uniquely identifies a source row (e.g.
   ``alert:abc123``, ``digest:def456``, ``operator:a1b2``,
   ``action:factors.strong_rate_pressure``).  If the same key
   appears twice (unlikely but possible when two sources converge)
   we keep the first occurrence after priority ordering.
4. **Cap the result.**  Hard limit of ``MAX_INBOX_ITEMS`` to avoid
   runaway inbox growth.  Operators can always go to the underlying
   surface for the full list.

Design rules (copied from the Phase 9P brief)
---------------------------------------------
* no new scoring math
* no LLM dependency
* no new source types beyond the deterministic ones that already exist
* no portfolio leakage — every item carries ``portfolio_id`` and the
  caller passes already-filtered rows
* no synthesis — every field is copied from existing trusted data
* JSON-safe, compact, directly renderable
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Mapping, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Hard cap on the number of items returned from a single inbox call.
#: Chosen so the UI can render the whole list without pagination.
MAX_INBOX_ITEMS: int = 25

#: How far back we look for operator audit rows.  Older rows are
#: still reachable via Settings → Operator → Recent operator actions.
OPERATOR_WINDOW_HOURS: int = 72

#: How far back we look for events-as-notifications.  Events are
#: noisy so the window is deliberately short.
EVENT_WINDOW_HOURS: int = 24

#: Priority tiers used by the ordering rule.
Priority = Literal["high", "medium", "low"]

_PRIORITY_RANK: Mapping[str, int] = {"high": 0, "medium": 1, "low": 2}


#: Source-level priority mapping.  Documented in the Phase 9P
#: deliverable — this is the *only* place where the mapping from
#: severity/source kind to inbox priority lives.
_ALERT_SEVERITY_PRIORITY: Mapping[str, Priority] = {
    "critical": "high",
    "high":     "high",
    "warning":  "medium",
    "medium":   "medium",
    "info":     "low",
    "low":      "low",
}

_OPERATOR_ENTITY_PRIORITY: Mapping[str, Priority] = {
    # Mutations that change what the deterministic engine sees
    "holding_factor_sensitivity": "medium",
    "holding_relationship":       "medium",
    # Global maintenance — lower signal unless it failed
    "holding_relationships":      "low",   # reconcile
    "intelligence_backfill":      "low",   # backfill
}

_DIGEST_PRIORITY: Priority = "medium"

#: Phase 9N action priority → inbox priority.  Straight pass-through
#: — Phase 9N already uses the same three-tier scale.
_ACTION_PRIORITY_PASSTHROUGH: Mapping[str, Priority] = {
    "high":   "high",
    "medium": "medium",
    "low":    "low",
}


# ---------------------------------------------------------------------------
# NotificationItem — the shared UX contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NotificationItem:
    """A single inbox item, ready for JSON serialization.

    Every field is derived from an existing trusted row — nothing
    here is synthesised or scored.  The dataclass is frozen so
    callers can't mutate the list shapes by accident.

    Phase 9Q — ``action_target`` is a structured dict built by
    :mod:`src.intelligence.navigation`.  The legacy string form
    (``"alerts"``, ``"digest"``, ...) is no longer emitted; the
    frontend's ``jumpToTarget`` dispatcher is the only consumer.
    """

    key: str                        # stable, unique per portfolio
    source_type: str                # "alert", "digest", "operator", "action", "event"
    source_id: str                  # id of the underlying row
    portfolio_id: str
    priority: Priority
    title: str
    body: str
    timestamp: str                  # ISO-8601
    unread: bool
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    action_label: str | None = None
    #: Phase 9Q structured navigation target (dict) — produced via
    #: :mod:`src.intelligence.navigation` builders.  ``None`` when
    #: the source row has no navigable destination.
    action_target: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "portfolio_id": self.portfolio_id,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            "timestamp": self.timestamp,
            "unread": self.unread,
            "evidence_refs": list(self.evidence_refs),
            "action_label": self.action_label,
            "action_target": dict(self.action_target) if self.action_target is not None else None,
            "metadata": dict(self.metadata) if self.metadata else {},
        }


# ---------------------------------------------------------------------------
# InboxInputs — caller-provided snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboxInputs:
    """Everything the inbox builder needs to compose a portfolio's
    notification list.

    All collections are expected to be already portfolio-filtered by
    the caller.  The builder never queries the DB directly — that
    keeps it pure, trivially testable, and impossible to leak rows
    across portfolios by mistake.
    """

    portfolio_id: str
    alerts: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    digests: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    operator_entries: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    recommended_actions: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    #: set of notification_key strings known to be read for this portfolio
    read_keys: frozenset[str] = field(default_factory=frozenset)
    now_iso: str | None = None


# ---------------------------------------------------------------------------
# Private helpers — source-specific shaping
# ---------------------------------------------------------------------------


def _safe_list(raw: Any) -> list[str]:
    """Parse a JSON-list-of-strings or accept a Python list as-is."""
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    if isinstance(raw, str):
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _map_alert_priority(severity: str | None) -> Priority:
    if not severity:
        return "low"
    return _ALERT_SEVERITY_PRIORITY.get(severity.lower(), "low")


def _map_operator_priority(entry: Mapping[str, Any]) -> Priority:
    entity_type = entry.get("entity_type") or ""
    base = _OPERATOR_ENTITY_PRIORITY.get(entity_type, "low")
    # Escalate if the row represents a FAILED backfill — Phase 9O
    # shaper puts failed counts in ``new_highlights``.
    highlights = entry.get("new_highlights") or {}
    if entity_type == "intelligence_backfill":
        try:
            if int(highlights.get("events_failed") or 0) > 0:
                return "high"
        except (TypeError, ValueError):
            pass
    return base  # type: ignore[return-value]


def _shape_alert(
    alert: Mapping[str, Any], read_keys: frozenset[str], portfolio_id: str,
) -> NotificationItem | None:
    alert_id = alert.get("id")
    if not alert_id:
        return None
    # Alerts have a native ``acknowledged`` flag — an acknowledged
    # alert is considered read at the inbox level unless the operator
    # explicitly hasn't marked it read via the notification route.
    # We treat ``acknowledged`` as a strong "read" signal but the
    # notification_reads table wins over it if the operator toggled
    # read state here.
    key = f"alert:{alert_id}"
    severity = str(alert.get("severity") or "info").lower()
    priority = _map_alert_priority(severity)
    title = str(alert.get("title") or "Alert")
    # The Phase 9N alerts route exposes the body as ``message``;
    # the raw ORM row uses ``body``.  Accept either.
    body = str(alert.get("message") or alert.get("body") or "")
    ack = bool(alert.get("acknowledged", False))
    unread = (key not in read_keys) and not ack

    # Grounded evidence refs from the alert's own fields.
    refs: list[str] = []
    for ev_id in _safe_list(alert.get("related_events"))[:2]:
        refs.append(f"event:{ev_id}")
    for h in _safe_list(alert.get("related_holdings"))[:2]:
        refs.append(f"holding:{h}")

    # Phase 9Q — build a structured deep link target so the inbox
    # jump button lands on the exact alert card, not just the Alerts
    # tab.  Falls back to a tab-level target if the alert has no id.
    from src.intelligence.navigation import target_for_alert
    nav = target_for_alert(str(alert_id), portfolio_id, label="Open alert")
    return NotificationItem(
        key=key,
        source_type="alert",
        source_id=str(alert_id),
        portfolio_id=portfolio_id,
        priority=priority,
        title=title,
        body=body,
        timestamp=str(alert.get("created_at") or ""),
        unread=unread,
        evidence_refs=tuple(refs),
        action_label="Open alert",
        action_target=nav.to_dict() if nav is not None else None,
        metadata={
            "severity": severity,
            "acknowledged": ack,
            "alert_type": str(alert.get("alert_type") or ""),
        },
    )


def _shape_digest(
    digest: Mapping[str, Any], read_keys: frozenset[str], portfolio_id: str,
) -> NotificationItem | None:
    d_id = digest.get("id")
    if not d_id:
        return None
    key = f"digest:{d_id}"
    # Pull the headline out of the grounded content if present.
    content = digest.get("content")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = None
    elif isinstance(content, Mapping):
        parsed = content
    else:
        parsed = None

    headline = None
    assessment = None
    if isinstance(parsed, Mapping):
        headline = parsed.get("headline")
        assessment = parsed.get("portfolio_assessment")

    title = str(headline or digest.get("title") or "New intelligence digest")
    body = str(assessment or "Tap to read the latest portfolio digest.")
    # Trim body so the card stays compact
    if len(body) > 240:
        body = body[:237] + "…"

    refs: list[str] = []
    if isinstance(parsed, Mapping):
        risk_flags = parsed.get("risk_flags")
        if isinstance(risk_flags, list) and risk_flags:
            refs.append(f"risk_flags:{len(risk_flags)}")
    # Count pieces from the digest row itself
    evt = digest.get("event_count")
    if evt:
        refs.append(f"events:{evt}")
    alt = digest.get("alert_count")
    if alt:
        refs.append(f"alerts:{alt}")

    # Phase 9Q — structured deep link to Intelligence → Digest sub-tab
    from src.intelligence.navigation import target_for_digest
    nav = target_for_digest(portfolio_id, label="Read digest")
    return NotificationItem(
        key=key,
        source_type="digest",
        source_id=str(d_id),
        portfolio_id=portfolio_id,
        priority=_DIGEST_PRIORITY,
        title=title,
        body=body,
        timestamp=str(digest.get("created_at") or ""),
        unread=(key not in read_keys),
        evidence_refs=tuple(refs),
        action_label="Read digest",
        action_target=nav.to_dict() if nav is not None else None,
        metadata={"digest_type": str(digest.get("digest_type") or "daily")},
    )


def _shape_operator(
    entry: Mapping[str, Any], read_keys: frozenset[str], portfolio_id: str,
) -> NotificationItem | None:
    """Shape a Phase 9O ``TraceabilityEntry``-style dict into a
    notification item.  The entry is expected to be the dict form
    produced by :func:`src.intelligence.traceability.shape_audit_entry`
    (or an API response of the same shape)."""
    e_id = entry.get("id")
    if not e_id:
        return None
    key = f"operator:{e_id}"
    title = str(entry.get("title") or "Operator action")
    body = str(entry.get("summary") or "")
    priority = _map_operator_priority(entry)
    refs = tuple(entry.get("evidence_refs") or ())
    # Phase 9Q — entity-type-aware deep link (factors vs relationships
    # vs maintenance section).
    from src.intelligence.navigation import target_for_operator_entry
    nav = target_for_operator_entry(entry, portfolio_id, label="Open in Operator")
    return NotificationItem(
        key=key,
        source_type="operator",
        source_id=str(e_id),
        portfolio_id=portfolio_id,
        priority=priority,
        title=title,
        body=body,
        timestamp=str(entry.get("timestamp") or ""),
        unread=(key not in read_keys),
        evidence_refs=tuple(str(r) for r in refs),
        action_label="Open Operator",
        action_target=nav.to_dict() if nav is not None else None,
        metadata={
            "entity_type": str(entry.get("entity_type") or ""),
            "actor": str(entry.get("actor") or ""),
        },
    )


def _shape_action(
    action: Mapping[str, Any], read_keys: frozenset[str], portfolio_id: str, now_iso: str,
) -> NotificationItem | None:
    """Shape a Phase 9N ``RecommendedAction`` dict into a notification
    item.  Only *high* priority actions become inbox notifications by
    default — lower priority actions stay on the overview card to
    avoid doubling the noise."""
    a_key = action.get("key")
    if not a_key:
        return None
    # Only elevate high-priority actions to the inbox
    a_priority = str(action.get("priority") or "low").lower()
    if a_priority != "high":
        return None
    key = f"action:{a_key}"
    title = str(action.get("title") or "Recommended action")
    body = str(action.get("description") or "")
    refs = tuple(str(r) for r in (action.get("rationale_refs") or []))
    # Phase 9Q — route by action key family (alerts.*, factors.*,
    # holdings.*, relationships.*, etc.).  Falls back to the
    # overview/portfolio tab when the family has no exact target.
    from src.intelligence.navigation import target_for_action, _safe_target
    nav = target_for_action(action, portfolio_id)
    if nav is None:
        nav = _safe_target(
            surface="portfolio",
            portfolio_id=portfolio_id,
            label="View in overview",
        )
    return NotificationItem(
        key=key,
        source_type="action",
        source_id=str(a_key),
        portfolio_id=portfolio_id,
        priority=_ACTION_PRIORITY_PASSTHROUGH.get(a_priority, "low"),
        title=title,
        body=body,
        timestamp=now_iso,
        unread=(key not in read_keys),
        evidence_refs=refs,
        action_label=(nav.label if nav is not None else "View in overview"),
        action_target=nav.to_dict() if nav is not None else None,
        metadata={
            "related_tickers": list(action.get("related_tickers") or []),
        },
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_inbox(inputs: InboxInputs) -> list[NotificationItem]:
    """Compose an ordered list of notification items from trusted rows.

    Pipeline:

    1. Fan-out — each source produces zero or more ``NotificationItem``
       via a private shaper that tolerates missing fields.
    2. Dedupe — items with identical ``key`` collapse to the first
       occurrence (shouldn't happen in practice because the key
       prefixes are source-specific, but we enforce it defensively).
    3. Sort — stable three-key sort:
         (a) unread first (unread=True before unread=False)
         (b) priority rank (high→medium→low)
         (c) newest first by ISO timestamp
    4. Cap at ``MAX_INBOX_ITEMS``.

    The caller is expected to pass **already-filtered** collections
    for the target portfolio.  ``inputs.portfolio_id`` is used for
    per-item tagging and as the fallback when a source row doesn't
    carry its own portfolio_id.
    """
    portfolio_id = inputs.portfolio_id
    read_keys = inputs.read_keys or frozenset()
    now_iso = inputs.now_iso or datetime.now(timezone.utc).isoformat()

    items: list[NotificationItem] = []

    # --- Alerts ------------------------------------------------------
    for a in inputs.alerts or ():
        item = _shape_alert(a, read_keys, portfolio_id)
        if item is not None:
            items.append(item)

    # --- Digests -----------------------------------------------------
    for d in inputs.digests or ():
        item = _shape_digest(d, read_keys, portfolio_id)
        if item is not None:
            items.append(item)

    # --- Operator audit rows (pre-shaped by Phase 9O) ----------------
    for e in inputs.operator_entries or ():
        item = _shape_operator(e, read_keys, portfolio_id)
        if item is not None:
            items.append(item)

    # --- Recommended actions (Phase 9N) ------------------------------
    for act in inputs.recommended_actions or ():
        item = _shape_action(act, read_keys, portfolio_id, now_iso)
        if item is not None:
            items.append(item)

    # --- Dedupe by key (stable: keep first occurrence) ---------------
    seen: set[str] = set()
    unique: list[NotificationItem] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        unique.append(item)

    # --- Sort: unread first, then priority, then newest first -------
    def _sort_key(i: NotificationItem) -> tuple[int, int, str]:
        return (
            0 if i.unread else 1,
            _PRIORITY_RANK.get(i.priority, 3),
            # Reverse newest-first: ISO strings sort lex; we negate by
            # using a descending key — Python tuple sort is stable and
            # ascending, so we invert the timestamp with a trick: take
            # the negative ordinal of each char?  Simpler: sort list
            # then reverse at the end.  But we need three keys so we
            # use a wrapper that inverts the timestamp to sort desc.
            # We'll subtract from a constant to invert sort order:
            # but since we want string sort, we use a reversed view.
            # Simpler still — Python supports tuple sort with mixed
            # asc/desc via a two-pass sort below.  Here we just put
            # the raw string; the final reverse sort handles the
            # newest-first part.
            i.timestamp or "",
        )

    # Two-pass stable sort gives us a clean asc/asc/desc result:
    # first by timestamp desc (last key), then by the primary keys.
    items_sorted = sorted(unique, key=lambda i: i.timestamp or "", reverse=True)
    items_sorted.sort(key=lambda i: (
        0 if i.unread else 1,
        _PRIORITY_RANK.get(i.priority, 3),
    ))

    # --- Cap ---------------------------------------------------------
    return items_sorted[:MAX_INBOX_ITEMS]


def summarise_inbox(items: Sequence[NotificationItem]) -> dict[str, Any]:
    """Produce the small summary header the inbox UI renders at the
    top of the panel (unread count, total, per-source breakdown)."""
    total = len(items)
    unread = sum(1 for i in items if i.unread)
    by_source: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for i in items:
        by_source[i.source_type] = by_source.get(i.source_type, 0) + 1
        by_priority[i.priority] = by_priority.get(i.priority, 0) + 1
    return {
        "total": total,
        "unread": unread,
        "by_source": by_source,
        "by_priority": by_priority,
    }


def within_window(
    iso_timestamp: str | None, *, hours: int, now_iso: str | None = None,
) -> bool:
    """Utility for callers that want to filter source rows to a time
    window before feeding them to :func:`build_inbox`.

    Returns True when ``iso_timestamp`` is within the last ``hours``
    hours from ``now_iso`` (or now).  Unparseable timestamps return
    False — the caller can then decide whether to include them.
    """
    if not iso_timestamp:
        return False
    try:
        t = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    now = (
        datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now_iso
        else datetime.now(timezone.utc)
    )
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=max(0, hours))
    return t >= cutoff
