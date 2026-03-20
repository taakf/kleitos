"""Rule-based fallback classifiers for when the LLM is unavailable.

Provides lightweight heuristic-based classification and analysis so
that the system remains functional without an Anthropic API key.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker → Sector / Industry lookup (common US equities)
# ---------------------------------------------------------------------------
_TICKER_SECTOR_MAP: dict[str, dict[str, str]] = {
    # Technology
    "AAPL": {"sector": "Information Technology", "industry": "Technology Hardware", "geography": "US"},
    "MSFT": {"sector": "Information Technology", "industry": "Software", "geography": "US"},
    "GOOGL": {"sector": "Communication Services", "industry": "Interactive Media", "geography": "US"},
    "GOOG": {"sector": "Communication Services", "industry": "Interactive Media", "geography": "US"},
    "META": {"sector": "Communication Services", "industry": "Interactive Media", "geography": "US"},
    "AMZN": {"sector": "Consumer Discretionary", "industry": "Internet & Direct Retail", "geography": "US"},
    "NVDA": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "US"},
    "AMD": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "US"},
    "INTC": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "US"},
    "TSM": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "TW"},
    "TSLA": {"sector": "Consumer Discretionary", "industry": "Automobiles", "geography": "US"},
    "CRM": {"sector": "Information Technology", "industry": "Software", "geography": "US"},
    "ORCL": {"sector": "Information Technology", "industry": "Software", "geography": "US"},
    "ADBE": {"sector": "Information Technology", "industry": "Software", "geography": "US"},
    "CSCO": {"sector": "Information Technology", "industry": "Communications Equipment", "geography": "US"},
    "AVGO": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "US"},
    "QCOM": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "US"},
    # Financials
    "JPM": {"sector": "Financials", "industry": "Diversified Banks", "geography": "US"},
    "BAC": {"sector": "Financials", "industry": "Diversified Banks", "geography": "US"},
    "WFC": {"sector": "Financials", "industry": "Diversified Banks", "geography": "US"},
    "GS": {"sector": "Financials", "industry": "Investment Banking", "geography": "US"},
    "MS": {"sector": "Financials", "industry": "Investment Banking", "geography": "US"},
    "C": {"sector": "Financials", "industry": "Diversified Banks", "geography": "US"},
    "V": {"sector": "Financials", "industry": "Transaction Processing", "geography": "US"},
    "MA": {"sector": "Financials", "industry": "Transaction Processing", "geography": "US"},
    "BRK.B": {"sector": "Financials", "industry": "Multi-Sector Holdings", "geography": "US"},
    "BLK": {"sector": "Financials", "industry": "Asset Management", "geography": "US"},
    # Healthcare
    "JNJ": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "US"},
    "UNH": {"sector": "Health Care", "industry": "Managed Health Care", "geography": "US"},
    "PFE": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "US"},
    "ABBV": {"sector": "Health Care", "industry": "Biotechnology", "geography": "US"},
    "MRK": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "US"},
    "LLY": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "US"},
    "TMO": {"sector": "Health Care", "industry": "Life Sciences Tools", "geography": "US"},
    # Consumer
    "PG": {"sector": "Consumer Staples", "industry": "Household Products", "geography": "US"},
    "KO": {"sector": "Consumer Staples", "industry": "Soft Drinks", "geography": "US"},
    "PEP": {"sector": "Consumer Staples", "industry": "Soft Drinks", "geography": "US"},
    "WMT": {"sector": "Consumer Staples", "industry": "Hypermarkets", "geography": "US"},
    "COST": {"sector": "Consumer Staples", "industry": "Hypermarkets", "geography": "US"},
    "HD": {"sector": "Consumer Discretionary", "industry": "Home Improvement Retail", "geography": "US"},
    "NKE": {"sector": "Consumer Discretionary", "industry": "Apparel & Accessories", "geography": "US"},
    "MCD": {"sector": "Consumer Discretionary", "industry": "Restaurants", "geography": "US"},
    "SBUX": {"sector": "Consumer Discretionary", "industry": "Restaurants", "geography": "US"},
    # Energy
    "XOM": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "US"},
    "CVX": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "US"},
    "COP": {"sector": "Energy", "industry": "Oil & Gas Exploration", "geography": "US"},
    # Industrials
    "BA": {"sector": "Industrials", "industry": "Aerospace & Defense", "geography": "US"},
    "CAT": {"sector": "Industrials", "industry": "Construction Machinery", "geography": "US"},
    "GE": {"sector": "Industrials", "industry": "Industrial Conglomerates", "geography": "US"},
    "UPS": {"sector": "Industrials", "industry": "Air Freight & Logistics", "geography": "US"},
    "HON": {"sector": "Industrials", "industry": "Industrial Conglomerates", "geography": "US"},
    # Materials
    "LIN": {"sector": "Materials", "industry": "Industrial Gases", "geography": "US"},
    # Real Estate
    "AMT": {"sector": "Real Estate", "industry": "Specialized REITs", "geography": "US"},
    "PLD": {"sector": "Real Estate", "industry": "Industrial REITs", "geography": "US"},
    # Utilities
    "NEE": {"sector": "Utilities", "industry": "Electric Utilities", "geography": "US"},
    "DUK": {"sector": "Utilities", "industry": "Electric Utilities", "geography": "US"},
    # International -- common ADRs and suffixes
    "BABA": {"sector": "Consumer Discretionary", "industry": "Internet & Direct Retail", "geography": "CN"},
    "NVO": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "DK"},
    "ASML": {"sector": "Information Technology", "industry": "Semiconductor Equipment", "geography": "NL"},
    "SAP": {"sector": "Information Technology", "industry": "Software", "geography": "DE"},
    "TM": {"sector": "Consumer Discretionary", "industry": "Automobiles", "geography": "JP"},
    "SONY": {"sector": "Consumer Discretionary", "industry": "Consumer Electronics", "geography": "JP"},
    "SHOP": {"sector": "Information Technology", "industry": "Software", "geography": "CA"},
    # -- Switzerland --
    "NESN": {"sector": "Consumer Staples", "industry": "Packaged Foods", "geography": "CH"},
    "NSRGY": {"sector": "Consumer Staples", "industry": "Packaged Foods", "geography": "CH"},
    "NOVN": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "CH"},
    "ROG": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "CH"},
    "UBSG": {"sector": "Financials", "industry": "Diversified Banks", "geography": "CH"},
    "ABB": {"sector": "Industrials", "industry": "Electrical Equipment", "geography": "CH"},
    # -- United Kingdom --
    "SHEL": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "GB"},
    "AZN": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "GB"},
    "HSBA": {"sector": "Financials", "industry": "Diversified Banks", "geography": "GB"},
    "UL": {"sector": "Consumer Staples", "industry": "Household Products", "geography": "GB"},
    "BP": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "GB"},
    "GSK": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "GB"},
    # -- Australia --
    "BHP": {"sector": "Materials", "industry": "Diversified Metals & Mining", "geography": "AU"},
    "RIO": {"sector": "Materials", "industry": "Diversified Metals & Mining", "geography": "AU"},
    # -- France --
    "LVMH": {"sector": "Consumer Discretionary", "industry": "Apparel & Luxury", "geography": "FR"},
    "MC.PA": {"sector": "Consumer Discretionary", "industry": "Apparel & Luxury", "geography": "FR"},
    "SAN.PA": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "FR"},
    "OR.PA": {"sector": "Consumer Staples", "industry": "Personal Products", "geography": "FR"},
    "TTE": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "FR"},
    # -- Germany --
    "SIE.DE": {"sector": "Industrials", "industry": "Industrial Conglomerates", "geography": "DE"},
    "ALV.DE": {"sector": "Financials", "industry": "Insurance", "geography": "DE"},
    "BAYN.DE": {"sector": "Health Care", "industry": "Pharmaceuticals", "geography": "DE"},
    "VOW3.DE": {"sector": "Consumer Discretionary", "industry": "Automobiles", "geography": "DE"},
    # -- Asia-Pacific --
    "005930.KS": {"sector": "Information Technology", "industry": "Semiconductors", "geography": "KR"},
    "7203.T": {"sector": "Consumer Discretionary", "industry": "Automobiles", "geography": "JP"},
    "9984.T": {"sector": "Communication Services", "industry": "Telecom & Internet", "geography": "JP"},
    "6758.T": {"sector": "Communication Services", "industry": "Consumer Electronics", "geography": "JP"},
    "9988.HK": {"sector": "Consumer Discretionary", "industry": "Internet & Direct Retail", "geography": "CN"},
    "0700.HK": {"sector": "Communication Services", "industry": "Interactive Media", "geography": "CN"},
    # -- India --
    "RELIANCE.NS": {"sector": "Energy", "industry": "Diversified Operations", "geography": "IN"},
    "TCS.NS": {"sector": "Information Technology", "industry": "IT Consulting", "geography": "IN"},
    "INFY": {"sector": "Information Technology", "industry": "IT Consulting", "geography": "IN"},
    # -- Latin America --
    "VALE": {"sector": "Materials", "industry": "Diversified Metals & Mining", "geography": "BR"},
    "PBR": {"sector": "Energy", "industry": "Integrated Oil & Gas", "geography": "BR"},
    # -- China ADRs --
    "BIDU": {"sector": "Communication Services", "industry": "Interactive Media", "geography": "CN"},
    "JD": {"sector": "Consumer Discretionary", "industry": "Internet & Direct Retail", "geography": "CN"},
    # -- European Utilities --
    "ENEL.MI": {"sector": "Utilities", "industry": "Electric Utilities", "geography": "IT"},
    "IBE.MC": {"sector": "Utilities", "industry": "Electric Utilities", "geography": "ES"},
}


# ---------------------------------------------------------------------------
# Ticker suffix → geography
# ---------------------------------------------------------------------------
_SUFFIX_GEOGRAPHY: dict[str, str] = {
    ".L": "GB",
    ".LN": "GB",
    ".TO": "CA",
    ".AX": "AU",
    ".HK": "HK",
    ".T": "JP",
    ".DE": "DE",
    ".PA": "FR",
    ".AS": "NL",
    ".MI": "IT",
    ".MC": "ES",
    ".ST": "SE",
    ".CO": "DK",
    ".OL": "NO",
    ".HE": "FI",
    ".SW": "CH",
    ".SG": "DE",
    ".BR": "BE",
    ".LS": "PT",
    ".VI": "AT",
    ".SS": "CN",
    ".SZ": "CN",
    ".KS": "KR",
    ".KQ": "KR",
    ".TW": "TW",
    ".SI": "SG",
    ".BK": "TH",
    ".JK": "ID",
    ".NS": "IN",
    ".BO": "IN",
    ".SA": "BR",
    ".MX": "MX",
}


# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------
class RuleBasedClassifier:
    """Heuristic security classification using ticker lookup and suffix rules.

    Used as a fallback when the Anthropic LLM is not available.
    Confidence is capped at 0.3 to indicate lower reliability.
    """

    FALLBACK_CONFIDENCE = 0.3

    def classify(self, ticker: str, name: str | None = None) -> dict[str, Any]:
        """Return a classification dict for *ticker*.

        Parameters
        ----------
        ticker:
            Security ticker symbol (e.g. ``"AAPL"``, ``"VOD.L"``).
        name:
            Optional human-readable name (used for keyword heuristics).

        Returns
        -------
        dict
            Classification with ``sector``, ``industry``, ``geography``,
            ``market_cap_category``, ``themes``, ``asset_class``, ``confidence``.
        """
        ticker_upper = ticker.upper().strip()

        # 1. Direct lookup
        if ticker_upper in _TICKER_SECTOR_MAP:
            info = _TICKER_SECTOR_MAP[ticker_upper]
            themes = self._infer_themes(info.get("sector", ""), info.get("industry", ""), name)
            return {
                "sector": info["sector"],
                "industry": info["industry"],
                "geography": info.get("geography", "US"),
                "market_cap_category": "large",
                "themes": themes,
                "asset_class": "equity",
                "confidence": self.FALLBACK_CONFIDENCE,
            }

        # 2. Suffix-based geography inference
        geography = "US"  # default
        for suffix, geo in _SUFFIX_GEOGRAPHY.items():
            if ticker_upper.endswith(suffix.upper()):
                geography = geo
                break

        # 3. Name-based keyword heuristics
        sector, industry = self._infer_sector_from_name(name)
        themes = self._infer_themes(sector, industry, name)

        return {
            "sector": sector,
            "industry": industry,
            "geography": geography,
            "market_cap_category": "unknown",
            "themes": themes,
            "asset_class": self._infer_asset_class(ticker_upper),
            "confidence": self.FALLBACK_CONFIDENCE * 0.5 if sector == "Unknown" else self.FALLBACK_CONFIDENCE,
        }

    def _infer_sector_from_name(self, name: str | None) -> tuple[str, str]:
        """Attempt to guess sector/industry from the security name."""
        if not name:
            return ("Unknown", "Unknown")

        name_lower = name.lower()

        keyword_map: list[tuple[list[str], str, str]] = [
            (["bank", "financial", "capital", "credit"], "Financials", "Banking"),
            (["pharma", "biotech", "therapeutics", "medical", "health"], "Health Care", "Pharmaceuticals"),
            (["software", "cloud", "data", "cyber", "digital", "tech"], "Information Technology", "Software"),
            (["semiconductor", "chip"], "Information Technology", "Semiconductors"),
            (["energy", "oil", "gas", "petrol", "solar", "wind"], "Energy", "Energy Equipment"),
            (["mining", "gold", "silver", "copper", "metal"], "Materials", "Metals & Mining"),
            (["real estate", "reit", "property"], "Real Estate", "REITs"),
            (["utility", "electric", "power", "water"], "Utilities", "Electric Utilities"),
            (["retail", "store", "shop", "consumer"], "Consumer Discretionary", "Retail"),
            (["food", "beverage", "grocery"], "Consumer Staples", "Food Products"),
            (["insurance"], "Financials", "Insurance"),
            (["aerospace", "defense", "defence"], "Industrials", "Aerospace & Defense"),
            (["auto", "motor", "vehicle", "car"], "Consumer Discretionary", "Automobiles"),
            (["telecom", "wireless", "mobile", "communication"], "Communication Services", "Telecom"),
            (["media", "entertainment", "stream", "gaming"], "Communication Services", "Entertainment"),
        ]

        for keywords, sector, industry in keyword_map:
            for kw in keywords:
                if kw in name_lower:
                    return (sector, industry)

        return ("Unknown", "Unknown")

    def _infer_themes(self, sector: str, industry: str, name: str | None) -> list[str]:
        """Derive simple theme tags from sector/industry/name."""
        themes: list[str] = []
        combined = f"{sector} {industry} {name or ''}".lower()

        theme_keywords: dict[str, list[str]] = {
            "AI": ["artificial intelligence", "ai", "machine learning", "deep learning", "neural"],
            "Cloud": ["cloud", "saas", "iaas"],
            "ESG": ["esg", "sustainability", "green", "renewable", "clean energy"],
            "EV": ["electric vehicle", "ev", "battery", "lithium"],
            "Fintech": ["fintech", "payment", "blockchain", "crypto"],
            "Biotech": ["biotech", "gene", "crispr", "mrna"],
            "Cybersecurity": ["cyber", "security", "firewall", "encryption"],
            "5G": ["5g", "wireless", "telecom"],
            "E-commerce": ["e-commerce", "ecommerce", "online retail"],
        }

        for theme, keywords in theme_keywords.items():
            for kw in keywords:
                if kw in combined:
                    themes.append(theme)
                    break

        return themes

    def _infer_asset_class(self, ticker: str) -> str:
        """Guess asset class from ticker patterns."""
        # Crypto tickers
        if ticker in ("BTC", "ETH", "SOL", "DOGE", "ADA", "DOT", "AVAX", "MATIC"):
            return "crypto"
        if ticker.endswith("-USD") or ticker.endswith("USD"):
            return "crypto"
        # Commodity ETFs
        if ticker in ("GLD", "SLV", "USO", "UNG", "DBA", "DBC"):
            return "commodity"
        # Currency
        if re.match(r"^[A-Z]{3}/[A-Z]{3}$", ticker):
            return "fx"
        return "equity"


# ---------------------------------------------------------------------------
# Rule-based analysis (keyword matching)
# ---------------------------------------------------------------------------
_POSITIVE_KEYWORDS = [
    "upgrade", "upgrades", "upgraded",
    "beat", "beats", "exceeded", "exceeds",
    "outperform", "outperforms",
    "raised", "raises", "raise",
    "growth", "growing",
    "record high", "record revenue", "record profit",
    "buy", "bullish",
    "dividend increase", "dividend hike",
    "approval", "approved",
    "breakout", "breakthrough",
    "innovation", "patent",
    "expansion", "expands",
    "partnership", "acquisition",
]

_NEGATIVE_KEYWORDS = [
    "downgrade", "downgrades", "downgraded",
    "miss", "missed", "misses",
    "underperform", "underperforms",
    "cut", "cuts", "lowered", "lowers",
    "decline", "declining", "declines",
    "loss", "losses",
    "sell", "bearish",
    "dividend cut", "dividend suspension",
    "recall", "recalled",
    "lawsuit", "litigation", "sued",
    "investigation", "investigated",
    "warning", "warns",
    "layoff", "layoffs", "restructuring",
    "default", "bankruptcy",
    "sanction", "sanctions",
    "shortage", "supply chain disruption",
]


def rule_based_analysis(event: dict[str, Any], holding: dict[str, Any]) -> dict[str, Any]:
    """Produce a simple impact analysis by keyword matching on the event text.

    Returns a dict matching the LLM analysis schema but with ``confidence: 0.0``
    to indicate it's a heuristic result.
    """
    text = f"{event.get('title', '')} {event.get('summary', '')}".lower()

    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)

    if pos_hits > neg_hits:
        direction = "positive"
    elif neg_hits > pos_hits:
        direction = "negative"
    else:
        direction = "neutral"

    total_hits = pos_hits + neg_hits
    if total_hits >= 3:
        magnitude = "high"
    elif total_hits >= 1:
        magnitude = "medium"
    else:
        magnitude = "low"

    # Build matched keywords for transparency
    matched: list[str] = []
    for kw in _POSITIVE_KEYWORDS:
        if kw in text:
            matched.append(f"+{kw}")
    for kw in _NEGATIVE_KEYWORDS:
        if kw in text:
            matched.append(f"-{kw}")

    # Infer impact categories from keywords
    earnings_kws = {"beat", "beats", "miss", "missed", "revenue", "profit", "eps", "earnings"}
    thesis_kws = {"upgrade", "downgrade", "restructuring", "acquisition", "bankruptcy", "innovation"}
    risk_kws = {"lawsuit", "investigation", "sanction", "sanctions", "shortage", "default", "recall"}
    valuation_kws = {"bullish", "bearish", "buy", "sell", "outperform", "underperform"}

    text_words = set(text.split())

    def _impact_level(kw_set: set[str]) -> str:
        hits = len(text_words & kw_set)
        if hits >= 2:
            return "high"
        if hits >= 1:
            return "medium"
        return "none"

    return {
        "impact_direction": direction,
        "impact_magnitude": magnitude,
        "materiality": "watch" if total_hits >= 1 else "noise",
        "thesis_impact": _impact_level(thesis_kws),
        "earnings_impact": _impact_level(earnings_kws),
        "valuation_impact": _impact_level(valuation_kws),
        "risk_impact": _impact_level(risk_kws),
        "short_term_outlook": f"Rule-based assessment: {direction} signal detected from keyword analysis.",
        "long_term_outlook": "Manual review recommended — this is a heuristic-only assessment.",
        "key_factors": matched[:5] if matched else ["No strong keyword signals detected"],
        "recommended_actions": ["Review event details manually"],
        "confidence": 0.0,
    }


# ---------------------------------------------------------------------------
# Rule-based digest (structured summary without LLM)
# ---------------------------------------------------------------------------
def rule_based_digest(notes: list[dict[str, Any]], period: str) -> dict[str, Any]:
    """Build a structured digest from analysis notes without LLM.

    Groups notes by direction/magnitude and produces bullet-point summaries.
    """
    if not notes:
        return {
            "headline": f"No activity in {period} period",
            "key_developments": [],
            "risk_flags": [],
            "action_items": [],
            "market_context": "No analysis notes available for this period.",
        }

    # Count by direction
    direction_counts: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
    high_impact: list[str] = []
    negative_tickers: list[str] = []
    all_tickers: set[str] = set()

    for n in notes:
        direction = n.get("impact_direction", "neutral")
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        ticker = n.get("ticker", "Unknown")
        all_tickers.add(ticker)

        magnitude = n.get("impact_magnitude", "low")
        if magnitude == "high":
            high_impact.append(f"{ticker} ({direction})")
        if direction == "negative":
            negative_tickers.append(ticker)

    # Build headline
    total = len(notes)
    pos = direction_counts.get("positive", 0)
    neg = direction_counts.get("negative", 0)

    if pos > neg * 2:
        sentiment = "predominantly positive"
    elif neg > pos * 2:
        sentiment = "predominantly negative"
    elif pos > neg:
        sentiment = "slightly positive"
    elif neg > pos:
        sentiment = "slightly negative"
    else:
        sentiment = "mixed"

    headline = f"{period.capitalize()} digest: {total} events across {len(all_tickers)} holdings — {sentiment} outlook"

    # Key developments
    key_developments = [
        f"{pos} positive, {neg} negative, {direction_counts.get('neutral', 0)} neutral signals",
    ]
    if high_impact:
        key_developments.append(f"High-impact events: {', '.join(high_impact[:5])}")

    # Risk flags
    risk_flags = []
    if neg >= 3:
        risk_flags.append(f"Elevated negative signals ({neg} events)")
    if negative_tickers:
        unique_neg = list(set(negative_tickers))
        risk_flags.append(f"Negative signals for: {', '.join(unique_neg[:5])}")

    # Action items
    action_items = []
    if high_impact:
        action_items.append(f"Review high-impact events: {', '.join(high_impact[:3])}")
    if negative_tickers:
        action_items.append(f"Assess positions in: {', '.join(list(set(negative_tickers))[:3])}")
    if not action_items:
        action_items.append("No urgent actions required — routine monitoring recommended")

    # Sector patterns (group notes by sector if available)
    # For rule-based, we just group by ticker since we may not have sector info
    sector_patterns = []
    if neg > pos:
        sector_patterns.append({
            "sector": "Portfolio-wide",
            "signal": "negative",
            "summary": f"{neg} of {total} signals negative — portfolio under pressure",
        })
    elif pos > neg:
        sector_patterns.append({
            "sector": "Portfolio-wide",
            "signal": "positive",
            "summary": f"{pos} of {total} signals positive — portfolio trending well",
        })

    # Holdings requiring attention
    holdings_attention = list(set(negative_tickers))[:5] if negative_tickers else []
    if high_impact:
        for item in high_impact[:3]:
            ticker = item.split(" ")[0]
            if ticker not in holdings_attention:
                holdings_attention.append(ticker)

    return {
        "headline": headline,
        "portfolio_assessment": (
            f"Portfolio received {total} signals across {len(all_tickers)} holdings. "
            f"Overall sentiment is {sentiment}. "
            f"{'High-impact events detected — manual review recommended.' if high_impact else 'No high-impact events detected.'}"
        ),
        "sector_patterns": sector_patterns,
        "key_developments": key_developments,
        "risk_flags": risk_flags,
        "action_items": action_items,
        "market_context": f"Automated digest covering {total} analysis notes. LLM-enhanced summary unavailable — using rule-based aggregation.",
        "holdings_requiring_attention": holdings_attention,
    }
