"""Phase 9 — manual CSV import for corporate events.

The primary supported way to populate the top-level *Events* tab in
the Phase 9 release.  Each row is validated, normalised, then matched
to a holding via ISIN-then-ticker (see :mod:`.matcher`).  Unmatched
rows are still stored — they show up in the calendar tagged
``match_method='unmatched'`` so the operator can audit them.

CSV schema
----------
Required headers (case-insensitive):

* ``ticker`` *or* ``isin``  — at least one must be set per row
* ``event_type``            — earnings | dividend | agm | corporate_action | announcement | other
* ``title``                 — short headline
* ``event_date``            — ISO ``YYYY-MM-DD`` or any ``date.fromisoformat``-compatible value

Optional headers:

* ``exchange``    — defaults to ``ATHEX`` when the listing detector
                    confirms the ticker is Greek-listed.
* ``event_time``  — ``HH:MM`` (24h)
* ``timezone``    — IANA name (``Europe/Athens``)
* ``description`` — free text
* ``url`` / ``source_url`` — scrubbed via the events.py URL scrubber
                    before storage.
* ``status``      — raw vendor status (``confirmed``, ``tentative``)
* ``external_id`` — upstream id, for dedupe

Every row that fails validation is collected into the returned
:class:`ImportSummary` so the UI can render row-level errors — we
never abort on a single bad row.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.corporate_events.matcher import match_to_holding
from src.database.models import CorporateEvent
from src.intelligence.listing import detect_listing

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary + validation
# ---------------------------------------------------------------------------

#: Accepted event-type values.  We keep the set small + opinionated
#: so the calendar's "Type" filter doesn't sprawl.  ``other`` is the
#: explicit escape hatch.
_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "earnings",
    "dividend",
    "agm",
    "egm",
    "corporate_action",
    "announcement",
    "other",
})

#: Synonyms a hand-edited CSV is likely to ship with.
_EVENT_TYPE_ALIASES: dict[str, str] = {
    "annual general meeting": "agm",
    "annual_general_meeting": "agm",
    "extraordinary general meeting": "egm",
    "extraordinary_general_meeting": "egm",
    "earnings release": "earnings",
    "earnings_release": "earnings",
    "result": "earnings",
    "results": "earnings",
    "dividend payment": "dividend",
    "ex-dividend": "dividend",
    "ex_dividend": "dividend",
    "ca": "corporate_action",
    "announcement_note": "announcement",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportRowError:
    """A single row that failed validation, with its 1-based line number."""

    line_number: int            # 1 = first data row (after header)
    field: str
    message: str
    raw_row: dict[str, Any]


@dataclass
class ImportSummary:
    """Outcome of an import run.

    All fields are JSON-serialisable so the API can return this
    directly.
    """

    imported: int = 0
    skipped_duplicate: int = 0
    matched_by_isin: int = 0
    matched_by_ticker: int = 0
    unmatched: int = 0
    errors: list[ImportRowError] = field(default_factory=list)
    batch_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped_duplicate": self.skipped_duplicate,
            "matched_by_isin": self.matched_by_isin,
            "matched_by_ticker": self.matched_by_ticker,
            "unmatched": self.unmatched,
            "errors": [
                {
                    "line_number": e.line_number,
                    "field": e.field,
                    "message": e.message,
                }
                for e in self.errors
            ],
            "batch_id": self.batch_id,
        }


# ---------------------------------------------------------------------------
# CSV → list[dict] (pure)
# ---------------------------------------------------------------------------


def _normalise_event_type(raw: str) -> str | None:
    """Return the canonical event_type or ``None`` if unrecognised."""
    if not raw:
        return None
    key = raw.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _ALLOWED_EVENT_TYPES:
        return key
    # Try the alias table with the original casing collapsed.
    alias_key = raw.strip().lower()
    return _EVENT_TYPE_ALIASES.get(alias_key) or _EVENT_TYPE_ALIASES.get(key)


def _normalise_date(raw: str) -> str | None:
    """Return ``YYYY-MM-DD`` or ``None`` if unparseable.

    Accepts the ISO form and the common ``DD/MM/YYYY`` Greek format.
    """
    if not raw:
        return None
    s = raw.strip()
    # Try ISO first
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    # Fall back to ``DD/MM/YYYY`` (also accept ``DD-MM-YYYY``)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalise_time(raw: str) -> str | None:
    """Return ``HH:MM`` or ``None``.  Accepts ``HH:MM[:SS]``."""
    if not raw:
        return None
    s = raw.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _scrub_url(url: str | None) -> str | None:
    """Use the Phase 8 URL scrubber when available — defence-in-depth."""
    if not url:
        return url
    try:
        from src.api.routes.events import _scrub_url as scrub
        return scrub(url)
    except Exception:  # pragma: no cover — defensive
        return url


def _dedup_hash(*, ticker: str | None, isin: str | None,
                event_type: str, event_date: str, title: str,
                external_id: str | None) -> str:
    """Stable dedup hash for a corporate-event row."""
    key_parts = [
        (external_id or "").strip(),
        (isin or "").strip().upper(),
        (ticker or "").strip().upper(),
        event_type.strip().lower(),
        event_date,
        title.strip().lower(),
    ]
    blob = "|".join(key_parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_csv(csv_text: str) -> tuple[list[dict[str, Any]], list[ImportRowError]]:
    """Parse a CSV into ``(rows, errors)`` without touching the DB.

    Exposed publicly so the API + tests can validate input shape
    without spinning up a session.
    """
    rows: list[dict[str, Any]] = []
    errors: list[ImportRowError] = []
    if not csv_text or not csv_text.strip():
        return rows, errors

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return rows, errors

    # Normalise header casing for case-insensitive access.
    headers = [h.strip() for h in reader.fieldnames or []]
    lower_to_actual = {h.lower(): h for h in headers}

    def col(row: dict[str, Any], name: str) -> str:
        actual = lower_to_actual.get(name.lower())
        if not actual:
            return ""
        v = row.get(actual)
        return (v or "").strip() if isinstance(v, str) else ""

    for line_number, raw in enumerate(reader, start=1):
        ticker = col(raw, "ticker").upper() or None
        isin = col(raw, "isin").upper() or None
        event_type_raw = col(raw, "event_type")
        title = col(raw, "title")
        event_date_raw = col(raw, "event_date")

        # Required-field validation
        if not (ticker or isin):
            errors.append(ImportRowError(
                line_number=line_number,
                field="ticker/isin",
                message="At least one of ticker or isin is required.",
                raw_row=dict(raw),
            ))
            continue
        if not title:
            errors.append(ImportRowError(
                line_number=line_number, field="title",
                message="title is required.", raw_row=dict(raw),
            ))
            continue
        if not event_type_raw:
            errors.append(ImportRowError(
                line_number=line_number, field="event_type",
                message="event_type is required.", raw_row=dict(raw),
            ))
            continue
        if not event_date_raw:
            errors.append(ImportRowError(
                line_number=line_number, field="event_date",
                message="event_date is required.", raw_row=dict(raw),
            ))
            continue

        event_type = _normalise_event_type(event_type_raw)
        if event_type is None:
            errors.append(ImportRowError(
                line_number=line_number, field="event_type",
                message=(
                    f"Unknown event_type {event_type_raw!r}; expected one of "
                    + ", ".join(sorted(_ALLOWED_EVENT_TYPES))
                ),
                raw_row=dict(raw),
            ))
            continue

        event_date = _normalise_date(event_date_raw)
        if event_date is None:
            errors.append(ImportRowError(
                line_number=line_number, field="event_date",
                message=(
                    f"Unparseable event_date {event_date_raw!r}; "
                    "expected YYYY-MM-DD."
                ),
                raw_row=dict(raw),
            ))
            continue

        event_time = _normalise_time(col(raw, "event_time"))
        if col(raw, "event_time") and event_time is None:
            errors.append(ImportRowError(
                line_number=line_number, field="event_time",
                message="Unparseable event_time; expected HH:MM.",
                raw_row=dict(raw),
            ))
            continue

        url = (
            col(raw, "url")
            or col(raw, "source_url")
            or None
        )

        # Auto-fill ``exchange`` from the listing detector when the
        # CSV did not supply it (so the calendar's exchange filter
        # stays useful).
        explicit_exchange = col(raw, "exchange") or None
        if explicit_exchange:
            exchange = explicit_exchange.strip().upper()
        else:
            listing = detect_listing({"ticker": ticker, "isin": isin})
            exchange = listing.exchange

        rows.append({
            "ticker": ticker,
            "isin": isin,
            "exchange": exchange,
            "event_type": event_type,
            "title": title,
            "description": col(raw, "description") or None,
            "event_date": event_date,
            "event_time": event_time,
            "timezone": col(raw, "timezone") or None,
            "status": col(raw, "status") or None,
            "source_url": _scrub_url(url),
            "external_id": col(raw, "external_id") or None,
        })

    return rows, errors


# ---------------------------------------------------------------------------
# DB import (impure)
# ---------------------------------------------------------------------------


async def import_csv(
    session: AsyncSession,
    *,
    portfolio_id: str,
    csv_text: str,
    source_id: str = "manual_csv",
    source_name: str = "Manual CSV Import",
) -> ImportSummary:
    """Parse + persist a CSV import.  Returns the per-row summary.

    Caller owns the transaction (``session.commit()`` is invoked here
    only after every row is processed so a partial commit can't leave
    half a batch behind).

    Portfolio safety: every row carries ``portfolio_id``, and the
    ISIN/ticker match is scoped to that same portfolio.  No row can
    leak into another portfolio.
    """
    summary = ImportSummary(batch_id=str(uuid.uuid4()))
    rows, errors = parse_csv(csv_text)
    summary.errors.extend(errors)

    if not rows:
        return summary

    now = datetime.now(timezone.utc).isoformat()

    # Pre-load existing dedup hashes for this portfolio so we can
    # short-circuit duplicates with one query instead of N.
    existing_hashes = set((await session.execute(
        select(CorporateEvent.dedup_hash).where(
            CorporateEvent.portfolio_id == portfolio_id
        )
    )).scalars().all())

    for row in rows:
        match = await match_to_holding(
            session,
            portfolio_id=portfolio_id,
            isin=row["isin"],
            ticker=row["ticker"],
        )
        if match.method == "isin":
            summary.matched_by_isin += 1
        elif match.method == "ticker":
            summary.matched_by_ticker += 1
        else:
            summary.unmatched += 1

        dedup = _dedup_hash(
            ticker=row["ticker"],
            isin=row["isin"],
            event_type=row["event_type"],
            event_date=row["event_date"],
            title=row["title"],
            external_id=row["external_id"],
        )
        if dedup in existing_hashes:
            summary.skipped_duplicate += 1
            continue
        existing_hashes.add(dedup)

        session.add(CorporateEvent(
            id=str(uuid.uuid4()),
            portfolio_id=portfolio_id,
            holding_id=match.holding_id,
            ticker=row["ticker"],
            isin=row["isin"],
            exchange=row["exchange"],
            source_id=source_id,
            source_name=source_name,
            source_url=row["source_url"],
            external_id=row["external_id"],
            event_type=row["event_type"],
            title=row["title"],
            description=row["description"],
            event_date=row["event_date"],
            event_time=row["event_time"],
            timezone=row["timezone"],
            status=row["status"],
            confidence="unscored",
            match_method=match.method,
            dedup_hash=dedup,
            raw_payload=json.dumps({k: v for k, v in row.items() if v is not None},
                                   sort_keys=True),
            import_batch_id=summary.batch_id,
            created_at=now,
            updated_at=now,
        ))
        summary.imported += 1

    await session.commit()
    logger.info(
        "Imported %d corporate event(s) into portfolio %s (batch %s)",
        summary.imported, portfolio_id, summary.batch_id,
    )
    return summary


__all__ = [
    "ImportRowError",
    "ImportSummary",
    "import_csv",
    "parse_csv",
    "_dedup_hash",
    "_normalise_date",
    "_normalise_event_type",
    "_normalise_time",
]
