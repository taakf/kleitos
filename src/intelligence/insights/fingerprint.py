"""Phase 13 — Insight card key + fingerprint helpers.

Two deterministic functions used by the notifier and the snapshot
table:

* :func:`card_key` returns a **stable string** that uniquely
  identifies an insight slot per portfolio.  Two re-generations of
  the same underlying signal (same news event linked to the same
  ticker, same data-gap, etc.) produce the same key.  The key is
  therefore the right thing to dedup on.

* :func:`card_fingerprint` returns a **content hash** that changes
  when material card content changes — title, severity, evidence
  set, affected holdings.  Changes in narration / wording alone
  don't move the fingerprint (the narration fields are excluded).

Combined, ``(card_key, card_fingerprint)`` lets the notifier answer
three questions deterministically:

* new                — key not yet in ``insight_snapshots``
* escalated          — key exists, fingerprint differs **and**
                       severity rank improved (got more important)
* unchanged          — key exists and fingerprint matches

The helpers are pure — they don't touch the DB or the network.
Easy to unit-test, safe to call from a scheduler job.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from src.intelligence.insights.models import InsightCard


# Severity ladder reused so "escalated" has a single source of truth.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
    "info":     4,
}


def severity_rank(severity: str | None) -> int:
    """Lower rank = more important.  Unknown maps to the bottom."""
    return _SEVERITY_RANK.get((severity or "info").lower(), 5)


def is_escalation(*, old_severity: str | None, new_severity: str | None) -> bool:
    """True when ``new_severity`` ranks **strictly higher** than old."""
    return severity_rank(new_severity) < severity_rank(old_severity)


# ─────────────────────────────────────────────────────────────────────
# Stable key
# ─────────────────────────────────────────────────────────────────────


def _first_evidence_ref(card: InsightCard) -> str | None:
    """Pick the most stable evidence ref for the key.

    Generator output puts the canonical source (news event id /
    corporate event id / alert id / region label / config token) in
    the first evidence row.  Falling back to ``None`` lets the key
    fall through to title-only.
    """
    if not card.evidence:
        return None
    first = card.evidence[0]
    return first.ref or None


def card_key(card: InsightCard) -> str:
    """Return the deterministic notification key for an insight card.

    The shape is ``"insight:<category>:<ref-or-fingerprint>"`` — so a
    re-generation of the same underlying signal (same news event
    linked to the same ticker) produces the same string.  Two
    different signals never collide because the prefix carries the
    category and the suffix carries either the source ref or a
    hash of the affected-holdings + title when no ref exists.
    """
    ref = _first_evidence_ref(card)
    if ref:
        # ``event:evt_abc`` / ``corporate_event:ce_123`` / ``factor:interest_rate``
        # — already shaped like a stable suffix.
        slug = ref
    else:
        # Data-gap / config-only cards have no DB-backed evidence ref;
        # hash the (title + affected) instead so re-generations stay
        # idempotent without leaking PII.
        body = _normalise_title(card.title)
        if card.affected_holdings:
            body += "|" + ",".join(sorted({h.upper() for h in card.affected_holdings}))
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        slug = f"meta:{digest}"
    return f"insight:{card.category}:{slug}"


# ─────────────────────────────────────────────────────────────────────
# Content fingerprint
# ─────────────────────────────────────────────────────────────────────


_WHITESPACE_RX = re.compile(r"\s+")


def _normalise_title(title: str | None) -> str:
    if not title:
        return ""
    return _WHITESPACE_RX.sub(" ", title.strip().lower())


def _sorted_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return sorted(out)


def card_fingerprint(card: InsightCard) -> str:
    """SHA-256 hash over the material fields of an insight card.

    Material means anything that affects the customer-visible meaning
    of the card.  Wording (summary / why_it_matters /
    recommended_action) is **excluded** so an AI narrator pass can
    rewrite copy without forcing a re-notification.
    """
    payload_parts = [
        card.category,
        (card.severity or "info").lower(),
        _normalise_title(card.title),
        "|".join(_sorted_unique(card.affected_holdings)),
        "|".join(_sorted_unique([e.ref for e in card.evidence])),
        "|".join(_sorted_unique(card.data_gaps)),
        "|".join(_sorted_unique([
            f"{dl.surface}:{dl.entity_type or ''}:{dl.entity_id or ''}:{dl.subtab or ''}"
            for dl in card.deep_links
        ])),
    ]
    payload = "\x1f".join(payload_parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "card_key",
    "card_fingerprint",
    "is_escalation",
    "severity_rank",
]
