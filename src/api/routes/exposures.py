"""Phase 10 — Exposure routes (listing country vs revenue geography).

This router is the explicit, customer-friendly home for the two
exposure concepts the Portfolio tab cares about:

* ``GET  /api/v1/exposures/listing-country``
    Returns the same listing-country breakdown that
    ``GET /api/v1/portfolio/exposure?dimension=geography`` returns,
    but with a clearer name + a customer-safe ``data_source``
    metadata field that makes the distinction obvious to API
    consumers.  The legacy endpoint stays in place untouched for
    back-compat with anything that still calls it.

* ``GET  /api/v1/exposures/revenue-geography``
    Returns the Phase 10 revenue-geography breakdown built from the
    operator-uploaded ``revenue_geography`` table.  When nothing has
    been uploaded the response is honest: ``status="missing"``,
    empty bucket list, and a customer-safe note.

* ``POST /api/v1/exposures/revenue-geography/import``
    Operator CSV upload.

* ``GET  /api/v1/exposures/revenue-geography/missing``
    Holdings in the portfolio with no revenue-geography upload yet.

Every URL returned from this router is scrubbed by the Phase 8
helper.  Errors are typed and customer-safe (no tracebacks).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.api.routes.events import _scrub_url
from src.database.models import Holding, Portfolio, RevenueGeography, Security
from src.intelligence.revenue_geography import (
    EXTRACTION_PROMPT,                       # noqa: F401 — re-exported for tests
    ExtractionResult,
    compute_portfolio_revenue_exposure,
    extract_from_pdf_bytes,
    extract_from_text,
    import_csv,
    list_missing_revenue_holdings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/exposures", tags=["exposures"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ListingCountryBucket(BaseModel):
    label: str
    weight_pct: float
    holding_count: int
    tickers: list[str] = Field(default_factory=list)


class ListingCountryExposureResponse(BaseModel):
    """Listing-country / listing-exchange breakdown.

    ``data_source`` documents that this is **not** revenue geography —
    callers should read it and surface the right label.
    """

    portfolio_id: str
    dimension: str = "listing_country"
    data_source: str = "isin_prefix_or_venue"
    buckets: list[ListingCountryBucket]


class RevenueGeographyBucket(BaseModel):
    region: str
    weight_pct: float
    holding_count: int
    tickers: list[str] = Field(default_factory=list)


class MissingHolding(BaseModel):
    holding_id: str
    ticker: str
    isin: str | None = None
    weight_pct: float


class RevenueGeographyResponse(BaseModel):
    """Revenue-geography breakdown built from uploaded rows."""

    portfolio_id: str
    dimension: str = "revenue_geography"
    data_source: str = "manual_upload"
    status: str                                # missing | partial | available
    buckets: list[RevenueGeographyBucket]
    missing_holdings: list[MissingHolding]
    holdings_with_data: int
    holdings_without_data: int
    fiscal_years_covered: list[int]
    notes: list[str]


class ImportPayload(BaseModel):
    portfolio_id: str = Field(..., description="Target portfolio id")
    csv_text: str = Field(..., description="Full CSV body")
    source_type: str = "manual_csv"
    source_name: str = "Manual CSV Import"


class ImportResponse(BaseModel):
    imported: int
    skipped_duplicate: int
    matched_by_isin: int
    matched_by_ticker: int
    unmatched: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    batch_id: str


# ---------------------------------------------------------------------------
# Listing-country exposure
# ---------------------------------------------------------------------------


@router.get("/listing-country", response_model=ListingCountryExposureResponse)
async def get_listing_country_exposure(
    portfolio_id: str = Query("default"),
    session: AsyncSession = Depends(get_session),
) -> ListingCountryExposureResponse:
    """Listing-country breakdown for the active portfolio.

    Reads ``securities.geography`` (which is ISIN-prefix-derived) so
    the answer is the same vintage the legacy ``/portfolio/exposure``
    endpoint has always returned — just with a customer-honest name.
    Never returns revenue geography.
    """
    stmt = (
        select(Holding, Security)
        .outerjoin(Security, Holding.ticker == Security.ticker)
        .where(
            Holding.portfolio_id == portfolio_id,
            Holding.status == "active",
        )
    )
    rows = (await session.execute(stmt)).all()

    accum: dict[str, dict[str, Any]] = {}
    for h, s in rows:
        label = (s.geography if s and s.geography else None) or "Unknown"
        slot = accum.setdefault(
            label,
            {"weight_pct": 0.0, "holding_count": 0, "tickers": set()},
        )
        slot["weight_pct"] += float(h.weight_pct or 0.0)
        slot["holding_count"] += 1
        slot["tickers"].add(h.ticker)

    buckets = sorted(
        [
            ListingCountryBucket(
                label=label,
                weight_pct=round(data["weight_pct"], 4),
                holding_count=data["holding_count"],
                tickers=sorted(data["tickers"]),
            )
            for label, data in accum.items()
        ],
        key=lambda b: b.weight_pct,
        reverse=True,
    )
    return ListingCountryExposureResponse(
        portfolio_id=portfolio_id,
        buckets=buckets,
    )


# ---------------------------------------------------------------------------
# Revenue-geography exposure
# ---------------------------------------------------------------------------


@router.get("/revenue-geography", response_model=RevenueGeographyResponse)
async def get_revenue_geography(
    portfolio_id: str = Query("default"),
    fiscal_year: int | None = Query(None, description="Optional fiscal year filter"),
    session: AsyncSession = Depends(get_session),
) -> RevenueGeographyResponse:
    """Portfolio revenue-geography breakdown.

    Returns ``status="missing"`` honestly when no rows are uploaded —
    never falls back to listing country.
    """
    rep = await compute_portfolio_revenue_exposure(
        session, portfolio_id=portfolio_id, fiscal_year=fiscal_year,
    )
    return RevenueGeographyResponse(
        portfolio_id=portfolio_id,
        status=rep.status,
        buckets=[
            RevenueGeographyBucket(
                region=b.region,
                weight_pct=round(b.weight_pct, 4),
                holding_count=b.holding_count,
                tickers=b.tickers,
            )
            for b in rep.buckets
        ],
        missing_holdings=[
            MissingHolding(
                holding_id=m["holding_id"],
                ticker=m["ticker"],
                isin=m.get("isin"),
                weight_pct=float(m["weight_pct"]),
            )
            for m in rep.missing_holdings
        ],
        holdings_with_data=rep.holdings_with_data,
        holdings_without_data=rep.holdings_without_data,
        fiscal_years_covered=rep.fiscal_years_covered,
        notes=rep.notes,
    )


@router.get("/revenue-geography/missing", response_model=list[MissingHolding])
async def get_missing_revenue_holdings(
    portfolio_id: str = Query("default"),
    session: AsyncSession = Depends(get_session),
) -> list[MissingHolding]:
    rows = await list_missing_revenue_holdings(session, portfolio_id=portfolio_id)
    return [
        MissingHolding(
            holding_id=r["holding_id"],
            ticker=r["ticker"],
            isin=r.get("isin"),
            weight_pct=float(r["weight_pct"]),
        )
        for r in rows
    ]


@router.post("/revenue-geography/import", response_model=ImportResponse)
async def import_revenue_geography(
    payload: ImportPayload = Body(...),
    session: AsyncSession = Depends(get_session),
) -> ImportResponse:
    """Operator-supplied CSV import for revenue-geography rows."""
    if not payload.csv_text or not payload.csv_text.strip():
        raise HTTPException(status_code=400, detail="csv_text is empty")
    if not payload.portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")

    pf = (await session.execute(
        select(Portfolio.id).where(Portfolio.id == payload.portfolio_id)
    )).first()
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    summary = await import_csv(
        session,
        portfolio_id=payload.portfolio_id,
        csv_text=payload.csv_text,
        source_type=payload.source_type,
        source_name=payload.source_name,
    )
    return ImportResponse(**summary.to_dict())


# ---------------------------------------------------------------------------
# Single-row read (for audit / detail surface, optional but cheap)
# ---------------------------------------------------------------------------


class RevenueGeographyRow(BaseModel):
    id: str
    portfolio_id: str
    holding_id: str | None
    ticker: str | None
    isin: str | None
    company_name: str | None
    region: str
    country: str | None
    revenue_share: float
    fiscal_year: int | None
    period: str | None
    currency: str | None
    source_type: str
    source_name: str | None
    source_url: str | None
    match_method: str | None
    created_at: str
    updated_at: str


@router.get("/revenue-geography/rows", response_model=list[RevenueGeographyRow])
async def list_revenue_geography_rows(
    portfolio_id: str = Query("default"),
    ticker: str | None = Query(None),
    isin: str | None = Query(None),
    holding_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> list[RevenueGeographyRow]:
    """Raw audit list of stored revenue-geography rows."""
    stmt = select(RevenueGeography).where(
        RevenueGeography.portfolio_id == portfolio_id
    )
    if ticker:
        stmt = stmt.where(RevenueGeography.ticker == ticker.strip().upper())
    if isin:
        stmt = stmt.where(RevenueGeography.isin == isin.strip().upper())
    if holding_id:
        stmt = stmt.where(RevenueGeography.holding_id == holding_id)
    stmt = stmt.order_by(
        RevenueGeography.fiscal_year.desc().nulls_last(),
        RevenueGeography.region.asc(),
    ).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        RevenueGeographyRow(
            id=r.id,
            portfolio_id=r.portfolio_id,
            holding_id=r.holding_id,
            ticker=r.ticker,
            isin=r.isin,
            company_name=r.company_name,
            region=r.region,
            country=r.country,
            revenue_share=float(r.revenue_share),
            fiscal_year=r.fiscal_year,
            period=r.period,
            currency=r.currency,
            source_type=r.source_type,
            source_name=r.source_name,
            source_url=_scrub_url(r.source_url),
            match_method=r.match_method,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Phase 11 — AI-assisted extraction (review-first)
# ---------------------------------------------------------------------------
#
# The extract route NEVER persists.  It returns a typed
# :class:`ExtractionResult` (status + candidate list + validation
# errors/warnings) for the operator to review in the UI.  Persistence
# happens only when the operator calls ``/confirm-extraction`` with
# the (possibly edited) candidate rows.
#
# Uploads are processed entirely in memory; PDF bytes never touch
# disk and are not logged.  Logs record filename + byte count + status
# only.  Anything user-facing is scrubbed via the Phase 8 helper.


class ExtractedCandidateOut(BaseModel):
    region: str
    country: str | None = None
    revenue_share: float
    fiscal_year: int | None = None
    period: str | None = None
    currency: str | None = None
    ticker: str | None = None
    isin: str | None = None
    company_name: str | None = None
    evidence_text: str | None = None
    page_number: int | None = None
    confidence: float | None = None
    share_note: str | None = None


class ExtractionResponse(BaseModel):
    status: str
    reason: str
    provider: str | None = None
    model: str | None = None
    source_filename: str | None = None
    source_size_bytes: int | None = None
    fiscal_year: int | None = None
    period: str | None = None
    currency: str | None = None
    company_name: str | None = None
    ticker: str | None = None
    isin: str | None = None
    candidates: list[ExtractedCandidateOut] = Field(default_factory=list)
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    validation_warnings: list[dict[str, Any]] = Field(default_factory=list)


class ExtractTextPayload(BaseModel):
    portfolio_id: str
    text: str
    ticker: str | None = None
    isin: str | None = None
    source_filename: str | None = None


def _result_to_response(result: ExtractionResult) -> ExtractionResponse:
    return ExtractionResponse(
        status=result.status,
        reason=result.reason,
        provider=result.provider,
        model=result.model,
        source_filename=result.source_filename,
        source_size_bytes=result.source_size_bytes,
        fiscal_year=result.fiscal_year,
        period=result.period,
        currency=result.currency,
        company_name=result.company_name,
        ticker=result.ticker,
        isin=result.isin,
        candidates=[
            ExtractedCandidateOut(
                region=c.region,
                country=c.country,
                revenue_share=c.revenue_share,
                fiscal_year=c.fiscal_year,
                period=c.period,
                currency=c.currency,
                ticker=c.ticker,
                isin=c.isin,
                company_name=c.company_name,
                evidence_text=c.evidence_text,
                page_number=c.page_number,
                confidence=c.confidence,
                share_note=c.share_note,
            )
            for c in result.candidates
        ],
        validation_errors=result.validation_errors,
        validation_warnings=result.validation_warnings,
    )


@router.post("/revenue-geography/extract", response_model=ExtractionResponse)
async def extract_revenue_geography(
    portfolio_id: str = Form("default"),
    ticker: str | None = Form(None),
    isin: str | None = Form(None),
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> ExtractionResponse:
    """Phase 11 — AI extract a candidate review payload.  No persistence.

    Either a PDF file (``multipart/form-data``) or a ``text`` field
    must be supplied.  The route returns the typed
    :class:`ExtractionResponse` straight back to the UI for review.
    """
    if not portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")

    # Portfolio must exist (no silent writes against ghost ids).
    pf = (await session.execute(
        select(Portfolio.id).where(Portfolio.id == portfolio_id)
    )).first()
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if file is None and not (text and text.strip()):
        raise HTTPException(
            status_code=400,
            detail="Provide either a 'file' upload or a 'text' field.",
        )

    if file is not None:
        # Read fully into memory and discard the temp upload.  We
        # don't keep the bytes anywhere after the call returns.
        pdf_bytes = await file.read()
        try:
            await file.close()
        except Exception:  # pragma: no cover — best-effort
            pass
        filename = file.filename or "uploaded.pdf"
        logger.info(
            "phase11 extract: %s (%d bytes) portfolio=%s",
            filename, len(pdf_bytes), portfolio_id,
        )
        result = await extract_from_pdf_bytes(
            pdf_bytes=pdf_bytes,
            source_filename=filename,
            ticker_hint=ticker, isin_hint=isin,
        )
    else:
        logger.info(
            "phase11 extract (text): %d chars portfolio=%s",
            len(text or ""), portfolio_id,
        )
        result = await extract_from_text(
            text=text or "",
            source_filename=None,
            ticker_hint=ticker, isin_hint=isin,
        )

    return _result_to_response(result)


class ConfirmCandidateIn(BaseModel):
    region: str
    revenue_share: float = Field(..., ge=0)
    country: str | None = None
    fiscal_year: int | None = None
    period: str | None = None
    currency: str | None = None
    ticker: str | None = None
    isin: str | None = None
    company_name: str | None = None
    evidence_text: str | None = None
    page_number: int | None = None
    confidence: float | None = None


class ConfirmExtractionPayload(BaseModel):
    portfolio_id: str
    candidates: list[ConfirmCandidateIn]
    source_filename: str | None = None
    source_name: str = "AI Extraction (operator-confirmed)"


@router.post(
    "/revenue-geography/confirm-extraction",
    response_model=ImportResponse,
)
async def confirm_revenue_geography_extraction(
    payload: ConfirmExtractionPayload = Body(...),
    session: AsyncSession = Depends(get_session),
) -> ImportResponse:
    """Phase 11 — persist operator-confirmed AI-extracted rows.

    Reuses the Phase 10 ``import_csv`` pipeline by building an
    equivalent CSV body in memory.  This keeps the validation,
    matching, dedup, and audit-log behaviour identical to the manual
    CSV path.  Rows are stored with ``source_type="ai_extracted"`` so
    they're visibly distinct from manual uploads in
    ``/api/v1/exposures/revenue-geography/rows`` and in the support
    bundle.
    """
    if not payload.portfolio_id:
        raise HTTPException(status_code=400, detail="portfolio_id is required")
    if not payload.candidates:
        raise HTTPException(
            status_code=400,
            detail="candidates list is empty — nothing to confirm.",
        )

    pf = (await session.execute(
        select(Portfolio.id).where(Portfolio.id == payload.portfolio_id)
    )).first()
    if pf is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    # Build a CSV body so we reuse the Phase 10 importer wholesale.
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    headers = [
        "ticker", "isin", "company_name", "fiscal_year", "period",
        "region", "country", "revenue_share", "currency", "source_url",
    ]
    writer.writerow(headers)
    for c in payload.candidates:
        writer.writerow([
            (c.ticker or "").strip().upper(),
            (c.isin or "").strip().upper(),
            c.company_name or "",
            "" if c.fiscal_year is None else c.fiscal_year,
            c.period or "",
            c.region,
            c.country or "",
            f"{c.revenue_share:.6f}",
            c.currency or "",
            "",
        ])
    summary = await import_csv(
        session,
        portfolio_id=payload.portfolio_id,
        csv_text=buf.getvalue(),
        source_type="ai_extracted",
        source_name=payload.source_name,
    )
    return ImportResponse(**summary.to_dict())
