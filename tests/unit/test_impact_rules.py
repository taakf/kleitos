"""Tests for the rule-based impact matching engine."""

from src.impact.rules import RuleEngine


class TestRuleEngine:
    """Test the deterministic rule-based matching."""

    def setup_method(self):
        self.engine = RuleEngine()
        self.holdings = [
            {"id": "h1", "ticker": "AAPL", "currency": "USD"},
            {"id": "h2", "ticker": "MSFT", "currency": "USD"},
            {"id": "h3", "ticker": "NESN", "currency": "CHF"},
            {"id": "h4", "ticker": "TSM", "currency": "USD"},
        ]
        self.securities = [
            {"ticker": "AAPL", "isin": "US0378331005", "name": "Apple Inc",
             "sector": "technology", "geography": "united states", "themes": '["ai"]'},
            {"ticker": "MSFT", "isin": "US5949181045", "name": "Microsoft Corporation",
             "sector": "technology", "geography": "united states", "themes": '["ai", "cloud"]'},
            {"ticker": "NESN", "isin": "CH0038863350", "name": "Nestle SA",
             "sector": "consumer staples", "geography": "switzerland", "themes": '[]'},
            {"ticker": "TSM", "isin": "US8740391003", "name": "Taiwan Semiconductor",
             "sector": "technology", "geography": "taiwan", "themes": '["semiconductors"]'},
        ]

    def test_ticker_match(self):
        """Direct ticker mention should match."""
        matches = self.engine.find_matches(
            "AAPL reports record earnings",
            "Apple stock surges after results.",
            self.holdings,
            self.securities,
        )
        ticker_matches = [m for m in matches if m.match_type == "ticker"]
        assert len(ticker_matches) >= 1
        assert any(m.holding_id == "h1" for m in ticker_matches)

    def test_company_name_match(self):
        """Company name mention should match."""
        matches = self.engine.find_matches(
            "Apple Inc announces new product",
            "The Cupertino company reveals its latest device.",
            self.holdings,
            self.securities,
        )
        name_matches = [m for m in matches if m.match_type in ("company_name", "ticker")]
        assert any(m.holding_id == "h1" for m in name_matches)

    def test_sector_match(self):
        """Sector keyword should produce a sector match."""
        matches = self.engine.find_matches(
            "Technology sector faces new regulation",
            "New rules impact technology companies globally.",
            self.holdings,
            self.securities,
        )
        sector_matches = [m for m in matches if m.match_type == "sector"]
        assert len(sector_matches) >= 1
        assert any(m.matched_value == "technology" for m in sector_matches)

    def test_market_wide_match(self):
        """Systemic keywords should produce market-wide match."""
        matches = self.engine.find_matches(
            "Federal Reserve raises interest rates",
            "The Fed announced a rate increase amid inflation concerns.",
            self.holdings,
            self.securities,
        )
        market_matches = [m for m in matches if m.match_type == "market_wide"]
        assert len(market_matches) >= 1

    def test_no_false_positive_for_short_tickers(self):
        """Short tickers should not match common words."""
        # "A" would be a bad ticker to match everywhere
        holdings = [{"id": "x1", "ticker": "A", "currency": "USD"}]
        securities = [{"ticker": "A", "name": "Agilent", "sector": "healthcare",
                       "geography": "us", "themes": "[]"}]
        matches = self.engine.find_matches(
            "A new study shows promising results",
            "Researchers published a breakthrough paper.",
            holdings,
            securities,
        )
        # Single-char ticker "A" should not match (min length check)
        ticker_matches = [m for m in matches if m.match_type == "ticker"]
        assert len(ticker_matches) == 0

    def test_scope_single_stock(self):
        """Single holding match should classify as single_stock."""
        matches = self.engine.find_matches(
            "AAPL reports earnings", "", self.holdings, self.securities
        )
        scope = self.engine.classify_scope(matches, len(self.holdings))
        assert scope == "single_stock"

    def test_scope_systemic(self):
        """Market-wide keyword should classify as systemic."""
        matches = self.engine.find_matches(
            "Global recession fears mount", "", self.holdings, self.securities
        )
        scope = self.engine.classify_scope(matches, len(self.holdings))
        assert scope == "systemic"

    def test_scope_unrelated(self):
        """No matches should classify as unrelated."""
        matches = self.engine.find_matches(
            "Local sports team wins championship", "", self.holdings, self.securities
        )
        scope = self.engine.classify_scope(matches, len(self.holdings))
        assert scope == "unrelated"

    def test_currency_match(self):
        """Currency keyword should match."""
        matches = self.engine.find_matches(
            "Swiss franc strengthens against dollar",
            "CHF gains as safe haven flows increase.",
            self.holdings,
            self.securities,
        )
        ccy_matches = [m for m in matches if m.match_type == "currency"]
        assert len(ccy_matches) >= 1


class TestRSSParser:
    """Test the RSS parser."""

    def test_parse_valid_rss(self, sample_rss_feed):
        from src.sources.parsers.rss_generic import RSSGenericParser
        parser = RSSGenericParser()
        events = parser.parse(sample_rss_feed, "test-source")
        assert len(events) == 3
        assert events[0].title == "Apple Reports Record Q4 Earnings"
        assert events[0].event_type == "earnings"

    def test_parse_empty_feed(self):
        from src.sources.parsers.rss_generic import RSSGenericParser
        parser = RSSGenericParser()
        events = parser.parse("", "test-source")
        assert len(events) == 0

    def test_event_type_classification(self, sample_rss_feed):
        from src.sources.parsers.rss_generic import RSSGenericParser
        parser = RSSGenericParser()
        events = parser.parse(sample_rss_feed, "test-source")
        # Fed rate event should be classified as macro
        fed_event = [e for e in events if "Federal Reserve" in e.title][0]
        assert fed_event.event_type == "macro"

    def test_dedup_hash_uniqueness(self, sample_rss_feed):
        from src.sources.parsers.rss_generic import RSSGenericParser
        parser = RSSGenericParser()
        events = parser.parse(sample_rss_feed, "test-source")
        hashes = [e.dedup_hash for e in events]
        assert len(hashes) == len(set(hashes))  # All unique
