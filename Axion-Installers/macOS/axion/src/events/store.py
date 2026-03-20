"""Event Store — manages normalized events with deduplication."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from src.database.connection import get_db
from src.database.models import Event, EventLink, Holding

logger = logging.getLogger(__name__)


class EventStore:
    """Stores and manages normalized events from all sources."""

    async def store_event(self, event: dict) -> tuple[str | None, bool]:
        """Store an event. Returns (event_id, is_new)."""
        dedup_hash = event.get("dedup_hash", "")

        async with get_db() as session:
            stmt = select(Event).where(Event.dedup_hash == dedup_hash)
            existing = (await session.execute(stmt)).scalars().first()
            if existing:
                return existing.id, False

            event_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()

            new_event = Event(
                id=event_id,
                source_id=event.get("source_id"),
                external_id=event.get("external_id"),
                title=event.get("title", ""),
                summary=event.get("summary"),
                content=event.get("content"),
                url=event.get("url"),
                published_at=event.get("published_at"),
                fetched_at=now,
                event_type=event.get("event_type", "general"),
                scope=event.get("scope"),
                direction=event.get("direction"),
                horizon=event.get("horizon"),
                materiality=event.get("materiality", "unscored"),
                confidence=event.get("confidence", "unscored"),
                dedup_hash=dedup_hash,
                raw_data=json.dumps(event.get("raw_data")) if isinstance(event.get("raw_data"), dict) else event.get("raw_data"),
                created_at=now,
            )
            session.add(new_event)
            await session.commit()

        logger.info("Stored new event: %s — %s", event_id, event.get("title", "")[:80])
        return event_id, True

    async def store_event_link(self, link: dict) -> str:
        """Store an event-to-entity link."""
        link_id = link.get("id", str(uuid.uuid4()))
        now = link.get("created_at", datetime.now(timezone.utc).isoformat())

        async with get_db() as session:
            new_link = EventLink(
                id=link_id,
                event_id=link["event_id"],
                link_type=link["link_type"],
                link_target=link["link_target"],
                relevance_score=link.get("relevance_score"),
                impact_channel=link.get("impact_channel"),
                link_source=link.get("link_source", "rules"),
                created_at=now,
            )
            session.add(new_link)
            await session.commit()

        return link_id

    async def get_recent_events(self, days: int = 1, limit: int = 50) -> list[dict]:
        """Get recent events."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with get_db() as session:
            stmt = (
                select(Event)
                .where(Event.fetched_at >= cutoff)
                .order_by(Event.fetched_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {
                "id": e.id, "source_id": e.source_id, "title": e.title,
                "summary": e.summary, "url": e.url, "published_at": e.published_at,
                "fetched_at": e.fetched_at, "event_type": e.event_type,
                "materiality": e.materiality, "confidence": e.confidence,
                "created_at": e.created_at,
            }
            for e in rows
        ]

    async def get_events_by_ticker(self, ticker: str, days: int = 7) -> list[dict]:
        """Get events linked to a specific ticker/holding.

        EventLink.link_target stores holding UUIDs, so we join through
        the Holding table to resolve ticker → UUID → events.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with get_db() as session:
            stmt = (
                select(Event)
                .join(EventLink, Event.id == EventLink.event_id)
                .join(Holding, EventLink.link_target == Holding.id)
                .where(Holding.ticker == ticker.upper(), Event.fetched_at >= cutoff)
                .order_by(Event.fetched_at.desc())
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {"id": e.id, "title": e.title, "summary": e.summary, "url": e.url,
             "event_type": e.event_type, "materiality": e.materiality, "fetched_at": e.fetched_at}
            for e in rows
        ]

    async def get_event_with_links(self, event_id: str) -> dict | None:
        """Get a single event with all its links."""
        async with get_db() as session:
            event = await session.get(Event, event_id)
            if not event:
                return None

            links_stmt = select(EventLink).where(EventLink.event_id == event_id)
            links = (await session.execute(links_stmt)).scalars().all()

        result = {
            "id": event.id, "title": event.title, "summary": event.summary,
            "url": event.url, "event_type": event.event_type,
            "materiality": event.materiality, "published_at": event.published_at,
        }
        result["links"] = [
            {"id": l.id, "link_type": l.link_type, "link_target": l.link_target,
             "relevance_score": l.relevance_score}
            for l in links
        ]
        return result

    async def get_unscored_events(self, limit: int = 50) -> list[dict]:
        """Get events that haven't been scored yet."""
        async with get_db() as session:
            stmt = (
                select(Event)
                .where(Event.materiality == "unscored")
                .order_by(Event.fetched_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {"id": e.id, "title": e.title, "summary": e.summary,
             "event_type": e.event_type, "fetched_at": e.fetched_at}
            for e in rows
        ]

    async def update_event_scoring(self, event_id: str, materiality: str,
                                    confidence: str, scope: str, direction: str) -> None:
        """Update scoring fields on an event."""
        async with get_db() as session:
            event = await session.get(Event, event_id)
            if event:
                event.materiality = materiality
                event.confidence = confidence
                event.scope = scope
                event.direction = direction
                await session.commit()
