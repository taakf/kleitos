"""Seed relationship registry loader (Phase 9D).

Reads ``config/relationships.yaml`` into a list of immutable
``SeedRelationship`` dataclasses.  The loader validates every row
conservatively and drops (with a warning) anything that would make
the runtime unsafe:

* missing holding ticker
* missing relationship type
* unknown relationship type
* missing both ``related_ticker`` and ``related_entity_key``
* strength outside [0.0, 1.0]

This file is **read-only** at runtime.  The loader does NOT write
to the database — upserting into ``holding_relationships`` is a
separate operation that takes ``(ticker → holding_id)`` mapping,
because tickers alone can't resolve to portfolio-scoped holding
UUIDs without looking at the live portfolio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import yaml

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


#: Path to the repo-managed seed registry.  Kept resolvable from
#: ``PROJECT_ROOT`` so the loader works regardless of the caller's
#: current working directory.
RELATIONSHIPS_YAML_PATH: Path = PROJECT_ROOT / "config" / "relationships.yaml"


#: Valid relationship types.  Kept in sync with the brief's minimum
#: list.  Any type outside this set is dropped by the loader with a
#: warning so a typo can't pollute the runtime.
VALID_RELATIONSHIP_TYPES: frozenset[str] = frozenset({
    "supplier",
    "customer",
    "competitor",
    "regulator",
    "parent",
    "subsidiary",
})


@dataclass(frozen=True)
class SeedRelationship:
    """One relationship edge as authored in ``config/relationships.yaml``.

    This is the unresolved form: the holding side is still a ticker
    string (``ticker``), not a holding UUID.  Resolving to UUIDs is
    the caller's job because the mapping is portfolio-dependent.
    """

    ticker: str                    # the HELD company's ticker
    relationship_type: str          # supplier|customer|competitor|regulator|parent|subsidiary
    related_ticker: str | None
    related_entity_key: str | None
    related_name: str | None
    strength: float                 # [0.0, 1.0]
    description: str | None = None

    def normalized(self) -> "SeedRelationship":
        """Return a copy with tickers uppercased + strength clamped."""
        return replace(
            self,
            ticker=(self.ticker or "").strip().upper(),
            related_ticker=(self.related_ticker.strip().upper()
                            if self.related_ticker else None),
            related_entity_key=(self.related_entity_key.strip().lower()
                                if self.related_entity_key else None),
            strength=max(0.0, min(1.0, float(self.strength))),
        )


def load_seed_relationships(
    path: Path | str | None = None,
) -> list[SeedRelationship]:
    """Load and validate the seed registry.

    Returns a list of ``SeedRelationship`` in file order.  Rows that
    fail validation are dropped with a warning — the loader NEVER
    raises so a broken seed file can't take down collection.

    Parameters
    ----------
    path:
        Optional override for testing.  Defaults to
        ``config/relationships.yaml`` at the repo root.
    """
    target = Path(path) if path is not None else RELATIONSHIPS_YAML_PATH
    if not target.exists():
        logger.info(
            "Relationship seed file not found at %s — returning empty list",
            target,
        )
        return []

    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse relationship seed YAML: %s", exc)
        return []
    except OSError as exc:
        logger.error("Failed to read relationship seed YAML: %s", exc)
        return []

    if not isinstance(raw, dict):
        logger.error("Relationship seed YAML root is not a mapping")
        return []

    entries = raw.get("relationships")
    if not isinstance(entries, list):
        logger.info("Relationship seed YAML has no 'relationships' list")
        return []

    result: list[SeedRelationship] = []
    for idx, entry in enumerate(entries):
        seed = _coerce_entry(idx, entry)
        if seed is not None:
            result.append(seed)

    logger.info(
        "Loaded %d seed relationship(s) from %s (rejected %d)",
        len(result), target, len(entries) - len(result),
    )
    return result


def _coerce_entry(idx: int, entry: object) -> SeedRelationship | None:
    """Validate a single YAML entry and return a normalized seed row."""
    if not isinstance(entry, dict):
        logger.warning("Relationship seed entry #%d is not a mapping", idx)
        return None

    ticker = entry.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        logger.warning("Relationship seed entry #%d missing 'ticker'", idx)
        return None

    rel_type = entry.get("type")
    if not isinstance(rel_type, str):
        logger.warning(
            "Relationship seed entry #%d missing 'type' (ticker=%s)", idx, ticker,
        )
        return None
    rel_type = rel_type.strip().lower()
    if rel_type not in VALID_RELATIONSHIP_TYPES:
        logger.warning(
            "Relationship seed entry #%d has unknown type %r (valid: %s)",
            idx, rel_type, sorted(VALID_RELATIONSHIP_TYPES),
        )
        return None

    related_ticker = entry.get("related_ticker")
    related_entity_key = entry.get("related_entity_key")
    related_name = entry.get("related_name")

    if not (related_ticker or related_entity_key):
        logger.warning(
            "Relationship seed entry #%d has neither related_ticker nor "
            "related_entity_key (ticker=%s, type=%s)",
            idx, ticker, rel_type,
        )
        return None

    strength_raw = entry.get("strength", 0.5)
    try:
        strength = float(strength_raw)
    except (ValueError, TypeError):
        logger.warning(
            "Relationship seed entry #%d has invalid strength %r",
            idx, strength_raw,
        )
        return None
    if not (0.0 <= strength <= 1.0):
        logger.warning(
            "Relationship seed entry #%d strength %.3f outside [0, 1]",
            idx, strength,
        )
        return None

    seed = SeedRelationship(
        ticker=ticker,
        relationship_type=rel_type,
        related_ticker=related_ticker if isinstance(related_ticker, str) else None,
        related_entity_key=related_entity_key if isinstance(related_entity_key, str) else None,
        related_name=related_name if isinstance(related_name, str) else None,
        strength=strength,
        description=entry.get("description") if isinstance(entry.get("description"), str) else None,
    )
    return seed.normalized()


def group_by_holding_ticker(
    seeds: Iterable[SeedRelationship],
) -> dict[str, list[SeedRelationship]]:
    """Group seed rows by the HELD company's ticker for efficient lookup."""
    result: dict[str, list[SeedRelationship]] = {}
    for seed in seeds:
        result.setdefault(seed.ticker, []).append(seed)
    return result
