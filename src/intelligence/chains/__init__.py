"""Canonical causal-chain infrastructure (Phase 9B).

A single source of truth for how the app represents, validates, and
renders causal chains — the ``event → cause → holding → effect``
data that explains why an event is attached to a holding.

Phase 9A introduced full structured chains for deterministic
``macro_factor`` links (stored in ``EventLink.details_json``).  This
module provides:

* a shared data shape (``NormalizedChain``) that the backend
  emits and the frontend consumes, regardless of the underlying
  link type;
* a normalizer that turns any existing link row — including legacy
  direct-match links without stored chains — into that shape, with
  best-effort field population;
* a small validator that refuses to render malformed Phase 9A
  payloads instead of crashing downstream.

The shape is deliberately flat and JSON-safe so the Pydantic
response model and the dashboard can render it without extra
parsing gymnastics.
"""

from src.intelligence.chains.normalize import (
    NormalizedChain,
    build_chain_for_link,
    normalize_chain_dict,
)

__all__ = [
    "NormalizedChain",
    "build_chain_for_link",
    "normalize_chain_dict",
]
