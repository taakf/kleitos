"""Generic RSS/Atom feed parser."""

import logging
from datetime import datetime, timezone

import feedparser

from src.sources.parsers.base import BaseParser, ParsedEvent

logger = logging.getLogger(__name__)


class RSSGenericParser(BaseParser):
    """Parses generic RSS and Atom feeds into normalized events."""

    def parse(self, raw_content: str, source_id: str) -> list[ParsedEvent]:
        """Parse RSS/Atom feed content."""
        events = []

        try:
            feed = feedparser.parse(raw_content)
        except Exception as e:
            logger.error(f"Failed to parse RSS feed for {source_id}: {e}")
            return events

        if feed.bozo and not feed.entries:
            logger.warning(f"RSS parse error for {source_id}: {feed.bozo_exception}")
            return events

        for entry in feed.entries:
            try:
                # Extract published date
                published_at = ""
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published_at = dt.isoformat()
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                    published_at = dt.isoformat()

                # Extract content
                content = ""
                if hasattr(entry, "content") and entry.content:
                    content = entry.content[0].get("value", "")
                elif hasattr(entry, "summary"):
                    content = entry.get("summary", "")

                # Clean HTML tags for summary
                summary = self._strip_html(entry.get("summary", ""))[:500]

                event = ParsedEvent(
                    source_id=source_id,
                    external_id=entry.get("id", entry.get("link", "")),
                    title=entry.get("title", "Untitled"),
                    summary=summary,
                    content=content,
                    url=entry.get("link", ""),
                    published_at=published_at,
                    event_type=self.classify_event_type(
                        entry.get("title", ""),
                        summary
                    ),
                    tags=self._extract_tags(entry),
                    raw_data=str(entry),
                )
                events.append(event)

            except Exception as e:
                logger.warning(f"Failed to parse RSS entry in {source_id}: {e}")
                continue

        logger.info(f"Parsed {len(events)} events from RSS feed {source_id}")
        return events

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        from html import unescape
        import re
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = unescape(clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    def _extract_tags(self, entry) -> list[str]:
        """Extract tags/categories from RSS entry."""
        tags = []
        if hasattr(entry, "tags"):
            for tag in entry.tags:
                term = tag.get("term", "")
                if term:
                    tags.append(term.lower())
        return tags
