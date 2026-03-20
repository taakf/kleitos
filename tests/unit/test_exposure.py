"""Tests for the exposure calculator."""

from src.security_master.exposure import ExposureCalculator


class TestExposureCalculator:
    """Test portfolio exposure calculations."""

    def setup_method(self):
        self.calculator = ExposureCalculator()
        self.holdings = [
            {"ticker": "AAPL", "currency": "USD", "market_value": 18000, "weight_pct": 24.0},
            {"ticker": "MSFT", "currency": "USD", "market_value": 20000, "weight_pct": 26.7},
            {"ticker": "NESN", "currency": "CHF", "market_value": 19000, "weight_pct": 25.3},
            {"ticker": "TSM", "currency": "USD", "market_value": 18000, "weight_pct": 24.0},
        ]
        self.securities = [
            {"ticker": "AAPL", "sector": "technology", "geography": "united states", "themes": '["ai"]'},
            {"ticker": "MSFT", "sector": "technology", "geography": "united states", "themes": '["ai", "cloud"]'},
            {"ticker": "NESN", "sector": "consumer staples", "geography": "switzerland", "themes": '[]'},
            {"ticker": "TSM", "sector": "technology", "geography": "taiwan", "themes": '["semiconductors"]'},
        ]

    def test_sector_exposure(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        tech = [b for b in report.by_sector if b.value == "technology"]
        assert len(tech) == 1
        # AAPL + MSFT + TSM = 24 + 26.7 + 24 = 74.7
        assert tech[0].weight_pct == 74.7
        assert tech[0].holdings_count == 3

    def test_geography_exposure(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        us = [b for b in report.by_geography if b.value == "united states"]
        assert len(us) == 1
        assert us[0].weight_pct == 50.7  # AAPL + MSFT

    def test_currency_exposure(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        usd = [b for b in report.by_currency if b.value == "USD"]
        assert len(usd) == 1
        assert usd[0].weight_pct == 74.7

    def test_theme_exposure(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        ai = [b for b in report.by_theme if b.value == "ai"]
        assert len(ai) == 1
        # AAPL + MSFT = 24 + 26.7 = 50.7
        assert ai[0].weight_pct == 50.7

    def test_concentration_alerts(self):
        """With default thresholds, technology at 74.7% should breach 30% limit."""
        report = self.calculator.calculate(self.holdings, self.securities)
        alerts = report.concentration_alerts
        sector_alerts = [a for a in alerts if a["dimension"] == "sector"]
        assert len(sector_alerts) >= 1
        assert any(a["value"] == "technology" for a in sector_alerts)

    def test_single_name_concentration(self):
        """MSFT at 26.7% should breach 10% single name limit."""
        report = self.calculator.calculate(self.holdings, self.securities)
        name_alerts = [a for a in report.concentration_alerts if a["dimension"] == "single_name"]
        assert len(name_alerts) >= 1

    def test_total_market_value(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        assert report.total_market_value == 75000

    def test_serialization(self):
        report = self.calculator.calculate(self.holdings, self.securities)
        d = self.calculator.to_dict(report)
        assert "by_sector" in d
        assert "concentration_alerts" in d
        assert isinstance(d["by_sector"], list)
