"""Phase 9N — Deterministic actionable-intelligence layer.

Turns Axion's already-trusted deterministic artifacts (alerts, factor
touchpoints, relationship touchpoints, analysis notes, intelligence
summary posture, freshness state) into a tiny, stable, JSON-safe list
of *operator-facing recommended actions*.

Design principles
-----------------
1. **Deterministic-first.**  Every action is derived from a small,
   explicit rule family that reads an existing row or aggregate.
   No new scoring model, no new probability, no LLM dependency.
2. **Grounded or silent.**  If the evidence is thin, the builder
   returns nothing.  No filler, no "review your portfolio" noise.
3. **Operator-facing, not predictive.**  Action titles describe
   things the operator can DO, not predictions about prices or
   future events.  Zero trading advice.
4. **Explainable.**  Every action carries a list of
   ``rationale_refs`` pointing at the exact inputs that produced it
   (alert ids, factor keys, relationship types, note ids).  The
   caller can surface them as tooltips or audit trail.
5. **Stable keys.**  Every rule family produces actions with a
   deterministic ``key`` prefix (e.g. ``alerts.critical_present``,
   ``factors.rate_pressure``).  Keys are stable across releases so
   frontends and analytics can track recurrences.
6. **Bounded.**  The builder never returns more than
   :data:`MAX_ACTIONS_PER_CALL` items and ranks them by a small
   finite priority scale.

Rule families (all opt-in, evidence-gated)
------------------------------------------

* ``alerts.critical_present``        — if there is any critical alert
* ``alerts.high_cluster``            — if there are ≥2 high alerts
* ``factors.strong_rate_pressure``   — interest-rate factor ↑ with
                                        ≥2 affected holdings
* ``factors.strong_energy_pressure`` — oil_energy factor with ≥2
                                        affected holdings
* ``factors.broad_pressure``         — ≥3 distinct factor touchpoints
                                        (breadth signal, posture-neutral)
* ``relationships.single_dependency``— a seeded or manual supplier/parent
                                        relationship linked to a recent
                                        event
* ``holdings.under_attention``       — ≥1 holding with a recent
                                        material negative note
* ``holdings.repeated_negative``     — ≥2 negative analysis notes on
                                        the same ticker within 7d
* ``freshness.stale_feed``           — Event.fetched_at older than the
                                        stale threshold
* ``maintenance.backfill_after_edit``— operator added a manual row in
                                        the last 24h but hasn't run
                                        backfill since
* ``maintenance.reconcile_after_yaml``— placeholder for a future phase;
                                        currently reports only if there
                                        are zero seed rows at all

The last two families are produced ONLY by the
:func:`build_operator_maintenance_action` helper the operator
last-action hint calls, not by the general
:func:`build_actions_for_portfolio`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning knobs (deliberately small)
# ---------------------------------------------------------------------------

#: Maximum number of actions the top-level builder will return in one
#: call.  The UI typically renders at most 3; we return up to 5 so
#: downstream callers have a little headroom for filtering.
MAX_ACTIONS_PER_CALL: int = 5

#: Priority order — used to sort actions consistently everywhere.
#: Keep this short and explainable.
_PRIORITY_RANK: dict[str, int] = {
    "high":   0,
    "medium": 1,
    "low":    2,
}


# ---------------------------------------------------------------------------
# Action object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecommendedAction:
    """A single actionable recommendation.

    Stable JSON-safe shape.  Every field has a defined type and a
    sensible default so callers can round-trip through ``to_dict``
    without worrying about nulls.

    ``key`` is stable across releases — frontends match on it for
    styling.  ``rationale_refs`` is a list of short strings
    describing the evidence the action is grounded in (alert ids,
    factor keys, ticker symbols, note ids).  They are display-safe.
    """

    key: str
    title: str
    description: str
    priority: str                                       # "high" | "medium" | "low"
    related_tickers: tuple[str, ...] = field(default_factory=tuple)
    rationale_refs: tuple[str, ...] = field(default_factory=tuple)
    portfolio_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "related_tickers": list(self.related_tickers),
            "rationale_refs": list(self.rationale_refs),
            "portfolio_id": self.portfolio_id,
        }


def _sort_actions(actions: Iterable[RecommendedAction]) -> list[RecommendedAction]:
    """Return a stable-sorted list: priority first, then title."""
    return sorted(
        actions,
        key=lambda a: (_PRIORITY_RANK.get(a.priority, 99), a.title),
    )


# ---------------------------------------------------------------------------
# Phase 9T — Action fingerprinting
# ---------------------------------------------------------------------------


def compute_action_fingerprint(action: Mapping[str, Any]) -> str:
    """Compute a deterministic fingerprint for a recommended action dict.

    The fingerprint captures the action's grounded evidence so the
    reappearance rule can detect material changes:

    * **same key + same fingerprint** → stays handled (read/dismissed)
    * **same key + different fingerprint** → reappears as new

    The inputs are:
      * ``priority`` — a change from medium → high is material
      * ``rationale_refs`` — sorted for stability
      * ``related_tickers`` — sorted for stability

    The output is a short hex digest (SHA-256 truncated to 16 chars)
    that is stable across Python restarts and JSON round-trips.

    >>> compute_action_fingerprint({"key": "alerts.critical_present",
    ...     "priority": "high", "rationale_refs": ["alerts.critical=2"],
    ...     "related_tickers": ["AAPL"]})
    'a1b2c3d4...'   # doctest: +SKIP
    """
    import hashlib
    parts: list[str] = []
    parts.append(str(action.get("priority") or ""))
    refs = sorted(str(r) for r in (action.get("rationale_refs") or []))
    parts.extend(refs)
    tickers = sorted(str(t) for t in (action.get("related_tickers") or []))
    parts.extend(tickers)
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def filter_actions_by_state(
    actions: list[dict[str, Any]],
    handled_states: Mapping[str, tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split actions into visible and hidden lists based on handled state.

    ``handled_states`` is a dict keyed by ``action_key``, mapping to
    ``(state, fingerprint)`` tuples from the ``action_states`` table.

    An action is **hidden** when:
      * its key appears in ``handled_states``
      * AND the stored fingerprint matches the current fingerprint

    An action is **visible** (even if previously handled) when:
      * its key is not in ``handled_states``
      * OR the stored fingerprint differs from the current fingerprint
        (material change → reappearance)

    Returns ``(visible, hidden)``.
    """
    visible: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for a in actions:
        key = a.get("key") or ""
        fp = compute_action_fingerprint(a)
        a["fingerprint"] = fp  # attach for downstream consumers
        entry = handled_states.get(key)
        if entry is not None:
            stored_state, stored_fp = entry
            if stored_fp == fp:
                # Same fingerprint → still handled
                a["action_state"] = stored_state
                hidden.append(a)
                continue
            # Fingerprint changed → material change → reappears
        a["action_state"] = None
        visible.append(a)
    return visible, hidden


# ---------------------------------------------------------------------------
# Input contract — a thin NamedTuple-ish dict the caller passes in.
# The intelligence summary already has every field we need, so this
# module never does its own SQL.  Callers are responsible for
# portfolio-scoping the inputs.
# ---------------------------------------------------------------------------


@dataclass
class ActionInputs:
    """Grounded inputs for :func:`build_actions_for_portfolio`.

    Every field is a snapshot of already-trusted data the caller
    already computed.  The action builder does not read the DB
    itself — that keeps it pure, testable, and trivially portfolio-
    scoped by construction.

    Callers typically feed this from an already-built
    :class:`src.intelligence.summary.IntelligenceSummary` + the list
    of active :class:`Alert` rows for the portfolio.
    """

    portfolio_id: str
    holding_count: int
    posture: str
    alerts: dict[str, int]                          # {critical, high, warning, info, total}
    top_factors: list[dict[str, Any]]               # from intelligence.summary
    top_relationships: list[dict[str, Any]]         # from intelligence.summary
    holdings_under_attention: list[str]             # tickers
    analysis_notes_by_ticker: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)
    intelligence_health: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_actions_for_portfolio(inputs: ActionInputs) -> list[RecommendedAction]:
    """Return a prioritised list of recommended actions for a portfolio.

    Every action is grounded in the supplied ``inputs`` — if the
    caller didn't compute a field, the corresponding rule family
    stays silent.  The result is sorted high→low priority with
    alphabetic tie-break, then truncated to
    :data:`MAX_ACTIONS_PER_CALL`.

    Never raises.  Empty inputs → empty list.
    """
    if not inputs or not inputs.portfolio_id:
        return []

    actions: list[RecommendedAction] = []

    # ── Alerts families ─────────────────────────────────────────────
    actions.extend(_actions_from_alerts(inputs))

    # ── Holdings under attention ────────────────────────────────────
    actions.extend(_actions_from_attention(inputs))

    # ── Repeated negatives ──────────────────────────────────────────
    actions.extend(_actions_from_repeated_negative(inputs))

    # ── Factor pressure ─────────────────────────────────────────────
    actions.extend(_actions_from_factors(inputs))

    # ── Relationship dependencies ───────────────────────────────────
    actions.extend(_actions_from_relationships(inputs))

    # ── Freshness / feed health ─────────────────────────────────────
    actions.extend(_actions_from_freshness(inputs))

    # Deduplicate by key — if two rule families produce the same key
    # keep the higher-priority one.  (This is defensive — the current
    # families produce disjoint keys but a future family might
    # overlap.)
    by_key: dict[str, RecommendedAction] = {}
    for a in actions:
        existing = by_key.get(a.key)
        if existing is None:
            by_key[a.key] = a
            continue
        if _PRIORITY_RANK.get(a.priority, 99) < _PRIORITY_RANK.get(existing.priority, 99):
            by_key[a.key] = a

    ranked = _sort_actions(by_key.values())
    return ranked[:MAX_ACTIONS_PER_CALL]


# ---------------------------------------------------------------------------
# Rule families
# ---------------------------------------------------------------------------


def _actions_from_alerts(inputs: ActionInputs) -> list[RecommendedAction]:
    out: list[RecommendedAction] = []
    alerts = inputs.alerts or {}
    critical = int(alerts.get("critical", 0) or 0)
    high = int(alerts.get("high", 0) or 0)

    if critical >= 1:
        out.append(RecommendedAction(
            key="alerts.critical_present",
            title="Review critical alerts",
            description=(
                f"{critical} critical alert{'s' if critical != 1 else ''} "
                f"active for this portfolio. Inspect before market open "
                f"to confirm whether further action is required."
            ),
            priority="high",
            rationale_refs=(f"alerts.critical={critical}",),
            portfolio_id=inputs.portfolio_id,
        ))

    if high >= 2:
        out.append(RecommendedAction(
            key="alerts.high_cluster",
            title="Investigate high-severity cluster",
            description=(
                f"{high} high-severity alerts are active simultaneously. "
                f"Look for a common theme (sector, factor, event) before "
                f"acknowledging individual rows."
            ),
            priority="high" if critical == 0 else "medium",
            rationale_refs=(f"alerts.high={high}",),
            portfolio_id=inputs.portfolio_id,
        ))
    elif high == 1 and critical == 0:
        out.append(RecommendedAction(
            key="alerts.high_single",
            title="Review high-severity alert",
            description=(
                "One high-severity alert is active. Check its related "
                "holdings and causal chain before deciding next steps."
            ),
            priority="medium",
            rationale_refs=("alerts.high=1",),
            portfolio_id=inputs.portfolio_id,
        ))
    return out


def _actions_from_attention(inputs: ActionInputs) -> list[RecommendedAction]:
    attention = [t for t in (inputs.holdings_under_attention or []) if t]
    if not attention:
        return []
    # One action per batch of ≤5 tickers — we don't spam 10 rows.
    preview = attention[:5]
    return [RecommendedAction(
        key="holdings.under_attention",
        title="Review holdings under attention",
        description=(
            f"{len(attention)} holding"
            f"{'s' if len(attention) != 1 else ''} flagged by recent "
            f"important-materiality negative analysis: "
            f"{', '.join(preview)}"
            f"{'…' if len(attention) > len(preview) else ''}. "
            f"Open the holding detail to see the grounded rationale."
        ),
        priority="high" if len(attention) >= 3 else "medium",
        related_tickers=tuple(attention),
        rationale_refs=tuple(f"attention:{t}" for t in preview),
        portfolio_id=inputs.portfolio_id,
    )]


def _actions_from_repeated_negative(inputs: ActionInputs) -> list[RecommendedAction]:
    """Pick out any ticker with ≥2 negative analysis notes in the
    supplied ``analysis_notes_by_ticker`` map.

    The caller is responsible for scoping the note window
    (typically last 7 days).  Empty map → empty result.
    """
    repeated: list[str] = []
    for ticker, notes in (inputs.analysis_notes_by_ticker or {}).items():
        neg = [n for n in notes if (n.get("impact_direction") or "").lower() == "negative"]
        if len(neg) >= 2:
            repeated.append(ticker)
    if not repeated:
        return []
    preview = repeated[:4]
    return [RecommendedAction(
        key="holdings.repeated_negative",
        title="Inspect repeated negative signals",
        description=(
            f"{len(repeated)} holding"
            f"{'s' if len(repeated) != 1 else ''} received multiple "
            f"negative analysis notes in the recent window: "
            f"{', '.join(preview)}"
            f"{'…' if len(repeated) > len(preview) else ''}."
        ),
        priority="medium",
        related_tickers=tuple(repeated),
        rationale_refs=tuple(f"repeat_neg:{t}" for t in preview),
        portfolio_id=inputs.portfolio_id,
    )]


_RATE_FACTORS = frozenset({"interest_rate", "credit_conditions"})
_ENERGY_FACTORS = frozenset({"oil_energy"})


def _actions_from_factors(inputs: ActionInputs) -> list[RecommendedAction]:
    """Factor-pressure families.

    Emits an action when a specific factor has a ``direction`` set
    AND affects at least 2 distinct holdings in the portfolio.
    Breadth matters more than magnitude — a narrow single-holding
    touchpoint is already covered by the per-holding attention family.
    """
    out: list[RecommendedAction] = []
    distinct_factor_count = 0
    for f in (inputs.top_factors or []):
        factor_key = (f.get("factor") or "").lower()
        if not factor_key:
            continue
        distinct_factor_count += 1
        holdings = f.get("holdings") or []
        if len(holdings) < 2:
            continue
        direction = (f.get("direction") or "unknown").lower()

        if factor_key in _RATE_FACTORS and direction == "up":
            out.append(RecommendedAction(
                key="factors.strong_rate_pressure",
                title="Review rate-sensitive exposure",
                description=(
                    f"Interest-rate pressure is building across "
                    f"{len(holdings)} holdings: {', '.join(holdings[:4])}"
                    f"{'…' if len(holdings) > 4 else ''}. "
                    f"Check duration profile and refinancing windows."
                ),
                priority="high" if len(holdings) >= 4 else "medium",
                related_tickers=tuple(holdings),
                rationale_refs=(f"factor:{factor_key}", f"holdings:{len(holdings)}"),
                portfolio_id=inputs.portfolio_id,
            ))
        elif factor_key in _ENERGY_FACTORS:
            out.append(RecommendedAction(
                key="factors.strong_energy_pressure",
                title="Review energy exposure",
                description=(
                    f"Oil & energy pressure touches {len(holdings)} "
                    f"holdings: {', '.join(holdings[:4])}"
                    f"{'…' if len(holdings) > 4 else ''}. "
                    f"Confirm whether positions are direction-aligned."
                ),
                priority="medium",
                related_tickers=tuple(holdings),
                rationale_refs=(f"factor:{factor_key}", f"holdings:{len(holdings)}"),
                portfolio_id=inputs.portfolio_id,
            ))

    # Breadth signal: if the portfolio sees factor pressure across 3+
    # distinct factors, suggest a regime check.
    if distinct_factor_count >= 3:
        out.append(RecommendedAction(
            key="factors.broad_pressure",
            title="Consider regime check",
            description=(
                f"{distinct_factor_count} distinct macro factors are "
                f"active on this portfolio. A broad regime review may "
                f"reveal a common driver."
            ),
            priority="low",
            rationale_refs=(f"distinct_factors={distinct_factor_count}",),
            portfolio_id=inputs.portfolio_id,
        ))
    return out


def _actions_from_relationships(inputs: ActionInputs) -> list[RecommendedAction]:
    """Flag relationship touchpoints of dependency-like types.

    Only supplier / parent / subsidiary relationships produce
    actions — customer / competitor / regulator are informational
    in Phase 9D and don't warrant a standalone recommendation.
    """
    out: list[RecommendedAction] = []
    dependency_types = {"supplier", "parent", "subsidiary"}
    for r in (inputs.top_relationships or []):
        rel_type = (r.get("relationship_type") or "").lower()
        if rel_type not in dependency_types:
            continue
        ticker = r.get("ticker") or ""
        related = r.get("related_entity") or rel_type
        if not ticker:
            continue
        out.append(RecommendedAction(
            key=f"relationships.{rel_type}_dependency",
            title=f"Inspect {rel_type} dependency on {related}",
            description=(
                f"{ticker} has a {rel_type} relationship with "
                f"{related}. Recent events may propagate through this "
                f"link — review the causal chain."
            ),
            priority="medium",
            related_tickers=(ticker,),
            rationale_refs=(f"rel:{rel_type}", f"ticker:{ticker}"),
            portfolio_id=inputs.portfolio_id,
        ))
    return out


def _actions_from_freshness(inputs: ActionInputs) -> list[RecommendedAction]:
    freshness = inputs.freshness or {}
    stale_minutes = freshness.get("stale_minutes")
    is_fresh = freshness.get("is_fresh")
    if is_fresh is False and stale_minutes is not None:
        hours = int(stale_minutes) // 60
        return [RecommendedAction(
            key="freshness.stale_feed",
            title="Refresh news collection",
            description=(
                f"No new events in the last {hours}h. Trigger a "
                f"collection run or check source health before "
                f"relying on current intelligence."
            ),
            priority="low",
            rationale_refs=(f"stale_minutes={stale_minutes}",),
            portfolio_id=inputs.portfolio_id,
        )]
    return []


# ---------------------------------------------------------------------------
# Operator-facing maintenance hint
# ---------------------------------------------------------------------------


@dataclass
class MaintenanceInputs:
    """Inputs for the operator maintenance hint builder.

    The operator panel's last-action block uses this helper to turn
    reconcile / backfill stats into a smart next-step suggestion,
    rather than a generic "Saved" text.  Stats come straight from
    the Phase 9H route responses.
    """

    action: str                             # "reconcile" | "backfill" | "manual_relationship" | "manual_factor"
    stats: dict[str, Any] = field(default_factory=dict)


def build_operator_maintenance_action(
    inputs: MaintenanceInputs,
) -> RecommendedAction | None:
    """Return the smartest possible follow-up hint for a completed
    operator action.  Returns ``None`` when the stats don't warrant
    any additional recommendation (e.g. a reconcile that changed nothing).

    The caller surfaces this as a one-line "tip" under the last-action
    echo block — it's explicitly NOT a toast, NOT an alert, and
    NOT a scheduled task.
    """
    action = (inputs.action or "").lower()
    stats = inputs.stats or {}

    if action == "reconcile":
        changed = int(stats.get("created", 0) or 0) + int(stats.get("updated", 0) or 0)
        pruned = int(stats.get("pruned", 0) or 0)
        if changed == 0 and pruned == 0:
            return None
        return RecommendedAction(
            key="maintenance.backfill_after_reconcile",
            title="Consider running backfill",
            description=(
                f"Reconcile changed {changed} seed row"
                f"{'s' if changed != 1 else ''} and pruned {pruned}. "
                f"A bounded backfill will apply these to recent events "
                f"so historical links reflect the current seed graph."
            ),
            priority="medium",
            rationale_refs=(f"reconcile.created={stats.get('created', 0)}",
                            f"reconcile.updated={stats.get('updated', 0)}",
                            f"reconcile.pruned={stats.get('pruned', 0)}"),
        )

    if action == "backfill":
        links_added = int(stats.get("links_added", 0) or 0)
        mfe_added = int(stats.get("mfe_added", 0) or 0)
        failed = int(stats.get("events_failed", 0) or 0)
        if links_added == 0 and mfe_added == 0 and failed == 0:
            return RecommendedAction(
                key="maintenance.backfill_no_op",
                title="Backfill was a no-op",
                description=(
                    "No new links or factor rows landed — historical "
                    "data is already consistent with the current graph."
                ),
                priority="low",
                rationale_refs=("backfill.noop",),
            )
        if failed > 0:
            return RecommendedAction(
                key="maintenance.backfill_partial",
                title="Backfill completed with failures",
                description=(
                    f"{failed} event{'s' if failed != 1 else ''} failed to "
                    f"re-link. Inspect the audit log for the failure "
                    f"reason before retrying."
                ),
                priority="medium",
                rationale_refs=(f"backfill.failed={failed}",),
            )
        return RecommendedAction(
            key="maintenance.backfill_applied",
            title="Backfill applied new links",
            description=(
                f"{links_added} new link{'s' if links_added != 1 else ''} "
                f"and {mfe_added} factor row{'s' if mfe_added != 1 else ''} "
                f"landed. Open the intelligence overview to see the "
                f"updated posture."
            ),
            priority="low",
            rationale_refs=(f"backfill.links_added={links_added}",
                            f"backfill.mfe_added={mfe_added}"),
        )

    if action in ("manual_relationship", "manual_factor"):
        return RecommendedAction(
            key="maintenance.backfill_after_edit",
            title="Run backfill to apply this to recent events",
            description=(
                "Manual edits only affect new events by default. "
                "A bounded backfill will push them onto the last 7 "
                "days of stored events so historical links reflect "
                "your change."
            ),
            priority="medium",
            rationale_refs=("manual_edit",),
        )

    return None


# ---------------------------------------------------------------------------
# Per-event explanation helpers (for the event detail modal)
# ---------------------------------------------------------------------------


def explain_event(
    *,
    event_title: str,
    factor_tags: list[dict[str, Any]],
    chains: list[dict[str, Any]],
    affected_holdings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a compact ``why_it_matters`` + ``suggested_action`` block
    for the event detail modal.

    Pure function.  Reads only the data Phase 9B already surfaces in
    the event detail API response.  Returns an empty-ish dict when
    the evidence is thin — the caller hides the block in that case.

    Returned shape::

        {
          "why_it_matters": "Short prose that names the factor/relationship",
          "suggested_action": "Short operator-facing next step" | None,
          "grounded_in": ["factor:interest_rate", "holdings:AAPL,MSFT"],
        }
    """
    tags = factor_tags or []
    chn = chains or []
    affected = affected_holdings or []

    grounded_refs: list[str] = []
    why_parts: list[str] = []

    # Pick the top factor tag (first one, they're already ranked by
    # confidence in the Phase 9B payload).
    top_tag = next((t for t in tags if t and t.get("label")), None)
    if top_tag:
        label = top_tag.get("label") or top_tag.get("key") or "factor"
        direction = (top_tag.get("direction") or "").lower()
        magnitude = (top_tag.get("magnitude") or "").lower()
        direction_text = {"up": "upward", "down": "downward"}.get(direction, "")
        magnitude_text = f" {magnitude}" if magnitude and magnitude != "unknown" else ""
        why_parts.append(
            f"Axion classified this event as a{magnitude_text} "
            f"{direction_text + ' ' if direction_text else ''}"
            f"{label} touchpoint.".replace("  ", " ").strip()
        )
        grounded_refs.append(f"factor:{top_tag.get('key', 'unknown')}")

    # Count affected holdings
    affected_tickers = []
    for h in affected:
        t = h.get("ticker") or h.get("holding_ticker")
        if t and t not in affected_tickers:
            affected_tickers.append(t)
    if affected_tickers:
        preview = affected_tickers[:4]
        why_parts.append(
            f"It touches {len(affected_tickers)} portfolio holding"
            f"{'s' if len(affected_tickers) != 1 else ''} "
            f"({', '.join(preview)}"
            f"{'…' if len(affected_tickers) > len(preview) else ''})."
        )
        grounded_refs.append(f"holdings:{','.join(preview)}")

    # Relationship chain — if any origin is 'relationship', call it out
    rel_chain = next(
        (c for c in chn if c and (c.get("origin") == "relationship")),
        None,
    )
    if rel_chain:
        related_name = rel_chain.get("related_entity") or ""
        channel = rel_chain.get("channel") or rel_chain.get("link_type") or "relationship"
        if related_name:
            why_parts.append(
                f"The link propagates via a {channel} relationship with {related_name}."
            )
        else:
            why_parts.append(
                f"The link propagates via a {channel} relationship."
            )
        grounded_refs.append(f"relationship:{channel}")

    why_it_matters = " ".join(why_parts).strip() or None

    # Suggested action is derived from the grounded factor family —
    # same tables as the portfolio-level builder.
    suggested_action: str | None = None
    if top_tag:
        factor_key = (top_tag.get("key") or "").lower()
        if factor_key in _RATE_FACTORS and len(affected_tickers) >= 1:
            suggested_action = (
                f"Review duration-sensitive positions in "
                f"{', '.join(affected_tickers[:3])}"
                f"{'…' if len(affected_tickers) > 3 else ''}."
            )
        elif factor_key in _ENERGY_FACTORS and len(affected_tickers) >= 1:
            suggested_action = (
                f"Confirm energy-exposure direction for "
                f"{', '.join(affected_tickers[:3])}"
                f"{'…' if len(affected_tickers) > 3 else ''}."
            )

    return {
        "why_it_matters": why_it_matters,
        "suggested_action": suggested_action,
        "grounded_in": grounded_refs,
    }


# ---------------------------------------------------------------------------
# Per-alert suggested-step helper
# ---------------------------------------------------------------------------


def suggest_next_step_for_alert(alert: dict[str, Any]) -> str | None:
    """Return a tiny "suggested next step" string for an alert row.

    Reads only the alert fields the existing Phase 9H/I pipeline
    already produces — severity, alert_type, related_holdings.
    Returns ``None`` when the alert type is too generic to produce
    a grounded recommendation.

    Typical outputs (short and operator-facing):

      * "Inspect related holding(s)"
      * "Review now before market open"
      * "Check operator settings"
      * "Review sector concentration"
    """
    if not alert:
        return None
    severity = (alert.get("severity") or "info").lower()
    alert_type = (alert.get("alert_type") or "").lower()
    related_holdings = alert.get("related_holdings") or []

    if severity == "critical":
        if related_holdings:
            return "Review now and inspect the related holding(s)."
        return "Review now."
    if severity == "high":
        if alert_type.startswith("macro") or alert_type in ("supply_chain", "oil_risk"):
            return "Inspect the causal chain before acknowledging."
        if alert_type in ("drift", "concentration", "sector_risk"):
            return "Review sector / concentration balance."
        if related_holdings:
            return "Inspect related holding exposure."
        return "Review before acknowledging."
    if severity in ("warning", "medium"):
        if alert_type in ("drift", "concentration"):
            return "Re-check portfolio balance when convenient."
        return None
    return None
