"""Deterministic macro factor reasoning (Phase 9A).

This package contains the four cooperating stages that turn a raw
news event into portfolio-safe, explainable factor-driven impact
hypotheses — all without any AI calls:

1. ``taxonomy``    — the stable factor vocabulary and labels.
2. ``classifier``  — regex/pattern-based event → factor detection.
3. ``sensitivity`` — per-holding factor weights (manual, inferred,
   or sector default).
4. ``propagation`` — combines classified factors with holding
   sensitivities to produce bounded-confidence impact hypotheses.

Everything here is deterministic: given the same event text and
the same holding metadata the output is byte-identical across runs.
"""

from src.intelligence.factors.taxonomy import (
    FACTORS,
    FACTOR_KEYS,
    FactorDefinition,
    get_factor,
)

__all__ = ["FACTORS", "FACTOR_KEYS", "FactorDefinition", "get_factor"]
