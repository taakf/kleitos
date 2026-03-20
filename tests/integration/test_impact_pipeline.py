"""Integration test – rule-based impact pipeline end-to-end."""

from src.impact.rules import RuleEngine
from src.security_master.classifier import SecurityClassifier
from src.security_master.exposure import ExposureCalculator


class TestImpactPipeline:
    """Test the full pipeline: classify → match → expose."""

    def setup_method(self):
        self.classifier = SecurityClassifier()
        self.engine = RuleEngine()
        self.exposure_calc = ExposureCalculator()

    def test_classify_then_match(self):
        """Classify raw securities, then run rule matching."""
        raw_securities = [
            {"ticker": "AAPL", "isin": "US0378331005", "name": "Apple Inc",
             "sector": "technology", "themes": '["ai"]'},
            {"ticker": "NESN", "isin": "CH0038863350", "name": "Nestle SA",
             "sector": "consumer staples", "themes": '[]'},
        ]
        profiles = self.classifier.classify_batch(raw_securities)
        assert profiles[0].geography == "united states"
        assert profiles[1].geography == "switzerland"

        # Convert profiles back to dicts for rule engine
        securities = [
            {"ticker": p.ticker, "isin": p.isin, "name": p.name,
             "sector": p.sector, "geography": p.geography,
             "themes": str(p.themes).replace("'", '"')}
            for p in profiles
        ]
        holdings = [
            {"id": "h1", "ticker": "AAPL", "currency": "USD"},
            {"id": "h2", "ticker": "NESN", "currency": "CHF"},
        ]

        matches = self.engine.find_matches(
            "AAPL: Apple reports strong AI revenue growth",
            "The technology giant saw significant gains from artificial intelligence products.",
            holdings, securities,
        )
        # Should match AAPL by ticker in headline
        aapl_matches = [m for m in matches if m.holding_id == "h1"]
        assert len(aapl_matches) >= 1

    def test_exposure_after_classification(self):
        """Classify securities and calculate portfolio exposures."""
        raw = [
            {"ticker": "AAPL", "isin": "US0378331005", "name": "Apple",
             "sector": "technology", "themes": '["ai"]'},
            {"ticker": "MSFT", "isin": "US5949181045", "name": "Microsoft",
             "sector": "technology", "themes": '["ai", "cloud"]'},
        ]
        profiles = self.classifier.classify_batch(raw)

        holdings = [
            {"ticker": "AAPL", "currency": "USD", "market_value": 50000, "weight_pct": 50},
            {"ticker": "MSFT", "currency": "USD", "market_value": 50000, "weight_pct": 50},
        ]
        securities = [
            {"ticker": p.ticker, "sector": p.sector, "geography": p.geography,
             "themes": str(p.themes).replace("'", '"')}
            for p in profiles
        ]

        report = self.exposure_calc.calculate(holdings, securities)
        assert report.total_market_value == 100000
        assert len(report.by_sector) >= 1
        # 100% technology
        tech = [b for b in report.by_sector if b.value == "technology"]
        assert tech[0].weight_pct == 100.0

    def test_scope_classification_with_real_data(self):
        """Verify scope classification through the full pipeline."""
        securities = [
            {"ticker": "AAPL", "isin": "US0378331005", "name": "Apple Inc",
             "sector": "technology", "geography": "united states", "themes": '["ai"]'},
        ]
        holdings = [{"id": "h1", "ticker": "AAPL", "currency": "USD"}]

        # Single stock event
        matches = self.engine.find_matches(
            "AAPL beats earnings estimates", "", holdings, securities,
        )
        scope = self.engine.classify_scope(matches, len(holdings))
        assert scope in ("single_stock", "multi_factor")

        # Systemic event
        matches = self.engine.find_matches(
            "Global recession fears grow as GDP contracts", "", holdings, securities,
        )
        scope = self.engine.classify_scope(matches, len(holdings))
        assert scope == "systemic"
