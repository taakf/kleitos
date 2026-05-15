"""Phase 10 — Revenue-geography service.

Pure (mostly) helpers used by the API, the importer, the dashboard,
and the grounded AI context.  Database I/O is encapsulated in two
small async functions that take an :class:`AsyncSession` so callers
own the transaction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Holding, RevenueGeography


# ─────────────────────────────────────────────────────────────────────
# Normalization
# ─────────────────────────────────────────────────────────────────────

#: Canonical regions used when an operator writes "EMEA" / "Europe,
#: Middle East and Africa" / similar.  The casing is what the calendar
#: + chart will show.  Free-text values that don't match fall through
#: unchanged (lower-cased + stripped) so an operator can pin their own
#: bucket name (e.g. "Greater China") without us "correcting" it.
_REGION_ALIASES: dict[str, str] = {
    "north america":         "North America",
    "na":                    "North America",
    "us":                    "North America",
    "usa":                   "North America",
    "united states":         "North America",
    "americas":              "Americas",
    "south america":         "South America",
    "latam":                 "Latin America",
    "latin america":         "Latin America",
    "europe":                "Europe",
    "eu":                    "Europe",
    "emea":                  "EMEA",
    "europe middle east and africa": "EMEA",
    "middle east":           "Middle East",
    "africa":                "Africa",
    "mena":                  "MENA",
    "asia":                  "Asia",
    "asia pacific":          "Asia Pacific",
    "apac":                  "Asia Pacific",
    "greater china":         "Greater China",
    "china":                 "China",
    "japan":                 "Japan",
    "korea":                 "South Korea",
    "south korea":           "South Korea",
    "india":                 "India",
    "oceania":               "Oceania",
    "australia":             "Australia",
    "rest of world":         "Rest of world",
    "row":                   "Rest of world",
    "other":                 "Other",
    "unknown":               "Unknown",
}


def normalize_region(region: str | None) -> str:
    """Return a canonical, presentable region name.

    Empty / None inputs become ``"Unknown"`` so the bucket layout
    stays consistent (callers can still distinguish "Unknown
    region" from "missing" — the latter is its own bucket
    produced by :func:`compute_portfolio_revenue_exposure`).
    """
    if region is None:
        return "Unknown"
    s = region.strip()
    if not s:
        return "Unknown"
    return _REGION_ALIASES.get(s.lower(), s.strip())


def normalize_country(country: str | None) -> str | None:
    """Return a stripped, title-cased country name or ``None``.

    We don't apply heavy mapping here — the country field is
    optional and stored as-supplied so the operator's data isn't
    over-corrected.
    """
    if country is None:
        return None
    s = country.strip()
    return s or None


# ─────────────────────────────────────────────────────────────────────
# Share parsing
# ─────────────────────────────────────────────────────────────────────


def parse_revenue_share(raw: Any) -> tuple[float, str | None]:
    """Parse ``45`` / ``45%`` / ``0.45`` into a 0–1 fraction.

    Rules:

    * Strings ending in ``%`` are read as percent (so ``"45%"`` ⇒ 0.45).
    * Bare numbers > 1 are read as percent (so ``45`` ⇒ 0.45) and a
      note is returned describing the interpretation.
    * Bare numbers ≤ 1 are kept as a fraction (``0.45`` ⇒ 0.45).
    * Negative values are rejected (``raise ValueError``).

    Returns ``(share, note)``.  ``note`` is a non-empty string when
    the interpretation rule kicked in (so the importer can surface a
    soft warning) and ``None`` otherwise.
    """
    if raw is None:
        raise ValueError("revenue_share is required")
    s = str(raw).strip().replace(",", "")
    if not s:
        raise ValueError("revenue_share is empty")

    note: str | None = None
    if s.endswith("%"):
        s = s[:-1].strip()
        try:
            v = float(s) / 100.0
        except ValueError as exc:
            raise ValueError(f"Unparseable revenue_share {raw!r}") from exc
        note = "Parsed as percent (trailing %)."
    else:
        try:
            n = float(s)
        except ValueError as exc:
            raise ValueError(f"Unparseable revenue_share {raw!r}") from exc
        if n < 0:
            raise ValueError("revenue_share must be non-negative")
        if n > 1.0:
            v = n / 100.0
            note = "Interpreted >1.0 input as percent (e.g. 45 → 0.45)."
        else:
            v = n

    if v < 0:
        raise ValueError("revenue_share must be non-negative")
    return v, note


# ─────────────────────────────────────────────────────────────────────
# Allocation validation
# ─────────────────────────────────────────────────────────────────────


_SUM_TOLERANCE = 0.05  # within ±5% of 1.0 = "looks complete"


@dataclass(frozen=True)
class AllocationWarning:
    """Soft warning surfaced by :func:`validate_company_allocations`."""

    key: str                # ticker or isin used for the grouping
    kind: str               # "sum_high" | "sum_low" | "duplicate_region"
    message: str
    sum_pct: float          # the computed total as a percentage


def validate_company_allocations(
    rows: Iterable[dict[str, Any]],
) -> list[AllocationWarning]:
    """Inspect a batch of parsed rows for per-company sanity.

    We never *reject* a batch for failing to sum to 100 % — operators
    routinely upload partial breakdowns or breakdowns that exclude
    'Other'.  We emit soft warnings so the UI / importer can show
    "ACME Co. allocations sum to 87%, looks low — missing
    a region?" without blocking the upload.

    Grouping is by ``(ticker || isin, fiscal_year, period)``.
    """
    grouped: dict[tuple[str, int | None, str | None], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (
            (r.get("isin") or r.get("ticker") or "").upper(),
            r.get("fiscal_year"),
            r.get("period"),
        )
        if not key[0]:
            continue
        grouped[key].append(r)

    warnings: list[AllocationWarning] = []
    for (group_key, fy, period), bucket in grouped.items():
        # Region duplicates per group
        seen_regions: set[str] = set()
        for r in bucket:
            region_norm = normalize_region(r.get("region"))
            if region_norm in seen_regions:
                warnings.append(AllocationWarning(
                    key=group_key,
                    kind="duplicate_region",
                    message=(
                        f"{group_key}: region {region_norm!r} appears more "
                        f"than once for fiscal_year={fy}, period={period}. "
                        f"Later rows will overwrite earlier ones."
                    ),
                    sum_pct=0.0,
                ))
            seen_regions.add(region_norm)

        total = sum(float(r.get("revenue_share", 0.0)) for r in bucket)
        total_pct = total * 100.0
        if total > 1.0 + _SUM_TOLERANCE:
            warnings.append(AllocationWarning(
                key=group_key, kind="sum_high",
                message=(
                    f"{group_key}: allocations sum to {total_pct:.1f}% "
                    f"(>{(1.0 + _SUM_TOLERANCE) * 100:.0f}%) for "
                    f"fiscal_year={fy}, period={period}."
                ),
                sum_pct=total_pct,
            ))
        elif total < 1.0 - _SUM_TOLERANCE:
            warnings.append(AllocationWarning(
                key=group_key, kind="sum_low",
                message=(
                    f"{group_key}: allocations sum to {total_pct:.1f}% "
                    f"(<{(1.0 - _SUM_TOLERANCE) * 100:.0f}%) for "
                    f"fiscal_year={fy}, period={period}. The remainder "
                    f"will be shown in the 'Other / unallocated' bucket."
                ),
                sum_pct=total_pct,
            ))
    return warnings


# ─────────────────────────────────────────────────────────────────────
# Portfolio-level aggregation
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RevenueExposureBucket:
    """One row in the portfolio revenue-geography breakdown."""

    region: str
    weight_pct: float           # 0–100, summed across holdings
    holding_count: int
    tickers: list[str] = field(default_factory=list)


@dataclass
class RevenueExposureReport:
    """Aggregated portfolio revenue-geography breakdown.

    ``data_source`` tells the customer where this came from (always
    ``"manual_upload"`` in Phase 10).  ``status`` reports whether the
    portfolio's revenue-geography coverage is ``missing`` / ``partial``
    / ``available`` (see :class:`RevenueGeographyStatus`).
    """

    buckets: list[RevenueExposureBucket]
    missing_holdings: list[dict[str, Any]]    # [{ticker, holding_id, weight_pct}]
    holdings_with_data: int
    holdings_without_data: int
    data_source: str = "manual_upload"
    fiscal_years_covered: list[int] = field(default_factory=list)
    status: str = "missing"                   # see RevenueGeographyStatus
    notes: list[str] = field(default_factory=list)


#: Typed availability status the grounded AI context + UI use.
RevenueGeographyStatus = Literal["missing", "partial", "available"]


def _aggregate_for_holding(
    rows: list[RevenueGeography],
    holding_weight_pct: float,
    ticker: str,
    *,
    fiscal_year: int | None,
) -> dict[str, float]:
    """Spread one holding's portfolio weight across its uploaded regions.

    Honours the *latest* fiscal year present in ``rows`` (or the
    explicit ``fiscal_year`` if supplied).  When the regional shares
    sum to less than 1.0 the unallocated portion flows to the bucket
    ``"Other / unallocated"`` so the dashboard can show it honestly.
    """
    if not rows:
        return {}

    # Filter to the requested fiscal year or default to the most recent.
    if fiscal_year is not None:
        scoped = [r for r in rows if (r.fiscal_year or 0) == fiscal_year]
    else:
        latest = max((r.fiscal_year for r in rows if r.fiscal_year), default=None)
        scoped = [r for r in rows if (r.fiscal_year or None) == latest]
        if not scoped:
            scoped = rows

    region_totals: dict[str, float] = defaultdict(float)
    for r in scoped:
        region = normalize_region(r.region)
        region_totals[region] += float(r.revenue_share or 0.0)
    total_share = sum(region_totals.values())

    out: dict[str, float] = {}
    for region, share in region_totals.items():
        out[region] = holding_weight_pct * share

    leftover = 1.0 - total_share
    if leftover > _SUM_TOLERANCE:
        out["Other / unallocated"] = out.get("Other / unallocated", 0.0) + \
            holding_weight_pct * leftover
    return out


async def compute_portfolio_revenue_exposure(
    session: AsyncSession,
    *,
    portfolio_id: str,
    fiscal_year: int | None = None,
) -> RevenueExposureReport:
    """Build a portfolio-wide revenue-geography breakdown.

    Strategy:

    1. Load active holdings for the portfolio with their ``weight_pct``.
    2. Load every ``revenue_geography`` row matched to one of those
       holdings (via ``holding_id`` if set, else via ISIN, else via
       ticker).
    3. For each holding with data, spread its portfolio weight across
       the uploaded regions.  Anything left unallocated flows to
       ``"Other / unallocated"``.
    4. Holdings with **no** data go into ``missing_holdings`` AND into
       a top-level bucket ``"Revenue geography not uploaded"`` so the
       chart always sums to 100 %.

    We **never** invent regions from listing country or sector.  When
    no rows exist for a holding, the report says so.
    """
    holdings_rows = (await session.execute(
        select(Holding).where(
            Holding.portfolio_id == portfolio_id,
            Holding.status == "active",
        )
    )).scalars().all()
    holdings: list[Holding] = list(holdings_rows)
    total_weight = sum(h.weight_pct or 0.0 for h in holdings)

    # Load all revenue rows for this portfolio in one shot.
    rg_rows = (await session.execute(
        select(RevenueGeography).where(
            RevenueGeography.portfolio_id == portfolio_id,
        )
    )).scalars().all()

    # Index by holding_id / isin / ticker for fast lookup.
    by_holding: dict[str, list[RevenueGeography]] = defaultdict(list)
    by_isin:    dict[str, list[RevenueGeography]] = defaultdict(list)
    by_ticker:  dict[str, list[RevenueGeography]] = defaultdict(list)
    for r in rg_rows:
        if r.holding_id:
            by_holding[r.holding_id].append(r)
        if r.isin:
            by_isin[r.isin.upper()].append(r)
        if r.ticker:
            by_ticker[r.ticker.upper()].append(r)

    region_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"weight": 0.0, "count": 0, "tickers": set()}
    )
    missing_holdings: list[dict[str, Any]] = []
    holdings_with_data = 0
    holdings_without_data = 0
    fiscal_years: set[int] = set()

    for h in holdings:
        rows = (
            by_holding.get(h.id)
            or (by_isin.get(h.isin.upper()) if h.isin else None)
            or by_ticker.get(h.ticker.upper())
        )
        if not rows:
            holdings_without_data += 1
            missing_holdings.append({
                "holding_id": h.id,
                "ticker": h.ticker,
                "isin": h.isin,
                "weight_pct": float(h.weight_pct or 0.0),
            })
            continue

        holdings_with_data += 1
        for r in rows:
            if r.fiscal_year:
                fiscal_years.add(r.fiscal_year)

        per_region = _aggregate_for_holding(
            rows, float(h.weight_pct or 0.0), h.ticker, fiscal_year=fiscal_year,
        )
        for region, w in per_region.items():
            bucket = region_totals[region]
            bucket["weight"] += w
            bucket["count"] += 1
            bucket["tickers"].add(h.ticker)

    # Holdings without revenue data go into a dedicated bucket so the
    # chart still sums to ~100 % of the portfolio.
    missing_weight = sum(m["weight_pct"] for m in missing_holdings)
    if missing_weight > 0:
        region_totals["Revenue geography not uploaded"] = {
            "weight": missing_weight,
            "count": len(missing_holdings),
            "tickers": {m["ticker"] for m in missing_holdings},
        }

    buckets = [
        RevenueExposureBucket(
            region=region,
            weight_pct=round(data["weight"], 4),
            holding_count=data["count"],
            tickers=sorted(data["tickers"]),
        )
        for region, data in region_totals.items()
    ]
    buckets.sort(key=lambda b: b.weight_pct, reverse=True)

    if holdings_with_data == 0:
        status: str = "missing"
    elif holdings_without_data > 0:
        status = "partial"
    else:
        status = "available"

    notes: list[str] = []
    if status == "missing":
        notes.append(
            "No revenue geography uploaded for this portfolio. "
            "Listing country is not used as a fallback."
        )
    elif status == "partial":
        notes.append(
            f"{holdings_without_data} of {len(holdings)} holding(s) "
            f"have no revenue-geography upload."
        )
    if total_weight and abs(total_weight - 100.0) > 5:
        notes.append(
            f"Sum of holding weights is {total_weight:.1f}% — totals "
            f"below may not align with 100%."
        )

    return RevenueExposureReport(
        buckets=buckets,
        missing_holdings=missing_holdings,
        holdings_with_data=holdings_with_data,
        holdings_without_data=holdings_without_data,
        data_source="manual_upload",
        fiscal_years_covered=sorted(fiscal_years),
        status=status,
        notes=notes,
    )


async def list_missing_revenue_holdings(
    session: AsyncSession, *, portfolio_id: str,
) -> list[dict[str, Any]]:
    """Holdings in ``portfolio_id`` with no matching revenue rows.

    Convenience wrapper used by the "missing" API route + the
    dashboard's "Holdings without breakdowns" panel.
    """
    report = await compute_portfolio_revenue_exposure(
        session, portfolio_id=portfolio_id,
    )
    return report.missing_holdings


async def portfolio_revenue_geography_status(
    session: AsyncSession, *, portfolio_id: str,
) -> str:
    """Return ``missing`` / ``partial`` / ``available`` for grounded AI.

    Cheap-ish (one COUNT + holdings load); see
    :func:`compute_portfolio_revenue_exposure` for the full report.
    """
    rep = await compute_portfolio_revenue_exposure(
        session, portfolio_id=portfolio_id,
    )
    return rep.status
