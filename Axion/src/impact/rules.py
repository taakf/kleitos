"""Rule-based impact matching — deterministic first stage of the Impact Mapping Engine."""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RuleMatch:
    """A single rule-based match between an event and a portfolio entity."""
    rule_name: str
    match_type: str  # ticker, isin, company_name, sector, geography, theme, currency, market_wide
    matched_value: str  # The specific value that matched
    holding_id: str | None = None
    sector: str | None = None
    geography: str | None = None
    theme: str | None = None
    currency: str | None = None
    confidence: float = 1.0


MARKET_WIDE_KEYWORDS = [
    "federal reserve", "fed raises", "fed cuts", "interest rate decision",
    "global recession", "market crash", "systemic risk", "black swan",
    "pandemic", "financial crisis", "quantitative easing", "qe ",
    "quantitative tightening", "qt ", "credit crisis", "liquidity crisis",
    "vix spike", "market selloff", "bear market", "bull market",
    "global trade", "trade war", "tariff war", "sanctions",
    "sovereign debt", "debt ceiling", "government shutdown",
    "inflation surge", "deflation", "stagflation",
    "currency crisis", "dollar index", "dxy ",
    "oil shock", "energy crisis", "commodity supercycle",
]


class RuleEngine:
    """Deterministic rule-based matching engine.

    Stage 1 of the Impact Mapping Engine. Produces candidate links
    between events and portfolio entities using exact matching rules.
    """

    def find_matches(
        self,
        event_title: str,
        event_content: str,
        holdings: list[dict],
        securities: list[dict],
    ) -> list[RuleMatch]:
        """Find all rule-based matches for an event against the portfolio.

        Args:
            event_title: Event headline
            event_content: Event body/summary text
            holdings: List of holding dicts with at least {id, ticker, currency}
            securities: List of security dicts with {ticker, isin, name, sector, geography, themes}

        Returns:
            List of RuleMatch objects
        """
        matches: list[RuleMatch] = []
        text = f"{event_title} {event_content}".lower()

        # Build lookup maps
        ticker_to_holding = {h["ticker"].upper(): h for h in holdings}
        ticker_to_security = {s["ticker"].upper(): s for s in securities}

        # Rule 1: Ticker Match
        for ticker, holding in ticker_to_holding.items():
            # Match ticker as whole word (avoid matching "A" in every sentence)
            if len(ticker) >= 2 and re.search(rf'\b{re.escape(ticker)}\b', text, re.IGNORECASE):
                matches.append(RuleMatch(
                    rule_name="ticker_match",
                    match_type="ticker",
                    matched_value=ticker,
                    holding_id=holding["id"],
                    confidence=0.95,
                ))

        # Rule 2: ISIN Match
        for sec in securities:
            isin = sec.get("isin", "")
            if isin and len(isin) == 12 and isin in event_content:
                holding = ticker_to_holding.get(sec["ticker"].upper())
                if holding:
                    matches.append(RuleMatch(
                        rule_name="isin_match",
                        match_type="isin",
                        matched_value=isin,
                        holding_id=holding["id"],
                        confidence=1.0,
                    ))

        # Rule 3: Company Name Match
        for sec in securities:
            name = sec.get("name", "")
            if name and len(name) > 3 and name.lower() in text:
                holding = ticker_to_holding.get(sec["ticker"].upper())
                if holding:
                    # Check not already matched by ticker
                    already = any(m.holding_id == holding["id"] for m in matches)
                    if not already:
                        matches.append(RuleMatch(
                            rule_name="company_name_match",
                            match_type="company_name",
                            matched_value=name,
                            holding_id=holding["id"],
                            confidence=0.8,
                        ))

        # Rule 4: Sector Match
        sectors_in_portfolio = set()
        for sec in securities:
            s = sec.get("sector", "")
            if s:
                sectors_in_portfolio.add(s.lower())

        for sector in sectors_in_portfolio:
            if sector in text:
                matches.append(RuleMatch(
                    rule_name="sector_match",
                    match_type="sector",
                    matched_value=sector,
                    sector=sector,
                    confidence=0.6,
                ))

        # Rule 5: Geography Match
        geos_in_portfolio = set()
        for sec in securities:
            g = sec.get("geography", "")
            if g:
                geos_in_portfolio.add(g.lower())

        for geo in geos_in_portfolio:
            if geo in text:
                matches.append(RuleMatch(
                    rule_name="geography_match",
                    match_type="geography",
                    matched_value=geo,
                    geography=geo,
                    confidence=0.5,
                ))

        # Rule 6: Currency Match
        currencies_in_portfolio = set()
        for h in holdings:
            c = h.get("currency", "")
            if c:
                currencies_in_portfolio.add(c.upper())

        currency_patterns = {
            "USD": ["dollar", "usd", "us dollar", "greenback"],
            "EUR": ["euro", "eur"],
            "GBP": ["pound", "gbp", "sterling"],
            "JPY": ["yen", "jpy"],
            "CHF": ["swiss franc", "chf"],
        }

        for ccy in currencies_in_portfolio:
            patterns = currency_patterns.get(ccy, [ccy.lower()])
            if any(p in text for p in patterns):
                matches.append(RuleMatch(
                    rule_name="currency_match",
                    match_type="currency",
                    matched_value=ccy,
                    currency=ccy,
                    confidence=0.5,
                ))

        # Rule 7: Market-Wide / Systemic Match
        if any(kw in text for kw in MARKET_WIDE_KEYWORDS):
            matches.append(RuleMatch(
                rule_name="market_wide_match",
                match_type="market_wide",
                matched_value="systemic",
                confidence=0.7,
            ))

        # Rule 8: Theme Match
        themes_in_portfolio = set()
        for sec in securities:
            themes_raw = sec.get("themes", "")
            if isinstance(themes_raw, str):
                try:
                    import json
                    themes_list = json.loads(themes_raw) if themes_raw else []
                except (json.JSONDecodeError, TypeError):
                    themes_list = []
            elif isinstance(themes_raw, list):
                themes_list = themes_raw
            else:
                themes_list = []
            for t in themes_list:
                themes_in_portfolio.add(t.lower())

        for theme in themes_in_portfolio:
            if theme in text:
                matches.append(RuleMatch(
                    rule_name="theme_match",
                    match_type="theme",
                    matched_value=theme,
                    theme=theme,
                    confidence=0.5,
                ))

        logger.debug(f"Rule engine found {len(matches)} matches for event")
        return matches

    def classify_scope(self, matches: list[RuleMatch], portfolio_size: int) -> str:
        """Determine the scope of an event based on its matches.

        Returns: single_stock, peer_group, sector, geography, theme, currency, systemic, multi_factor, unrelated
        """
        if not matches:
            return "unrelated"

        # Check for market-wide match
        if any(m.match_type == "market_wide" for m in matches):
            return "systemic"

        # Count unique holdings matched
        holding_ids = {m.holding_id for m in matches if m.holding_id}

        if len(holding_ids) == 1:
            return "single_stock"
        elif len(holding_ids) > 1:
            # Check if they share a common dimension
            sectors = {m.sector for m in matches if m.sector}
            geos = {m.geography for m in matches if m.geography}
            themes = {m.theme for m in matches if m.theme}

            if len(sectors) == 1:
                return "sector"
            elif len(geos) == 1:
                return "geography"
            elif len(themes) == 1:
                return "theme"
            elif len(holding_ids) > portfolio_size * 0.5:
                return "systemic"
            else:
                return "multi_factor"

        # No specific holdings but dimension matches exist
        if any(m.match_type == "sector" for m in matches):
            return "sector"
        if any(m.match_type == "geography" for m in matches):
            return "geography"
        if any(m.match_type == "currency" for m in matches):
            return "currency"
        if any(m.match_type == "theme" for m in matches):
            return "theme"

        return "unrelated"
