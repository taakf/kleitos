"""Causal-chain normalization (Phase 9B).

Turns any ``EventLink`` row into a single normalized dict shape that
the API and frontend share.  The public entry point is
``build_chain_for_link``.

Design notes
------------
* ``NormalizedChain`` is a plain ``TypedDict`` so it serializes as a
  JSON object without Pydantic ceremony — the API response model
  exposes it as an ``Any``-typed field and the frontend reads it
  directly.  This keeps the contract tiny and the schema visible.
* For ``macro_factor`` links, the Phase 9A ``EventLink.details_json``
  already holds a structured payload produced by
  ``FactorImpact.to_details_json``; we validate it and flatten it to
  the normalized shape.
* For legacy direct-match link types (``ticker_match``,
  ``sector_geo_match``, ``macro_screen``) there is no stored chain
  and no storage migration is in scope for Phase 9B.  We synthesize
  a minimal chain at render time from the link's own fields plus
  the holding metadata so the frontend can render a uniform UI.
* Malformed or missing payloads degrade gracefully into an
  ``origin="unknown"`` chain with no rationale — they are never
  dropped and never crash the response.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from src.intelligence.factors.taxonomy import get_factor as get_factor_definition

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalized chain shape
# ---------------------------------------------------------------------------


class NormalizedChain(TypedDict, total=False):
    """Shared causal-chain shape emitted by the API and consumed by the UI.

    ``origin`` is the discriminator:
      * ``"deterministic_factor"`` — Phase 9A factor-driven chain from
        ``EventLink.details_json``.
      * ``"direct_match"`` — legacy direct link (ticker / company /
        sector+geo), synthesized from row fields at render time.
      * ``"llm_screen"`` — Pass-2 macro LLM screen from
        ``CollectionAgent._macro_screen_events`` (rationale not
        currently persisted; origin is tagged so the UI can say so).
      * ``"unknown"`` — fallback for rows whose link_type we don't
        recognise or whose payload failed validation.
    """

    # Discriminator
    origin: str

    # Link context (always present)
    link_type: str
    link_id: str

    # Channel — factor key for factor links, free label otherwise
    channel: str | None
    channel_label: str | None

    # Holding target (resolved by caller)
    holding_id: str | None
    holding_ticker: str | None
    holding_portfolio_id: str | None

    # Factor block (only populated for origin="deterministic_factor")
    factor_key: str | None
    factor_label: str | None
    factor_direction: str | None
    factor_magnitude: str | None
    factor_confidence: float | None

    # Sensitivity block (deterministic factor origin only)
    sensitivity_value: float | None
    sensitivity_source: str | None
    sensitivity_sector: str | None

    # Expected effect on the holding
    effect_direction: str | None  # positive | negative | unclear | None
    effect_confidence: float | None

    # Rationale — short, human-readable bullets
    rationale: list[str]

    # Human one-line summary the UI can render directly
    summary: str


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_VALID_DIRECTIONS = frozenset({"up", "down", "unknown", None})
_VALID_EFFECTS = frozenset({"positive", "negative", "unclear", None})
_VALID_MAGNITUDES = frozenset({"minor", "moderate", "major", "extreme", "unknown", None})


def normalize_chain_dict(payload: Any) -> dict | None:
    """Validate and normalize a raw Phase 9A factor ``details_json``.

    Accepts either a JSON string or an already-parsed dict.  Returns
    a plain dict with the expected top-level keys filled in (possibly
    None) when the payload at least looks like a Phase 9A factor chain;
    returns ``None`` if the payload is syntactically invalid or
    missing the minimum keys.

    This function does NOT build a full ``NormalizedChain`` — use
    ``build_chain_for_link`` for that.  It exists so other callers
    (tests, future rendering code) can safely sanity-check a factor
    payload.  For Phase 9D relationship payloads, see
    ``normalize_relationship_chain_dict``.
    """
    parsed = _parse_chain_payload(payload)
    if parsed is None:
        return None

    factor_block = parsed.get("factor")
    holding_block = parsed.get("holding")
    if not isinstance(factor_block, dict) or not isinstance(holding_block, dict):
        return None
    if "key" not in factor_block or "id" not in holding_block:
        return None

    # Validate enum fields
    if factor_block.get("direction") not in _VALID_DIRECTIONS:
        return None
    if factor_block.get("magnitude") not in _VALID_MAGNITUDES:
        return None
    effect = parsed.get("expected_effect")
    if effect is not None and not isinstance(effect, dict):
        return None
    if isinstance(effect, dict) and effect.get("direction") not in _VALID_EFFECTS:
        return None

    return parsed


def normalize_relationship_chain_dict(payload: Any) -> dict | None:
    """Validate and normalize a raw Phase 9D relationship ``details_json``.

    Shape expected (produced by ``RelationshipImpact.to_details_json``):

    ::

        {
          "event": {"id": ..., "title": ...},
          "related_entity": {"key": ..., "ticker": ..., "name": ...,
                             "matched_value": ..., "match_type": ...,
                             "match_score": ...},
          "relationship": {"id": ..., "type": ..., "strength": ...},
          "holding":  {"id": ..., "ticker": ..., "portfolio_id": ...},
          "expected_effect": {"direction": ..., "confidence": ...},
          "rationale": [...],
        }

    Returns a plain dict if the minimum keys are present and the
    enum fields are sane; otherwise returns ``None``.  Malformed
    payloads degrade to ``origin="relationship"`` with null
    metadata — they are never dropped.
    """
    parsed = _parse_chain_payload(payload)
    if parsed is None:
        return None

    rel_block = parsed.get("relationship")
    entity_block = parsed.get("related_entity")
    holding_block = parsed.get("holding")
    if (
        not isinstance(rel_block, dict)
        or not isinstance(entity_block, dict)
        or not isinstance(holding_block, dict)
    ):
        return None
    if "type" not in rel_block:
        return None
    if "id" not in holding_block:
        return None

    effect = parsed.get("expected_effect")
    if effect is not None and not isinstance(effect, dict):
        return None
    if isinstance(effect, dict) and effect.get("direction") not in _VALID_EFFECTS:
        return None

    return parsed


def _parse_chain_payload(payload: Any) -> dict | None:
    """Accept JSON string or dict, return a dict or ``None``."""
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(payload, dict):
        parsed = payload
    else:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Chain construction
# ---------------------------------------------------------------------------


def build_chain_for_link(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    impact_channel: str | None,
    link_source: str | None,
    channel: str | None,
    details_json: str | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
    event_title: str | None = None,
) -> NormalizedChain:
    """Build a NormalizedChain for any EventLink row.

    Caller responsibilities: resolve ``link_target`` to a
    ``holding_ticker`` and ``holding_portfolio_id`` via a single bulk
    query and pass them in (we never hit the DB here, so this stays
    a pure render-time transform).

    Returns a ``NormalizedChain`` — never raises, never returns None.
    Malformed payloads degrade to ``origin="unknown"``.
    """
    # --- Deterministic factor (Phase 9A) -------------------------------
    if link_type == "macro_factor":
        return _build_factor_chain(
            link_id=link_id,
            link_type=link_type,
            link_target=link_target,
            relevance_score=relevance_score,
            impact_channel=impact_channel,
            channel=channel,
            details_json=details_json,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
            event_title=event_title,
        )

    # --- Deterministic relationship (Phase 9D) ------------------------
    if link_type == "relationship":
        return _build_relationship_chain(
            link_id=link_id,
            link_type=link_type,
            link_target=link_target,
            relevance_score=relevance_score,
            channel=channel,
            impact_channel=impact_channel,
            details_json=details_json,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
        )

    # --- Legacy direct matches ----------------------------------------
    if link_type in ("ticker_match", "sector_geo_match"):
        return _build_direct_chain(
            link_id=link_id,
            link_type=link_type,
            link_target=link_target,
            relevance_score=relevance_score,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
        )

    # --- LLM Pass-2 screen --------------------------------------------
    if link_type == "macro_screen":
        return _build_macro_screen_chain(
            link_id=link_id,
            link_type=link_type,
            link_target=link_target,
            relevance_score=relevance_score,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
        )

    # --- Unknown link type --------------------------------------------
    return _build_unknown_chain(
        link_id=link_id,
        link_type=link_type,
        link_target=link_target,
        relevance_score=relevance_score,
        channel=channel or impact_channel,
        holding_ticker=holding_ticker,
        holding_portfolio_id=holding_portfolio_id,
    )


# ---------------------------------------------------------------------------
# Origin-specific builders
# ---------------------------------------------------------------------------


def _build_factor_chain(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    impact_channel: str | None,
    channel: str | None,
    details_json: str | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
    event_title: str | None,
) -> NormalizedChain:
    """Build a normalized chain from a Phase 9A macro_factor link."""

    parsed = normalize_chain_dict(details_json)
    if parsed is None:
        # Payload missing or malformed — degrade without crashing.
        logger.debug(
            "macro_factor link %s has invalid details_json; falling back",
            link_id,
        )
        factor_key = channel or impact_channel
        defn = get_factor_definition(factor_key) if factor_key else None
        return NormalizedChain(
            origin="deterministic_factor",
            link_type=link_type,
            link_id=link_id,
            channel=factor_key,
            channel_label=defn.label if defn else factor_key,
            holding_id=link_target,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
            factor_key=factor_key,
            factor_label=defn.label if defn else factor_key,
            factor_direction=None,
            factor_magnitude=None,
            factor_confidence=None,
            sensitivity_value=None,
            sensitivity_source=None,
            sensitivity_sector=None,
            effect_direction=None,
            effect_confidence=relevance_score,
            rationale=[],
            summary=_factor_fallback_summary(factor_key, holding_ticker),
        )

    factor_block = parsed.get("factor", {}) or {}
    holding_block = parsed.get("holding", {}) or {}
    sensitivity_block = parsed.get("sensitivity", {}) or {}
    effect_block = parsed.get("expected_effect", {}) or {}

    factor_key = factor_block.get("key") or channel or impact_channel
    defn = get_factor_definition(factor_key) if factor_key else None
    factor_label = defn.label if defn else factor_key

    factor_direction = factor_block.get("direction")
    factor_magnitude = factor_block.get("magnitude")
    factor_confidence = _safe_float(factor_block.get("confidence"))

    sensitivity_value = _safe_float(sensitivity_block.get("value"))
    sensitivity_source = sensitivity_block.get("source")
    sensitivity_sector = sensitivity_block.get("sector")

    effect_direction = effect_block.get("direction")
    effect_confidence = _safe_float(effect_block.get("confidence"))
    if effect_confidence is None:
        effect_confidence = relevance_score

    rationale_raw = factor_block.get("rationale")
    if isinstance(rationale_raw, list):
        rationale = [str(r) for r in rationale_raw if r][:6]
    else:
        rationale = []

    summary = _factor_summary(
        factor_label=factor_label or factor_key or "macro factor",
        factor_direction=factor_direction,
        factor_magnitude=factor_magnitude,
        holding_ticker=holding_ticker or holding_block.get("ticker"),
        effect_direction=effect_direction,
        sensitivity_source=sensitivity_source,
    )

    return NormalizedChain(
        origin="deterministic_factor",
        link_type=link_type,
        link_id=link_id,
        channel=factor_key,
        channel_label=factor_label,
        holding_id=link_target,
        holding_ticker=holding_ticker or holding_block.get("ticker"),
        holding_portfolio_id=holding_portfolio_id or holding_block.get("portfolio_id"),
        factor_key=factor_key,
        factor_label=factor_label,
        factor_direction=factor_direction,
        factor_magnitude=factor_magnitude,
        factor_confidence=factor_confidence,
        sensitivity_value=sensitivity_value,
        sensitivity_source=sensitivity_source,
        sensitivity_sector=sensitivity_sector,
        effect_direction=effect_direction,
        effect_confidence=effect_confidence,
        rationale=rationale,
        summary=summary,
    )


def _build_direct_chain(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
) -> NormalizedChain:
    """Minimal normalized chain for legacy direct-match links.

    No stored chain exists for these rows.  We synthesize the
    smallest honest representation from the row fields themselves:
    the link type is the "reason", the holding is the target, and
    the relevance score stands in for confidence.  ``rationale`` is
    a single short explanation string that matches the link type.
    """
    channel_label, reason = _direct_link_labels(link_type, holding_ticker)

    return NormalizedChain(
        origin="direct_match",
        link_type=link_type,
        link_id=link_id,
        channel=link_type,
        channel_label=channel_label,
        holding_id=link_target,
        holding_ticker=holding_ticker,
        holding_portfolio_id=holding_portfolio_id,
        factor_key=None,
        factor_label=None,
        factor_direction=None,
        factor_magnitude=None,
        factor_confidence=None,
        sensitivity_value=None,
        sensitivity_source=None,
        sensitivity_sector=None,
        effect_direction=None,
        effect_confidence=relevance_score,
        rationale=[reason],
        summary=reason,
    )


def _build_relationship_chain(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    channel: str | None,
    impact_channel: str | None,
    details_json: str | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
) -> NormalizedChain:
    """Build a normalized chain from a Phase 9D relationship link.

    The structured payload (from ``RelationshipImpact.to_details_json``)
    is the primary source of truth.  When the payload is missing or
    malformed, we degrade gracefully to a minimal chain that still
    labels the origin as ``"relationship"``, preserving auditability.
    """
    parsed = normalize_relationship_chain_dict(details_json)
    if parsed is None:
        logger.debug(
            "relationship link %s has invalid details_json; falling back",
            link_id,
        )
        rel_type = channel or impact_channel
        return NormalizedChain(
            origin="relationship",
            link_type=link_type,
            link_id=link_id,
            channel=rel_type,
            channel_label=_relationship_type_label(rel_type),
            holding_id=link_target,
            holding_ticker=holding_ticker,
            holding_portfolio_id=holding_portfolio_id,
            factor_key=None,
            factor_label=None,
            factor_direction=None,
            factor_magnitude=None,
            factor_confidence=None,
            sensitivity_value=None,
            sensitivity_source=None,
            sensitivity_sector=None,
            effect_direction=None,
            effect_confidence=relevance_score,
            rationale=[],
            summary=_relationship_fallback_summary(rel_type, holding_ticker),
        )

    rel_block = parsed.get("relationship", {}) or {}
    entity_block = parsed.get("related_entity", {}) or {}
    holding_block = parsed.get("holding", {}) or {}
    effect_block = parsed.get("expected_effect", {}) or {}

    rel_type = rel_block.get("type") or channel or impact_channel
    rel_type_label = _relationship_type_label(rel_type)
    strength = _safe_float(rel_block.get("strength"))

    match_score = _safe_float(entity_block.get("match_score"))
    match_type = entity_block.get("match_type")
    related_name = entity_block.get("name")
    related_ticker = entity_block.get("ticker")
    related_entity_key = entity_block.get("entity_key")

    effect_direction = effect_block.get("direction")
    effect_confidence = _safe_float(effect_block.get("confidence"))
    if effect_confidence is None:
        effect_confidence = relevance_score

    rationale_raw = parsed.get("rationale")
    if isinstance(rationale_raw, list):
        rationale = [str(r) for r in rationale_raw if r][:6]
    else:
        rationale = []

    summary = _relationship_summary(
        rel_type_label=rel_type_label or rel_type or "relationship",
        related_name=related_name,
        related_ticker=related_ticker,
        related_entity_key=related_entity_key,
        holding_ticker=holding_ticker or holding_block.get("ticker"),
        match_type=match_type,
    )

    # Use ``channel`` to carry the relationship type string so the UI
    # badge matches the Phase 9B contract (factor links put the
    # factor key in ``channel``; relationship links put the type).
    return NormalizedChain(
        origin="relationship",
        link_type=link_type,
        link_id=link_id,
        channel=rel_type,
        channel_label=rel_type_label,
        holding_id=link_target,
        holding_ticker=holding_ticker or holding_block.get("ticker"),
        holding_portfolio_id=holding_portfolio_id or holding_block.get("portfolio_id"),
        factor_key=None,
        factor_label=None,
        factor_direction=None,
        factor_magnitude=None,
        factor_confidence=None,
        sensitivity_value=strength,
        sensitivity_source=match_type,
        sensitivity_sector=None,
        effect_direction=effect_direction,
        effect_confidence=effect_confidence,
        rationale=rationale,
        summary=summary,
    )


def _build_macro_screen_chain(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
) -> NormalizedChain:
    """LLM Pass-2 screen — rationale is not persisted today."""
    reason = (
        f"LLM macro screen inferred an indirect impact on "
        f"{holding_ticker or 'this holding'}."
    )
    return NormalizedChain(
        origin="llm_screen",
        link_type=link_type,
        link_id=link_id,
        channel="macro_screen",
        channel_label="LLM macro screen",
        holding_id=link_target,
        holding_ticker=holding_ticker,
        holding_portfolio_id=holding_portfolio_id,
        factor_key=None,
        factor_label=None,
        factor_direction=None,
        factor_magnitude=None,
        factor_confidence=None,
        sensitivity_value=None,
        sensitivity_source=None,
        sensitivity_sector=None,
        effect_direction=None,
        effect_confidence=relevance_score,
        rationale=[reason],
        summary=reason,
    )


def _build_unknown_chain(
    *,
    link_id: str,
    link_type: str,
    link_target: str,
    relevance_score: float | None,
    channel: str | None,
    holding_ticker: str | None,
    holding_portfolio_id: str | None,
) -> NormalizedChain:
    summary = (
        f"Unrecognised link type {link_type!r}" +
        (f" on {holding_ticker}." if holding_ticker else ".")
    )
    return NormalizedChain(
        origin="unknown",
        link_type=link_type,
        link_id=link_id,
        channel=channel,
        channel_label=channel,
        holding_id=link_target,
        holding_ticker=holding_ticker,
        holding_portfolio_id=holding_portfolio_id,
        factor_key=None,
        factor_label=None,
        factor_direction=None,
        factor_magnitude=None,
        factor_confidence=None,
        sensitivity_value=None,
        sensitivity_source=None,
        sensitivity_sector=None,
        effect_direction=None,
        effect_confidence=relevance_score,
        rationale=[],
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


_DIRECT_LABELS: dict[str, tuple[str, str]] = {
    "ticker_match": (
        "Ticker match",
        "Ticker {ticker} mentioned directly in the event text.",
    ),
    "sector_geo_match": (
        "Sector × geography match",
        "{ticker} sits in a sector and geography both mentioned by this event.",
    ),
}


def _direct_link_labels(link_type: str, ticker: str | None) -> tuple[str, str]:
    label, template = _DIRECT_LABELS.get(
        link_type,
        (link_type.replace("_", " ").title(), "Direct link via {link_type}."),
    )
    reason = template.format(
        ticker=ticker or "This holding",
        link_type=link_type,
    )
    return label, reason


_DIRECTION_WORDS = {
    "up": "rose",
    "down": "fell",
    "unknown": "moved",
}

_EFFECT_WORDS = {
    "positive": "a positive effect",
    "negative": "a negative effect",
    "unclear": "an unclear effect",
}


def _factor_summary(
    *,
    factor_label: str,
    factor_direction: str | None,
    factor_magnitude: str | None,
    holding_ticker: str | None,
    effect_direction: str | None,
    sensitivity_source: str | None,
) -> str:
    """One-line deterministic summary for the factor chain."""
    ticker = holding_ticker or "this holding"
    verb = _DIRECTION_WORDS.get(factor_direction or "unknown", "moved")
    magnitude_str = (
        f" ({factor_magnitude})"
        if factor_magnitude and factor_magnitude != "unknown"
        else ""
    )
    effect_str = _EFFECT_WORDS.get(effect_direction or "unclear", "an unclear effect")
    source_str = ""
    if sensitivity_source and sensitivity_source != "zero":
        source_str = f" [{sensitivity_source} sensitivity]"
    return (
        f"{factor_label} {verb}{magnitude_str} → {effect_str} on {ticker}"
        f"{source_str}."
    )


def _factor_fallback_summary(
    factor_key: str | None,
    holding_ticker: str | None,
) -> str:
    ticker = holding_ticker or "this holding"
    factor = factor_key or "macro factor"
    return f"{factor} touchpoint on {ticker} (structured chain unavailable)."


# ---------------------------------------------------------------------------
# Phase 9D — relationship label/summary helpers
# ---------------------------------------------------------------------------


_RELATIONSHIP_LABELS: dict[str, str] = {
    "supplier":    "Supplier relationship",
    "customer":    "Customer relationship",
    "competitor":  "Competitor",
    "regulator":   "Regulator",
    "parent":      "Parent company",
    "subsidiary":  "Subsidiary",
}


def _relationship_type_label(rel_type: str | None) -> str | None:
    if not rel_type:
        return None
    return _RELATIONSHIP_LABELS.get(rel_type, rel_type.replace("_", " ").title())


def _relationship_summary(
    *,
    rel_type_label: str,
    related_name: str | None,
    related_ticker: str | None,
    related_entity_key: str | None,
    holding_ticker: str | None,
    match_type: str | None,
) -> str:
    """One-line deterministic summary for a relationship chain."""
    ticker = holding_ticker or "this holding"
    related = (
        related_name
        or related_ticker
        or related_entity_key
        or "related entity"
    )
    via = f" via {match_type} match" if match_type else ""
    return (
        f"{rel_type_label} with {related}{via} — "
        f"event affecting {related} propagates to {ticker}."
    )


def _relationship_fallback_summary(
    rel_type: str | None,
    holding_ticker: str | None,
) -> str:
    ticker = holding_ticker or "this holding"
    label = _relationship_type_label(rel_type) or "Relationship"
    return f"{label} touchpoint on {ticker} (structured chain unavailable)."
