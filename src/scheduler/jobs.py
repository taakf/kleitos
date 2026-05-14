"""Job Scheduler — manages periodic agent runs using APScheduler."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Grace period for missed jobs (seconds).  If a job fires more than this
# many seconds late it will be skipped.  300 s = 5 min, generous enough to
# cover transient load spikes without letting stale runs pile up.
DEFAULT_MISFIRE_GRACE = 300


def _parse_time(raw: str, default_h: int = 7, default_m: int = 0) -> tuple[int, int]:
    """Parse an 'HH:MM' string into (hour, minute) with safe fallback."""
    try:
        parts = raw.split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.warning("Invalid time string '%s', falling back to %02d:%02d", raw, default_h, default_m)
        return default_h, default_m


class AxionScheduler:
    """Manages periodic jobs for the Axion system.

    Jobs include:
    - News collection (every 30 minutes)
    - Event analysis (after collection, every 30 minutes)
    - Security classification (every 6 hours)
    - Coverage QA (every 4 hours)
    - Risk assessment (every hour)
    - Daily digest generation (7:00 AM)
    - Database backup (2:00 AM)
    - Health check (every 5 minutes)
    """

    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._is_running = False

    def setup(self, config: dict) -> None:
        """Configure all scheduled jobs based on settings."""
        sched_config = config.get("scheduler", {})

        # ---- Collection -------------------------------------------------
        collection_interval = sched_config.get("collection", {}).get("interval_minutes", 30)
        self._scheduler.add_job(
            self._run_collection,
            IntervalTrigger(minutes=collection_interval),
            id="collection",
            name="News & Event Collection",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Event analysis (runs after collection to analyse new events)
        analysis_interval = sched_config.get("analysis", {}).get("interval_minutes", 30)
        self._scheduler.add_job(
            self._run_event_analysis,
            IntervalTrigger(minutes=analysis_interval),
            id="event_analysis",
            name="Event Impact Analysis",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Security classification ------------------------------------
        classification_interval = sched_config.get("classification", {}).get("interval_hours", 6)
        self._scheduler.add_job(
            self._run_classification,
            IntervalTrigger(hours=classification_interval),
            id="classification",
            name="Security Classification",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Coverage QA ------------------------------------------------
        coverage_interval = sched_config.get("coverage_qa", {}).get("interval_hours", 4)
        self._scheduler.add_job(
            self._run_coverage_qa,
            IntervalTrigger(hours=coverage_interval),
            id="coverage_qa",
            name="Coverage QA Check",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Risk assessment -------------------------------------------
        risk_interval = sched_config.get("risk_check", {}).get("interval_hours", 1)
        self._scheduler.add_job(
            self._run_risk_assessment,
            IntervalTrigger(hours=risk_interval),
            id="risk",
            name="Risk Assessment",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Insights generation (Phase 13) -----------------------------
        # Runs after the risk pass so any new alerts/factor classifications
        # already exist on disk.  Idempotent + non-blocking; on any
        # backend hiccup it logs and returns without raising.
        insights_interval = sched_config.get(
            "insights_generation", {}
        ).get("interval_minutes", 15)
        self._scheduler.add_job(
            self._run_insights_generation,
            IntervalTrigger(minutes=insights_interval),
            id="insights_generation",
            name="Insights Generation",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Daily digest -----------------------------------------------
        digest_time = sched_config.get("digest", {}).get("time", "07:00")
        hour, minute = _parse_time(digest_time, 7, 0)
        self._scheduler.add_job(
            self._run_daily_digest,
            CronTrigger(hour=hour, minute=minute),
            id="daily_digest",
            name="Daily Digest",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Database backup --------------------------------------------
        backup_time = sched_config.get("backup", {}).get("time", "02:00")
        bh, bm = _parse_time(backup_time, 2, 0)
        self._scheduler.add_job(
            self._run_backup,
            CronTrigger(hour=bh, minute=bm),
            id="backup",
            name="Database Backup",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=DEFAULT_MISFIRE_GRACE,
        )

        # ---- Health check -----------------------------------------------
        health_interval = sched_config.get("health_check", {}).get("interval_minutes", 5)
        self._scheduler.add_job(
            self._run_health_check,
            IntervalTrigger(minutes=health_interval),
            id="health_check",
            name="Health Check",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=60,  # short grace for frequent pings
        )

        logger.info("Scheduler configured with %d jobs", len(self._scheduler.get_jobs()))

    def start(self) -> None:
        """Start the scheduler."""
        if not self._is_running:
            self._scheduler.start()
            self._is_running = True
            logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler gracefully, waiting for in-flight jobs."""
        if self._is_running:
            self._scheduler.shutdown(wait=True)
            self._is_running = False
            logger.info("Scheduler stopped")

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_jobs_status(self) -> list[dict]:
        """Get status of all scheduled jobs."""
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs

    # --- Job implementations (delegate to agents) ---

    async def _run_collection(self) -> None:
        """Run the news collection cycle."""
        logger.info("Scheduled job: Starting news collection")
        try:
            from src.agents.collection import CollectionAgent
            agent = CollectionAgent()
            result = await agent.run()
            logger.info("Scheduled job: News collection completed — %s", result)
        except Exception as exc:
            logger.error("Scheduled job: News collection failed — %s", exc, exc_info=True)

    async def _run_event_analysis(self) -> None:
        """Analyse newly collected events for portfolio impact."""
        logger.info("Scheduled job: Starting event analysis")
        try:
            from src.agents.analysis import AnalysisAgent
            agent = AnalysisAgent()
            result = await agent.run()
            logger.info("Scheduled job: Event analysis completed — %s", result)
        except Exception as exc:
            logger.error("Scheduled job: Event analysis failed — %s", exc, exc_info=True)

    async def _run_classification(self) -> None:
        """Classify unclassified securities."""
        logger.info("Scheduled job: Starting security classification")
        try:
            from src.agents.classification import ClassificationAgent
            agent = ClassificationAgent()
            result = await agent.run()
            logger.info("Scheduled job: Security classification completed — %s", result)
        except Exception as exc:
            logger.error("Scheduled job: Security classification failed — %s", exc, exc_info=True)

    async def _run_coverage_qa(self) -> None:
        """Run coverage quality check."""
        logger.info("Scheduled job: Starting coverage QA")
        try:
            from src.agents.coverage_qa import CoverageQAAgent
            agent = CoverageQAAgent()
            result = await agent.run()
            logger.info("Scheduled job: Coverage QA completed — %s", result)
        except Exception as exc:
            logger.error("Scheduled job: Coverage QA failed — %s", exc, exc_info=True)

    async def _run_risk_assessment(self) -> None:
        """Run risk assessment per portfolio."""
        logger.info("Scheduled job: Starting risk assessment")
        try:
            from src.agents.risk import RiskAgent
            from src.database.connection import get_db
            from src.database.models import Portfolio
            from sqlalchemy import select

            async with get_db() as session:
                portfolios = (await session.execute(select(Portfolio))).scalars().all()
                portfolio_ids = [p.id for p in portfolios] if portfolios else ["default"]

            for pid in portfolio_ids:
                agent = RiskAgent()
                result = await agent.run(portfolio_id=pid)
                logger.info("Scheduled job: Risk assessment for '%s' — %s", pid, result)
        except Exception as exc:
            logger.error("Scheduled job: Risk assessment failed — %s", exc, exc_info=True)

    async def _run_insights_generation(self) -> None:
        """Phase 13 — periodic insight generation + diff-aware notify.

        For each portfolio:

        1. Build the Phase 12 deterministic insight response.
        2. Diff against the persisted ``insight_snapshots``.
        3. Upsert snapshots so the next pass is idempotent.
        4. If Telegram is configured, deliver new + escalated cards
           above the severity floor.  If not configured, this is a
           silent no-op locally.

        Never raises: on any backend hiccup the job logs and returns,
        so a transient DB lock doesn't poison the scheduler loop.
        """
        logger.info("Scheduled job: Insights generation start")
        try:
            from src.database.connection import get_db
            from src.database.models import Portfolio
            from src.intelligence.insights import (
                build_insights, notify_new_or_escalated,
            )
            from sqlalchemy import select

            async with get_db() as session:
                portfolios = (await session.execute(select(Portfolio))).scalars().all()
                portfolio_ids = [p.id for p in portfolios] if portfolios else ["default"]

            totals = {"new": 0, "escalated": 0, "unchanged": 0}
            for pid in portfolio_ids:
                try:
                    async with get_db() as session:
                        response = await build_insights(
                            session, portfolio_id=pid, limit=60,
                        )
                        outcome = await notify_new_or_escalated(
                            session, response, deliver_telegram=True,
                        )
                    totals["new"] += len(outcome.new)
                    totals["escalated"] += len(outcome.escalated)
                    totals["unchanged"] += len(outcome.unchanged)
                    logger.info(
                        "Scheduled job: Insights for '%s' — new=%d escalated=%d "
                        "unchanged=%d telegram=%s",
                        pid, len(outcome.new), len(outcome.escalated),
                        len(outcome.unchanged), outcome.telegram_status,
                    )
                except Exception as inner:  # per-portfolio failure isolated
                    logger.warning(
                        "Scheduled job: Insights for '%s' failed — %s",
                        pid, inner,
                    )
            logger.info(
                "Scheduled job: Insights generation complete — totals=%s",
                totals,
            )
        except Exception as exc:
            logger.error(
                "Scheduled job: Insights generation failed — %s",
                exc, exc_info=True,
            )

    async def _run_daily_digest(self) -> None:
        """Generate and deliver daily digest per portfolio, then push to Telegram/email."""
        logger.info("Scheduled job: Generating daily digests")
        try:
            from src.agents.analysis import AnalysisAgent
            from src.database.connection import get_db
            from src.database.models import Portfolio
            from sqlalchemy import select

            async with get_db() as session:
                portfolios = (await session.execute(select(Portfolio))).scalars().all()
                portfolio_ids = [p.id for p in portfolios] if portfolios else ["default"]

            for pid in portfolio_ids:
                agent = AnalysisAgent()
                result = await agent.run(digest=True, period="daily", portfolio_id=pid)
                logger.info("Scheduled job: Daily digest for '%s' — %s", pid, result)

            # Push digest to Telegram if available (sends latest digest)
            try:
                from src.integrations.telegram.notifications import deliver_digest
                if isinstance(result, dict):
                    await deliver_digest(result)
            except ImportError:
                pass
            except Exception as tg_err:
                logger.warning("Digest Telegram delivery failed: %s", tg_err)

            # Send digest via email if configured
            try:
                from src.reporting.email import (
                    send_digest_email, format_digest_html, format_digest_text,
                )
                if isinstance(result, dict):
                    html = format_digest_html(result)
                    text = format_digest_text(result)
                    period = result.get("period_start", "")
                    send_digest_email(
                        subject=f"Axion Digest — {period}",
                        body_html=html,
                        body_text=text,
                    )
            except ImportError:
                pass
            except Exception as email_err:
                logger.warning("Digest email delivery failed: %s", email_err)

        except Exception as exc:
            logger.error("Scheduled job: Daily digest failed — %s", exc, exc_info=True)

    async def _run_backup(self) -> None:
        """Backup the database using SQLite's built-in backup API.

        Uses the configured database path (not hardcoded), runs blocking
        file I/O in an executor to avoid blocking the event loop, and
        performs a proper SQLite checkpoint before copying so the WAL is
        flushed.
        """
        import shutil

        logger.info("Scheduled job: Starting database backup")
        try:
            from src.config import get_settings
            settings = get_settings()
            db_path = settings.database.path
            if not isinstance(db_path, Path):
                db_path = Path(db_path).expanduser()

            if not db_path.exists():
                logger.warning("Backup skipped — DB file not found: %s", db_path)
                return

            backup_dir = db_path.parent / "backups"

            loop = asyncio.get_running_loop()

            def _blocking_backup():
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup_path = backup_dir / f"axion-{timestamp}.db"
                shutil.copy2(str(db_path), str(backup_path))

                # Also copy WAL + SHM if they exist (ensures backup consistency)
                for suffix in ("-wal", "-shm"):
                    wal_file = db_path.parent / (db_path.name + suffix)
                    if wal_file.exists():
                        shutil.copy2(str(wal_file), str(backup_path.parent / (backup_path.name + suffix)))

                # Clean old backups (keep last 7)
                # Match both new (axion-*) and legacy (kleitos-*) backup filenames
                backups = sorted(
                    list(backup_dir.glob("axion-*.db")) + list(backup_dir.glob("kleitos-*.db")),
                    reverse=True,
                )
                for old_backup in backups[7:]:
                    old_backup.unlink(missing_ok=True)
                    # Also remove companion WAL/SHM files
                    for suffix in ("-wal", "-shm"):
                        companion = old_backup.parent / (old_backup.name + suffix)
                        companion.unlink(missing_ok=True)
                    logger.info("Removed old backup: %s", old_backup.name)

                return backup_path.name

            # Checkpoint WAL first to flush pending writes
            from sqlalchemy import text as sa_text
            from src.database.connection import get_db
            async with get_db() as session:
                await session.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))

            backup_name = await loop.run_in_executor(None, _blocking_backup)
            logger.info("Backup completed: %s", backup_name)
        except Exception as e:
            logger.error("Backup failed: %s", e, exc_info=True)

    async def _run_health_check(self) -> None:
        """Check system health, DB connectivity, and disk usage."""
        try:
            from sqlalchemy import text
            from src.database.connection import get_db
            async with get_db() as session:
                await session.execute(text("SELECT 1"))
            logger.debug("Health check: OK (DB reachable)")

            # Check DB file size — warn at 500MB, alert at 1GB
            await self._check_db_size()
        except Exception as exc:
            logger.error("Health check: FAILED — %s", exc)

    async def _check_db_size(self) -> None:
        """Monitor database file size and log warnings if it grows too large."""
        try:
            from src.config import get_settings
            settings = get_settings()
            db_path = Path(settings.database.path)
            if not db_path.exists():
                return

            size_mb = db_path.stat().st_size / (1024 * 1024)

            # Also check WAL file size
            wal_path = db_path.parent / (db_path.name + "-wal")
            wal_mb = wal_path.stat().st_size / (1024 * 1024) if wal_path.exists() else 0

            total_mb = size_mb + wal_mb

            if total_mb > 1024:
                logger.error(
                    "DB SIZE ALERT: Database is %.0f MB (db=%.0f, wal=%.0f). "
                    "Consider running VACUUM or archiving old data.",
                    total_mb, size_mb, wal_mb,
                )
            elif total_mb > 500:
                logger.warning(
                    "DB size warning: %.0f MB (db=%.0f, wal=%.0f)",
                    total_mb, size_mb, wal_mb,
                )
            else:
                logger.debug("DB size: %.1f MB (db=%.1f, wal=%.1f)", total_mb, size_mb, wal_mb)
        except Exception as exc:
            logger.warning("DB size check failed: %s", exc)
