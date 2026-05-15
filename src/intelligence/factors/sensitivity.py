"""Holding factor sensitivities — manual, inferred, or sector default.

This module is the single source of truth for translating a
``(holding, factor)`` pair into a signed sensitivity weight in
``[-1, 1]``.  The resolver layers three sources, in strict priority
order:

1. Explicit row in ``holding_factor_sensitivities`` (``source`` may
   be ``manual``, ``ai_inferred``, or ``default``).
2. Sector-based prior from :data:`SECTOR_PRIORS`.
3. Zero — and the caller is expected to skip propagation entirely.

The layering is deliberate: the conservative defaults cover most
portfolios out-of-the-box, while operator overrides (manual rows)
always win so the system is tunable per-portfolio without code
changes.

The sector key-space uses **canonical lowercase** names matching the
``SECTOR_TAXONOMY`` in ``src/security_master/classifier.py``.  The
``normalize_sector`` helper also recognises the GICS-style strings
used by the fallback classifier (``"Information Technology"``) so
either source of sector metadata works.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sector priors
# ---------------------------------------------------------------------------
#
# Keyed by canonical lowercase sector name.  Factor keys match
# ``taxonomy.FACTOR_KEYS`` exactly.  Any factor not listed for a
# sector defaults to 0.0 (no first-order exposure).
#
# These priors are intentionally conservative.  They are not
# point-estimates of factor betas — they are "does this sector
# reasonably move with this factor?" tiebreakers.  Operators who
# want tighter numbers should populate the
# ``holding_factor_sensitivities`` table with manual rows, which
# always win.

SECTOR_PRIORS: dict[str, dict[str, float]] = {
    # From the Phase 9A brief
    "technology": {
        "interest_rate": -0.6,
        "inflation": -0.3,
        "credit_conditions": -0.3,
        "oil_energy": -0.1,
        "usd_fx": 0.1,
        "trade_policy": -0.3,
        "geopolitical_risk": -0.2,
        "regulation_policy": -0.3,
        "technology_cycle": 0.7,
        "consumer_demand": 0.2,
    },
    "financials": {
        "interest_rate": 0.3,
        "inflation": 0.1,
        "credit_conditions": -0.6,
        "oil_energy": 0.0,
        "usd_fx": 0.0,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.2,
        "regulation_policy": -0.4,
        "consumer_demand": 0.3,
    },
    "energy": {
        "interest_rate": 0.1,
        "inflation": 0.2,
        "credit_conditions": -0.2,
        "oil_energy": 0.8,
        "usd_fx": 0.1,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.2,
        "regulation_policy": -0.2,
    },
    "industrials": {
        "interest_rate": -0.2,
        "inflation": -0.4,
        "credit_conditions": -0.3,
        "oil_energy": -0.2,
        "usd_fx": 0.2,
        "trade_policy": -0.5,
        "geopolitical_risk": -0.3,
        "consumer_demand": 0.3,
    },
    "utilities": {
        "interest_rate": -0.5,
        "inflation": -0.3,
        "credit_conditions": -0.2,
        "oil_energy": -0.1,
        "usd_fx": 0.0,
        "trade_policy": -0.1,
        "geopolitical_risk": -0.1,
    },
    # Extensions beyond the brief — conservative, same priors shape.
    # Added so common sectors aren't silently zeroed-out.
    "healthcare": {
        "interest_rate": -0.2,
        "inflation": -0.2,
        "credit_conditions": -0.2,
        "oil_energy": 0.0,
        "usd_fx": 0.1,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.1,
        "regulation_policy": -0.5,
        "consumer_demand": 0.2,
    },
    "health care": {  # GICS variant spelling
        "interest_rate": -0.2,
        "inflation": -0.2,
        "credit_conditions": -0.2,
        "oil_energy": 0.0,
        "usd_fx": 0.1,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.1,
        "regulation_policy": -0.5,
        "consumer_demand": 0.2,
    },
    "consumer discretionary": {
        "interest_rate": -0.4,
        "inflation": -0.4,
        "credit_conditions": -0.4,
        "oil_energy": -0.3,
        "usd_fx": 0.1,
        "trade_policy": -0.3,
        "geopolitical_risk": -0.2,
        "consumer_demand": 0.7,
    },
    "consumer staples": {
        "interest_rate": -0.2,
        "inflation": -0.2,
        "credit_conditions": -0.2,
        "oil_energy": -0.2,
        "usd_fx": 0.1,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.1,
        "consumer_demand": 0.3,
    },
    "materials": {
        "interest_rate": -0.2,
        "inflation": 0.2,
        "credit_conditions": -0.3,
        "oil_energy": 0.1,
        "usd_fx": 0.2,
        "trade_policy": -0.4,
        "geopolitical_risk": -0.3,
    },
    "communication services": {
        "interest_rate": -0.3,
        "inflation": -0.2,
        "credit_conditions": -0.2,
        "oil_energy": 0.0,
        "usd_fx": 0.1,
        "trade_policy": -0.2,
        "geopolitical_risk": -0.1,
        "regulation_policy": -0.4,
        "technology_cycle": 0.4,
        "consumer_demand": 0.2,
    },
    "real estate": {
        "interest_rate": -0.6,
        "inflation": -0.2,
        "credit_conditions": -0.4,
        "oil_energy": 0.0,
        "usd_fx": 0.0,
        "trade_policy": -0.1,
        "geopolitical_risk": -0.1,
    },
}


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------

# Map the GICS-ish labels used by ``agents/fallbacks.py`` and other
# data paths into the lowercase keys used in :data:`SECTOR_PRIORS`.
_SECTOR_ALIASES: dict[str, str] = {
    # technology
    "technology": "technology",
    "information technology": "technology",
    "info tech": "technology",
    "it": "technology",
    # financials
    "financials": "financials",
    "financial": "financials",
    "finance": "financials",
    # healthcare
    "healthcare": "healthcare",
    "health care": "health care",
    "health": "healthcare",
    # consumer
    "consumer discretionary": "consumer discretionary",
    "consumer staples": "consumer staples",
    # energy / utilities / materials / industrials
    "energy": "energy",
    "utilities": "utilities",
    "materials": "materials",
    "industrials": "industrials",
    # communication services
    "communication services": "communication services",
    "telecommunication services": "communication services",
    "telecom": "communication services",
    "media": "communication services",
    # real estate
    "real estate": "real estate",
    "reits": "real estate",
}


def normalize_sector(sector: str | None) -> str | None:
    """Return a canonical sector key or None if unrecognised."""
    if not sector:
        return None
    key = sector.strip().lower()
    return _SECTOR_ALIASES.get(key, key if key in SECTOR_PRIORS else None)


# ---------------------------------------------------------------------------
# Resolved sensitivity result type
# ---------------------------------------------------------------------------


@dataclass
class ResolvedSensitivity:
    """Result of resolving a (holding, factor) sensitivity lookup."""

    value: float          # in [-1, 1]
    source: str           # "manual" | "ai_inferred" | "default" | "zero"
    sector: str | None    # canonical sector used for default lookup, if any


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class SensitivityResolver:
    """Resolves factor sensitivities for holdings.

    Usage::

        resolver = SensitivityResolver(manual_rows)  # from DB
        sens = resolver.resolve(
            holding_id="h1",
            factor="interest_rate",
            sector="Information Technology",
        )
        if abs(sens.value) < 0.25:
            skip_propagation()
    """

    def __init__(
        self,
        manual_overrides: Iterable[tuple[str, str, float, str]] | None = None,
    ) -> None:
        """Build the resolver.

        Parameters
        ----------
        manual_overrides:
            Iterable of ``(holding_id, factor, sensitivity, source)``
            tuples from ``holding_factor_sensitivities``.  Rows with
            ``source="default"`` are treated as overrides too — the
            presence of the row means someone explicitly wrote it.
        """
        self._overrides: dict[tuple[str, str], tuple[float, str]] = {}
        if manual_overrides:
            for holding_id, factor, sens, src in manual_overrides:
                try:
                    sens_f = float(sens)
                except (TypeError, ValueError):
                    continue
                # Clamp to [-1, 1] defensively
                sens_f = max(-1.0, min(1.0, sens_f))
                self._overrides[(holding_id, factor)] = (sens_f, src or "default")

    def resolve(
        self,
        holding_id: str,
        factor: str,
        sector: str | None,
    ) -> ResolvedSensitivity:
        """Resolve the sensitivity of *holding_id* to *factor*.

        Returns a ``ResolvedSensitivity`` with ``source="zero"`` when
        no data is available; the caller is responsible for treating
        those as "skip, do not emit".
        """
        # 1. Explicit override
        ovr = self._overrides.get((holding_id, factor))
        if ovr is not None:
            return ResolvedSensitivity(value=ovr[0], source=ovr[1], sector=normalize_sector(sector))

        # 2. Sector default
        canon = normalize_sector(sector)
        if canon is not None:
            priors = SECTOR_PRIORS.get(canon, {})
            if factor in priors:
                return ResolvedSensitivity(
                    value=priors[factor], source="default", sector=canon,
                )

        # 3. Nothing — caller will skip
        return ResolvedSensitivity(value=0.0, source="zero", sector=canon)
