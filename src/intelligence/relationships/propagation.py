"""Relationship → holding propagation (Phase 9D).

Takes the deterministic entity matches produced by the matcher and
the per-holding relationship rows loaded from the DB, and emits
``RelationshipImpact`` hypotheses with:

* a bounded confidence that stays below the direct-match ceiling
* an explicit causal-chain dict ready for the Phase 9B chain
  normalizer
* the target holding UUID, portfolio_id, and ticker so the runtime
  can write an ``EventLink`` without any DB join

Confidence model (conservative, explicit, auditable — NOT
calibrated statistically)
-----------------------------------------------------------------
``p_relationship = clamp(
    match_score *
    strength *
    relationship_type_weight *
    indirectness_decay,
    0.05,
    RELATIONSHIP_MAX_CONFIDENCE,
)``

Where:

* ``match_score`` is the entity-detection confidence from the
  matcher (ticker title > ticker body > name title > name body)
* ``strength`` is the 0–1 strength from the seed row
* ``relationship_type_weight`` is a small per-type multiplier
* ``indirectness_decay`` is a fixed constant — relationships are
  always one hop indirect, so we pay a flat decay vs. direct links
* ``RELATIONSHIP_MAX_CONFIDENCE`` is the hard ceiling (< direct
  match's 1.0 by design)

The emission floor ``RELATIONSHIP_MIN_EMIT`` is set equal to the
factor-link emission floor (0.25) so the analysis pipeline applies
a consistent "meaningful exposure" bar across deterministic origins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from src.intelligence.relationships.matcher import EntityMatch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — tuned conservatively, to be revisited in a later
# Phase 9-series calibration step.
# ---------------------------------------------------------------------------

#: Hard ceiling on any relationship-link confidence.  Must stay
#: strictly below the direct-match ceiling (direct ticker matches
#: routinely hit 0.9–1.0).  We pick 0.60 — below the AnalysisAgent
#: generic 0.5 gate sometimes, above the honest factor floor 0.25
#: always.  Manual calibration can raise this later.
RELATIONSHIP_MAX_CONFIDENCE: float = 0.60

#: Persistence floor — below this, the propagator skips emission.
#: Set slightly BELOW the factor-link floor (0.25) because
#: relationship confidence structurally absorbs an indirectness
#: decay the factor path does not; a 0.20 floor is the practical
#: minimum for a conservative relationship with good evidence
#: (e.g. a competitor-type edge with ticker title match + 0.60
#: strength lands at ~0.24).  Still far from the direct-match
#: ceiling 1.0 and well above the 0.05 bottom clamp.
RELATIONSHIP_MIN_EMIT: float = 0.20

#: Flat indirectness decay applied to every relationship edge,
#: because a relationship path is always strictly one hop more
#: indirect than a direct ticker/name mention.
_INDIRECTNESS_DECAY: float = 0.75


#: Per-type weight.  Numbers are conservative and ordered by how
#: reliably the relationship type predicts a meaningful spillover.
#: ``supplier`` and ``customer`` dominate because they're direct
#: business-model dependencies; ``competitor`` is smaller because
#: the sign is often ambiguous; regulators are smaller still because
#: one action typically affects a whole sector.
RELATIONSHIP_LINK_TYPE_WEIGHTS: dict[str, float] = {
    "supplier":    0.80,
    "customer":    0.75,
    "parent":      0.85,
    "subsidiary":  0.85,
    "competitor":  0.55,
    "regulator":   0.50,
}


#: Expected-effect direction per relationship type.  This is NOT a
#: sign for the event itself (we don't know what the event did yet)
#: — it's the expected sign of the effect IF the related entity was
#: helped or hurt:
#:
#: - supplier: helped → good (holding benefits) / hurt → bad
#: - customer: helped → good (holding benefits from bigger customer)
#: - competitor: helped → bad (competitor wins at our expense) /
#:               hurt → good
#: - regulator: always "unclear" — regulator actions are too broad
#: - parent/subsidiary: co-moving → helped=good, hurt=bad
#:
#: For Phase 9D we keep the runtime honest: we don't try to infer
#: the event's own sign here (that's the factor pipeline's job), so
#: the runtime emits ``effect_direction = "unclear"`` unless the
#: relationship type has a clearly symmetric sign profile.
_RELATIONSHIP_DEFAULT_EFFECT: dict[str, str] = {
    "supplier":    "unclear",
    "customer":    "unclear",
    "parent":      "unclear",
    "subsidiary":  "unclear",
    "competitor":  "unclear",
    "regulator":   "unclear",
}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationshipRow:
    """Flattened view of a ``HoldingRelationship`` DB row.

    The runtime passes these into the propagator so the propagation
    logic stays DB-independent and unit-testable.
    """

    id: str
    holding_id: str
    ticker: str
    portfolio_id: str
    relationship_type: str
    related_ticker: str | None
    related_entity_key: str | None
    related_name: str | None
    strength: float
    source: str


@dataclass
class RelationshipImpact:
    """A single relationship-driven impact hypothesis.

    Each impact targets exactly one held holding, through exactly
    one relationship edge, triggered by exactly one entity match.
    Confidence is already bounded and ready to become the
    ``EventLink.relevance_score``.
    """

    relationship_id: str
    holding_id: str
    ticker: str
    portfolio_id: str
    relationship_type: str
    related_ticker: str | None
    related_entity_key: str | None
    related_name: str | None
    strength: float
    match: EntityMatch
    holding_confidence: float
    effect_direction: str
    rationale: list[str] = field(default_factory=list)

    def to_details_json(self, event_id: str, event_title: str) -> dict:
        """Return the structured causal chain for storage.

        Shape is consistent with the Phase 9B normalized-chain
        contract: event → related entity → relationship type →
        holding → expected effect.
        """
        return {
            "event": {
                "id": event_id,
                "title": (event_title or "")[:200],
            },
            "related_entity": {
                "key": (
                    self.related_ticker
                    or self.related_entity_key
                    or "?"
                ),
                "ticker": self.related_ticker,
                "entity_key": self.related_entity_key,
                "name": self.related_name,
                "matched_value": self.match.matched_value,
                "match_type": self.match.match_type,
                "match_score": round(self.match.match_score, 4),
            },
            "relationship": {
                "id": self.relationship_id,
                "type": self.relationship_type,
                "strength": round(self.strength, 4),
            },
            "holding": {
                "id": self.holding_id,
                "ticker": self.ticker,
                "portfolio_id": self.portfolio_id,
            },
            "expected_effect": {
                "direction": self.effect_direction,
                "confidence": round(self.holding_confidence, 4),
            },
            "rationale": list(self.rationale),
        }


# ---------------------------------------------------------------------------
# Propagator
# ---------------------------------------------------------------------------


class RelationshipPropagator:
    """Deterministic relationship → holding propagator.

    Stateless; holds no config beyond the constants at module top.
    """

    #: Public alias used by tests and the collection-agent caller.
    MIN_EMIT: float = RELATIONSHIP_MIN_EMIT
    MAX_CONFIDENCE: float = RELATIONSHIP_MAX_CONFIDENCE

    def propagate(
        self,
        *,
        entity_matches: Iterable[EntityMatch],
        relationships: Iterable[RelationshipRow],
    ) -> list[RelationshipImpact]:
        """Produce one ``RelationshipImpact`` per (match, relationship).

        Relationships without a matching entity are silently skipped.
        Impacts below ``MIN_EMIT`` are dropped in the caller, not
        here — this method returns every candidate so tests and
        future policy layers can inspect the full set.
        """
        matches_by_key: dict[str, EntityMatch] = {}
        for m in entity_matches:
            # First match wins for a given entity_key (the matcher
            # already keeps best-per-key).
            if m.entity_key not in matches_by_key:
                matches_by_key[m.entity_key] = m

        impacts: list[RelationshipImpact] = []
        for row in relationships:
            key = _row_entity_key(row)
            if key is None:
                continue
            match = matches_by_key.get(key)
            if match is None:
                continue
            impacts.append(self._build_impact(row, match))
        return impacts

    @staticmethod
    def _build_impact(
        row: RelationshipRow, match: EntityMatch,
    ) -> RelationshipImpact:
        type_weight = RELATIONSHIP_LINK_TYPE_WEIGHTS.get(
            row.relationship_type, 0.50,
        )
        raw_confidence = (
            match.match_score
            * max(0.0, min(1.0, row.strength))
            * type_weight
            * _INDIRECTNESS_DECAY
        )
        confidence = max(0.05, min(RELATIONSHIP_MAX_CONFIDENCE, round(raw_confidence, 4)))
        effect = _RELATIONSHIP_DEFAULT_EFFECT.get(row.relationship_type, "unclear")

        related_name_str = (
            row.related_name or row.related_ticker or row.related_entity_key or "?"
        )
        rel_phrasing = {
            "supplier":   f"{related_name_str} is a supplier to {row.ticker}",
            "customer":   f"{related_name_str} is a customer of {row.ticker}",
            "competitor": f"{related_name_str} is a competitor of {row.ticker}",
            "regulator":  f"{related_name_str} is a regulator of {row.ticker}",
            "parent":     f"{related_name_str} is the parent of {row.ticker}",
            "subsidiary": f"{related_name_str} is a subsidiary of {row.ticker}",
        }.get(
            row.relationship_type,
            f"{related_name_str} has a {row.relationship_type} relationship with {row.ticker}",
        )
        rationale = [
            (
                f"matched: {match.matched_value} "
                f"(via {match.match_type})"
            ),
            rel_phrasing,
            (
                f"strength={row.strength:.2f} × "
                f"type_weight={type_weight:.2f} × "
                f"match={match.match_score:.2f} × "
                f"decay={_INDIRECTNESS_DECAY:.2f}"
                f" → p={confidence:.2f}"
            ),
        ]

        return RelationshipImpact(
            relationship_id=row.id,
            holding_id=row.holding_id,
            ticker=row.ticker,
            portfolio_id=row.portfolio_id,
            relationship_type=row.relationship_type,
            related_ticker=row.related_ticker,
            related_entity_key=row.related_entity_key,
            related_name=row.related_name,
            strength=row.strength,
            match=match,
            holding_confidence=confidence,
            effect_direction=effect,
            rationale=rationale,
        )


def propagate_relationship_impacts(
    *,
    entity_matches: Iterable[EntityMatch],
    relationships: Iterable[RelationshipRow],
) -> list[RelationshipImpact]:
    """Module-level convenience wrapper for callers that don't want
    to instantiate the class."""
    return RelationshipPropagator().propagate(
        entity_matches=entity_matches,
        relationships=relationships,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_entity_key(row: RelationshipRow) -> str | None:
    """Return the stable key the matcher will use for this row.

    The matcher is fed a list of ``(entity_key, related_ticker,
    related_name)`` tuples; by convention we use the ticker when
    present and the non-ticker entity_key otherwise.  Matches come
    back keyed by the same string.
    """
    if row.related_ticker:
        return row.related_ticker.upper()
    if row.related_entity_key:
        return row.related_entity_key
    return None
