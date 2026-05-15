"""Phase 10 — Revenue-geography CSV import.

Operator-uploaded path.  Mirrors the Phase 9 corporate-events
importer in shape (per-row errors, dedup, ISIN-first matching, URL
scrubbing) but writes :class:`src.database.models.RevenueGeography`
rows.

CSV schema
----------
Required headers (case-insensitive):

* ``region``        — free text; normalised via
                      :func:`src.intelligence.revenue_geography.service.normalize_region`
* ``revenue_share`` — number/percent/fraction
                      (see :func:`parse_revenue_share`)
* At least one of  ``ticker`` / ``isin`` — match key.

Optional headers:

* ``country``, ``company_name``
* ``fiscal_year`` (int), ``period`` (e.g. ``FY``, ``Q1``)
* ``currency``
* ``source_name``, ``source_url`` (scrubbed)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.corporate_events.matcher import match_to_holding
from src.database.models import RevenueGeography
from src.intelligence.revenue_geography.service import (
    AllocationWarning,
    normalize_country,
    normalize_region,
    parse_revenue_share,
    validate_company_allocations,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportRowError:
    line_number: int
    field: str
    message: str
    raw_row: dict[str, Any]


@dataclass
class ImportSummary:
    imported: int = 0
    skipped_duplicate: int = 0
    matched_by_isin: int = 0
    matched_by_ticker: int = 0
    unmatched: int = 0
    errors: list[ImportRowError] = field(default_factory=list)
    warnings: list[AllocationWarning] = field(default_factory=list)
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
            "warnings": [
                {"key": w.key, "kind": w.kind, "message": w.message,
                 "sum_pct": w.sum_pct}
                for w in self.warnings
            ],
            "batch_id": self.batch_id,
        }


# ─────────────────────────────────────────────────────────────────────
# Pure CSV parsing
# ─────────────────────────────────────────────────────────────────────


def _scrub_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        from src.api.routes.events import _scrub_url as scrub
        return scrub(url)
    except Exception:  # pragma: no cover — defensive
        return url


def parse_csv(csv_text: str) -> tuple[list[dict[str, Any]], list[ImportRowError]]:
    """Validate + normalise a CSV body without touching the DB."""
    rows: list[dict[str, Any]] = []
    errors: list[ImportRowError] = []
    if not csv_text or not csv_text.strip():
        return rows, errors

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return rows, errors

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
        region_raw = col(raw, "region")
        share_raw = col(raw, "revenue_share")

        if not (ticker or isin):
            errors.append(ImportRowError(
                line_number, "ticker/isin",
                "At least one of ticker or isin is required.",
                dict(raw),
            ))
            continue
        if not region_raw:
            errors.append(ImportRowError(
                line_number, "region",
                "region is required.", dict(raw),
            ))
            continue
        if not share_raw:
            errors.append(ImportRowError(
                line_number, "revenue_share",
                "revenue_share is required.", dict(raw),
            ))
            continue

        try:
            share, share_note = parse_revenue_share(share_raw)
        except ValueError as exc:
            errors.append(ImportRowError(
                line_number, "revenue_share", str(exc), dict(raw),
            ))
            continue

        fiscal_year_raw = col(raw, "fiscal_year")
        fiscal_year: int | None = None
        if fiscal_year_raw:
            try:
                fiscal_year = int(fiscal_year_raw)
            except ValueError:
                errors.append(ImportRowError(
                    line_number, "fiscal_year",
                    f"Unparseable fiscal_year {fiscal_year_raw!r}",
                    dict(raw),
                ))
                continue

        url = (col(raw, "source_url") or col(raw, "url") or None)

        rows.append({
            "ticker": ticker,
            "isin": isin,
            "company_name": col(raw, "company_name") or None,
            "region": normalize_region(region_raw),
            "country": normalize_country(col(raw, "country") or None),
            "revenue_share": share,
            "share_note": share_note,
            "fiscal_year": fiscal_year,
            "period": col(raw, "period") or None,
            "currency": (col(raw, "currency") or None),
            "source_name": col(raw, "source_name") or None,
            "source_url": _scrub_url(url),
        })
    return rows, errors


# ─────────────────────────────────────────────────────────────────────
# DB import
# ─────────────────────────────────────────────────────────────────────


async def import_csv(
    session: AsyncSession,
    *,
    portfolio_id: str,
    csv_text: str,
    source_type: str = "manual_csv",
    source_name: str = "Manual CSV Import",
) -> ImportSummary:
    """Parse + persist a revenue-geography CSV.  Caller owns the session.

    Portfolio safety: every row carries ``portfolio_id``, matches are
    scoped to that same portfolio, no leakage across portfolios.
    """
    summary = ImportSummary(batch_id=str(uuid.uuid4()))
    rows, errors = parse_csv(csv_text)
    summary.errors.extend(errors)
    summary.warnings.extend(validate_company_allocations(rows))

    if not rows:
        return summary

    now = datetime.now(timezone.utc).isoformat()

    # Pre-load existing dedup keys for this portfolio.
    existing = (await session.execute(
        select(
            RevenueGeography.holding_id,
            RevenueGeography.isin,
            RevenueGeography.ticker,
            RevenueGeography.region,
            RevenueGeography.fiscal_year,
            RevenueGeography.period,
        ).where(RevenueGeography.portfolio_id == portfolio_id)
    )).all()
    existing_keys = {
        (h or "", (i or "").upper(), (t or "").upper(),
         normalize_region(r), fy, p)
        for h, i, t, r, fy, p in existing
    }

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

        dedup = (
            (match.holding_id or ""),
            (row["isin"] or "").upper(),
            (row["ticker"] or "").upper(),
            row["region"],
            row["fiscal_year"],
            row["period"],
        )
        if dedup in existing_keys:
            summary.skipped_duplicate += 1
            continue
        existing_keys.add(dedup)

        session.add(RevenueGeography(
            id=str(uuid.uuid4()),
            portfolio_id=portfolio_id,
            holding_id=match.holding_id,
            ticker=row["ticker"],
            isin=row["isin"],
            company_name=row["company_name"],
            region=row["region"],
            country=row["country"],
            revenue_share=row["revenue_share"],
            fiscal_year=row["fiscal_year"],
            period=row["period"],
            currency=row["currency"],
            source_type=source_type,
            source_name=source_name,
            source_url=row["source_url"],
            confidence=None,
            raw_payload=json.dumps(
                {k: v for k, v in row.items() if v is not None},
                sort_keys=True,
            ),
            import_batch_id=summary.batch_id,
            match_method=match.method,
            created_at=now,
            updated_at=now,
        ))
        summary.imported += 1

    await session.commit()
    logger.info(
        "Imported %d revenue-geography row(s) into portfolio %s (batch %s)",
        summary.imported, portfolio_id, summary.batch_id,
    )
    return summary
