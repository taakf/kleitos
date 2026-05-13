"""Stable macro factor taxonomy for Phase 9A.

The keys in this module are the contract between the classifier, the
propagation engine, the sensitivity tables, and any future UI.  They
MUST remain stable across releases; adding a new factor is a minor
version bump, renaming or removing one is a breaking change.

Each factor has:
  * ``key``          — short, lowercase, snake_case identifier used
                        in the database and API (NEVER change).
  * ``label``        — human-readable display label.
  * ``description``  — one-line explanation suitable for tooltips.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorDefinition:
    """Immutable description of a single macro factor."""

    key: str
    label: str
    description: str


# Ordered so that UI lists and debug output stay stable.
FACTORS: tuple[FactorDefinition, ...] = (
    FactorDefinition(
        key="interest_rate",
        label="Interest Rates",
        description=(
            "Central-bank policy rates and sovereign yields. "
            "Direction 'up' means tightening / higher yields."
        ),
    ),
    FactorDefinition(
        key="inflation",
        label="Inflation",
        description=(
            "Consumer, producer, and wage inflation. "
            "Direction 'up' means hotter-than-expected prints."
        ),
    ),
    FactorDefinition(
        key="credit_conditions",
        label="Credit Conditions",
        description=(
            "Credit spreads, funding stress, rating actions, and "
            "default risk. Direction 'up' means wider spreads / "
            "tighter credit."
        ),
    ),
    FactorDefinition(
        key="oil_energy",
        label="Oil & Energy",
        description=(
            "Crude oil, natural gas, and refined-product pricing "
            "and physical supply. Direction 'up' means higher "
            "prices or supply disruption."
        ),
    ),
    FactorDefinition(
        key="usd_fx",
        label="USD / FX",
        description=(
            "Strength of the US dollar versus major peers. "
            "Direction 'up' means a stronger dollar."
        ),
    ),
    FactorDefinition(
        key="trade_policy",
        label="Trade Policy",
        description=(
            "Tariffs, duties, export controls, sanctions, embargoes, "
            "and bilateral trade agreements. Direction 'up' means "
            "more restrictive trade policy."
        ),
    ),
    FactorDefinition(
        key="geopolitical_risk",
        label="Geopolitical Risk",
        description=(
            "Armed conflict, mobilisation, strikes, and escalation "
            "events. Direction 'up' means escalation."
        ),
    ),
    FactorDefinition(
        key="regulation_policy",
        label="Regulation & Policy",
        description=(
            "Antitrust, enforcement, rulemaking, and broad policy "
            "shocks. Direction 'up' means tighter regulation / "
            "enforcement."
        ),
    ),
    FactorDefinition(
        key="consumer_demand",
        label="Consumer Demand",
        description=(
            "Retail sales, consumer confidence, discretionary "
            "spending, and employment effects. Direction 'up' "
            "means stronger demand / employment."
        ),
    ),
    FactorDefinition(
        key="technology_cycle",
        label="Technology Cycle",
        description=(
            "Semiconductor cycle, AI adoption waves, platform "
            "shifts, and major tech restrictions. Direction 'up' "
            "means a positive / expanding cycle."
        ),
    ),
)


# Fast lookup map and stable key list
_FACTOR_MAP: dict[str, FactorDefinition] = {f.key: f for f in FACTORS}
FACTOR_KEYS: tuple[str, ...] = tuple(f.key for f in FACTORS)


def get_factor(key: str) -> FactorDefinition | None:
    """Return the factor definition for *key* or None if unknown."""
    return _FACTOR_MAP.get(key)
