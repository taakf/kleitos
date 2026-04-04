"""Digest Generator — produces structured portfolio intelligence digests."""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.database.connection import get_db
from src.database.models import Alert, AnalysisNote, Digest, Event, Holding

logger = logging.getLogger(__name__)


class DigestGenerator:
    """Generates structured daily/weekly digests of portfolio intelligence.

    Each digest is scoped to a specific portfolio.  Events are global
    (shared across portfolios), but alerts, analysis notes, and
    holdings are filtered by ``portfolio_id``.
    """

    def __init__(self, portfolio_id: str = "default"):
        self.portfolio_id = portfolio_id

    async def generate_daily_digest(self) -> dict:
        """Generate a daily digest for the configured portfolio."""
        now = datetime.now(timezone.utc)
        digest_id = str(uuid.uuid4())
        cutoff_24h = (now - timedelta(days=1)).isoformat()

        async with get_db() as session:
            # Material events from last 24 hours (GLOBAL — events are shared)
            events_stmt = (
                select(Event)
                .where(Event.fetched_at >= cutoff_24h, Event.materiality.in_(["important", "critical"]))
                .order_by(Event.fetched_at.desc())
                .limit(20)
            )
            events = (await session.execute(events_stmt)).scalars().all()

            # Watch items (GLOBAL)
            watch_stmt = (
                select(Event)
                .where(Event.fetched_at >= cutoff_24h, Event.materiality == "watch")
                .order_by(Event.fetched_at.desc())
                .limit(10)
            )
            watch_items = (await session.execute(watch_stmt)).scalars().all()

            # Active alerts (PORTFOLIO-SCOPED)
            alerts_stmt = (
                select(Alert)
                .where(Alert.acknowledged == 0, Alert.portfolio_id == self.portfolio_id)
                .order_by(Alert.created_at.desc())
                .limit(10)
            )
            alerts = (await session.execute(alerts_stmt)).scalars().all()

            # Analysis notes (PORTFOLIO-SCOPED via holding)
            notes_stmt = (
                select(AnalysisNote)
                .join(Holding, AnalysisNote.holding_id == Holding.id)
                .where(
                    AnalysisNote.created_at >= cutoff_24h,
                    Holding.portfolio_id == self.portfolio_id,
                )
                .order_by(AnalysisNote.created_at.desc())
                .limit(10)
            )
            analysis = (await session.execute(notes_stmt)).scalars().all()

            # Holdings count (PORTFOLIO-SCOPED)
            count_stmt = (
                select(func.count())
                .select_from(Holding)
                .where(Holding.status == "active", Holding.portfolio_id == self.portfolio_id)
            )
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

            # Store digest (PORTFOLIO-SCOPED)
            new_digest = Digest(
                id=digest_id,
                portfolio_id=self.portfolio_id,
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

        logger.info("Generated daily digest %s for portfolio '%s': %d material, %d watch, %d alerts",
                     digest_id, self.portfolio_id, len(events), len(watch_items), len(alerts))

        return {"id": digest_id, "type": "daily", "period_end": now.isoformat(), "content": content}

    async def get_latest_digest(self) -> dict | None:
        """Get the most recent digest for this portfolio."""
        async with get_db() as session:
            stmt = (
                select(Digest)
                .where(Digest.portfolio_id == self.portfolio_id)
                .order_by(Digest.created_at.desc())
                .limit(1)
            )
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
        """Get recent digests for this portfolio."""
        async with get_db() as session:
            stmt = (
                select(Digest)
                .where(Digest.portfolio_id == self.portfolio_id)
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
