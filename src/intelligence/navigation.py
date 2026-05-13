"""Phase 9Q — Deep-link / contextual navigation helpers.

This module is the single source of truth for how Axion surfaces
emit structured "jump here" hints.  Every premium surface (inbox,
recommended actions, event detail evidence chips, alert cards,
operator recent actions, digest) uses this module to produce a
:class:`NavigationTarget`, so the frontend only needs one
``jumpToTarget(target)`` dispatcher instead of five duplicated
string-parsing implementations.

Design goals (from the Phase 9Q brief)
--------------------------------------
* deterministic, pure, zero-DB
* JSON-safe, compact
* portfolio-safe by construction — every target carries
  ``portfolio_id`` so the frontend can switch portfolios before
  navigating
* minimal surface area — no router, no URL state framework
* fallback-friendly — unknown inputs return ``None`` instead of
  raising, so callers can render a disabled button instead of a
  broken link

The :class:`NavigationTarget` shape is deliberately small:

``surface``       one of ``alerts | digest | events | operator | portfolio``
``portfolio_id``  the target portfolio (enforces portfolio safety)
``entity_type``   optional — ``event``, ``alert``, ``factor``, ``relationship``
``entity_id``     optional — the id of the row to focus
``subtab``        optional — sub-tab hint when the surface has sub-tabs
``filter``        optional — small filter hint (e.g. operator factor key)
``open_modal``    optional — ``True`` to auto-open a modal on the target
``highlight_key`` optional — DOM key the frontend can flash as a focus cue
``label``         optional — button label for the emitter's UI
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Surface registry
# ---------------------------------------------------------------------------

Surface = Literal["alerts", "digest", "events", "operator", "portfolio"]

#: Set of surface values the frontend dispatcher knows how to route to.
#: Any other string is rejected by the shared validator so we can't
#: emit a "jump to X" that the dashboard silently drops.
_KNOWN_SURFACES: frozenset[str] = frozenset({
    "alerts", "digest", "events", "operator", "portfolio",
})

#: Operator sub-sections the target can focus.  The frontend maps
#: these to anchor ids inside Settings → Operator.
_OPERATOR_SUBTABS: frozenset[str] = frozenset({
    "factors", "relationships", "maintenance", "recent-actions",
})


# ---------------------------------------------------------------------------
# Phase 9U — Approved filter keys
# ---------------------------------------------------------------------------

#: Registry of filter keys that can appear in ``NavigationTarget.filters``.
#: Only these keys survive validation so the hash can't carry arbitrary
#: state.  Keyed by ``(surface, subtab?)`` → set of allowed filter keys.
#: A ``None`` subtab means the filter applies at the surface level.
_APPROVED_FILTERS: dict[tuple[str, str | None], frozenset[str]] = {
    ("operator", "factors"):        frozenset({"factor"}),
    ("operator", "relationships"):  frozenset({"source"}),
    ("events", "events"):           frozenset({"search"}),
    ("alerts", None):               frozenset({"severity", "ack"}),
}


def validate_filters(
    surface: str,
    subtab: str | None,
    raw_filters: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Strip unknown filter keys from a raw filter payload.

    Returns ``None`` when no approved filters survive so callers
    can skip the field entirely.  Unknown keys are silently dropped
    — never raised as errors so stale saved views degrade gracefully.
    """
    if not raw_filters:
        return None
    approved = _APPROVED_FILTERS.get((surface, subtab), frozenset())
    # Also check the surface-level key (subtab=None) as a fallback
    if not approved:
        approved = _APPROVED_FILTERS.get((surface, None), frozenset())
    result = {k: str(v) for k, v in raw_filters.items() if k in approved}
    return result or None


# ---------------------------------------------------------------------------
# Phase 9V — View summary labeling (single source of truth)
# ---------------------------------------------------------------------------

#: Human labels for surfaces used by ``describe_view``.
_SURFACE_LABELS: dict[str, str] = {
    "alerts":    "Alerts",
    "digest":    "Digest",
    "events":    "Events",
    "operator":  "Operator",
    "portfolio": "Portfolio",
}

#: Human labels for subtabs.
_SUBTAB_LABELS: dict[str, str] = {
    "events":        "Events",
    "analysis":      "Analysis",
    "digest":        "Digest",
    "inbox":         "Inbox",
    "factors":       "Factors",
    "relationships": "Relationships",
    "maintenance":   "Maintenance",
    "recent-actions": "Recent Actions",
}

#: Human labels for the severity filter options.  These map from the
#: filter *value* (as stored in the hash / saved-view payload) to a
#: compact label suitable for one-line descriptions.
_SEVERITY_FILTER_LABELS: dict[str, str] = {
    "":              "All severities",
    "all":           "All severities",
    "critical":      "Critical only",
    "critical_high": "Critical & High",
    "high":          "High & above",
    "warning":       "Warning & above",
    "info":          "Info only",
}

#: Human labels for the acknowledged/open state filter options.
_ACK_FILTER_LABELS: dict[str, str] = {
    "":     "Open & acknowledged",
    "all":  "Open & acknowledged",
    "open": "Open only",
    "ack":  "Acknowledged only",
}

#: Human labels for filter keys → rendered filter descriptions.
_FILTER_KEY_LABELS: dict[str, str] = {
    "factor": "Factor",
    "source": "Source",
    "search": "Search",
    "severity": "Severity",
    "ack": "State",
}


def describe_view(
    payload: Mapping[str, Any] | None,
) -> str:
    """Build a compact, human-readable one-line description of a view.

    This is the **single source of truth** for view summary text used
    by both the backend (saved-view API response) and the frontend
    (rendered inline on each saved-view row).  The output is stable,
    deterministic, and safe for E2E assertions.

    Examples::

        >>> describe_view({"surface": "alerts", "filters": {"severity": "critical_high"}})
        'Alerts · Critical & High'
        >>> describe_view({"surface": "operator", "subtab": "relationships", "filters": {"source": "manual"}})
        'Operator · Relationships · Source: manual'
        >>> describe_view({"surface": "events", "subtab": "events", "filters": {"search": "fed"}})
        'Events · Search: fed'
        >>> describe_view({"surface": "portfolio"})
        'Portfolio'
    """
    if not payload or not isinstance(payload, Mapping):
        return "Unknown view"

    surface = str(payload.get("surface") or "")
    label = _SURFACE_LABELS.get(surface, surface.title() if surface else "Unknown")

    subtab = payload.get("subtab")
    if subtab and subtab in _SUBTAB_LABELS:
        # Don't repeat when the subtab label is the same as the surface
        sub_label = _SUBTAB_LABELS[subtab]
        if sub_label.lower() != label.lower():
            label += f" · {sub_label}"

    filters = payload.get("filters")
    if isinstance(filters, Mapping):
        for fk, fv in filters.items():
            if not fk or not fv:
                continue
            if fk == "severity":
                sev_label = _SEVERITY_FILTER_LABELS.get(str(fv), str(fv))
                label += f" · {sev_label}"
            elif fk == "ack":
                ack_label = _ACK_FILTER_LABELS.get(str(fv), str(fv))
                label += f" · {ack_label}"
            elif fk == "search":
                label += f" · Search: {fv}"
            else:
                fk_label = _FILTER_KEY_LABELS.get(fk, fk.title())
                label += f" · {fk_label}: {fv}"

    # Entity hints
    if payload.get("open_modal") and payload.get("entity_type") == "holding":
        label += " · Holding detail"
    elif payload.get("open_modal") and payload.get("entity_type") == "event":
        label += " · Event detail"

    return label


# ---------------------------------------------------------------------------
# NavigationTarget — the shared contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NavigationTarget:
    """A compact, portfolio-safe navigation hint.

    Every field is JSON-safe.  The dataclass is frozen so callers
    can't mutate a target by accident (targets often flow through
    multiple layers before reaching the frontend).

    Phase 9U — ``filters`` is an optional dict of approved filter
    key→value pairs that can be restored on the target surface.
    Unlike the legacy ``filter`` field (a single string used only
    for operator factor filter), ``filters`` can carry multiple
    filter dimensions per surface.  Both fields coexist for backward
    compatibility — the frontend reads ``filters`` first, then
    falls back to the legacy ``filter`` field.
    """

    surface: str                      # must be in _KNOWN_SURFACES
    portfolio_id: str
    entity_type: str | None = None
    entity_id: str | None = None
    subtab: str | None = None
    filter: str | None = None         # LEGACY: single filter hint (backward compat)
    open_modal: bool = False
    highlight_key: str | None = None
    label: str | None = None
    #: Phase 9U — multi-dimensional filter payload.  Only approved
    #: keys survive validation; unknown keys are stripped.
    filters: Mapping[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "surface": self.surface,
            "portfolio_id": self.portfolio_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "subtab": self.subtab,
            "filter": self.filter,
            "open_modal": self.open_modal,
            "highlight_key": self.highlight_key,
            "label": self.label,
        }
        if self.filters:
            d["filters"] = dict(self.filters)
        return d


def _safe_target(
    *,
    surface: str,
    portfolio_id: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    subtab: str | None = None,
    filter: str | None = None,
    open_modal: bool = False,
    highlight_key: str | None = None,
    label: str | None = None,
    filters: Mapping[str, str] | None = None,
) -> NavigationTarget | None:
    """Build a NavigationTarget with full safety checks.

    Returns ``None`` when the inputs are invalid so callers can
    render a disabled button instead of a broken link.  Rules:

    * ``surface`` must be a registered surface name
    * ``portfolio_id`` must be a non-empty string (portfolio safety)
    * ``subtab`` must be a known operator sub-section when
      ``surface == "operator"``

    Phase 9U — ``filters`` is validated against the approved filter
    registry so only known keys survive.
    """
    if surface not in _KNOWN_SURFACES:
        return None
    if not portfolio_id or not isinstance(portfolio_id, str):
        return None
    if surface == "operator" and subtab is not None and subtab not in _OPERATOR_SUBTABS:
        subtab = None
    # Phase 9U — validate + strip unknown filter keys
    clean_filters: Mapping[str, str] | None = None
    if filters:
        clean_filters = validate_filters(surface, subtab, filters) or None
    return NavigationTarget(
        surface=surface,
        portfolio_id=portfolio_id,
        entity_type=entity_type,
        entity_id=entity_id,
        subtab=subtab,
        filter=filter,
        open_modal=open_modal,
        highlight_key=highlight_key,
        label=label,
        filters=clean_filters,
    )


# ---------------------------------------------------------------------------
# Builders — one per source type
# ---------------------------------------------------------------------------


def target_for_alert(
    alert_id: str,
    portfolio_id: str,
    *,
    label: str | None = None,
) -> NavigationTarget | None:
    """Build a deep link to the Alerts tab focused on a specific alert.

    The frontend scrolls the matching alert card into view and flashes
    a highlight ring for ~1.8s.  Falls back to the Alerts tab with no
    highlight if the id is missing.
    """
    if not alert_id:
        return _safe_target(
            surface="alerts", portfolio_id=portfolio_id,
            label=label or "Open alerts",
        )
    return _safe_target(
        surface="alerts",
        portfolio_id=portfolio_id,
        entity_type="alert",
        entity_id=alert_id,
        highlight_key=f"alert:{alert_id}",
        label=label or "Open alert",
    )


def target_for_holding(
    holding_id: str,
    portfolio_id: str,
    *,
    open_detail: bool = False,
    label: str | None = None,
) -> NavigationTarget | None:
    """Build a deep link to the Portfolio → Holdings tab focused on a
    specific holding.

    Phase 9S — when ``open_detail=True``, the frontend will open the
    holding detail side-panel after scrolling the row into view.  When
    ``False`` (the default), only the row highlight fires.
    """
    if not holding_id:
        return _safe_target(
            surface="portfolio", portfolio_id=portfolio_id,
            label=label or "View holdings",
        )
    return _safe_target(
        surface="portfolio",
        portfolio_id=portfolio_id,
        entity_type="holding",
        entity_id=holding_id,
        open_modal=open_detail,
        highlight_key=f"holding:{holding_id}",
        label=label or ("Open holding detail" if open_detail else "View holding"),
    )


def target_for_event(
    event_id: str,
    portfolio_id: str,
    *,
    open_modal: bool = True,
    label: str | None = None,
) -> NavigationTarget | None:
    """Build a deep link to the Intelligence → Events sub-tab that
    auto-opens the event detail modal for the given event id.

    ``open_modal=False`` opens just the sub-tab + highlights the row
    without popping the modal — useful for mid-flight navigation
    where a modal would obscure the events table.
    """
    if not event_id:
        return _safe_target(
            surface="events",
            portfolio_id=portfolio_id,
            subtab="events",
            label=label or "Open events",
        )
    return _safe_target(
        surface="events",
        portfolio_id=portfolio_id,
        subtab="events",
        entity_type="event",
        entity_id=event_id,
        open_modal=open_modal,
        highlight_key=f"event:{event_id}",
        label=label or "Open event",
    )


def target_for_digest(
    portfolio_id: str,
    *,
    label: str | None = None,
) -> NavigationTarget | None:
    """Build a deep link to the Intelligence → Digest sub-tab."""
    return _safe_target(
        surface="digest",
        portfolio_id=portfolio_id,
        subtab="digest",
        label=label or "Read digest",
    )


def target_for_operator_entry(
    entry: Mapping[str, Any],
    portfolio_id: str,
    *,
    label: str | None = None,
) -> NavigationTarget | None:
    """Build a deep link into Settings → Operator for a specific
    recent-action row.

    The operator entry is expected to be the dict form produced by
    :func:`src.intelligence.traceability.shape_audit_entry` (Phase 9O).
    The entity_type drives the sub-section hint:

    * ``holding_factor_sensitivity`` → operator factors table,
      filtered by factor key when available
    * ``holding_relationship`` → operator relationships table
    * ``holding_relationships`` → operator maintenance section
      (reconcile)
    * ``intelligence_backfill`` → operator maintenance section
      (backfill)
    """
    entity_type = str(entry.get("entity_type") or "")
    entity_id = entry.get("id") or None

    if entity_type == "holding_factor_sensitivity":
        # Extract factor key + holding_id from the evidence refs
        factor_filter: str | None = None
        holding_id: str | None = None
        for ref in entry.get("evidence_refs") or ():
            if isinstance(ref, str):
                if ref.startswith("factor:"):
                    factor_filter = ref.split(":", 1)[1]
                elif ref.startswith("holding:"):
                    holding_id = ref.split(":", 1)[1]
        # Phase 9R — exact factor-row highlight when we know both IDs
        hl_key: str | None = None
        if holding_id and factor_filter:
            hl_key = f"factor-row:{holding_id}:{factor_filter}"
        elif entity_id:
            hl_key = f"audit:{entity_id}"
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="factors",
            entity_type="factor_override",
            entity_id=entity_id,
            filter=factor_filter,
            highlight_key=hl_key,
            label=label or "Open in Operator",
        )

    if entity_type == "holding_relationship":
        # Phase 9R — exact relationship-row highlight
        # The entity_id of the audit row IS the relationship id, so
        # we can use it directly as a rel-row anchor.
        hl_key_rel = f"rel-row:{entity_id}" if entity_id else None
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="relationships",
            entity_type="relationship",
            entity_id=entity_id,
            highlight_key=hl_key_rel,
            label=label or "Open in Operator",
        )

    if entity_type in ("holding_relationships", "intelligence_backfill"):
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="maintenance",
            entity_type=entity_type,
            entity_id=entity_id,
            highlight_key=f"audit:{entity_id}" if entity_id else None,
            label=label or "Open Operator",
        )

    # Unknown / legacy operator row → land on the operator panel
    return _safe_target(
        surface="operator",
        portfolio_id=portfolio_id,
        subtab="recent-actions",
        label=label or "Open Operator",
    )


#: Mapping from Phase 9N action key **prefix** to a navigation target
#: builder.  We key off the prefix (before the first dot) so new
#: sub-rules in the same family inherit the target automatically.
def target_for_action(
    action: Mapping[str, Any],
    portfolio_id: str,
) -> NavigationTarget | None:
    """Build a deep link for a Phase 9N ``RecommendedAction`` dict.

    The mapping is conservative — only actions whose rule family has
    an unambiguous "where to look next" destination get a target.
    Unknown action keys return ``None`` so the overview card still
    renders the action text without a clickable affordance.

    Mappings:

    * ``alerts.*``        → Alerts tab
    * ``holdings.*``      → Portfolio tab (intelligence overview)
    * ``factors.*``       → Operator factors table, filtered by
                            factor key when present in rationale_refs
    * ``relationships.*`` → Operator relationships table
    * ``freshness.*``     → Settings → Operator (maintenance)
    * ``maintenance.*``   → Settings → Operator (maintenance)
    """
    key = str(action.get("key") or "")
    if not key:
        return None
    family = key.split(".", 1)[0]

    if family == "alerts":
        return _safe_target(
            surface="alerts", portfolio_id=portfolio_id,
            label="Review in Alerts",
        )

    if family == "holdings":
        return _safe_target(
            surface="portfolio", portfolio_id=portfolio_id,
            label="Review in overview",
        )

    if family == "factors":
        # Extract the specific factor key from the rationale refs so
        # the operator filter lands on the matching row.
        factor_filter: str | None = None
        for ref in action.get("rationale_refs") or ():
            if isinstance(ref, str) and ref.startswith("factor:"):
                factor_filter = ref.split(":", 1)[1]
                break
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="factors",
            filter=factor_filter,
            label="Open factor table",
        )

    if family == "relationships":
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="relationships",
            label="Open relationship table",
        )

    if family in ("freshness", "maintenance"):
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="maintenance",
            label="Open maintenance",
        )

    return None


def target_for_evidence_ref(
    ref: str,
    portfolio_id: str,
) -> NavigationTarget | None:
    """Build a deep link for a Phase 9N evidence ref string like
    ``"factor:interest_rate"`` or ``"event:evt_123"``.

    Refs whose prefix is not in the navigation registry return
    ``None`` — the frontend renders them as plain (non-clickable)
    chips.  Only refs that point at a known, navigable entity are
    made clickable.

    Registry:

    * ``event:<id>``   → Events sub-tab, open modal
    * ``alert:<id>``   → Alerts tab, highlight
    * ``holding:<id>`` → Portfolio tab
    * ``ticker:<sym>`` → Portfolio tab
    * ``factor:<key>`` → Operator factors table, filtered by key
    * ``rel:<type>``   → Operator relationships table
    """
    if not isinstance(ref, str) or ":" not in ref:
        return None
    prefix, value = ref.split(":", 1)
    value = value.strip()
    if not value:
        return None

    if prefix == "event":
        return target_for_event(value, portfolio_id)
    if prefix == "alert":
        return target_for_alert(value, portfolio_id)
    if prefix in ("holding", "ticker"):
        return _safe_target(
            surface="portfolio",
            portfolio_id=portfolio_id,
            entity_type=prefix,
            entity_id=value,
            # Phase 9R — emit an exact highlight key so the holdings
            # table row can be scrolled into view and flash-highlighted.
            highlight_key=f"{prefix}:{value}",
            label="View in portfolio",
        )
    if prefix == "factor":
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="factors",
            filter=value,
            label="Open factor",
        )
    if prefix == "rel":
        return _safe_target(
            surface="operator",
            portfolio_id=portfolio_id,
            subtab="relationships",
            filter=value,
            label="Open relationships",
        )
    return None


# ---------------------------------------------------------------------------
# Enrichment helpers — add nav_target fields to existing dict payloads
# ---------------------------------------------------------------------------


def enrich_actions_with_targets(
    actions: Iterable[Mapping[str, Any]],
    portfolio_id: str,
) -> list[dict[str, Any]]:
    """Return a new list of action dicts with a ``nav_target`` key
    attached to each entry.

    Pure function — the input iterable is not mutated.  Actions that
    have no navigable destination get ``nav_target=None`` so the
    frontend can render them without a clickable affordance.
    """
    out: list[dict[str, Any]] = []
    for a in actions or ():
        if not isinstance(a, Mapping):
            continue
        enriched = dict(a)
        target = target_for_action(a, portfolio_id)
        enriched["nav_target"] = target.to_dict() if target is not None else None
        out.append(enriched)
    return out


def enrich_evidence_refs(
    refs: Iterable[str],
    portfolio_id: str,
) -> list[dict[str, Any]]:
    """Return a parallel list of evidence-ref dicts.

    Each dict has ``{ref: "<prefix>:<value>", nav_target: {...} | None}``
    so the frontend can iterate once and render either a clickable
    chip (when ``nav_target`` is present) or a plain chip (when it is
    None).  The ref order is preserved.
    """
    out: list[dict[str, Any]] = []
    for r in refs or ():
        if not isinstance(r, str) or not r:
            continue
        target = target_for_evidence_ref(r, portfolio_id)
        out.append({
            "ref": r,
            "nav_target": target.to_dict() if target is not None else None,
        })
    return out


# ---------------------------------------------------------------------------
# Phase 9R — URL hash encoding / decoding
# ---------------------------------------------------------------------------
#
# Format: ``#nav=<base64url-json>``
#
# The JSON payload is the compact ``.to_dict()`` output of a
# ``NavigationTarget``.  The ``label`` field is stripped (it's a UI
# label, not navigation state) so the hash stays as short as
# possible.  Base64url is used instead of raw JSON because JSON
# contains characters that are not safe in URL fragments.
#
# Decoding: the frontend reads ``location.hash``, strips the
# ``#nav=`` prefix, base64url-decodes, JSON-parses, validates, and
# pipes the result into ``jumpToTarget``.  Malformed hashes are
# silently ignored.
#
# The contract is versioned implicitly by the ``surface`` registry —
# an unknown surface in the decoded target causes the dispatcher to
# no-op, so old hashes from a future schema revision never crash
# the app.


#: The hash prefix used for deep-link targets.  Changing this
#: prefix would break all previously-shared links, so it should be
#: treated as a frozen constant.
NAV_HASH_PREFIX: str = "nav="


def encode_nav_hash(target: NavigationTarget | Mapping[str, Any]) -> str:
    """Encode a ``NavigationTarget`` (or its ``.to_dict()`` form) into
    a URL-fragment string suitable for ``location.hash``.

    Returns the full fragment including the leading ``#``, e.g.
    ``#nav=eyJz...``.  The ``label`` field is stripped to minimise
    the hash length.

    >>> t = _safe_target(surface="alerts", portfolio_id="pA",
    ...                  entity_type="alert", entity_id="a1",
    ...                  highlight_key="alert:a1")
    >>> encode_nav_hash(t)  # doctest: +SKIP
    '#nav=eyJzd...'
    """
    if isinstance(target, NavigationTarget):
        d = target.to_dict()
    elif isinstance(target, Mapping):
        d = dict(target)
    else:
        return ""

    # Strip fields that are purely presentational
    d.pop("label", None)

    # Strip None values for compactness
    d = {k: v for k, v in d.items() if v is not None}

    payload = json.dumps(d, separators=(",", ":"), sort_keys=True)
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    # Remove trailing '=' padding (safe for base64url; the decoder
    # adds it back automatically).
    encoded = encoded.rstrip("=")
    return f"#{NAV_HASH_PREFIX}{encoded}"


def decode_nav_hash(hash_str: str) -> dict[str, Any] | None:
    """Decode a URL fragment produced by :func:`encode_nav_hash` back
    into a target dict.

    Returns ``None`` for any malformed, missing, or invalid input —
    the caller can then simply skip the navigation step instead of
    crashing.

    Accepts the fragment with or without the leading ``#``.
    """
    if not hash_str or not isinstance(hash_str, str):
        return None

    raw = hash_str.lstrip("#")
    if not raw.startswith(NAV_HASH_PREFIX):
        return None

    encoded = raw[len(NAV_HASH_PREFIX):]
    if not encoded:
        return None

    # Re-add base64 padding if necessary
    padding = 4 - (len(encoded) % 4)
    if padding != 4:
        encoded += "=" * padding

    try:
        payload = base64.urlsafe_b64decode(encoded).decode("utf-8")
    except Exception:
        return None

    try:
        d = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(d, dict):
        return None

    # Validate the required fields
    if not d.get("surface") or not d.get("portfolio_id"):
        return None
    if d["surface"] not in _KNOWN_SURFACES:
        return None

    return d
