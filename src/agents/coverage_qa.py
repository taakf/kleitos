"""Coverage QA agent -- identifies gaps in event coverage for holdings.

Iterates over every holding, checks for the presence of key event types
(earnings, dividends, analyst actions, news), flags gaps as alerts, and
produces a coverage report.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from sqlalchemy import func, select

from src.database.models import (
    Alert,
    CoverageReport,
    Event,
    EventLink,
    Holding,
)

from .base import BaseAgent

logger = logging.getLogger(__name__)

# Event types that every holding should have recent coverage for.
REQUIRED_EVENT_TYPES: list[str] = [
    "earnings",
    "dividend",
    "analyst_action",
    "news",
]

# How many days back to consider an event "recent".
RECENCY_WINDOW_DAYS: int = 90


class CoverageQAAgent(BaseAgent):
    """Reviews holdings for event-coverage gaps and generates reports."""

    agent_name: ClassVar[str] = "coverage_qa"
    read_permissions: ClassVar[list[str]] = [
        "holdings",
        "securities",
        "events",
        "event_links",
        "coverage_reports",
    ]
    write_permissions: ClassVar[list[str]] = [
        "coverage_reports",
        "alerts",
        "agent_runs",
    ]

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """Entry point -- delegates to :meth:`check_coverage`."""
        return await self.check_coverage()

    async def check_coverage(self) -> dict[str, Any]:
        """Scan all holdings and flag coverage gaps.

        Returns
        -------
        dict
            Summary with ``holdings_checked``, ``gaps_found``,
            ``alerts_created``, ``report_id``.
        """
        await self._log_run_start()
        gaps: list[dict[str, Any]] = []
        alerts_created: int = 0

        try:
            self._check_permission("holdings", "read")
            holdings = await self._get_all_holdings()

            for holding in holdings:
                holding_gaps = await self._check_holding_coverage(holding)
                if holding_gaps:
                    gaps.extend(holding_gaps)
                    for gap in holding_gaps:
                        alert_id = await self._create_gap_alert(holding, gap)
                        if alert_id:  # empty string means dedup skipped
                            alerts_created += 1

            report_id = await self._produce_coverage_report(holdings, gaps)

            summary = {
                "holdings_checked": len(holdings),
                "gaps_found": len(gaps),
                "alerts_created": alerts_created,
                "report_id": report_id,
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    # -- internal helpers --------------------------------------------------

    async def _get_all_holdings(self) -> list[dict[str, Any]]:
        """Fetch every active holding."""
        async with self._get_db() as session:
            stmt = select(Holding).where(Holding.status == "active")
            rows = (await session.execute(stmt)).scalars().all()
        return [{"id": h.id, "ticker": h.ticker} for h in rows]

    async def _check_holding_coverage(
        self, holding: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Check whether *holding* has recent events of each required type.

        Returns a list of gap descriptors (empty if fully covered).
        """
        self._check_permission("event_links", "read")
        self._check_permission("events", "read")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENCY_WINDOW_DAYS)).isoformat()
        gaps: list[dict[str, Any]] = []

        async with self._get_db() as session:
            for event_type in REQUIRED_EVENT_TYPES:
                stmt = (
                    select(func.count())
                    .select_from(EventLink)
                    .join(Event, Event.id == EventLink.event_id)
                    .where(
                        EventLink.link_target == holding["id"],
                        Event.event_type == event_type,
                        Event.published_at >= cutoff,
                    )
                )
                count = (await session.execute(stmt)).scalar() or 0

                if count == 0:
                    gaps.append({
                        "holding_id": holding["id"],
                        "ticker": holding["ticker"],
                        "missing_event_type": event_type,
                        "window_days": RECENCY_WINDOW_DAYS,
                    })
                    logger.info(
                        "Gap: %s missing '%s' events in last %d days",
                        holding["ticker"],
                        event_type,
                        RECENCY_WINDOW_DAYS,
                    )

        return gaps

    async def _create_gap_alert(
        self,
        holding: dict[str, Any],
        gap: dict[str, Any],
    ) -> str:
        """Persist an alert for a coverage gap.  Returns the alert ID.

        Deduplicates: if an unacknowledged coverage_gap alert already exists
        for the same holding and missing event type, skip creation.
        """
        self._check_permission("alerts", "write")

        # -- dedup check: skip if identical unacknowledged alert exists ------
        expected_title = f"No recent {gap['missing_event_type']} for {holding['ticker']}"
        async with self._get_db() as session:
            dup_stmt = (
                select(func.count())
                .select_from(Alert)
                .where(
                    Alert.alert_type == "coverage_gap",
                    Alert.title == expected_title,
                    Alert.acknowledged == 0,
                )
            )
            existing = (await session.execute(dup_stmt)).scalar() or 0

        if existing > 0:
            logger.debug(
                "Skipping duplicate alert for %s / %s — unacknowledged alert already exists",
                holding["ticker"],
                gap["missing_event_type"],
            )
            return ""

        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        alert = Alert(
            id=alert_id,
            alert_type="coverage_gap",
            severity="medium",
            title=expected_title,
            body=(
                f"Holding {holding['ticker']} has no {gap['missing_event_type']} "
                f"events in the past {gap['window_days']} days."
            ),
            related_holdings=json.dumps([holding["id"]]),
            acknowledged=0,
            agent_id=self.agent_name,
            created_at=now,
        )

        async with self._get_db() as session:
            session.add(alert)
            await session.commit()

        logger.info("Created alert  id=%s  type=coverage_gap  ticker=%s", alert_id, holding["ticker"])
        return alert_id

    async def _produce_coverage_report(
        self,
        holdings: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
    ) -> str:
        """Create per-holding CoverageReport rows. Returns a summary report ID."""
        self._check_permission("coverage_reports", "write")
        now = datetime.now(timezone.utc).isoformat()

        # Group gaps by holding
        gaps_by_holding: dict[str, set[str]] = {}
        for g in gaps:
            gaps_by_holding.setdefault(g["holding_id"], set()).add(g["missing_event_type"])

        report_ids = []
        async with self._get_db() as session:
            for h in holdings:
                report_id = str(uuid.uuid4())
                h_gaps = gaps_by_holding.get(h["id"], set())

                # Quality score: (4 - number_of_gaps) / 4 * 100
                gap_count = len(h_gaps)
                quality = (4 - gap_count) / 4 * 100

                report = CoverageReport(
                    id=report_id,
                    holding_id=h["id"],
                    ticker=h["ticker"],
                    has_recent_earnings=0 if "earnings" in h_gaps else 1,
                    has_recent_dividend=0 if "dividend" in h_gaps else 1,
                    has_recent_analyst=0 if "analyst_action" in h_gaps else 1,
                    has_recent_news=0 if "news" in h_gaps else 1,
                    quality_score=quality,
                    flag="gap" if h_gaps else "ok",
                    checked_at=now,
                )
                session.add(report)
                report_ids.append(report_id)

            await session.commit()

        await self._audit_log(
            action="report_generated",
            entity_type="coverage_report",
            entity_id=report_ids[0] if report_ids else "none",
            details={"holdings_checked": len(holdings), "gaps": len(gaps)},
        )

        logger.info(
            "Coverage reports generated: %d holdings, %d gaps",
            len(holdings),
            len(gaps),
        )
        return report_ids[0] if report_ids else ""
