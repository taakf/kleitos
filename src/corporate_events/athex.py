"""Phase 9 — ATHEX corporate-events fetcher.

Why this file is mostly a degraded-state stub
---------------------------------------------
Athens Exchange does not currently publish a stable public, machine-
readable corporate-events / corporate-actions feed.  The historical
HTML pages at ``athexgroup.gr/corporate-actions`` change shape often
enough that a naive scrape would silently break.  Phase 9's charter
is explicit: **do not fake events, do not invent data**.  So:

* The architecture, schema, API, UI, and matching pipeline are all
  fully built and tested via the manual CSV import path
  (:mod:`src.corporate_events.manual_import`).
* The ATHEX fetcher returns a typed :class:`AthexFetchResult` carrying
  ``status="degraded"`` and an honest reason string.  The Sources UI
  + the Events tab pick that up and render a clean degraded message
  instead of empty data.
* Operators who *do* have a reliable internal feed for ATHEX events
  can:
    1. Flip ``unsupported: false`` in ``config/sources.yaml`` for the
       ``athex-corporate-events`` row.
    2. Set the YAML ``url:`` field to their endpoint.
    3. Implement :func:`_parse_athex_payload` below — the function is
       a clearly-marked extension point.  Once implemented, the rest
       of the pipeline (matcher, storage, API, UI) needs no changes.

This module never makes a real HTTP request in the default build.
The unit tests pin the degraded contract so a future re-enablement
must be deliberate, not accidental.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AthexFetchResult:
    """Typed outcome from a single ATHEX fetch attempt.

    ``status`` follows the same vocabulary the Phase 7
    :data:`src.sources.source_status.Status` literal uses, so the UI
    can render this row in the same Sources health table.

    ``events`` is the list of parsed dicts ready for storage; empty
    on any non-``active`` status.
    """

    status: str                 # "active" | "degraded" | "unsupported" | "error"
    reason: str                 # one-line customer-safe summary
    events: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: str = ""
    source_id: str = "athex-corporate-events"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_athex_payload(payload: str) -> list[dict[str, Any]]:
    """Extension point — parse a real ATHEX response into event dicts.

    Returns a list of dicts with the keys ``import_csv`` expects:
    ``ticker``, ``isin``, ``exchange``, ``event_type``, ``title``,
    ``event_date`` (YYYY-MM-DD), and optionally ``event_time``,
    ``description``, ``source_url``, ``external_id``, ``status``.

    Default implementation is a no-op so the rest of Phase 9 can be
    tested deterministically without hitting the network.  Returns
    ``[]`` and lets the caller emit ``status="degraded"``.
    """
    return []


async def fetch_athex_events(
    *,
    holdings: list[Any] | None = None,
    config: dict[str, Any] | None = None,
) -> AthexFetchResult:
    """Phase 9 — fetch corporate events from ATHEX.

    Parameters
    ----------
    holdings:
        Optional list of holding-like objects in the active portfolio,
        used to scope the fetch.  Passed through to the parser when an
        upstream endpoint exists; ignored by the default degraded
        implementation.
    config:
        Optional source config dict — typically the result of looking
        up the ``athex-corporate-events`` row in the YAML registry.
        When ``config.get("unsupported")`` is true (the default
        build), we short-circuit to ``status="unsupported"`` so the
        operator sees the honest reason.

    Returns
    -------
    AthexFetchResult
        Typed status + (empty) event list.  Callers must respect the
        status — the Sources UI uses it directly and the Events tab
        renders an "ATHEX source unavailable/degraded" banner when it
        is not ``active``.
    """
    cfg = config or {}
    fetched_at = _now_iso()

    # 1) The default build ships with ``unsupported: true`` in YAML so
    #    nothing is ever fetched automatically.  Be explicit about it.
    if cfg.get("unsupported", True):
        return AthexFetchResult(
            status="unsupported",
            reason=(
                "ATHEX corporate-events automation is not enabled in this "
                "build. Use the manual CSV import to populate corporate "
                "events for Greek-listed holdings."
            ),
            events=[],
            fetched_at=fetched_at,
        )

    # 2) An operator may flip ``unsupported: false`` and point ``url``
    #    at an internal endpoint.  This branch is a deliberate
    #    placeholder — we do NOT issue a real HTTP request in this
    #    phase.  The branch exists so the integration point is
    #    documented; until ``_parse_athex_payload`` is implemented it
    #    returns ``degraded`` with no events.
    payload = ""
    try:
        events = _parse_athex_payload(payload)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ATHEX parser raised: %s", exc)
        return AthexFetchResult(
            status="error",
            reason="ATHEX parser raised an exception; check support bundle.",
            events=[],
            fetched_at=fetched_at,
        )

    if not events:
        return AthexFetchResult(
            status="degraded",
            reason=(
                "ATHEX corporate-events endpoint returned no rows. "
                "Athens Exchange does not currently publish a stable "
                "machine-readable corporate-events feed; use the manual "
                "CSV import path instead."
            ),
            events=[],
            fetched_at=fetched_at,
        )

    # 3) Real-fetch happy path (not exercised by the default build).
    return AthexFetchResult(
        status="active",
        reason="ATHEX corporate-events fetch succeeded.",
        events=events,
        fetched_at=fetched_at,
    )
