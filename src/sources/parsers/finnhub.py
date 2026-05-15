"""Finnhub.io parser — Phase 7.

Finnhub's news endpoints (``/api/v1/news`` for general market news and
``/api/v1/company-news`` for per-ticker) return a JSON array of articles
with this shape::

    [
      {
        "category": "general",
        "datetime": 1707840000,        # unix seconds
        "headline": "Apple beats Q4 ...",
        "id": 119551392,
        "image": "https://...",
        "related": "AAPL",
        "source": "Reuters",
        "summary": "Apple reported ...",
        "url": "https://..."
      },
      ...
    ]

We map each article to a :class:`ParsedEvent`. The Finnhub API key never
reaches this parser — the fetcher injects it as a query-string parameter
and only the response body lands here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.sources.parsers.base import BaseParser, ParsedEvent

logger = logging.getLogger(__name__)


def _iso_from_unix(ts: int | float | None) -> str:
    """Convert Finnhub's unix-seconds timestamp to an ISO-8601 string.

    Returns an empty string for None / 0 / out-of-range values so
    downstream code never has to guard against bad timestamps.
    """
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError, TypeError):
        return ""


class FinnhubParser(BaseParser):
    """Parses Finnhub news JSON responses into normalized events."""

    def parse(self, raw_content: str, source_id: str) -> list[ParsedEvent]:
        events: list[ParsedEvent] = []

        if not raw_content:
            return events

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Finnhub JSON for %s: %s", source_id, exc)
            return events

        # Finnhub returns a JSON array directly. If we got a dict, it's an
        # error response (``{"error": "..."}``); log + return empty.
        if isinstance(data, dict):
            err = data.get("error") or data.get("message") or "unknown"
            logger.warning("Finnhub error for %s: %s", source_id, err)
            return events

        if not isinstance(data, list):
            logger.warning(
                "Finnhub response was not an array for %s (got %s)",
                source_id, type(data).__name__,
            )
            return events

        for article in data:
            try:
                title = (article.get("headline") or "").strip() or "Untitled"
                summary = (article.get("summary") or "").strip()
                url = (article.get("url") or "").strip()
                if not url:
                    # Skip articles without a stable URL — the dedup hash
                    # uses title+url+published_at and gets noisy without it.
                    continue
                published_at = _iso_from_unix(article.get("datetime"))
                related = article.get("related") or ""
                tags: list[str] = []
                if related:
                    # Finnhub may return a comma-separated string of tickers.
                    tags.extend(
                        t.strip().upper()
                        for t in related.split(",")
                        if t.strip()
                    )

                events.append(
                    ParsedEvent(
                        source_id=source_id,
                        external_id=str(article.get("id") or url),
                        title=title,
                        summary=summary[:500],
                        content=summary,
                        url=url,
                        published_at=published_at,
                        event_type=self.classify_event_type(title, summary),
                        tags=tags,
                        # Drop the ``image`` field from raw_data to keep
                        # stored payloads small — it's a URL not a key.
                        raw_data=json.dumps({
                            k: v for k, v in article.items()
                            if k != "image"
                        }, ensure_ascii=False),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — skip the row, keep going
                logger.warning(
                    "Failed to parse Finnhub article in %s: %s",
                    source_id, exc,
                )
                continue

        logger.info("Parsed %d events from Finnhub %s", len(events), source_id)
        return events
