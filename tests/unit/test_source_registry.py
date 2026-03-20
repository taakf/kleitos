"""Tests for the Source Registry / Allowlist."""

import tempfile
from pathlib import Path

import yaml

from src.sources.registry import SourceRegistry


class TestSourceRegistry:
    """Test source registration and allowlisting."""

    def _create_registry(self, sources_data: list) -> SourceRegistry:
        """Helper to create a registry from source data."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"sources": sources_data}, f)
            return SourceRegistry(f.name)

    def test_load_sources(self):
        registry = self._create_registry([
            {"id": "test-rss", "name": "Test", "domain": "example.com",
             "type": "rss", "url": "https://example.com/feed", "parser": "rss_generic"},
        ])
        assert len(registry.get_all_sources()) == 1
        assert registry.get_source("test-rss") is not None

    def test_domain_allowlist(self):
        registry = self._create_registry([
            {"id": "test", "name": "Test", "domain": "example.com",
             "type": "rss", "url": "https://example.com/feed", "parser": "rss_generic"},
        ])
        assert registry.is_domain_allowed("example.com") is True
        assert registry.is_domain_allowed("evil.com") is False

    def test_url_allowlist(self):
        registry = self._create_registry([
            {"id": "test", "name": "Test", "domain": "reuters.com",
             "type": "rss", "url": "https://reuters.com/feed", "parser": "rss_generic"},
        ])
        assert registry.is_url_allowed("https://reuters.com/business") is True
        assert registry.is_url_allowed("https://evil.com/malware") is False

    def test_enabled_filter(self):
        registry = self._create_registry([
            {"id": "enabled", "name": "Enabled", "domain": "a.com",
             "type": "rss", "url": "https://a.com/f", "parser": "rss_generic", "enabled": True},
            {"id": "disabled", "name": "Disabled", "domain": "b.com",
             "type": "rss", "url": "https://b.com/f", "parser": "rss_generic", "enabled": False},
        ])
        enabled = registry.get_enabled_sources()
        assert len(enabled) == 1
        assert enabled[0].id == "enabled"

    def test_enable_disable(self):
        registry = self._create_registry([
            {"id": "src", "name": "Source", "domain": "x.com",
             "type": "rss", "url": "https://x.com/f", "parser": "rss_generic", "enabled": True},
        ])
        registry.disable_source("src")
        assert len(registry.get_enabled_sources()) == 0

        registry.enable_source("src")
        assert len(registry.get_enabled_sources()) == 1

    def test_priority_ordering(self):
        registry = self._create_registry([
            {"id": "low", "name": "Low", "domain": "a.com",
             "type": "rss", "url": "https://a.com/f", "parser": "rss_generic", "priority": 5},
            {"id": "high", "name": "High", "domain": "b.com",
             "type": "rss", "url": "https://b.com/f", "parser": "rss_generic", "priority": 1},
        ])
        sources = registry.get_enabled_sources()
        assert sources[0].id == "high"  # Priority 1 comes first
