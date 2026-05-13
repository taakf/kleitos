"""Phase 9H — Bounded deterministic backfill / replay.

This module is the safe operator entry point for *re-running the
deterministic link pipeline on recent events*.  It solves the real
Phase 9G gap:

    An operator adds a new factor sensitivity override or a new
    manual relationship row.  Future collection cycles will see it,
    but historical events already in the DB never get re-linked.

The backfill walks a bounded window of recent ``events`` rows and
re-runs :meth:`src.agents.collection.CollectionAgent._link_event_to_holdings`
on each one.  That method is already idempotent — every link path
(direct match, factor pipeline, relationship pipeline) deduplicates
against existing ``event_links`` rows, and the factor persistence
layer deduplicates ``macro_factor_events`` by
``(event_id, factor)``.  So calling it twice on the same event is
safe and guaranteed not to double-count.

Design constraints (non-negotiable)
-----------------------------------
1. **Bounded window.**  The default is 7 days; the maximum allowed is
   30 days.  Operator calls MAY pass a smaller window but never a
   larger one.  This is a hard guard — we never replay the whole DB.
2. **Portfolio-safe.**  Events are global (news), so the backfill is
   event-scoped.  Portfolio isolation is enforced downstream by the
   same ``Holding.portfolio_id`` FK pattern the live collection path
   uses — we don't touch that.
3. **Idempotent.**  Every write path the link pipeline takes already
   deduplicates.  Running the backfill twice in a row is a no-op.
4. **Deterministic.**  No LLM, no network, no new scoring model.
5. **Auditable.**  Every backfill run writes a single ``AuditLog``
   row (entity_type=``intelligence_backfill``) with its stats.
6. **Safe failure.**  Any exception inside the per-event link call
   is swallowed with a log line — one poisoned event never stops the
   backfill.  The stats dict reports ``failed`` counts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from src.database.connection import get_db
from src.database.models import AuditLog, Event, EventLink, MacroFactorEvent

logger = logging.getLogger(__name__)


class BackfillInProgressError(Exception):
    """Raised when ``backfill_recent_events`` is called while a prior
    backfill is still running in the same process.

    Phase 9K hardening: a process-local asyncio lock protects the
    backfill entry point so repeated button clicks or repeated API
    calls can't spawn N concurrent replays of the same event window.
    Callers are expected to catch this and return a 409/423-style
    response — see ``src/api/routes/operator.py::trigger_backfill``.
    """


# Process-local in-flight guard.  A ``bool`` would be enough for
# strictly synchronous callers, but ``asyncio.Lock`` is the right
# primitive in async code — it also serialises accidental concurrent
# callers by making the second one wait for the first to finish, if
# the caller opts into blocking.  We don't opt in: the public entry
# point uses ``lock.locked()`` for a non-blocking "is running?" check
# and raises immediately when another call is in flight.
_BACKFILL_LOCK: asyncio.Lock = asyncio.Lock()


def is_backfill_running() -> bool:
    """Return True iff a backfill is currently in flight in this process.

    Exposed for tests and for the ops dashboard.  Not a guarantee
    across processes — see the top-level module docstring.
    """
    return _BACKFILL_LOCK.locked()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default backfill window if the caller does not override.
DEFAULT_WINDOW_DAYS: int = 7

#: Hard maximum — the backfill will refuse any request above this.
#: Intentionally small so a bad operator request can't grind the DB
#: for hours on a fresh install.
MAX_WINDOW_DAYS: int = 30

#: Hard cap on the number of events the backfill will touch in one
#: pass.  Protects against pathological inputs (millions of events
#: from a CSV import, etc.) without requiring a complex pagination
#: scheme.
MAX_EVENTS_PER_RUN: int = 500


@dataclass
class BackfillStats:
    """Deterministic, auditable summary of one backfill pass.

    Every counter is mutually exclusive.  ``links_before`` and
    ``links_after`` bracket the whole pass so the operator can see
    exactly how many NEW links landed regardless of which per-event
    counter they came from.
    """

    window_days: int
    window_start: str
    window_end: str
    events_scanned: int = 0
    events_replayed: int = 0
    events_skipped_no_data: int = 0
    events_failed: int = 0
    links_before: int = 0
    links_after: int = 0
    mfe_before: int = 0
    mfe_after: int = 0
    started_at: str = ""
    finished_at: str = ""

    @property
    def links_added(self) -> int:
        return max(0, self.links_after - self.links_before)

    @property
    def mfe_added(self) -> int:
        return max(0, self.mfe_after - self.mfe_before)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "events_scanned": self.events_scanned,
            "events_replayed": self.events_replayed,
            "events_skipped_no_data": self.events_skipped_no_data,
            "events_failed": self.events_failed,
            "links_before": self.links_before,
            "links_after": self.links_after,
            "links_added": self.links_added,
            "mfe_before": self.mfe_before,
            "mfe_after": self.mfe_after,
            "mfe_added": self.mfe_added,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def backfill_recent_events(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_events: int = MAX_EVENTS_PER_RUN,
    reason: str | None = None,
) -> BackfillStats:
    """Re-run the deterministic link pipeline on recent events.

    Parameters
    ----------
    window_days:
        How far back the backfill should reach.  Clamped to
        ``[1, MAX_WINDOW_DAYS]``.  The window is computed from
        ``Event.fetched_at`` (the same column the freshness indicator
        and Phase 9G summary use).
    max_events:
        Hard cap on the number of events the backfill will iterate
        over.  Clamped to ``[1, MAX_EVENTS_PER_RUN]``.
    reason:
        Optional operator-supplied reason string written into the
        audit trail row.  Useful for "added new TSMC seed — backfill".

    Returns
    -------
    BackfillStats
        A deterministic, JSON-serialisable summary of what happened.
        Never raises individual-event failures — those are swallowed
        with a log line and counted in ``events_failed``.

    Raises
    ------
    BackfillInProgressError
        If a prior backfill is still running in this process.  Phase
        9K hardening: the public entry point is protected by a
        process-local ``asyncio.Lock`` so a double-click on the
        operator UI button can't spawn two concurrent replays.
    """
    # Phase 9K: in-flight guard.  We use a non-blocking check + try
    # to acquire pattern instead of ``async with`` so a second caller
    # fails fast with a clear error instead of silently queueing.
    if _BACKFILL_LOCK.locked():
        raise BackfillInProgressError(
            "A backfill is already running in this process. "
            "Wait for it to finish before starting another."
        )
    async with _BACKFILL_LOCK:
        return await _backfill_body(
            window_days=window_days,
            max_events=max_events,
            reason=reason,
        )


async def _backfill_body(
    *,
    window_days: int,
    max_events: int,
    reason: str | None,
) -> BackfillStats:
    """Actual backfill implementation — called only from inside the lock."""
    # Clamp inputs — HARD guards, never trust the caller blindly.
    if window_days < 1:
        window_days = 1
    if window_days > MAX_WINDOW_DAYS:
        window_days = MAX_WINDOW_DAYS
    if max_events < 1:
        max_events = 1
    if max_events > MAX_EVENTS_PER_RUN:
        max_events = MAX_EVENTS_PER_RUN

    now = datetime.now(timezone.utc)
    window_end = now
    window_start = now - timedelta(days=window_days)
    cutoff_iso = window_start.isoformat()

    stats = BackfillStats(
        window_days=window_days,
        window_start=cutoff_iso,
        window_end=window_end.isoformat(),
        started_at=now.isoformat(),
    )

    # Snapshot link + MFE counts BEFORE the pass so we can report
    # exactly how many new rows landed.
    async with get_db() as session:
        stats.links_before = int((await session.execute(
            select(func.count(EventLink.id))
        )).scalar() or 0)
        stats.mfe_before = int((await session.execute(
            select(func.count(MacroFactorEvent.id))
        )).scalar() or 0)

        event_rows = (await session.execute(
            select(Event)
            .where(Event.fetched_at >= cutoff_iso)
            .order_by(Event.fetched_at.desc())
            .limit(max_events)
        )).scalars().all()

    stats.events_scanned = len(event_rows)

    # Lazy import — the collection agent pulls in a lot of modules
    # and we want to keep backfill cheap to import from routes.
    from src.agents.collection import CollectionAgent
    agent = CollectionAgent()

    for ev in event_rows:
        raw = _rebuild_raw_dict(ev)
        if not raw.get("title") and not raw.get("summary") and not raw.get("content"):
            stats.events_skipped_no_data += 1
            continue
        try:
            await agent._link_event_to_holdings(ev.id, raw)
            stats.events_replayed += 1
        except Exception as exc:
            stats.events_failed += 1
            logger.warning(
                "Backfill: link replay failed for event %s: %s",
                (ev.id or "?")[:8], exc,
            )

    # Snapshot AFTER — and write the audit row in the same session so
    # the audit log always reflects the final counts.
    async with get_db() as session:
        stats.links_after = int((await session.execute(
            select(func.count(EventLink.id))
        )).scalar() or 0)
        stats.mfe_after = int((await session.execute(
            select(func.count(MacroFactorEvent.id))
        )).scalar() or 0)

        stats.finished_at = datetime.now(timezone.utc).isoformat()

        session.add(AuditLog(
            id=str(uuid.uuid4()),
            entity_type="intelligence_backfill",
            entity_id=f"window_{window_days}d",
            action="backfill",
            old_value=None,
            new_value=json.dumps(stats.as_dict()),
            agent_id="operator_backfill",
            reason=(reason or None),
            created_at=stats.finished_at,
        ))
        await session.commit()

    logger.info(
        "Backfill: window=%dd scanned=%d replayed=%d skipped=%d failed=%d "
        "links_added=%d mfe_added=%d",
        window_days,
        stats.events_scanned,
        stats.events_replayed,
        stats.events_skipped_no_data,
        stats.events_failed,
        stats.links_added,
        stats.mfe_added,
    )
    return stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rebuild_raw_dict(event: Event) -> dict[str, Any]:
    """Reconstruct the ``raw`` dict that ``CollectionAgent._link_event_to_holdings``
    expects, from a persisted :class:`Event` row.

    The ``raw_data`` column, when populated, holds the original
    collection-time dict as JSON — we prefer that because it also
    carries tickers / sectors / geographies tags from the original
    collector.  When ``raw_data`` is absent or malformed we fall back
    to the top-level ``title`` / ``summary`` / ``content`` columns.
    """
    raw: dict[str, Any] = {}
    if event.raw_data:
        try:
            parsed = json.loads(event.raw_data)
            if isinstance(parsed, dict):
                raw = dict(parsed)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    # Guarantee the three text fields the classifier reads are present
    # even when raw_data was empty or didn't carry them.  The column
    # values always reflect the final event state.
    raw.setdefault("title", event.title or "")
    raw.setdefault("summary", event.summary or "")
    raw.setdefault("content", event.content or "")

    # Pass through the fields the collection link pipeline inspects
    # when they aren't in raw_data.
    raw.setdefault("tickers", [])
    raw.setdefault("sectors", [])
    raw.setdefault("geographies", [])
    return raw
