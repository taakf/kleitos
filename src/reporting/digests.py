"""Digest Generator — produces structured portfolio intelligence digests."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.database.connection import get_db
from src.database.models import Alert, AnalysisNote, Digest, Event, EventLink, Holding

logger = logging.getLogger(__name__)


class DigestGenerator:
    """Generates structured daily/weekly digests of portfolio intelligence."""

    async def generate_daily_digest(self) -> dict:
        """Generate a daily digest combining events, analysis, and alerts."""
        now = datetime.now(timezone.utc)
        digest_id = str(uuid.uuid4())
        cutoff_24h = (now - timedelta(days=1)).isoformat()

        async with get_db() as session:
            # Material events from last 24 hours
            events_stmt = (
                select(Event)
                .where(Event.fetched_at >= cutoff_24h, Event.materiality.in_(["important", "critical"]))
                .order_by(Event.fetched_at.desc())
                .limit(20)
            )
            events = (await session.execute(events_stmt)).scalars().all()

            # Watch items
            watch_stmt = (
                select(Event)
                .where(Event.fetched_at >= cutoff_24h, Event.materiality == "watch")
                .order_by(Event.fetched_at.desc())
                .limit(10)
            )
            watch_items = (await session.execute(watch_stmt)).scalars().all()

            # Active alerts
            alerts_stmt = (
                select(Alert)
                .where(Alert.acknowledged == 0)
                .order_by(Alert.created_at.desc())
                .limit(10)
            )
            alerts = (await session.execute(alerts_stmt)).scalars().all()

            # Analysis notes
            notes_stmt = (
                select(AnalysisNote)
                .where(AnalysisNote.created_at >= cutoff_24h)
                .order_by(AnalysisNote.created_at.desc())
                .limit(10)
            )
            analysis = (await session.execute(notes_stmt)).scalars().all()

            # Holdings count
            count_stmt = select(func.count()).select_from(Holding).where(Holding.status == "active")
            holdings_count = (await session.execute(count_stmt)).scalar() or 0

            # Build content
            content = {
                "material_developments": [
                    {"title": e.title, "materiality": e.materiality,
                     "event_type": e.event_type, "summary": (e.summary or "")[:200],
                     "url": e.url}
                    for e in events
                ],
                "watch_list": [
                    {"title": e.title, "summary": (e.summary or "")[:150]}
                    for e in watch_items
                ],
                "active_alerts": [
                    {"severity": a.severity, "title": a.title, "body": (a.body or "")[:150]}
                    for a in alerts
                ],
                "analysis_highlights": [
                    {"content": (n.content or "")[:200], "materiality": n.materiality}
                    for n in analysis
                ],
                "portfolio_snapshot": {
                    "holdings_count": holdings_count,
                    "active_alerts": len(alerts),
                    "material_events": len(events),
                    "watch_items": len(watch_items),
                },
            }

            # Store digest
            new_digest = Digest(
                id=digest_id,
                digest_type="daily",
                period_start=now.replace(hour=0, minute=0, second=0).isoformat(),
                period_end=now.isoformat(),
                content=json.dumps(content),
                event_count=len(events) + len(watch_items),
                alert_count=len(alerts),
                holding_count=holdings_count,
                created_at=now.isoformat(),
            )
            session.add(new_digest)
            await session.commit()

        logger.info("Generated daily digest %s: %d material, %d watch, %d alerts",
                     digest_id, len(events), len(watch_items), len(alerts))

        return {"id": digest_id, "type": "daily", "period_end": now.isoformat(), "content": content}

    async def get_latest_digest(self) -> dict | None:
        """Get the most recent digest."""
        async with get_db() as session:
            stmt = select(Digest).order_by(Digest.created_at.desc()).limit(1)
            row = (await session.execute(stmt)).scalars().first()

        if not row:
            return None

        result = {
            "id": row.id, "digest_type": row.digest_type,
            "period_start": row.period_start, "period_end": row.period_end,
            "event_count": row.event_count, "alert_count": row.alert_count,
            "created_at": row.created_at,
        }
        try:
            result["content"] = json.loads(row.content)
        except json.JSONDecodeError:
            result["content"] = {}
        return result

    async def get_digests(self, limit: int = 10) -> list[dict]:
        """Get recent digests."""
        async with get_db() as session:
            stmt = (
                select(Digest)
                .order_by(Digest.created_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()

        return [
            {"id": d.id, "digest_type": d.digest_type,
             "period_start": d.period_start, "period_end": d.period_end,
             "event_count": d.event_count, "alert_count": d.alert_count,
             "created_at": d.created_at}
            for d in rows
        ]
