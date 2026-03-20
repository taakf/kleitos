"""Shared test fixtures for Axion tests."""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test_kleitos.db")


@pytest.fixture
def sample_holdings():
    """Sample holdings for testing."""
    return [
        {"id": "h1", "ticker": "AAPL", "currency": "USD", "quantity": 100,
         "current_price": 180.0, "market_value": 18000.0, "weight_pct": 6.2,
         "portfolio_id": "main", "status": "active"},
        {"id": "h2", "ticker": "MSFT", "currency": "USD", "quantity": 50,
         "current_price": 400.0, "market_value": 20000.0, "weight_pct": 6.9,
         "portfolio_id": "main", "status": "active"},
        {"id": "h3", "ticker": "NESN", "currency": "CHF", "quantity": 200,
         "current_price": 95.0, "market_value": 19000.0, "weight_pct": 6.5,
         "portfolio_id": "main", "status": "active"},
        {"id": "h4", "ticker": "TSM", "currency": "USD", "quantity": 150,
         "current_price": 120.0, "market_value": 18000.0, "weight_pct": 6.2,
         "portfolio_id": "main", "status": "active"},
    ]


@pytest.fixture
def sample_securities():
    """Sample securities for testing."""
    return [
        {"ticker": "AAPL", "isin": "US0378331005", "name": "Apple Inc",
         "sector": "technology", "subsector": "consumer electronics",
         "geography": "united states", "themes": '["ai", "hardware"]'},
        {"ticker": "MSFT", "isin": "US5949181045", "name": "Microsoft Corporation",
         "sector": "technology", "subsector": "software",
         "geography": "united states", "themes": '["ai", "cloud"]'},
        {"ticker": "NESN", "isin": "CH0038863350", "name": "Nestle SA",
         "sector": "consumer staples", "subsector": "food",
         "geography": "switzerland", "themes": '["emerging markets"]'},
        {"ticker": "TSM", "isin": "US8740391003", "name": "Taiwan Semiconductor",
         "sector": "technology", "subsector": "semiconductors",
         "geography": "taiwan", "themes": '["ai", "semiconductors"]'},
    ]


@pytest.fixture
def sample_csv():
    """Sample portfolio CSV content."""
    return """ticker,quantity,price,currency,isin
AAPL,100,180.50,USD,US0378331005
MSFT,50,401.20,USD,US5949181045
NESN,200,95.30,CHF,CH0038863350
TSM,150,120.80,USD,US8740391003
GOOGL,75,155.00,USD,US02079K3059
"""


@pytest.fixture
def sample_rss_feed():
    """Sample RSS feed content for parser testing."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Financial News</title>
    <item>
      <title>Apple Reports Record Q4 Earnings</title>
      <link>https://example.com/apple-q4</link>
      <description>Apple Inc reported record quarterly earnings, beating analyst expectations.</description>
      <pubDate>Wed, 12 Mar 2026 10:00:00 GMT</pubDate>
      <guid>https://example.com/apple-q4</guid>
    </item>
    <item>
      <title>Federal Reserve Raises Interest Rates by 25 Basis Points</title>
      <link>https://example.com/fed-rate</link>
      <description>The Federal Reserve announced a 25 basis point rate increase, citing persistent inflation.</description>
      <pubDate>Wed, 12 Mar 2026 14:00:00 GMT</pubDate>
      <guid>https://example.com/fed-rate</guid>
    </item>
    <item>
      <title>Semiconductor Shortage Impacts Global Supply Chains</title>
      <link>https://example.com/chip-shortage</link>
      <description>A new wave of semiconductor shortages is affecting manufacturers worldwide.</description>
      <pubDate>Wed, 12 Mar 2026 08:00:00 GMT</pubDate>
      <guid>https://example.com/chip-shortage</guid>
    </item>
  </channel>
</rss>
"""
