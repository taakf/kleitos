"""Phase 9O — shared traceability helpers.

This module is the single source of truth for how audit rows and
evidence refs are shaped for user-facing surfaces.  It is pure
deterministic Python with no DB access, no LLM calls, and no new
math — it only *reshapes* data that already exists.

Responsibilities
----------------
1. **Shape an ``AuditLog`` row into a ``TraceabilityEntry``.**
   Every operator mutation, reconcile pass, and backfill run is
   already written to ``audit_log`` by the Phase 9H/9I/9K writers.
   This module turns those raw rows into a compact, UI-friendly
   payload with a title, a summary line, and optional highlights.

2. **Prioritise recent entries for the operator surface.**
   ``select_recent_operator_entries`` enforces a finite, documented
   ranking rule so the operator sees the most relevant rows at the
   top without having to open the full audit tab.

3. **Group Phase 9N evidence refs by category.**
   ``group_evidence_refs`` categorises the short ref strings
   produced by ``intelligence.actions`` (``alert:``, ``factor:``,
   ``holding:``, ``ticker:``, ``rel:``, ``note:``) so a frontend
   renderer can show a grouped "Grounded in" block instead of a
   flat wall of chips.

Design rules (copied from the Phase 9O brief)
---------------------------------------------
* no new DB tables
* reuse existing ``AuditLog`` rows
* reuse existing ``rationale_refs`` / ``explanation_grounded_in``
* no giant provenance graph
* stable, JSON-safe, minimal shape
* portfolio-safe: the caller decides what to filter
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity type registry
# ---------------------------------------------------------------------------

#: The set of ``audit_log.entity_type`` values emitted by operator-facing
#: writers.  Used by the recent-actions surface to filter the global
#: audit log down to operator mutations + maintenance runs only.
_OPERATOR_ENTITY_TYPES: frozenset[str] = frozenset({
    "holding_factor_sensitivity",   # Phase 9H factor override CRUD
    "holding_relationship",         # Phase 9H manual relationship CRUD
    "holding_relationships",        # Phase 9H reconcile (plural = aggregate)
    "intelligence_backfill",        # Phase 9H deterministic backfill
})

#: Human labels used for the entity_type chip on the UI card.
_ENTITY_TYPE_LABELS: dict[str, str] = {
    "holding_factor_sensitivity": "factor override",
    "holding_relationship":       "relationship",
    "holding_relationships":      "reconcile",
    "intelligence_backfill":      "backfill",
}


def is_operator_entity_type(entity_type: str | None) -> bool:
    """Return True if the given entity_type is an operator-owned row.

    Used both by the shaping helper and by the route's query filter
    so there is exactly one definition of what "operator action" means.
    """
    return bool(entity_type) and entity_type in _OPERATOR_ENTITY_TYPES


def entity_type_label(entity_type: str | None) -> str:
    """Return the human label for an entity_type, falling back to the
    raw value when it's not in the registry."""
    if not entity_type:
        return "unknown"
    return _ENTITY_TYPE_LABELS.get(entity_type, entity_type)


# ---------------------------------------------------------------------------
# TraceabilityEntry — the shared UX contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceabilityEntry:
    """A single user-facing audit readback.

    Deliberately minimal — the UI only needs a title, a timestamp, a
    short summary line, and an optional list of highlights.  Larger
    context is reachable via the full ``/api/v1/audit`` route.

    All fields are JSON-safe and the dataclass is frozen so callers
    can't mutate the list shapes by accident.
    """

    id: str
    title: str
    timestamp: str                     # ISO-8601 string
    actor: str                         # agent_id / user_id / "operator"
    entity_type: str                   # one of the registry keys
    entity_id: str                     # audit_log.entity_id
    action: str                        # audit_log.action
    summary: str                       # short one-line summary
    old_highlights: dict[str, Any] | None = None
    new_highlights: dict[str, Any] | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None
    portfolio_id: str | None = None    # resolved by the caller if known

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "entity_type": self.entity_type,
            "entity_type_label": entity_type_label(self.entity_type),
            "entity_id": self.entity_id,
            "action": self.action,
            "summary": self.summary,
            "old_highlights": self.old_highlights,
            "new_highlights": self.new_highlights,
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "portfolio_id": self.portfolio_id,
        }


# ---------------------------------------------------------------------------
# Audit row shaping — one dispatcher + four private shapers
# ---------------------------------------------------------------------------


def _safe_json(raw: str | None) -> Any:
    """Decode a JSON string field, returning None on blank or error.

    Mirrors the helper in ``src/api/routes/audit.py`` but returns
    ``None`` on failure instead of the raw string — the UI contract
    here is strict about shapes.
    """
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _fmt_num(value: Any, digits: int = 2) -> str:
    """Format a numeric value for a summary line."""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _shape_factor_sensitivity(
    *, row: Any, old_value: Any, new_value: Any,
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, tuple[str, ...]]:
    """Shape a ``holding_factor_sensitivity`` audit row.

    Returns (title, summary, old_highlights, new_highlights, evidence_refs).
    Both old/new values are expected to be dicts with at least
    ``sensitivity``, ``holding_id`` and ``factor`` keys (matches the
    shape written by :func:`src.api.routes.operator._audit`).
    """
    action = (row.action or "").lower()
    old = old_value if isinstance(old_value, dict) else None
    new = new_value if isinstance(new_value, dict) else None
    source = new or old or {}

    ticker = source.get("ticker") or source.get("holding_id") or "holding"
    factor = source.get("factor") or "factor"
    holding_id = source.get("holding_id")

    refs: list[str] = []
    if holding_id:
        refs.append(f"holding:{holding_id}")
    if factor != "factor":
        refs.append(f"factor:{factor}")

    if action == "delete":
        title = f"Deleted override · {ticker} / {factor}"
        old_sens = old.get("sensitivity") if old else None
        summary = (
            f"{ticker} · {factor} override removed"
            + (f" (was {_fmt_num(old_sens)})" if old_sens is not None else "")
        )
        old_hl = {"sensitivity": old_sens} if old_sens is not None else None
        return title, summary, old_hl, None, tuple(refs)

    # create / upsert / update — we have a new value
    new_sens = new.get("sensitivity") if new else None
    old_sens = old.get("sensitivity") if old else None

    if action in ("upsert", "create") or old is None:
        title = f"Created override · {ticker} / {factor}"
        summary = f"{ticker} · {factor} → {_fmt_num(new_sens)}" if new_sens is not None else f"{ticker} · {factor} override saved"
    else:
        title = f"Updated override · {ticker} / {factor}"
        if new_sens is not None and old_sens is not None:
            summary = f"{ticker} · {factor}: {_fmt_num(old_sens)} → {_fmt_num(new_sens)}"
        elif new_sens is not None:
            summary = f"{ticker} · {factor} → {_fmt_num(new_sens)}"
        else:
            summary = f"{ticker} · {factor} override updated"

    old_hl = {"sensitivity": old_sens} if old_sens is not None else None
    new_hl = {"sensitivity": new_sens} if new_sens is not None else None
    return title, summary, old_hl, new_hl, tuple(refs)


def _shape_relationship(
    *, row: Any, old_value: Any, new_value: Any,
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, tuple[str, ...]]:
    """Shape a ``holding_relationship`` audit row (singular — a specific
    manual relationship create/update/delete)."""
    action = (row.action or "").lower()
    old = old_value if isinstance(old_value, dict) else None
    new = new_value if isinstance(new_value, dict) else None
    source = new or old or {}

    rel_type = source.get("relationship_type") or "related"
    related_ticker = (
        source.get("related_ticker")
        or source.get("related_entity_key")
        or source.get("related_name")
        or "related"
    )
    holding_id = source.get("holding_id")
    strength_new = new.get("strength") if new else None
    strength_old = old.get("strength") if old else None

    refs: list[str] = [f"rel:{rel_type}"]
    if holding_id:
        refs.append(f"holding:{holding_id}")
    if related_ticker and related_ticker != "related":
        refs.append(f"related:{related_ticker}")

    if action == "delete":
        title = f"Deleted relationship · {rel_type} → {related_ticker}"
        summary = (
            f"{rel_type} → {related_ticker}"
            + (f" (strength {_fmt_num(strength_old)})" if strength_old is not None else "")
        )
        return title, summary, {"strength": strength_old} if strength_old is not None else None, None, tuple(refs)

    if action == "create" or old is None:
        title = f"Created relationship · {rel_type} → {related_ticker}"
        if strength_new is not None:
            summary = f"{rel_type} → {related_ticker} (strength {_fmt_num(strength_new)})"
        else:
            summary = f"{rel_type} → {related_ticker}"
        return title, summary, None, {"strength": strength_new} if strength_new is not None else None, tuple(refs)

    # update
    title = f"Updated relationship · {rel_type} → {related_ticker}"
    if strength_new is not None and strength_old is not None:
        summary = f"{rel_type} → {related_ticker}: strength {_fmt_num(strength_old)} → {_fmt_num(strength_new)}"
    elif strength_new is not None:
        summary = f"{rel_type} → {related_ticker} (strength {_fmt_num(strength_new)})"
    else:
        summary = f"{rel_type} → {related_ticker} edited"
    return (
        title, summary,
        {"strength": strength_old} if strength_old is not None else None,
        {"strength": strength_new} if strength_new is not None else None,
        tuple(refs),
    )


def _shape_reconcile(
    *, row: Any, old_value: Any, new_value: Any,
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, tuple[str, ...]]:
    """Shape a ``holding_relationships`` reconcile audit row.

    The ``new_value`` payload is the stats dict returned by
    :func:`src.intelligence.relationship_seed_reconcile.run_relationship_seed_reconcile`.
    """
    stats = new_value if isinstance(new_value, dict) else {}
    created = int(stats.get("created") or 0)
    updated = int(stats.get("updated") or 0)
    unchanged = int(stats.get("unchanged") or 0)
    pruned = int(stats.get("pruned") or 0)
    skipped = int(stats.get("skipped_no_holding") or 0)

    total_changed = created + updated + pruned
    if total_changed == 0:
        title = "Reconciled relationship seeds (no-op)"
        summary = f"no changes · {unchanged} rows unchanged"
    else:
        title = "Reconciled relationship seeds"
        bits = []
        if created:
            bits.append(f"created {created}")
        if updated:
            bits.append(f"updated {updated}")
        if pruned:
            bits.append(f"pruned {pruned}")
        summary = ", ".join(bits) + (f" · {unchanged} unchanged" if unchanged else "")
        if skipped:
            summary += f" · {skipped} skipped"

    refs: tuple[str, ...] = ()
    new_hl = {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "pruned": pruned,
    }
    return title, summary, None, new_hl, refs


def _shape_backfill(
    *, row: Any, old_value: Any, new_value: Any,
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, tuple[str, ...]]:
    """Shape an ``intelligence_backfill`` audit row.

    The ``new_value`` payload is the stats dict returned by
    :func:`src.intelligence.backfill.BackfillStats.as_dict`.
    """
    stats = new_value if isinstance(new_value, dict) else {}
    window_days = stats.get("window_days")
    scanned = int(stats.get("events_scanned") or 0)
    replayed = int(stats.get("events_replayed") or 0)
    links_added = int(stats.get("links_added") or 0)
    mfe_added = int(stats.get("mfe_added") or 0)
    failed = int(stats.get("events_failed") or 0)

    window_bit = f"{window_days}d window" if window_days else "window"
    title = f"Backfill complete · {window_bit}"
    bits = [f"scanned {scanned}", f"replayed {replayed}"]
    if links_added:
        bits.append(f"+{links_added} links")
    if mfe_added:
        bits.append(f"+{mfe_added} factor rows")
    if failed:
        bits.append(f"{failed} failed")
    summary = " · ".join(bits)

    new_hl = {
        "events_scanned": scanned,
        "events_replayed": replayed,
        "links_added": links_added,
        "mfe_added": mfe_added,
        "events_failed": failed,
    }
    return title, summary, None, new_hl, ()


def shape_audit_entry(row: Any) -> TraceabilityEntry | None:
    """Shape a single ``AuditLog`` ORM row into a ``TraceabilityEntry``.

    Returns ``None`` for entity types outside the operator registry —
    the caller is expected to filter those out, but we double-check
    here so a stray row can never leak into the recent-actions card.

    The ``row`` parameter is typed as ``Any`` to avoid pulling a hard
    dependency on the ORM model — the function only reads attributes
    by name, so any object with matching attributes works (including
    simple test fixtures).
    """
    entity_type = getattr(row, "entity_type", None)
    if not is_operator_entity_type(entity_type):
        return None

    old_value = _safe_json(getattr(row, "old_value", None))
    new_value = _safe_json(getattr(row, "new_value", None))

    try:
        if entity_type == "holding_factor_sensitivity":
            title, summary, old_hl, new_hl, refs = _shape_factor_sensitivity(
                row=row, old_value=old_value, new_value=new_value,
            )
        elif entity_type == "holding_relationship":
            title, summary, old_hl, new_hl, refs = _shape_relationship(
                row=row, old_value=old_value, new_value=new_value,
            )
        elif entity_type == "holding_relationships":
            title, summary, old_hl, new_hl, refs = _shape_reconcile(
                row=row, old_value=old_value, new_value=new_value,
            )
        elif entity_type == "intelligence_backfill":
            title, summary, old_hl, new_hl, refs = _shape_backfill(
                row=row, old_value=old_value, new_value=new_value,
            )
        else:  # pragma: no cover — guarded by is_operator_entity_type
            return None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("traceability: failed to shape audit row %s: %s", getattr(row, "id", "?"), exc)
        return None

    return TraceabilityEntry(
        id=getattr(row, "id", "") or "",
        title=title,
        timestamp=getattr(row, "created_at", "") or "",
        actor=getattr(row, "agent_id", None) or getattr(row, "user_id", None) or "operator",
        entity_type=entity_type,
        entity_id=getattr(row, "entity_id", "") or "",
        action=getattr(row, "action", "") or "",
        summary=summary,
        old_highlights=old_hl,
        new_highlights=new_hl,
        evidence_refs=refs,
        reason=getattr(row, "reason", None),
    )


# ---------------------------------------------------------------------------
# Recent-entries prioritisation / dedupe rules
# ---------------------------------------------------------------------------


def select_recent_operator_entries(
    rows: Iterable[Any],
    *,
    limit: int = 10,
) -> list[TraceabilityEntry]:
    """Project ``AuditLog`` rows into ``TraceabilityEntry`` and apply
    the documented Phase 9O prioritisation rules:

    1. **Filter**: keep only operator-owned entity types
       (``_OPERATOR_ENTITY_TYPES``).
    2. **Sort**: newest first (callers are expected to pass rows
       already sorted this way, but we resort defensively).
    3. **Dedupe no-op reconciles**: if two or more consecutive
       ``holding_relationships`` reconcile entries all report zero
       changes, collapse them into a single row.  This keeps the
       recent-actions card useful when an operator hammers the
       reconcile button with no real effect.
    4. **Cap**: take the first ``limit`` rows (default 10, floor 1).
    """
    out: list[TraceabilityEntry] = []
    for row in rows:
        entry = shape_audit_entry(row)
        if entry is not None:
            out.append(entry)

    # Defensive sort — newest first on the ISO timestamp string works
    # because ISO-8601 is lexicographically ordered.
    out.sort(key=lambda e: e.timestamp or "", reverse=True)

    deduped: list[TraceabilityEntry] = []
    prev_was_noop_reconcile = False
    for entry in out:
        is_noop_reconcile = (
            entry.entity_type == "holding_relationships"
            and entry.action == "reconcile"
            and (entry.new_highlights or {}).get("created", 0) == 0
            and (entry.new_highlights or {}).get("updated", 0) == 0
            and (entry.new_highlights or {}).get("pruned", 0) == 0
        )
        if is_noop_reconcile and prev_was_noop_reconcile:
            continue  # collapse
        deduped.append(entry)
        prev_was_noop_reconcile = is_noop_reconcile

    lim = max(1, int(limit))
    return deduped[:lim]


# ---------------------------------------------------------------------------
# Evidence ref grouping — used by action + event detail surfaces
# ---------------------------------------------------------------------------


#: Ordered mapping from ref prefix → UI category key.  The order
#: defines the display order in the grouped renderer.
_REF_CATEGORY_ORDER: list[tuple[str, str]] = [
    ("factor:",       "factors"),
    ("alert:",        "alerts"),
    ("rel:",          "relationships"),
    ("holding:",      "holdings"),
    ("ticker:",       "tickers"),
    ("related:",      "related"),
    ("note:",         "notes"),
    ("attention:",    "attention"),
    ("repeat_neg:",   "repeat_negative"),
    ("holdings:",     "counts"),
    ("distinct_factors=", "counts"),
    ("stale_minutes=",    "freshness"),
    ("reconcile.",    "maintenance"),
    ("backfill.",     "maintenance"),
    ("manual_edit",   "maintenance"),
]


def group_evidence_refs(refs: Iterable[str]) -> dict[str, list[str]]:
    """Bucket a list of Phase 9N evidence refs into UI categories.

    Returns a dict keyed by the category name from
    ``_REF_CATEGORY_ORDER``, mapped to the list of refs that matched
    that prefix (preserving input order).  A "other" bucket catches
    anything that didn't match a known prefix.

    Example::

        >>> group_evidence_refs([
        ...     "factor:interest_rate", "holding:h_aapl",
        ...     "alert:abc", "mystery:x",
        ... ])
        {
            'factors': ['factor:interest_rate'],
            'alerts':  ['alert:abc'],
            'holdings':['holding:h_aapl'],
            'other':   ['mystery:x'],
        }

    This is the shared categoriser — every surface that renders
    grounded refs should go through it so the category labels stay
    consistent across actions, event detail, and future surfaces.
    """
    buckets: dict[str, list[str]] = {}
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            continue
        category = "other"
        for prefix, name in _REF_CATEGORY_ORDER:
            if ref.startswith(prefix):
                category = name
                break
        buckets.setdefault(category, []).append(ref)
    return buckets


#: Human labels for the ref categories, used by the frontend helper
#: to render the "Grounded in" sub-headings.  Exposed here so tests
#: and the route can snapshot the vocabulary.
CATEGORY_LABELS: Mapping[str, str] = {
    "factors":         "Factors",
    "alerts":          "Alerts",
    "relationships":   "Relationships",
    "holdings":        "Holdings",
    "tickers":         "Tickers",
    "related":         "Related",
    "notes":           "Notes",
    "attention":       "Attention",
    "repeat_negative": "Repeat negatives",
    "counts":          "Counts",
    "freshness":       "Freshness",
    "maintenance":     "Maintenance",
    "other":           "Other",
}
