"""Phase 10 — Revenue geography service.

This package is the single source of truth for the Phase 10 question
"where does a holding's *issuer* actually earn revenue?".

Hard separation from listing country
------------------------------------
``securities.geography`` (and :mod:`src.intelligence.listing`) answer
the **listing-country** question: which CSD issued the ISIN, which
exchange does the instrument trade on.  Those are derived
deterministically from identifiers.

This package answers the **revenue-geography** question — and it
*never* derives an answer from listing.  Either a row exists in the
:class:`src.database.models.RevenueGeography` table (uploaded by an
operator or extracted from an annual report) or the service reports
``missing`` for that holding.  No silent fallbacks, no inferences.

Public surface
--------------
* :func:`normalize_region`               — canonical region label
* :func:`parse_revenue_share`            — number / 45 / 45% / 0.45
* :func:`validate_company_allocations`   — sum-100% check + warnings
* :func:`compute_portfolio_revenue_exposure`
                                         — weighted regional breakdown
                                           + missing/unknown bucket
* :func:`portfolio_revenue_geography_status`
                                         — typed missing/partial/available
                                           status for grounded AI
* :mod:`.manual_import`                  — CSV upload pipeline
"""

from src.intelligence.revenue_geography.service import (
    RevenueExposureBucket,
    RevenueExposureReport,
    RevenueGeographyStatus,
    compute_portfolio_revenue_exposure,
    list_missing_revenue_holdings,
    normalize_country,
    normalize_region,
    parse_revenue_share,
    portfolio_revenue_geography_status,
    validate_company_allocations,
)
from src.intelligence.revenue_geography.manual_import import (
    ImportRowError,
    ImportSummary,
    import_csv,
    parse_csv,
)
from src.intelligence.revenue_geography.extraction import (
    EXTRACTION_PROMPT,
    ExtractedCandidate,
    ExtractionResult,
    extract_from_pdf_bytes,
    extract_from_text,
)

__all__ = [
    "EXTRACTION_PROMPT",
    "ExtractedCandidate",
    "ExtractionResult",
    "extract_from_pdf_bytes",
    "extract_from_text",
    "ImportRowError",
    "ImportSummary",
    "RevenueExposureBucket",
    "RevenueExposureReport",
    "RevenueGeographyStatus",
    "compute_portfolio_revenue_exposure",
    "import_csv",
    "list_missing_revenue_holdings",
    "normalize_country",
    "normalize_region",
    "parse_csv",
    "parse_revenue_share",
    "portfolio_revenue_geography_status",
    "validate_company_allocations",
]
