"""Source parsers — each parser knows how to extract events from a specific source format.

The ``get_parser()`` factory returns an instantiated parser for a given
parser identifier (as defined in ``config/sources.yaml``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazy registry — avoids circular imports and heavy init at import time
_PARSER_REGISTRY: dict[str, type] | None = None


def _build_registry() -> dict[str, type]:
    from src.sources.parsers.rss_generic import RSSGenericParser
    from src.sources.parsers.newsapi import NewsAPIParser

    return {
        "rss_generic": RSSGenericParser,
        "newsapi": NewsAPIParser,
        # Future: "sec_edgar": SECEdgarParser, "finnhub": FinnhubParser
    }


def get_parser(parser_id: str):
    """Return an instantiated parser for the given *parser_id*.

    Raises ``ValueError`` if no parser is registered for the ID.
    """
    global _PARSER_REGISTRY
    if _PARSER_REGISTRY is None:
        _PARSER_REGISTRY = _build_registry()

    cls = _PARSER_REGISTRY.get(parser_id)
    if cls is None:
        available = ", ".join(sorted(_PARSER_REGISTRY.keys()))
        raise ValueError(
            f"Unknown parser '{parser_id}'. Available parsers: {available}"
        )
    return cls()


__all__ = ["get_parser"]
