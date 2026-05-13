"""Deterministic relationship graph (Phase 9D).

Runtime layer that turns structured ``holding_relationships`` rows
(seeded from ``config/relationships.yaml``) into explainable
indirect-impact EventLinks.  Three cooperating pieces:

* ``seeds``        — load the YAML registry and build normalized
                     ``SeedRelationship`` dataclasses.
* ``matcher``      — deterministic detector that finds mentions of
                     related external entities in event text.
* ``propagation``  — turns entity matches + DB relationship rows
                     into ``RelationshipImpact`` hypotheses with
                     bounded confidence and structured causal chains.

Everything here is deterministic.  No LLM, no network, no entity
linker — just narrow regex matching on ticker/name + DB join +
explicit confidence math.  Given identical inputs, the runtime
produces byte-identical outputs across runs.
"""

from src.intelligence.relationships.seeds import (
    SeedRelationship,
    load_seed_relationships,
    RELATIONSHIPS_YAML_PATH,
)
from src.intelligence.relationships.matcher import (
    EntityMatch,
    RelationshipEntityMatcher,
)
from src.intelligence.relationships.propagation import (
    RELATIONSHIP_LINK_TYPE_WEIGHTS,
    RELATIONSHIP_MAX_CONFIDENCE,
    RELATIONSHIP_MIN_EMIT,
    RelationshipImpact,
    RelationshipPropagator,
    propagate_relationship_impacts,
)
from src.intelligence.relationships.reconciler import (
    ReconcileStats,
    reconcile_seed_relationships,
)

__all__ = [
    "SeedRelationship",
    "load_seed_relationships",
    "RELATIONSHIPS_YAML_PATH",
    "EntityMatch",
    "RelationshipEntityMatcher",
    "RELATIONSHIP_LINK_TYPE_WEIGHTS",
    "RELATIONSHIP_MAX_CONFIDENCE",
    "RELATIONSHIP_MIN_EMIT",
    "RelationshipImpact",
    "RelationshipPropagator",
    "propagate_relationship_impacts",
    "ReconcileStats",
    "reconcile_seed_relationships",
]
