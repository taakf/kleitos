"""Phase 12 — Optional grounded-AI narrator for Insights cards.

The narrator can rewrite the ``title`` / ``summary`` /
``why_it_matters`` / ``recommended_action`` of a deterministic card,
but **never** introduces new facts.  Concretely it cannot:

* add new holdings;
* invent percentages or numbers not in the deterministic card;
* change ``severity``, ``category``, ``rank``, ``evidence``,
  ``affected_holdings``, ``deep_links``, or ``data_gaps``;
* change ``portfolio_id`` or ``id``.

The output is validated against the deterministic original on a
field-by-field basis: any rewrite that touches a protected field is
discarded and the deterministic card is returned unchanged for that
slot.  The response-level ``grounding_status`` reports whether the
narrator was reached and whether it succeeded.

When AI is not available (no key, disabled, quota, network failure)
the narrator is a no-op — the deterministic cards flow through.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.intelligence.insights.models import (
    InsightCard,
    InsightsResponse,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Prompt — frozen anti-hallucination contract
# ─────────────────────────────────────────────────────────────────────


#: System / user prompt used to narrate a single deterministic card.
#: Asserted by ``tests/unit/test_phase12_insights.py``; changes here
#: must keep every rule below intact.
NARRATION_PROMPT: str = (
    "You are narrating an already-computed deterministic portfolio "
    "insight card. Your job is to improve the wording of the card's "
    "title, summary, why_it_matters, and recommended_action fields "
    "ONLY.\n\n"
    "GROUNDING CONTRACT (strict):\n"
    "- Do NOT add new holdings, tickers, percentages, dates, "
    "currencies, or numbers that are not already present in the "
    "deterministic card below.\n"
    "- Do NOT contradict the deterministic severity, category, "
    "evidence, affected_holdings, deep_links, or data_gaps.\n"
    "- Do NOT mention any holding ticker outside the "
    "``affected_holdings`` list.\n"
    "- Do NOT invent live prices, market values, or trading "
    "recommendations.\n"
    "- If you are unsure whether a phrase is grounded, omit it.\n"
    "- Return EXACTLY this JSON object and nothing else.\n\n"
    "JSON SCHEMA:\n"
    "{\n"
    "  \"title\": \"<string, <= 140 chars>\",\n"
    "  \"summary\": \"<string, <= 280 chars>\",\n"
    "  \"why_it_matters\": \"<string, <= 280 chars>\",\n"
    "  \"recommended_action\": \"<string or null>\"\n"
    "}\n"
)


# Fields a narrator may rewrite.  Everything else is protected.
_NARRATION_FIELDS = ("title", "summary", "why_it_matters", "recommended_action")


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


async def narrate_insights(
    response: InsightsResponse,
    *,
    include_ai: bool = False,
    max_cards: int = 5,
) -> InsightsResponse:
    """Return a (possibly AI-narrated) copy of ``response``.

    Hard guarantees:

    * If ``include_ai`` is False, returns ``response`` unchanged with
      ``grounding_status="deterministic_only"``.
    * If the LLM client is unavailable, returns the deterministic
      cards with ``grounding_status="ai_unavailable"``.
    * If the LLM call raises for any card, that card stays
      deterministic and a warning is appended at the response level.
    * The narrator can only rewrite ``title`` / ``summary`` /
      ``why_it_matters`` / ``recommended_action``.  Every other field
      is preserved byte-for-byte from the original.
    """
    if not include_ai:
        return _with(response, grounding_status="deterministic_only")

    try:
        from src.llm.client import is_llm_available
    except Exception:  # pragma: no cover — defensive
        return _with(response, grounding_status="ai_unavailable")
    if not is_llm_available():
        return _with(response, grounding_status="ai_unavailable")

    try:
        from src.llm.client import call_llm_json
    except Exception:  # pragma: no cover — defensive
        return _with(response, grounding_status="ai_unavailable")

    narrated: list[InsightCard] = []
    warnings = list(response.warnings)
    saw_failure = False
    saw_narration = False

    for idx, card in enumerate(response.insights):
        if idx >= max_cards:
            narrated.append(card)
            continue
        try:
            rewrite = await _narrate_one_card(card, call_llm_json)
        except Exception as exc:
            saw_failure = True
            logger.warning(
                "AI narration failed for card %s: %r", card.id, exc,
            )
            narrated.append(card)
            continue
        if rewrite is None:
            narrated.append(card)
            continue
        saw_narration = True
        narrated.append(_merge(card, rewrite))

    if saw_failure and not saw_narration:
        status = "ai_failed"
        warnings.append(
            "AI narrator failed; deterministic insights shown unchanged."
        )
    elif saw_narration:
        status = "ai_grounded"
    else:
        status = "ai_unavailable"

    return response.model_copy(update={
        "insights": narrated,
        "grounding_status": status,
        "warnings": warnings,
    })


# ─────────────────────────────────────────────────────────────────────
# Per-card narration + validation
# ─────────────────────────────────────────────────────────────────────


async def _narrate_one_card(
    card: InsightCard,
    call_llm_json,
) -> dict[str, Any] | None:
    """Ask the LLM to rewrite one card; return validated dict or None.

    ``None`` means: the LLM didn't produce a useful rewrite that
    passes the grounding check — keep the deterministic original.
    """
    payload = (
        f"{NARRATION_PROMPT}\n\n"
        f"DETERMINISTIC INSIGHT CARD (do not contradict):\n"
        f"{json.dumps(card.model_dump(), sort_keys=True)}\n"
    )
    raw = await call_llm_json(payload)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, Any] = {}
    for field in _NARRATION_FIELDS:
        v = raw.get(field)
        if v is None:
            continue
        if not isinstance(v, str):
            continue
        cleaned[field] = v.strip()
    if not cleaned:
        return None
    # Anti-hallucination: a rewrite must not name a ticker outside the
    # card's ``affected_holdings`` list.  We compare on upper-cased
    # token boundaries to catch "MSFT" but not "MSFT-style".
    allowed = {t.upper() for t in card.affected_holdings}
    for field, text in list(cleaned.items()):
        tokens = _scan_tickers(text)
        bad = tokens - allowed
        if bad:
            logger.info(
                "AI narration for %s mentioned untrusted tokens %s; "
                "discarding rewrite for that field.",
                card.id, sorted(bad)[:3],
            )
            cleaned.pop(field, None)
    return cleaned or None


def _scan_tickers(text: str) -> set[str]:
    """Extract uppercase tokens that look like tickers.

    Conservative: 2–6 uppercase letters with no surrounding letters.
    Captures bare tickers ("AAPL") and dotted suffixes ("OPAP.AT" →
    "OPAP" because the suffix is ignored for the membership check).
    """
    import re
    matches = set()
    for m in re.findall(r"\b([A-Z]{2,6})(?:\.[A-Z]{1,4})?\b", text):
        # Skip common English words we'd otherwise flag as a ticker.
        if m in _COMMON_UPPER_TOKENS:
            continue
        matches.add(m)
    return matches


_COMMON_UPPER_TOKENS: frozenset[str] = frozenset({
    "AI", "API", "URL", "JSON", "CSV", "PDF", "FY",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2", "AGM", "EGM",
    "FAQ", "OK", "EU", "US", "UK", "USA", "ATHEX",
    "ETF", "ETFS", "CSD", "CFO", "CEO", "ESG",
    "NEW", "USD", "EUR", "GBP", "JPY", "CHF",
    "FED", "ECB", "BOE", "BOJ", "OPEC",
    "RSS", "EDT", "EST", "GMT", "PST", "UTC",
    "GICS", "NA", "EMEA", "APAC",
    "NOT", "FOR", "AND", "THE", "ARE", "WAS", "WERE",
    "MORE", "LESS", "BUT", "ALL", "ANY", "BY", "ON",
    "AS", "AT", "IS", "BE", "TO", "OF", "OR", "IN", "IT",
    "TICKER", "ISIN", "VS", "VIA", "PCT", "REV",
})


# ─────────────────────────────────────────────────────────────────────
# Merge helpers
# ─────────────────────────────────────────────────────────────────────


def _merge(card: InsightCard, rewrite: dict[str, Any]) -> InsightCard:
    """Apply the validated rewrite to a card.

    Everything except the 4 narration fields is preserved byte-for-
    byte.  The card flips to ``source_type="ai_narrative"`` so the UI
    can render the right badge.
    """
    update: dict[str, Any] = {"source_type": "ai_narrative"}
    for field in _NARRATION_FIELDS:
        if field in rewrite:
            update[field] = rewrite[field]
    return card.model_copy(update=update)


def _with(response: InsightsResponse, *, grounding_status: str) -> InsightsResponse:
    return response.model_copy(update={"grounding_status": grounding_status})


__all__ = [
    "NARRATION_PROMPT",
    "narrate_insights",
]
