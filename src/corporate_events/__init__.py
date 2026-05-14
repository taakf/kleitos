"""Phase 9 — Corporate / issuer events.

This module is the home of the top-level *Events* surface, kept
deliberately separate from the news pipeline under
:mod:`src.sources` and :mod:`src.agents.collection`.  The vocabulary
the rest of the app uses:

* **News** (`/api/v1/events`, `events` table) — published news items,
  classified by macro factor, materiality, etc.  Phase 8 hardened
  this surface.
* **Corporate events** (`/api/v1/corporate-events`, `corporate_events`
  table) — scheduled issuer events (earnings, dividends, AGMs, …).
  This module.

Two ingestion paths:

1. :mod:`.athex` — placeholder for the ATHEX corporate-events feed.
   Currently returns an honest ``degraded`` status because Athens
   Exchange does not publish a stable public machine-readable
   endpoint.  The architecture is in place so a future build can
   point it at a reliable URL without re-shaping the schema or API.

2. :mod:`.manual_import` — operator-facing CSV import.  This is the
   primary supported path for the Phase 9 release.  Every imported
   row is matched to a holding via ISIN first then ticker; unmatched
   rows are still stored with ``match_method='unmatched'`` so the
   operator can audit them.
"""

from src.corporate_events.athex import (
    AthexFetchResult,
    fetch_athex_events,
)
from src.corporate_events.manual_import import (
    ImportRowError,
    ImportSummary,
    import_csv,
    parse_csv,
)
from src.corporate_events.matcher import match_to_holding

__all__ = [
    "AthexFetchResult",
    "fetch_athex_events",
    "ImportRowError",
    "ImportSummary",
    "import_csv",
    "parse_csv",
    "match_to_holding",
]
