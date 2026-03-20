"""Tests for the security classifier."""

from src.security_master.classifier import SecurityClassifier


class TestSecurityClassifier:
    """Test security classification logic."""

    def setup_method(self):
        self.classifier = SecurityClassifier()

    def test_geography_from_isin_us(self):
        assert SecurityClassifier.geography_from_isin("US0378331005") == "united states"

    def test_geography_from_isin_swiss(self):
        assert SecurityClassifier.geography_from_isin("CH0038863350") == "switzerland"

    def test_geography_from_isin_none(self):
        assert SecurityClassifier.geography_from_isin("") is None
        assert SecurityClassifier.geography_from_isin(None) is None

    def test_validate_sector_canonical(self):
        sector, sub = SecurityClassifier.validate_sector("technology")
        assert sector == "technology"
        assert sub is None

    def test_validate_sector_subsector(self):
        sector, sub = SecurityClassifier.validate_sector("semiconductors")
        assert sector == "technology"
        assert sub == "semiconductors"

    def test_validate_sector_unknown(self):
        sector, sub = SecurityClassifier.validate_sector("space mining")
        assert sector is None
        assert sub is None

    def test_parse_themes_json_string(self):
        themes = SecurityClassifier.parse_themes('["ai", "cloud"]')
        assert themes == ["ai", "cloud"]

    def test_parse_themes_list(self):
        themes = SecurityClassifier.parse_themes(["AI", "Cloud"])
        assert themes == ["ai", "cloud"]

    def test_parse_themes_csv(self):
        themes = SecurityClassifier.parse_themes("ai, semiconductors")
        assert themes == ["ai", "semiconductors"]

    def test_parse_themes_empty(self):
        assert SecurityClassifier.parse_themes("[]") == []
        assert SecurityClassifier.parse_themes("") == []

    def test_classify_full(self):
        profile = self.classifier.classify({
            "ticker": "AAPL",
            "isin": "US0378331005",
            "name": "Apple Inc",
            "sector": "technology",
            "subsector": "consumer electronics",
            "geography": "united states",
            "themes": '["ai", "hardware"]',
            "currency": "USD",
            "market_cap": 3000000000000,
        })
        assert profile.ticker == "AAPL"
        assert profile.sector == "technology"
        assert profile.subsector == "consumer electronics"
        assert profile.geography == "united states"
        assert profile.themes == ["ai", "hardware"]
        assert profile.market_cap_bucket == "mega"

    def test_classify_derives_geography_from_isin(self):
        profile = self.classifier.classify({
            "ticker": "NESN",
            "isin": "CH0038863350",
            "name": "Nestle",
            "sector": "consumer staples",
        })
        assert profile.geography == "switzerland"

    def test_classify_market_cap_buckets(self):
        assert self.classifier.classify({"ticker": "X", "market_cap": 300_000_000_000}).market_cap_bucket == "mega"
        assert self.classifier.classify({"ticker": "X", "market_cap": 50_000_000_000}).market_cap_bucket == "large"
        assert self.classifier.classify({"ticker": "X", "market_cap": 5_000_000_000}).market_cap_bucket == "mid"
        assert self.classifier.classify({"ticker": "X", "market_cap": 500_000_000}).market_cap_bucket == "small"
        assert self.classifier.classify({"ticker": "X", "market_cap": 100_000_000}).market_cap_bucket == "micro"

    def test_batch_classify(self):
        profiles = self.classifier.classify_batch([
            {"ticker": "A", "sector": "healthcare"},
            {"ticker": "B", "sector": "energy"},
        ])
        assert len(profiles) == 2
        assert profiles[0].ticker == "A"
        assert profiles[1].ticker == "B"
