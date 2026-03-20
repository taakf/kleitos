"""Alert management – create, query, acknowledge alerts."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class AlertManager:
    """Manages portfolio alerts lifecycle.

    All SQL matches the ORM Alert model columns:
        id, alert_type, severity, title, body, related_holdings,
        related_events, acknowledged, acknowledged_at, delivered,
        delivered_at, agent_id, created_at
    """

    SEVERITY_LEVELS = ("critical", "high", "warning", "info")

    def __init__(self, db=None):
        self._db = db

    async def create_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        *,
        source_event_id: Optional[str] = None,
        related_tickers: Optional[list[str]] = None,
        agent_id: str = "system",
        metadata: Optional[dict] = None,
    ) -> str:
        """Create a new alert. Returns alert_id."""
        import json

        alert_id = str(uuid.uuid4())
        severity = severity if severity in self.SEVERITY_LEVELS else "info"
        now = datetime.now(timezone.utc).isoformat()

        related_events_json = json.dumps([source_event_id] if source_event_id else [])
        related_holdings_json = json.dumps(related_tickers or [])

        if self._db:
            await self._db.execute(
                """INSERT INTO alerts
                    (id, alert_type, severity, title, body,
                     related_holdings, related_events, agent_id,
                     acknowledged, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    alert_id, alert_type, severity, title, message,
                    related_holdings_json,
                    related_events_json,
                    agent_id,
                    now,
                ),
            )
            logger.info("Alert created: [%s] %s – %s", severity, alert_type, title[:60])
        return alert_id

    async def acknowledge_alert(self, alert_id: str, agent_id: str = "user") -> bool:
        """Mark an alert as acknowledged."""
        if not self._db:
            return False
        now = datetime.now(timezone.utc).isoformat()
        result = await self._db.execute(
            """UPDATE alerts SET acknowledged = 1,
               acknowledged_at = ?
               WHERE id = ? AND acknowledged = 0""",
            (now, alert_id),
        )
        if result:
            logger.info("Alert %s acknowledged by %s", alert_id[:8], agent_id)
            return True
        return False

    async def resolve_alert(self, alert_id: str, agent_id: str = "system") -> bool:
        """Mark an alert as resolved (acknowledged)."""
        if not self._db:
            return False
        now = datetime.now(timezone.utc).isoformat()
        result = await self._db.execute(
            """UPDATE alerts SET acknowledged = 1,
               acknowledged_at = ?
               WHERE id = ? AND acknowledged = 0""",
            (now, alert_id),
        )
        return bool(result)

    async def get_active_alerts(
        self,
        severity: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get active (unacknowledged) alerts."""
        if not self._db:
            return []
        query = "SELECT * FROM alerts WHERE acknowledged = 0"
        params: list = []
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetch_all(query, tuple(params))
        return [dict(r) for r in rows]

    async def get_alert(self, alert_id: str) -> Optional[dict]:
        """Get a single alert by ID."""
        if not self._db:
            return None
        row = await self._db.fetch_one(
            "SELECT * FROM alerts WHERE id = ?", (alert_id,)
        )
        return dict(row) if row else None

    async def get_alert_counts(self) -> dict:
        """Get counts by severity and status."""
        if not self._db:
            return {"critical": 0, "high": 0, "warning": 0, "info": 0, "total_active": 0}
        rows = await self._db.fetch_all(
            """SELECT severity, COUNT(*) as cnt FROM alerts
               WHERE acknowledged = 0
               GROUP BY severity"""
        )
        counts = {r["severity"]: r["cnt"] for r in rows}
        return {
            "critical": counts.get("critical", 0),
            "high": counts.get("high", 0),
            "warning": counts.get("warning", 0),
            "info": counts.get("info", 0),
            "total_active": sum(counts.values()),
        }
