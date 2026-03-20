"""Risk agent -- monitors portfolio concentration, calendar clustering, and thesis drift.

Scans holdings and recent analysis notes to detect risk conditions, then
generates alerts with appropriate severity levels.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from sqlalchemy import func, select

from src.database.models import (
    Alert,
    AnalysisNote,
    Event,
    EventLink,
    Holding,
    Security,
)

from .base import BaseAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class RiskAgent(BaseAgent):
    """Identifies concentration, clustering, and drift risks across the portfolio."""

    agent_name: ClassVar[str] = "risk"
    read_permissions: ClassVar[list[str]] = [
        "holdings",
        "securities",
        "events",
        "event_links",
        "analysis_notes",
        "alerts",
    ]
    write_permissions: ClassVar[list[str]] = ["alerts", "agent_runs"]

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """Entry point -- delegates to :meth:`assess_risk`."""
        return await self.assess_risk()

    async def assess_risk(self) -> dict[str, Any]:
        """Run all risk checks and return a summary.

        Checks performed:
         1. Name concentration
         2. Sector concentration
         3. Geography concentration
         4. Currency concentration
         5. Theme concentration
         6. Calendar clustering (by event type)
         7. Thesis drift
         8. Dividend concentration (same-month clustering)
         9. Correlation risk (sector + geography overlap)
        """
        from src.config import get_settings

        settings = get_settings()
        conc = settings.risk.concentration
        name_threshold = conc.max_single_name_pct / 100.0
        sector_threshold = conc.max_sector_pct / 100.0
        geography_threshold = conc.max_geography_pct / 100.0
        currency_threshold = conc.max_currency_pct / 100.0
        cal = settings.risk.calendar
        cluster_window = cal.cluster_threshold_days
        cluster_max = cal.cluster_min_events
        thesis_drift_negative_threshold = 3

        # Dividend concentration config
        div_conc = settings.risk.dividend_concentration
        div_same_month_threshold = div_conc.max_same_month_pct / 100.0

        # Correlation risk config
        corr = settings.risk.correlation
        corr_sector_geo_threshold = corr.max_same_sector_geo_pct / 100.0
        corr_sector_threshold = corr.max_same_sector_pct / 100.0

        await self._log_run_start()
        alerts_created: list[dict[str, Any]] = []

        try:
            holdings = await self._load_holdings_with_metadata()

            # --- concentration checks ------------------------------------
            alerts_created.extend(await self._check_name_concentration(holdings, name_threshold))
            alerts_created.extend(await self._check_sector_concentration(holdings, sector_threshold))
            alerts_created.extend(await self._check_geography_concentration(holdings, geography_threshold))
            alerts_created.extend(await self._check_currency_concentration(holdings, currency_threshold))
            alerts_created.extend(await self._check_theme_concentration(holdings, sector_threshold))

            # --- calendar clustering --------------------------------------
            alerts_created.extend(await self._check_calendar_clustering(holdings, cluster_window, cluster_max))

            # --- thesis drift ---------------------------------------------
            alerts_created.extend(await self._check_thesis_drift(holdings, thesis_drift_negative_threshold))

            # --- dividend concentration -----------------------------------
            alerts_created.extend(await self._check_dividend_concentration(holdings, div_same_month_threshold))

            # --- correlation risk -----------------------------------------
            alerts_created.extend(
                await self._check_correlation_risk(
                    holdings, corr_sector_geo_threshold, corr_sector_threshold
                )
            )

            # --- persist all generated alerts to the database --------------
            if alerts_created:
                await self._persist_alerts(alerts_created)

            summary = {
                "checks_run": 9,
                "alerts_created": len(alerts_created),
                "alert_details": alerts_created,
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    # -- data loading ------------------------------------------------------

    async def _load_holdings_with_metadata(self) -> list[dict[str, Any]]:
        """Load holdings joined with security classification data."""
        self._check_permission("holdings", "read")
        self._check_permission("securities", "read")

        async with self._get_db() as session:
            stmt = select(Holding).where(Holding.status == "active")
            h_rows = (await session.execute(stmt)).scalars().all()

            holdings: list[dict[str, Any]] = []
            for h in h_rows:
                sec_stmt = select(Security).where(Security.ticker == h.ticker)
                sec = (await session.execute(sec_stmt)).scalars().first()

                # Parse themes from JSON text column
                themes_raw = sec.themes if sec else None
                try:
                    themes_list = json.loads(themes_raw) if themes_raw else []
                except (ValueError, TypeError):
                    themes_list = []

                mv = getattr(h, "market_value", None) or (
                    (h.quantity or 0) * (getattr(h, "current_price", None) or getattr(h, "avg_cost_basis", None) or 0)
                )
                holdings.append({
                    "id": h.id,
                    "ticker": h.ticker,
                    "quantity": h.quantity,
                    "avg_cost_basis": getattr(h, "avg_cost_basis", None),
                    "market_value": mv,
                    "weight_pct": getattr(h, "weight_pct", None) or 0,
                    "currency": getattr(h, "currency", "USD"),
                    "sector": sec.sector if sec else None,
                    "geography": sec.geography if sec else None,
                    "themes": themes_list,
                })

        return holdings

    # -- concentration checks ----------------------------------------------

    async def _check_name_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        """Alert if any single holding exceeds the name-concentration threshold."""
        return self._generic_concentration_check(
            holdings,
            group_key="ticker",
            threshold=threshold,
            alert_type="concentration_name",
            label="name",
        )

    async def _check_sector_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        return self._generic_concentration_check(
            holdings,
            group_key="sector",
            threshold=threshold,
            alert_type="concentration_sector",
            label="sector",
        )

    async def _check_geography_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        return self._generic_concentration_check(
            holdings,
            group_key="geography",
            threshold=threshold,
            alert_type="concentration_geography",
            label="geography",
        )

    async def _check_currency_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        return self._generic_concentration_check(
            holdings,
            group_key="currency",
            threshold=threshold,
            alert_type="concentration_currency",
            label="currency",
        )

    async def _check_theme_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        """Check if any single theme dominates the portfolio."""
        total = len(holdings) or 1
        theme_counter: Counter[str] = Counter()
        for h in holdings:
            for theme in h.get("themes", []):
                theme_counter[theme] += 1

        alerts: list[dict[str, Any]] = []
        for theme, count in theme_counter.items():
            share = count / total
            if share > threshold:
                alert = self._build_concentration_alert(
                    alert_type="concentration_theme",
                    label="theme",
                    group_value=theme,
                    share=share,
                    threshold=threshold,
                )
                alerts.append(alert)
        return alerts

    def _generic_concentration_check(
        self,
        holdings: list[dict[str, Any]],
        group_key: str,
        threshold: float,
        alert_type: str,
        label: str,
    ) -> list[dict[str, Any]]:
        """Shared logic for single-dimension concentration checks.

        Uses market-value weighting: each group's share is the sum of
        market_value for holdings in that group divided by total
        portfolio market_value.  Falls back to equal-weight if no
        market_value data is available.
        """
        total_mv = sum(h.get("market_value", 0) or 0 for h in holdings)
        use_value = total_mv > 0

        value_counter: defaultdict[str | None, float] = defaultdict(float)
        for h in holdings:
            key = h.get(group_key)
            value_counter[key] += (h.get("market_value", 0) or 0) if use_value else 1

        denominator = total_mv if use_value else (len(holdings) or 1)

        alerts: list[dict[str, Any]] = []
        for value, amount in value_counter.items():
            share = amount / denominator
            if share > threshold:
                alert = self._build_concentration_alert(
                    alert_type=alert_type,
                    label=label,
                    group_value=str(value),
                    share=share,
                    threshold=threshold,
                )
                alerts.append(alert)
        return alerts

    def _build_concentration_alert(
        self,
        alert_type: str,
        label: str,
        group_value: str,
        share: float,
        threshold: float,
    ) -> dict[str, Any]:
        """Create an alert dict (not yet persisted)."""
        severity = "high" if share > threshold * 1.5 else "warning"
        return {
            "alert_type": alert_type,
            "severity": severity,
            "title": f"{label.title()} concentration: {group_value} ({share:.0%})",
            "description": (
                f"{group_value} represents {share:.1%} of the portfolio, "
                f"exceeding the {threshold:.0%} threshold."
            ),
        }

    # -- calendar clustering -----------------------------------------------

    async def _check_calendar_clustering(
        self, holdings: list[dict[str, Any]], cluster_window: int, cluster_max: int
    ) -> list[dict[str, Any]]:
        """Flag when multiple holdings have events of the same type in a short window.

        Groups events by event_type across all holdings, then checks if the
        number of distinct holdings with that event type in the window meets
        ``cluster_max``.  Earnings clusters receive "high" severity (they tend
        to be more volatile); all other event types receive "medium".
        """
        self._check_permission("event_links", "read")
        self._check_permission("events", "read")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=cluster_window)
        ).isoformat()

        # Map holding id -> ticker for quick lookup
        id_to_ticker: dict[str, str] = {h["id"]: h["ticker"] for h in holdings}
        holding_ids = list(id_to_ticker.keys())

        # event_type -> set of (holding_id, ticker)
        type_holdings: dict[str, set[str]] = defaultdict(set)

        alerts: list[dict[str, Any]] = []

        async with self._get_db() as session:
            stmt = (
                select(Event.event_type, EventLink.link_target)
                .select_from(EventLink)
                .join(Event, Event.id == EventLink.event_id)
                .where(
                    EventLink.link_target.in_(holding_ids),
                    Event.published_at >= cutoff,
                )
            )
            rows = (await session.execute(stmt)).all()

            for event_type, link_target in rows:
                etype = event_type or "unknown"
                type_holdings[etype].add(link_target)

        for etype, target_ids in type_holdings.items():
            if len(target_ids) >= cluster_max:
                tickers = sorted(
                    id_to_ticker[tid] for tid in target_ids if tid in id_to_ticker
                )
                tickers_str = ", ".join(tickers)
                count = len(target_ids)
                severity = "high" if etype == "earnings" else "warning"
                alerts.append({
                    "alert_type": "calendar_clustering",
                    "severity": severity,
                    "title": (
                        f"{count} {etype} events in {cluster_window}d "
                        f"for {tickers_str}"
                    ),
                    "description": (
                        f"{count} {etype} events in the last {cluster_window} "
                        f"days across {tickers_str}, exceeding the "
                        f"{cluster_max} threshold."
                    ),
                })

        return alerts

    # -- thesis drift ------------------------------------------------------

    async def _check_thesis_drift(
        self, holdings: list[dict[str, Any]], thesis_drift_threshold: int = 3
    ) -> list[dict[str, Any]]:
        """Flag holdings with multiple consecutive negative analysis signals."""
        self._check_permission("analysis_notes", "read")

        alerts: list[dict[str, Any]] = []

        async with self._get_db() as session:
            for h in holdings:
                stmt = (
                    select(AnalysisNote)
                    .where(AnalysisNote.holding_id == h["id"])
                    .order_by(AnalysisNote.created_at.desc())
                    .limit(thesis_drift_threshold + 2)
                )
                notes = (await session.execute(stmt)).scalars().all()

                if len(notes) < thesis_drift_threshold:
                    continue

                # Count consecutive negatives from most recent
                consecutive_negative = 0
                for note in notes:
                    try:
                        data = json.loads(note.content) if note.content else {}
                    except json.JSONDecodeError:
                        data = {}
                    if data.get("impact_direction") == "negative":
                        consecutive_negative += 1
                    else:
                        break

                if consecutive_negative >= thesis_drift_threshold:
                    alerts.append({
                        "alert_type": "thesis_drift",
                        "severity": "high",
                        "title": f"Thesis drift: {h['ticker']} ({consecutive_negative} negative signals)",
                        "description": (
                            f"{h['ticker']} has {consecutive_negative} consecutive "
                            f"negative analysis signals, suggesting the investment "
                            f"thesis may no longer hold."
                        ),
                        "holding_id": h["id"],
                    })

        return alerts

    # -- dividend concentration --------------------------------------------

    async def _check_dividend_concentration(
        self, holdings: list[dict[str, Any]], threshold: float
    ) -> list[dict[str, Any]]:
        """Alert if too many holdings have dividend events in the same month.

        Queries events with ``event_type='dividend'`` linked to holdings via
        :class:`EventLink`, groups by calendar month (extracted from
        ``Event.published_at``), and fires an alert when the share of holdings
        with dividends in a single month exceeds *threshold*.
        """
        self._check_permission("event_links", "read")
        self._check_permission("events", "read")

        total = len(holdings) or 1
        id_to_ticker: dict[str, str] = {h["id"]: h["ticker"] for h in holdings}
        holding_ids = list(id_to_ticker.keys())

        # month_key -> set of holding ids that have a dividend that month
        month_holdings: dict[str, set[str]] = defaultdict(set)

        alerts: list[dict[str, Any]] = []

        async with self._get_db() as session:
            stmt = (
                select(Event.published_at, EventLink.link_target)
                .select_from(EventLink)
                .join(Event, Event.id == EventLink.event_id)
                .where(
                    EventLink.link_target.in_(holding_ids),
                    Event.event_type == "dividend",
                )
            )
            rows = (await session.execute(stmt)).all()

            for published_at, link_target in rows:
                if not published_at:
                    continue
                # Extract YYYY-MM from the ISO timestamp
                month_key = published_at[:7]  # "2026-03"
                month_holdings[month_key].add(link_target)

        for month_key, target_ids in month_holdings.items():
            share = len(target_ids) / total
            if share > threshold:
                tickers = sorted(
                    id_to_ticker[tid] for tid in target_ids if tid in id_to_ticker
                )
                alerts.append({
                    "alert_type": "dividend_concentration",
                    "severity": "warning",
                    "title": (
                        f"Dividend concentration in {month_key}: "
                        f"{len(target_ids)} holdings ({share:.0%})"
                    ),
                    "description": (
                        f"{len(target_ids)} of {total} holdings "
                        f"({', '.join(tickers)}) have dividend events in "
                        f"{month_key}, exceeding the {threshold:.0%} threshold."
                    ),
                })

        return alerts

    # -- correlation risk --------------------------------------------------

    async def _check_correlation_risk(
        self,
        holdings: list[dict[str, Any]],
        sector_geo_threshold: float,
        sector_threshold: float,
    ) -> list[dict[str, Any]]:
        """Flag high correlation when holdings cluster by sector/geography.

        Two checks:
        1. If >``sector_geo_threshold`` of holdings share **both** the same
           sector AND geography (e.g. US Technology).
        2. If >``sector_threshold`` of holdings share the same sector,
           regardless of geography.

        Both produce ``alert_type='correlation_risk'`` with severity "high".
        """
        total = len(holdings) or 1
        alerts: list[dict[str, Any]] = []

        # --- sector + geography overlap -----------------------------------
        sector_geo_counter: Counter[tuple[str | None, str | None]] = Counter()
        sector_counter: Counter[str | None] = Counter()

        for h in holdings:
            sector = h.get("sector")
            geo = h.get("geography")
            sector_geo_counter[(sector, geo)] += 1
            sector_counter[sector] += 1

        for (sector, geo), count in sector_geo_counter.items():
            share = count / total
            if share > sector_geo_threshold and sector is not None:
                alerts.append({
                    "alert_type": "correlation_risk",
                    "severity": "high",
                    "title": (
                        f"Correlation risk: {count} holdings in "
                        f"{geo} {sector} ({share:.0%})"
                    ),
                    "description": (
                        f"{count} of {total} holdings share both sector "
                        f"'{sector}' and geography '{geo}' ({share:.1%}), "
                        f"exceeding the {sector_geo_threshold:.0%} threshold."
                    ),
                })

        # --- sector-only overlap ------------------------------------------
        for sector, count in sector_counter.items():
            share = count / total
            if share > sector_threshold and sector is not None:
                alerts.append({
                    "alert_type": "correlation_risk",
                    "severity": "high",
                    "title": (
                        f"Correlation risk: {count} holdings in "
                        f"sector {sector} ({share:.0%})"
                    ),
                    "description": (
                        f"{count} of {total} holdings are in sector "
                        f"'{sector}' ({share:.1%}), exceeding the "
                        f"{sector_threshold:.0%} threshold."
                    ),
                })

        return alerts

    # -- alert persistence (shared) ----------------------------------------

    async def _persist_alerts(self, alerts: list[dict[str, Any]]) -> None:
        """Batch-persist a list of alert dicts, deduplicating against existing
        unacknowledged alerts with the same title to prevent duplicates on
        repeated risk-agent runs."""
        self._check_permission("alerts", "write")
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_db() as session:
            # Fetch existing unacknowledged alert titles for dedup
            existing_stmt = select(Alert.title).where(Alert.acknowledged == 0)
            existing_titles = set(
                (await session.execute(existing_stmt)).scalars().all()
            )

            created = 0
            for a in alerts:
                if a["title"] in existing_titles:
                    continue  # Skip duplicate
                alert_id = str(uuid.uuid4())
                session.add(Alert(
                    id=alert_id,
                    alert_type=a["alert_type"],
                    severity=a["severity"],
                    title=a["title"],
                    body=a.get("description", ""),
                    related_holdings=json.dumps([a["holding_id"]]) if a.get("holding_id") else None,
                    acknowledged=0,
                    agent_id=self.agent_name,
                    created_at=now,
                ))
                existing_titles.add(a["title"])  # Prevent within-batch dupes too
                a["id"] = alert_id
                created += 1

            await session.commit()

        logger.info("Persisted %d risk alerts (%d duplicates skipped)",
                     created, len(alerts) - created)
