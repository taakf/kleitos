"""NewsAPI.org parser."""

import json
import logging

from src.sources.parsers.base import BaseParser, ParsedEvent

logger = logging.getLogger(__name__)


class NewsAPIParser(BaseParser):
    """Parses NewsAPI.org JSON responses into normalized events."""

    def parse(self, raw_content: str, source_id: str) -> list[ParsedEvent]:
        """Parse NewsAPI JSON response."""
        events = []

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse NewsAPI JSON for {source_id}: {e}")
            return events

        if data.get("status") != "ok":
            logger.warning(f"NewsAPI error for {source_id}: {data.get('message', 'Unknown')}")
            return events

        articles = data.get("articles", [])
        for article in articles:
            try:
                event = ParsedEvent(
                    source_id=source_id,
                    external_id=article.get("url", ""),
                    title=article.get("title", "Untitled") or "Untitled",
                    summary=(article.get("description", "") or "")[:500],
                    content=article.get("content", "") or "",
                    url=article.get("url", ""),
                    published_at=article.get("publishedAt", ""),
                    event_type=self.classify_event_type(
                        article.get("title", ""),
                        article.get("description", "") or ""
                    ),
                    tags=[],
                    raw_data=json.dumps(article),
                )
                events.append(event)

            except Exception as e:
                logger.warning(f"Failed to parse NewsAPI article in {source_id}: {e}")
                continue

        logger.info(f"Parsed {len(events)} events from NewsAPI {source_id}")
        return events
