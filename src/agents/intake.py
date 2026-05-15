"""Intake agent -- ingests portfolio data from CSV/JSON sources.

Responsibilities:
- Parse raw portfolio uploads (CSV strings, JSON dicts)
- Validate required fields (ticker, quantity, cost basis, etc.)
- Standardise tickers (uppercase, stripped)
- Standardise venue names to MIC codes
- Detect conflicts with existing holdings
- Ingest individual trades (buy/sell/dividend) and update holdings
- Calculate portfolio weight percentages after intake
- Return a structured IntakeResult
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import select

from src.database.models import Holding, Trade

from .base import BaseAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class IntakeResult:
    """Structured output of an intake run."""

    added: list[dict[str, Any]] = field(default_factory=list)
    updated: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "conflicts": len(self.conflicts),
            "errors": len(self.errors),
        }


# ---------------------------------------------------------------------------
# Required / optional field definitions
# ---------------------------------------------------------------------------
REQUIRED_FIELDS: list[str] = ["ticker", "quantity"]
OPTIONAL_FIELDS: list[str] = [
    "cost_basis",
    "currency",
    "account",
    "acquired_date",
    "notes",
]


def _validate_isin(isin: str) -> bool:
    """Validate an ISIN using the Luhn check-digit algorithm (ISO 6166).

    An ISIN has format: 2 letter country code + 9 alphanumeric chars + 1 check digit.
    """
    if not isin or len(isin) != 12:
        return False

    # Must start with 2 uppercase letters
    if not isin[:2].isalpha() or not isin[:2].isupper():
        return False

    # Convert letters to numbers: A=10, B=11, ..., Z=35
    digits = ""
    for ch in isin:
        if ch.isdigit():
            digits += ch
        elif ch.isalpha():
            digits += str(ord(ch.upper()) - ord('A') + 10)
        else:
            return False

    # Luhn algorithm
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n

    return total % 10 == 0


# ISO 4217 currency codes (common subset)
_VALID_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "HKD", "SGD",
    "SEK", "NOK", "DKK", "CNY", "CNH", "KRW", "TWD", "INR", "BRL", "MXN",
    "ZAR", "TRY", "PLN", "CZK", "HUF", "ILS", "THB", "IDR", "MYR", "PHP",
    "RUB", "AED", "SAR", "QAR", "KWD", "BHD", "OMR", "CLP", "COP", "PEN",
    "ARS", "EGP", "NGN", "KES", "GHS",
}


def _validate_currency(currency: str) -> bool:
    """Check if currency is a valid ISO 4217 code."""
    return currency.upper().strip() in _VALID_CURRENCIES


# ---------------------------------------------------------------------------
# MIC code mapping for venue standardisation
# ---------------------------------------------------------------------------
_VENUE_TO_MIC: dict[str, str] = {
    # US
    "NYSE": "XNYS",
    "NEW YORK STOCK EXCHANGE": "XNYS",
    "NASDAQ": "XNAS",
    "NASD": "XNAS",
    "AMEX": "XASE",
    "AMERICAN STOCK EXCHANGE": "XASE",
    "CBOE": "XCBO",
    "ARCA": "ARCX",
    "NYSE ARCA": "ARCX",
    "BATS": "BATS",
    "IEX": "IEXG",
    # Europe
    "LSE": "XLON",
    "LONDON STOCK EXCHANGE": "XLON",
    "EURONEXT": "XPAR",
    "EURONEXT PARIS": "XPAR",
    "EURONEXT AMSTERDAM": "XAMS",
    "XETRA": "XETR",
    "FRANKFURT": "XFRA",
    "DEUTSCHE BOERSE": "XETR",
    "SIX": "XSWX",
    "SIX SWISS EXCHANGE": "XSWX",
    "BORSA ITALIANA": "XMIL",
    "BME": "XMAD",
    "MADRID": "XMAD",
    # Asia-Pacific
    "TSE": "XTKS",
    "TOKYO STOCK EXCHANGE": "XTKS",
    "HKEX": "XHKG",
    "HONG KONG": "XHKG",
    "HONG KONG STOCK EXCHANGE": "XHKG",
    "SSE": "XSHG",
    "SHANGHAI": "XSHG",
    "SHANGHAI STOCK EXCHANGE": "XSHG",
    "SZSE": "XSHE",
    "SHENZHEN": "XSHE",
    "SHENZHEN STOCK EXCHANGE": "XSHE",
    "ASX": "XASX",
    "AUSTRALIAN SECURITIES EXCHANGE": "XASX",
    "KRX": "XKRX",
    "KOREA EXCHANGE": "XKRX",
    "SGX": "XSES",
    "SINGAPORE EXCHANGE": "XSES",
    "BSE": "XBOM",
    "BOMBAY STOCK EXCHANGE": "XBOM",
    "NSE INDIA": "XNSE",
    # Americas
    "TSX": "XTSE",
    "TORONTO STOCK EXCHANGE": "XTSE",
    "B3": "BVMF",
    "BOVESPA": "BVMF",
}

# Also allow already-valid MIC codes to pass through
_VALID_MICS = set(_VENUE_TO_MIC.values())

_VALID_TRADE_TYPES = {"buy", "sell", "dividend"}


@dataclass
class TradeResult:
    """Structured output of a trade ingestion run."""

    trades_created: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "trades_created": len(self.trades_created),
            "errors": len(self.errors),
        }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class IntakeAgent(BaseAgent):
    """Ingests portfolio data, validates, and persists holdings/trades."""

    agent_name: ClassVar[str] = "intake"
    read_permissions: ClassVar[list[str]] = ["holdings", "trades", "securities"]
    write_permissions: ClassVar[list[str]] = ["holdings", "trades", "audit_log", "agent_runs"]

    # -- public API --------------------------------------------------------

    async def run(self, **kwargs: Any) -> IntakeResult:
        """Dispatch to the correct ingestion method based on provided kwargs.

        Keyword Args
        -------------
        csv_content : str, optional
            Raw CSV string to ingest.
        json_data : dict, optional
            Parsed JSON payload to ingest.
        """
        csv_content: str | None = kwargs.get("csv_content")
        json_data: dict | None = kwargs.get("json_data")

        if csv_content is not None:
            return await self.process_csv(csv_content)
        if json_data is not None:
            return await self.process_json(json_data)

        raise ValueError("IntakeAgent.run() requires either csv_content or json_data")

    async def process_csv(self, file_content: str) -> IntakeResult:
        """Parse a CSV string into holdings rows and persist them.

        Expected columns mirror ``REQUIRED_FIELDS`` + ``OPTIONAL_FIELDS``.
        """
        await self._log_run_start(parameters={"source": "csv", "bytes": len(file_content)})
        result = IntakeResult()

        try:
            reader = csv.DictReader(io.StringIO(file_content))
            rows = list(reader)

            if not rows:
                result.errors.append("CSV contained no data rows")
                await self._log_run_complete(result_summary=result.summary)
                return result

            for idx, row in enumerate(rows, start=1):
                await self._process_row(row, idx, result)

            # B4: recalculate portfolio weights after intake
            await self._update_weight_pcts()

            await self._log_run_complete(result_summary=result.summary)
        except Exception as exc:
            result.errors.append(f"CSV parsing failed: {exc}")
            await self._log_run_error(exc)

        return result

    async def process_json(self, data: dict) -> IntakeResult:
        """Ingest a JSON payload.

        Expected shape::

            {
                "holdings": [
                    {"ticker": "AAPL", "quantity": 100, ...},
                    ...
                ]
            }
        """
        await self._log_run_start(parameters={"source": "json"})
        result = IntakeResult()

        try:
            holdings_list = data.get("holdings", [])
            if not isinstance(holdings_list, list):
                result.errors.append("'holdings' key must be a list")
                await self._log_run_complete(result_summary=result.summary)
                return result

            for idx, row in enumerate(holdings_list, start=1):
                await self._process_row(row, idx, result)

            # B4: recalculate portfolio weights after intake
            await self._update_weight_pcts()

            await self._log_run_complete(result_summary=result.summary)
        except Exception as exc:
            result.errors.append(f"JSON processing failed: {exc}")
            await self._log_run_error(exc)

        return result

    # -- internal helpers --------------------------------------------------

    async def _process_row(
        self,
        row: dict[str, Any],
        row_num: int,
        result: IntakeResult,
    ) -> None:
        """Validate, standardise, and persist a single row."""
        # --- validation ---------------------------------------------------
        missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
        if missing:
            result.errors.append(f"Row {row_num}: missing required fields {missing}")
            return

        # --- standardise ticker -------------------------------------------
        ticker = str(row["ticker"]).strip().upper()
        row["ticker"] = ticker

        # --- validate ISIN if provided -----------------------------------
        isin = row.get("isin")
        if isin and not _validate_isin(str(isin).strip().upper()):
            result.errors.append(f"Row {row_num}: invalid ISIN '{isin}'")
            return

        # --- validate currency if provided --------------------------------
        currency = row.get("currency", "USD")
        if not _validate_currency(currency):
            result.errors.append(f"Row {row_num}: invalid currency '{currency}'")
            return

        # --- standardise venue if provided --------------------------------
        venue = row.get("venue")
        if venue:
            row["venue"] = self._standardise_venue(str(venue))

        # --- coerce quantity ----------------------------------------------
        try:
            quantity = float(row["quantity"])
        except (ValueError, TypeError):
            result.errors.append(f"Row {row_num}: invalid quantity '{row['quantity']}'")
            return

        # --- check for conflicts with existing holdings -------------------
        self._check_permission("holdings", "read")
        async with self._get_db() as session:
            stmt = select(Holding).where(Holding.ticker == ticker)
            existing = (await session.execute(stmt)).scalars().first()

        if existing:
            # Conflict: holding for this ticker already exists
            conflict_info = {
                "row": row_num,
                "ticker": ticker,
                "existing_id": existing.id,
                "existing_quantity": existing.quantity,
                "incoming_quantity": quantity,
            }
            result.conflicts.append(conflict_info)
            logger.info("Conflict detected for ticker=%s  row=%d", ticker, row_num)
            return

        # --- persist new holding ------------------------------------------
        self._check_permission("holdings", "write")
        holding_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        new_holding = Holding(
            id=holding_id,
            ticker=ticker,
            quantity=quantity,
            avg_cost_basis=float(row["cost_basis"]) if row.get("cost_basis") else None,
            currency=row.get("currency", "USD"),
            venue=row.get("venue"),
            isin=str(isin).strip().upper() if isin else None,
            created_at=now,
            updated_at=now,
        )

        async with self._get_db() as session:
            session.add(new_holding)
            await session.commit()

        await self._audit_log(
            action="created",
            entity_type="holding",
            entity_id=holding_id,
            details={"ticker": ticker, "quantity": quantity, "source_row": row_num},
        )

        result.added.append({"id": holding_id, "ticker": ticker, "quantity": quantity})
        logger.info("Added holding  id=%s  ticker=%s  qty=%s", holding_id, ticker, quantity)

    # -- B3: venue standardisation -----------------------------------------

    @staticmethod
    def _standardise_venue(venue: str) -> str:
        """Convert a human-readable exchange name to its ISO 10383 MIC code.

        If the input is already a known MIC code it is returned unchanged.
        Unknown venue strings are returned as-is (uppercased/stripped).
        """
        normalised = venue.strip().upper()
        if normalised in _VALID_MICS:
            return normalised
        return _VENUE_TO_MIC.get(normalised, normalised)

    # -- B1: trade ingestion -----------------------------------------------

    async def ingest_trades(self, trades: list[dict[str, Any]]) -> TradeResult:
        """Ingest a list of trade dicts, updating holdings accordingly.

        Each trade dict should contain:
            ticker (str, required), trade_type (str: buy/sell/dividend, required),
            quantity (float, required), price (float, required),
            trade_date (str, required), settlement_date (str, optional),
            currency (str, optional, default USD), notes (str, optional),
            venue (str, optional).

        Returns a TradeResult with created trades and any errors.
        """
        await self._log_run_start(parameters={"source": "trade_ingestion", "count": len(trades)})
        result = TradeResult()

        try:
            for idx, trade in enumerate(trades, start=1):
                await self._process_trade(trade, idx, result)

            # Recalculate portfolio weights after trades
            await self._update_weight_pcts()

            await self._log_run_complete(result_summary=result.summary)
        except Exception as exc:
            result.errors.append(f"Trade ingestion failed: {exc}")
            await self._log_run_error(exc)

        return result

    async def _process_trade(
        self,
        trade: dict[str, Any],
        idx: int,
        result: TradeResult,
    ) -> None:
        """Validate and persist a single trade, updating the holding."""
        # --- validate required fields -------------------------------------
        ticker_raw = trade.get("ticker")
        if not ticker_raw:
            result.errors.append(f"Trade {idx}: ticker is required")
            return
        ticker = str(ticker_raw).strip().upper()

        trade_type = str(trade.get("trade_type", "")).strip().lower()
        if trade_type not in _VALID_TRADE_TYPES:
            result.errors.append(
                f"Trade {idx}: trade_type must be one of {sorted(_VALID_TRADE_TYPES)}, got '{trade_type}'"
            )
            return

        # --- validate quantity / price ------------------------------------
        try:
            quantity = float(trade["quantity"])
        except (KeyError, ValueError, TypeError):
            result.errors.append(f"Trade {idx}: quantity is required and must be numeric")
            return
        if quantity <= 0:
            result.errors.append(f"Trade {idx}: quantity must be > 0")
            return

        try:
            price = float(trade["price"])
        except (KeyError, ValueError, TypeError):
            result.errors.append(f"Trade {idx}: price is required and must be numeric")
            return
        if price < 0:
            result.errors.append(f"Trade {idx}: price must be >= 0")
            return

        trade_date = trade.get("trade_date")
        if not trade_date:
            result.errors.append(f"Trade {idx}: trade_date is required")
            return

        currency = str(trade.get("currency", "USD")).strip().upper()
        if not _validate_currency(currency):
            result.errors.append(f"Trade {idx}: invalid currency '{currency}'")
            return

        settlement_date = trade.get("settlement_date")
        notes = trade.get("notes")

        # --- standardise venue if provided --------------------------------
        venue = trade.get("venue")
        if venue:
            venue = self._standardise_venue(str(venue))

        # --- look up existing holding -------------------------------------
        self._check_permission("holdings", "read")
        async with self._get_db() as session:
            stmt = select(Holding).where(
                Holding.ticker == ticker,
                Holding.status == "active",
            )
            holding = (await session.execute(stmt)).scalars().first()

        # --- trade-type-specific logic ------------------------------------
        now = datetime.now(timezone.utc).isoformat()
        trade_id = str(uuid.uuid4())

        if trade_type == "buy":
            # For buys: create or update holding
            self._check_permission("holdings", "write")
            if holding:
                # Update existing: recalculate avg_cost_basis
                old_total_cost = (holding.quantity or 0) * (holding.avg_cost_basis or 0)
                new_total_cost = old_total_cost + (quantity * price)
                new_quantity = (holding.quantity or 0) + quantity
                new_avg_cost = new_total_cost / new_quantity if new_quantity else 0

                async with self._get_db() as session:
                    h = await session.get(Holding, holding.id)
                    h.quantity = new_quantity
                    h.avg_cost_basis = round(new_avg_cost, 6)
                    if venue and not h.venue:
                        h.venue = venue
                    h.updated_at = now
                    await session.commit()
                    holding_id = h.id
            else:
                # Create new holding
                holding_id = str(uuid.uuid4())
                new_holding = Holding(
                    id=holding_id,
                    ticker=ticker,
                    quantity=quantity,
                    avg_cost_basis=price,
                    currency=currency,
                    venue=venue,
                    created_at=now,
                    updated_at=now,
                )
                async with self._get_db() as session:
                    session.add(new_holding)
                    await session.commit()

        elif trade_type == "sell":
            # For sells: holding must exist and have enough quantity
            if not holding:
                result.errors.append(
                    f"Trade {idx}: cannot sell {ticker} — no active holding found"
                )
                return
            if (holding.quantity or 0) < quantity:
                result.errors.append(
                    f"Trade {idx}: cannot sell {quantity} of {ticker} — only {holding.quantity} held"
                )
                return

            self._check_permission("holdings", "write")
            async with self._get_db() as session:
                h = await session.get(Holding, holding.id)
                h.quantity = (h.quantity or 0) - quantity
                h.updated_at = now
                await session.commit()
                holding_id = h.id

        elif trade_type == "dividend":
            # For dividends: holding must exist, no quantity change
            if not holding:
                result.errors.append(
                    f"Trade {idx}: cannot record dividend for {ticker} — no active holding found"
                )
                return
            holding_id = holding.id

        # --- persist the trade record -------------------------------------
        self._check_permission("trades", "write")
        new_trade = Trade(
            id=trade_id,
            holding_id=holding_id,
            ticker=ticker,
            trade_type=trade_type,
            quantity=quantity,
            price=price,
            currency=currency,
            trade_date=str(trade_date),
            settlement_date=str(settlement_date) if settlement_date else None,
            notes=notes,
            source="manual",
            created_at=now,
        )
        async with self._get_db() as session:
            session.add(new_trade)
            await session.commit()

        # --- audit log ----------------------------------------------------
        await self._audit_log(
            action=f"trade_{trade_type}",
            entity_type="trade",
            entity_id=trade_id,
            details={
                "ticker": ticker,
                "trade_type": trade_type,
                "quantity": quantity,
                "price": price,
                "holding_id": holding_id,
            },
        )

        result.trades_created.append({
            "id": trade_id,
            "ticker": ticker,
            "trade_type": trade_type,
            "quantity": quantity,
            "price": price,
            "holding_id": holding_id,
        })
        logger.info(
            "Trade created  id=%s  ticker=%s  type=%s  qty=%s  price=%s",
            trade_id, ticker, trade_type, quantity, price,
        )

    # -- B4: portfolio weight calculation ----------------------------------

    async def _update_weight_pcts(self) -> None:
        """Recalculate weight_pct for all active holdings.

        weight = market_value / total_portfolio_market_value * 100
        where market_value = quantity * (current_price or avg_cost_basis).
        """
        self._check_permission("holdings", "read")
        async with self._get_db() as session:
            stmt = select(Holding).where(Holding.status == "active")
            holdings = list((await session.execute(stmt)).scalars().all())

        if not holdings:
            return

        # Calculate market values
        mv_map: dict[str, float] = {}
        for h in holdings:
            price = h.current_price if h.current_price is not None else (h.avg_cost_basis or 0)
            mv = (h.quantity or 0) * price
            mv_map[h.id] = mv

        total_mv = sum(mv_map.values())
        if total_mv <= 0:
            return

        # Update each holding
        self._check_permission("holdings", "write")
        now = datetime.now(timezone.utc).isoformat()
        async with self._get_db() as session:
            for h_id, mv in mv_map.items():
                h = await session.get(Holding, h_id)
                if h:
                    h.market_value = round(mv, 4)
                    h.weight_pct = round(mv / total_mv * 100, 4)
                    h.updated_at = now
            await session.commit()

        logger.info(
            "Updated weight_pct for %d holdings  total_mv=%.2f",
            len(mv_map), total_mv,
        )
