"""Phase 9 — match a corporate-event row to a holding.

Match strategy (in priority order):

1. **ISIN exact** — single authoritative identifier.  Only used when
   the event row carries a non-empty ISIN AND a holding in the
   target portfolio has the same ISIN.
2. **Ticker exact, case-insensitive** — only within the target
   portfolio, never across portfolios.  Used when ISIN matching
   fails OR the event has no ISIN.
3. **Unmatched** — the row is still stored (so the operator can audit
   what came in) but ``holding_id`` is left ``None`` and
   ``match_method`` is recorded as ``"unmatched"``.

This module is pure I/O over an :class:`AsyncSession`; the caller
owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Holding


@dataclass(frozen=True)
class MatchResult:
    holding_id: str | None
    method: str   # "isin" | "ticker" | "unmatched"


async def match_to_holding(
    session: AsyncSession,
    *,
    portfolio_id: str,
    isin: str | None,
    ticker: str | None,
) -> MatchResult:
    """Return the best :class:`MatchResult` for an event row.

    Portfolio-safe by construction — every query filters on
    ``portfolio_id`` so a Greek ticker held in pA never resolves to a
    holding in pB.
    """
    # 1) ISIN exact (strongest)
    if isin and isinstance(isin, str) and isin.strip():
        isin_clean = isin.strip().upper()
        row = (await session.execute(
            select(Holding.id).where(
                Holding.portfolio_id == portfolio_id,
                Holding.isin == isin_clean,
            ).limit(1)
        )).first()
        if row:
            return MatchResult(holding_id=row[0], method="isin")

    # 2) Ticker exact (case-insensitive)
    if ticker and isinstance(ticker, str) and ticker.strip():
        tkr_clean = ticker.strip().upper()
        row = (await session.execute(
            select(Holding.id).where(
                Holding.portfolio_id == portfolio_id,
                Holding.ticker == tkr_clean,
            ).limit(1)
        )).first()
        if row:
            return MatchResult(holding_id=row[0], method="ticker")

    return MatchResult(holding_id=None, method="unmatched")
