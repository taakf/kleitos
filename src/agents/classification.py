"""Classification agent -- enriches holdings with sector, geography, and theme data.

Uses an LLM to classify securities that have not yet been tagged, then
persists the results to the ``securities`` table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import select

from src.database.models import Holding, Security

from .base import BaseAgent

# Common ticker → company name map used when the CSV / holding doesn't include a name.
# Not exhaustive — LLM classification will return names for unlisted tickers.
_TICKER_NAME_MAP: dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet", "GOOG": "Alphabet",
    "AMZN": "Amazon", "META": "Meta Platforms", "NVDA": "Nvidia", "TSLA": "Tesla",
    "TSM": "Taiwan Semiconductor", "AVGO": "Broadcom", "AMD": "AMD", "INTC": "Intel",
    "CRM": "Salesforce", "ORCL": "Oracle", "ADBE": "Adobe", "CSCO": "Cisco",
    "QCOM": "Qualcomm", "JPM": "JPMorgan Chase", "BAC": "Bank of America",
    "WFC": "Wells Fargo", "GS": "Goldman Sachs", "MS": "Morgan Stanley",
    "V": "Visa", "MA": "Mastercard", "BLK": "BlackRock",
    "JNJ": "Johnson & Johnson", "UNH": "UnitedHealth", "PFE": "Pfizer",
    "ABBV": "AbbVie", "MRK": "Merck", "LLY": "Eli Lilly",
    "PG": "Procter & Gamble", "KO": "Coca-Cola", "PEP": "PepsiCo",
    "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot",
    "NKE": "Nike", "MCD": "McDonald's", "SBUX": "Starbucks",
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "SHEL": "Shell", "BP": "BP", "TTE": "TotalEnergies",
    "NEE": "NextEra Energy", "DUK": "Duke Energy",
    "DIS": "Walt Disney", "NFLX": "Netflix", "CMCSA": "Comcast",
    "T": "AT&T", "VZ": "Verizon",
    "BA": "Boeing", "RTX": "RTX", "LMT": "Lockheed Martin", "GE": "GE Aerospace",
    "CAT": "Caterpillar", "DE": "Deere",
    "NESN": "Nestle", "NSRGY": "Nestle", "NOVN": "Novartis", "ROG": "Roche",
    "UBSG": "UBS", "ABB": "ABB",
    "AZN": "AstraZeneca", "HSBA": "HSBC", "UL": "Unilever", "GSK": "GSK",
    "BHP": "BHP Group", "RIO": "Rio Tinto",
    "LVMH": "LVMH", "MC.PA": "LVMH", "SAN.PA": "Sanofi", "OR.PA": "L'Oreal",
    "SIE.DE": "Siemens", "ALV.DE": "Allianz", "BAYN.DE": "Bayer", "VOW3.DE": "Volkswagen",
    "005930.KS": "Samsung", "7203.T": "Toyota", "9984.T": "SoftBank", "6758.T": "Sony",
    "9988.HK": "Alibaba", "0700.HK": "Tencent",
    "RELIANCE.NS": "Reliance", "TCS.NS": "Tata Consultancy", "INFY": "Infosys",
    "VALE": "Vale", "PBR": "Petrobras",
    "BIDU": "Baidu", "JD": "JD.com",
    "ENEL.MI": "Enel", "IBE.MC": "Iberdrola",
    "BABA": "Alibaba", "NVO": "Novo Nordisk", "ASML": "ASML", "SAP": "SAP",
    "TM": "Toyota", "SONY": "Sony", "SHOP": "Shopify",
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification prompt template (hardcoded fallback; override via config/prompts.yaml)
# ---------------------------------------------------------------------------
_DEFAULT_CLASSIFICATION_PROMPT = """\
You are a financial data analyst. Given the following security information,
return a JSON object with the fields below. Be precise and use standard
industry terms.

Security
--------
Ticker : {ticker}
Name   : {name}
Extra  : {extra}

Required JSON response
----------------------
{{
    "sector": "<GICS sector>",
    "industry": "<GICS industry>",
    "geography": "<primary country ISO-3166-1 alpha-2>",
    "market_cap_category": "mega|large|mid|small|micro",
    "themes": ["<theme1>", "<theme2>"],
    "asset_class": "equity|fixed_income|commodity|crypto|fx|other",
    "confidence": <0.0-1.0>
}}

Return ONLY valid JSON, no markdown fences, no commentary.
"""


def _get_classification_prompt() -> str:
    from src.llm.prompts import get_prompt
    return get_prompt("classification", fallback=_DEFAULT_CLASSIFICATION_PROMPT)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class ClassificationAgent(BaseAgent):
    """Classifies untagged holdings by sector, geography, and theme."""

    agent_name: ClassVar[str] = "classification"
    read_permissions: ClassVar[list[str]] = ["holdings", "securities", "trades"]
    write_permissions: ClassVar[list[str]] = ["securities", "audit_log", "agent_runs"]

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        """Entry point -- delegates to :meth:`classify_holdings`."""
        holding_ids: list[str] | None = kwargs.get("holding_ids")
        return await self.classify_holdings(holding_ids=holding_ids)

    async def classify_holdings(
        self,
        holding_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Classify holdings that lack sector/geography metadata.

        Parameters
        ----------
        holding_ids:
            Explicit list of holding IDs to classify.  When ``None`` the
            agent discovers all unclassified holdings automatically.

        Returns
        -------
        dict
            Summary with keys ``classified``, ``skipped``, ``errors``.
        """
        await self._log_run_start(parameters={"holding_ids": holding_ids})
        classified: list[dict[str, Any]] = []
        skipped: list[str] = []
        errors: list[str] = []

        try:
            self._check_permission("holdings", "read")
            holdings = await self._fetch_holdings(holding_ids)

            for holding in holdings:
                try:
                    result = await self._classify_single(holding)
                    if result is None:
                        skipped.append(holding["id"])
                    else:
                        classified.append(result)
                except Exception as exc:
                    msg = f"Failed to classify holding {holding['id']}: {exc}"
                    logger.error(msg, exc_info=True)
                    errors.append(msg)

            summary = {
                "classified": len(classified),
                "skipped": len(skipped),
                "errors": len(errors),
                "details": classified,
            }
            await self._log_run_complete(result_summary=summary)
            return summary

        except Exception as exc:
            await self._log_run_error(exc)
            raise

    # -- internal helpers --------------------------------------------------

    async def _fetch_holdings(
        self, holding_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        """Return holdings that need classification."""
        async with self._get_db() as session:
            if holding_ids:
                stmt = select(Holding).where(Holding.id.in_(holding_ids))
            else:
                # All holdings whose ticker has no corresponding Security row
                # with a non-null sector.
                stmt = select(Holding)

            result = await session.execute(stmt)
            rows = result.scalars().all()

        holdings: list[dict[str, Any]] = []
        for h in rows:
            holdings.append({
                "id": h.id,
                "ticker": h.ticker,
                "isin": getattr(h, "isin", None),
                "name": getattr(h, "name", None),
                "currency": getattr(h, "currency", "USD"),
            })
        return holdings

    async def _is_already_classified(self, ticker: str) -> bool:
        """Return True if a Security row with a sector already exists."""
        self._check_permission("securities", "read")
        async with self._get_db() as session:
            stmt = select(Security).where(
                Security.ticker == ticker,
                Security.sector.isnot(None),
            )
            row = (await session.execute(stmt)).scalars().first()
        return row is not None

    async def _classify_single(self, holding: dict[str, Any]) -> dict[str, Any] | None:
        """Classify a single holding via LLM (or rule-based fallback) and persist the result."""
        ticker = holding["ticker"]

        if await self._is_already_classified(ticker):
            logger.debug("Ticker %s already classified -- skipping", ticker)
            return None

        # Try LLM first, fall back to rule-based if unavailable
        from src.llm.client import is_llm_available

        if is_llm_available():
            classification = await self._call_llm(ticker, holding.get("name", ""))
            source = "llm"
        else:
            from src.agents.fallbacks import RuleBasedClassifier
            classifier = RuleBasedClassifier()
            classification = classifier.classify(ticker, holding.get("name"))
            source = "rule_based"
            logger.info("Used rule-based fallback for %s (LLM unavailable)", ticker)

        # Enrich geography from ISIN when the classifier returns a
        # default / uncertain value but the ISIN prefix is informative.
        isin = holding.get("isin")
        if isin and classification.get("geography") in (None, "US", "Unknown"):
            from src.security_master.classifier import SecurityClassifier
            isin_geo = SecurityClassifier().geography_from_isin(isin)
            if isin_geo:
                classification["geography"] = isin_geo

        # Derive a display name for the security if one wasn't provided.
        security_name = holding.get("name") or _TICKER_NAME_MAP.get(ticker.upper())

        # Persist to securities table
        self._check_permission("securities", "write")
        security_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        new_security = Security(
            id=security_id,
            ticker=ticker,
            name=security_name,
            currency=holding.get("currency", "USD"),
            sector=classification.get("sector"),
            industry=classification.get("industry"),
            geography=classification.get("geography"),
            market_cap_bucket=classification.get("market_cap_category"),
            themes=json.dumps(classification.get("themes", [])),
            classification_source=source,
            classification_confidence=str(classification.get("confidence", 0.0)),
            classified_at=now,
            created_at=now,
            updated_at=now,
        )

        async with self._get_db() as session:
            session.add(new_security)
            await session.commit()

        await self._audit_log(
            action="classified",
            entity_type="security",
            entity_id=security_id,
            details={
                "ticker": ticker,
                "sector": classification.get("sector"),
                "geography": classification.get("geography"),
                "themes": classification.get("themes"),
                "confidence": classification.get("confidence"),
            },
        )

        logger.info(
            "Classified %s -> sector=%s  geo=%s  themes=%s",
            ticker,
            classification.get("sector"),
            classification.get("geography"),
            classification.get("themes"),
        )

        return {"security_id": security_id, "ticker": ticker, **classification}

    async def _call_llm(self, ticker: str, name: str | None) -> dict[str, Any]:
        """Send the classification prompt to the configured LLM and return parsed JSON."""
        from src.llm.client import call_llm_json

        prompt = _get_classification_prompt().format(
            ticker=ticker,
            name=name or "N/A",
            extra="",
        )

        try:
            result = await call_llm_json(prompt)
            logger.info("LLM classification completed for %s", ticker)
            return result
        except Exception as exc:
            logger.error("LLM classification failed for %s: %s", ticker, exc)
            return {
                "sector": "Unknown",
                "industry": "Unknown",
                "geography": "US",
                "market_cap_category": "large",
                "themes": [],
                "asset_class": "equity",
                "confidence": 0.0,
            }
