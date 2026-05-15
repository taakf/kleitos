"""Phase 9 — Listing / exchange detection.

This module is the **single source of truth** for "which exchange is a
holding listed on?" and "is this a Greek/ATHEX-listed instrument?"
questions.  Every Phase 9 surface (corporate-events fetcher, calendar
filter, manual-import resolver, UI "no ATHEX holdings" empty state)
goes through here so we never duplicate the rules.

Why this lives in ``intelligence`` and not ``security_master``
------------------------------------------------------------
``security_master/classifier.py`` already has an
``ISIN_COUNTRY_MAP`` that derives the **listing country** from the
two-letter ISIN prefix.  That map is used by sector / theme tagging,
which conflates "country code" with "geography" and rolls it into the
``securities.geography`` column.  The Phase 9 charter is explicit:
**listing country is not revenue geography**.  Until a dedicated
revenue-geography phase lands, we keep:

* :data:`ISIN_COUNTRY_MAP` (in ``security_master``) — country derived
  from the ISIN prefix.  Still used by the classifier so existing
  surfaces don't change behavior.
* :func:`detect_listing` (in this module) — the clean, explicit
  "listing exposure" detector that Phase 9 consumers call.  Returns a
  small dataclass so callers know what they're looking at.
* :func:`is_athex_listed` — narrow helper for the ATHEX corporate-
  events fetcher and the calendar's "no ATHEX-listed holdings"
  empty state.

This file ships **no** revenue-geography logic, deliberately.  The
docstrings make that obvious so future code can't silently drift.

Detection inputs (in priority order)
------------------------------------
1. ``venue`` / ``exchange`` field on the Holding or Security — if the
   intake or operator filled it, trust it.  Aliases: ``ATHEX``, ``ATH``,
   ``ASE``, ``Athens``, ``Athens Stock Exchange``, ``XATH`` (ISO MIC).
2. ISIN prefix — ``GR`` → Greece (ATHEX is the only Greek venue we
   recognise; if a future Greek MTF appears we'll need to refine).
3. Ticker suffix — Yahoo-style ``.AT`` suffix (e.g. ``OPAP.AT``).
   Lowest priority because some non-Greek tickers also end in ``.AT``;
   we only honour it when no contradicting signal exists.

If nothing matches the result is ``None`` for the exchange and
``False`` for ``is_athex``.  That's the honest fallback — better than
guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─────────────────────────────────────────────────────────────────────
# Vocabulary
# ─────────────────────────────────────────────────────────────────────

#: Canonical ATHEX identifier we store internally.  When a parser /
#: importer / UI needs to render the exchange it should use this string
#: so saved-view filters and CSV import normalize to one value.
ATHEX = "ATHEX"

#: All free-text aliases we'll recognise when reading venue/exchange
#: fields the user or upstream source filled in.  Comparison is
#: case-insensitive and ignores extra whitespace; the canonical form
#: we emit is always :data:`ATHEX`.
_ATHEX_ALIASES: frozenset[str] = frozenset({
    "athex",
    "ath",
    "ase",
    "athens",
    "athens stock exchange",
    "athens exchange",
    "athens se",
    "xath",                  # ISO MIC code for ATHEX
    "athex securities",
})

#: Suffixes commonly appended to a ticker symbol to flag the Athens
#: listing in market-data vendors (Yahoo, Stooq).  Used as the
#: lowest-priority signal.
_ATHEX_TICKER_SUFFIXES: tuple[str, ...] = (".AT", ".ATH", ".ATHEX")


# ─────────────────────────────────────────────────────────────────────
# Result shape
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Listing:
    """A small, deterministic descriptor of where an instrument trades.

    ``exchange`` is the canonical exchange code (e.g. ``"ATHEX"``) when
    we have one, otherwise ``None``.  ``listing_country`` is the country
    of the listing venue (lowercase English name, matching
    :data:`src.security_master.classifier.ISIN_COUNTRY_MAP`).  Neither
    field describes the *revenue geography* of the issuer.
    """

    exchange: str | None
    listing_country: str | None
    confidence: str            # "venue" | "isin" | "ticker_suffix" | "unknown"
    source: str                # short trace label: "venue=ATHEX" / "isin=GR..."

    @property
    def is_athex(self) -> bool:
        return self.exchange == ATHEX


# ─────────────────────────────────────────────────────────────────────
# Detector
# ─────────────────────────────────────────────────────────────────────


def _get(holding: Any, *names: str) -> str | None:
    """Read the first attribute / mapping key with a non-empty value.

    Tolerates both ORM rows (attribute access) and plain dicts
    (mapping access).  Empty strings are treated as missing.
    """
    for name in names:
        v: Any
        if isinstance(holding, dict):
            v = holding.get(name)
        else:
            v = getattr(holding, name, None)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        return v
    return None


def _alias_matches_athex(text: str) -> bool:
    return text.strip().lower() in _ATHEX_ALIASES


def detect_listing(holding: Any) -> Listing:
    """Return a :class:`Listing` for the given holding-like object.

    Accepts:

    * a :class:`src.database.models.Holding` instance,
    * a :class:`src.database.models.Security` instance,
    * a plain ``dict`` carrying any of the same keys.

    Inspected fields (first match wins): ``venue``, ``exchange``,
    ``mic``, ``isin``, ``ticker``.
    """
    venue = _get(holding, "venue", "exchange", "mic")
    isin = _get(holding, "isin")
    ticker = _get(holding, "ticker", "symbol")

    # 1) Explicit venue / exchange ----------------------------------
    if venue:
        if _alias_matches_athex(venue):
            return Listing(
                exchange=ATHEX,
                listing_country="greece",
                confidence="venue",
                source=f"venue={venue.strip()}",
            )

    # 2) ISIN prefix → country --------------------------------------
    if isin and isinstance(isin, str) and len(isin) >= 2:
        prefix = isin[:2].upper()
        if prefix == "GR":
            return Listing(
                exchange=ATHEX,
                listing_country="greece",
                confidence="isin",
                source=f"isin={prefix}",
            )
        # Non-Greek ISIN prefixes: hand back the country (if known) so
        # the calendar can report "this holding is listed in <X>" but
        # leave ``exchange`` as None — we don't claim to know which
        # exchange inside that country it trades on.
        from src.security_master.classifier import ISIN_COUNTRY_MAP
        country = ISIN_COUNTRY_MAP.get(prefix)
        if country:
            return Listing(
                exchange=None,
                listing_country=country,
                confidence="isin",
                source=f"isin={prefix}",
            )

    # 3) Ticker suffix — lowest priority ----------------------------
    if ticker and isinstance(ticker, str):
        upper = ticker.upper()
        for suf in _ATHEX_TICKER_SUFFIXES:
            if upper.endswith(suf):
                return Listing(
                    exchange=ATHEX,
                    listing_country="greece",
                    confidence="ticker_suffix",
                    source=f"ticker_suffix={suf}",
                )

    return Listing(
        exchange=None,
        listing_country=None,
        confidence="unknown",
        source="no_signal",
    )


def is_athex_listed(holding: Any) -> bool:
    """Convenience predicate over :func:`detect_listing`.

    True iff the holding/security is detected as ATHEX-listed via any
    of the three signals (venue, ISIN prefix, ticker suffix).
    """
    return detect_listing(holding).is_athex


def filter_athex_holdings(holdings: list[Any]) -> list[Any]:
    """Return only the ATHEX-listed entries in ``holdings``.

    Pure, deterministic; preserves input order.  Used by the corporate-
    events fetcher to scope its work and by the UI to decide whether
    to render the "No ATHEX-listed holdings detected" empty state.
    """
    return [h for h in holdings if is_athex_listed(h)]
