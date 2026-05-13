"""Deterministic external-entity matcher (Phase 9D).

Given the set of *related entities* a portfolio's holdings care
about (extracted from ``holding_relationships`` DB rows), finds
mentions of those entities in raw event text — by ticker or by a
conservative company-name match.

Design principles
-----------------
* **Strict**: word-boundary regex only.  Short tickers (≤2 chars)
  require cash-tag / parenthetical / explicit context, mirroring
  the rigor of ``CollectionAgent._extract_tickers_from_text``.
* **Direct-match exclusion**: a related-entity match is ONLY useful
  if the matched entity is NOT itself a held ticker — when the
  entity is in the portfolio, the direct-match path already handles
  it.  The caller passes in the set of held tickers for this exclusion.
* **No NLP**: no generic entity linker, no LLM, no fuzzy matching.
  Precision over recall, always.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityMatch:
    """A single detected mention of a related external entity.

    ``entity_key`` is the stable identifier the caller passed into
    the matcher:
      * for ticker-based related entities → the ticker
      * for non-ticker related entities → the ``related_entity_key``
        from the seed registry

    ``match_type`` records which rule fired (``ticker`` or ``name``)
    so the causal chain can cite it, and ``match_score`` is a
    bounded confidence in the detection itself — higher for ticker
    matches, lower for name matches.
    """

    entity_key: str
    matched_value: str       # the specific string that matched
    match_type: str          # "ticker" | "name"
    match_score: float       # 0.0-1.0, bounded


# ---------------------------------------------------------------------------
# Rule-level match scores.  These are detection-confidence bounds,
# not propagated-to-holding confidence — the propagator multiplies
# them down further.
# ---------------------------------------------------------------------------

#: Title ticker hit is the strongest single signal.
_TICKER_TITLE_SCORE: float = 0.95
#: Ticker in body/summary is slightly weaker.
_TICKER_BODY_SCORE: float = 0.85
#: Company-name title hit.
_NAME_TITLE_SCORE: float = 0.80
#: Company-name body hit.
_NAME_BODY_SCORE: float = 0.70

#: A company name has to be this long (characters) before we use it
#: as a match key.  Short names like "HP" would produce too much
#: noise; operators who need those must add them via explicit
#: ticker rows.
_MIN_NAME_LENGTH: int = 4


class RelationshipEntityMatcher:
    """Detect mentions of related external entities in event text.

    Stateless; construct once per event or once per collection
    cycle — the matcher caches nothing.
    """

    def find_matches(
        self,
        *,
        title: str,
        summary: str,
        entities: list[tuple[str, str | None, str | None]],
        excluded_tickers: set[str] | None = None,
    ) -> list[EntityMatch]:
        """Find all entities mentioned in ``title`` or ``summary``.

        Parameters
        ----------
        title, summary:
            Event text.  Both are optional; empty strings are safe.
        entities:
            List of ``(entity_key, related_ticker, related_name)``
            tuples — one per distinct external entity the portfolio's
            relationships reference.
        excluded_tickers:
            Tickers that are already in the portfolio — matches on
            these are dropped because direct matching already handles
            them.  This is the primary anti-double-count guard.

        Returns
        -------
        list[EntityMatch]
            One match per detected entity, best-score kept if an
            entity is mentioned via both ticker and name.
        """
        title = (title or "").strip()
        summary = (summary or "").strip()
        if not (title or summary) or not entities:
            return []

        excluded = excluded_tickers or set()
        # Precompute uppercase versions for ticker scanning.
        title_upper = title.upper()
        summary_upper = summary.upper()
        title_lower = title.lower()
        summary_lower = summary.lower()

        best: dict[str, EntityMatch] = {}

        for entity_key, related_ticker, related_name in entities:
            # Skip entities that are themselves held tickers — direct
            # matching already covered them.
            if related_ticker and related_ticker.upper() in excluded:
                continue

            match = self._match_entity(
                entity_key=entity_key,
                related_ticker=related_ticker,
                related_name=related_name,
                title_upper=title_upper,
                summary_upper=summary_upper,
                title_lower=title_lower,
                summary_lower=summary_lower,
            )
            if match is None:
                continue
            # Keep best match per entity_key
            existing = best.get(entity_key)
            if existing is None or match.match_score > existing.match_score:
                best[entity_key] = match

        return list(best.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _match_entity(
        self,
        *,
        entity_key: str,
        related_ticker: str | None,
        related_name: str | None,
        title_upper: str,
        summary_upper: str,
        title_lower: str,
        summary_lower: str,
    ) -> EntityMatch | None:
        """Try ticker first, then company name — return the best hit."""
        # --- Ticker match -----------------------------------------------
        if related_ticker:
            t = related_ticker.upper()
            score, matched = self._match_ticker_in_scope(
                t, title_upper, summary_upper,
            )
            if score > 0:
                return EntityMatch(
                    entity_key=entity_key,
                    matched_value=matched,
                    match_type="ticker",
                    match_score=score,
                )

        # --- Name match -------------------------------------------------
        if related_name and len(related_name) >= _MIN_NAME_LENGTH:
            score, matched = self._match_name_in_scope(
                related_name.lower(), title_lower, summary_lower,
            )
            if score > 0:
                return EntityMatch(
                    entity_key=entity_key,
                    matched_value=matched,
                    match_type="name",
                    match_score=score,
                )

        return None

    def _match_ticker_in_scope(
        self, ticker: str, title_upper: str, summary_upper: str,
    ) -> tuple[float, str]:
        """Word-boundary ticker match, with short-ticker hardening.

        Mirrors ``CollectionAgent._extract_tickers_from_text`` so
        the precision profile is identical between direct and
        relationship paths.
        """
        escaped = re.escape(ticker)
        if len(ticker) <= 2:
            # Short ticker: require cash-tag / parenthetical / explicit
            strict = (
                r'(?:\$' + escaped + r'(?![A-Z0-9])'
                r'|\(' + escaped + r'\)'
                r'|(?:TICKER|SYMBOL|STOCK)[:\s]+' + escaped + r'(?![A-Z0-9])'
                r')'
            )
            if re.search(strict, title_upper):
                return _TICKER_TITLE_SCORE, ticker
            if re.search(strict, summary_upper):
                return _TICKER_BODY_SCORE, ticker
            return 0.0, ""
        # 3+ chars: standard word-boundary match
        pattern = r'(?<![A-Z0-9])' + escaped + r'(?![A-Z0-9])'
        if re.search(pattern, title_upper):
            return _TICKER_TITLE_SCORE, ticker
        if re.search(pattern, summary_upper):
            return _TICKER_BODY_SCORE, ticker
        return 0.0, ""

    @staticmethod
    def _match_name_in_scope(
        name_lower: str, title_lower: str, summary_lower: str,
    ) -> tuple[float, str]:
        """Conservative name match with a narrow fallback.

        Name is required to be ``>= _MIN_NAME_LENGTH`` characters
        by the caller.

        Strategy:
          1. Try the full name as a single word-boundary regex.
             Covers the common case and is the highest-precision rule.
          2. Fallback: if the full-name match fails AND the name
             contains at least TWO significant words (after stripping
             a small stop-word list), require EVERY significant word
             to appear as a whole word in the same scope (title or
             summary).  The fallback always scores lower than the
             exact match so the two tiers stay distinguishable.

        No fuzzy matching, no partial substrings, no stemming — just
        token-level all-or-nothing presence checks.
        """
        escaped = re.escape(name_lower)
        # Use lookahead/lookbehind for word boundaries that tolerate
        # possessives ("Nvidia's") without allowing "pineapple".
        pattern = r'(?<![a-zA-Z0-9])' + escaped + r"(?![a-zA-Z0-9])"
        if re.search(pattern, title_lower):
            return _NAME_TITLE_SCORE, name_lower
        if re.search(pattern, summary_lower):
            return _NAME_BODY_SCORE, name_lower

        # --- Multi-word significant-tokens fallback -------------------
        tokens = _significant_tokens(name_lower)
        if len(tokens) < 2:
            return 0.0, ""
        if all(_token_in(text=title_lower, token=t) for t in tokens):
            return _NAME_TITLE_TOKENS_SCORE, " ".join(tokens)
        if all(_token_in(text=summary_lower, token=t) for t in tokens):
            return _NAME_BODY_TOKENS_SCORE, " ".join(tokens)
        return 0.0, ""


#: Score for the all-significant-tokens fallback.  Strictly lower
#: than ``_NAME_TITLE_SCORE`` / ``_NAME_BODY_SCORE`` so a fallback
#: match never outranks an exact match.
_NAME_TITLE_TOKENS_SCORE: float = 0.70
_NAME_BODY_TOKENS_SCORE: float = 0.60


#: Stop words stripped from multi-word names before the fallback
#: match.  Short and conservative — this is NOT a linguistic parser,
#: it exists so "US Department of Justice" requires "department" AND
#: "justice" but not literally "u.s." / "of" / "the".
_NAME_STOP_WORDS: frozenset[str] = frozenset({
    "us", "u.s.", "u.s", "usa", "the", "of", "and", "a", "an",
    "inc", "corp", "corporation", "company", "co", "ltd", "plc",
    "holdings", "holding", "group",
})

#: Minimum length for a fallback token to be considered significant.
_MIN_TOKEN_LENGTH: int = 4


def _significant_tokens(name_lower: str) -> list[str]:
    """Split a name into significant lowercase tokens."""
    # Treat any non-letter/digit run as a separator.  This handles
    # "u.s. department of justice" → ["u", "s", "department", "of", "justice"]
    # and we then drop "u", "s", "of" via the stop-word / length filters.
    raw = re.split(r"[^a-zA-Z0-9]+", name_lower)
    return [
        t for t in raw
        if t and t not in _NAME_STOP_WORDS and len(t) >= _MIN_TOKEN_LENGTH
    ]


def _token_in(*, text: str, token: str) -> bool:
    """Return True if *token* appears as a whole word in *text*."""
    pattern = r'(?<![a-zA-Z0-9])' + re.escape(token) + r"(?![a-zA-Z0-9])"
    return bool(re.search(pattern, text))
