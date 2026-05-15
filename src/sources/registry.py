"""Source Registry — manages approved news/data sources (allowlist)."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """Configuration for a single approved source."""
    id: str
    name: str
    domain: str
    type: str  # rss, api, scrape
    url: str
    parser: str
    priority: int = 5
    trust_level: str = "standard"
    enabled: bool = True
    rate_limit_rpm: int = 10
    requires_auth: bool = False
    auth_type: str | None = None
    auth_env_var: str | None = None
    # Phase 7: name of the query-string parameter used to pass an
    # api_key (e.g. ``apiKey`` for NewsAPI, ``token`` for Finnhub).
    # When None, the fetcher defaults to ``apiKey``.
    auth_param_name: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str = ""
    params: dict = field(default_factory=dict)
    # Phase 7: human notes shown in the Sources UI + support bundle.
    notes: str = ""
    # Phase 7: structurally unsupported (e.g. parser not implemented).
    # When True the UI reports status="unsupported" and the collector
    # skips it even if ``enabled`` flips on.
    unsupported: bool = False


class SourceRegistry:
    """Manages the allowlist of approved news/data sources.

    Only sources registered here can be fetched by the Collection Agent.
    This is the single point of control for source access.
    """

    def __init__(self, sources_yaml_path: str | Path):
        self._sources: dict[str, SourceConfig] = {}
        self._allowed_domains: set[str] = set()
        self._load(sources_yaml_path)

    def _load(self, path: str | Path) -> None:
        """Load sources from YAML configuration file."""
        path = Path(path).expanduser()
        if not path.exists():
            logger.warning(f"Sources config not found at {path}")
            return

        with open(path) as f:
            data = yaml.safe_load(f)

        sources_list = data.get("sources", [])
        for src_data in sources_list:
            src = SourceConfig(**{k: v for k, v in src_data.items() if k in SourceConfig.__dataclass_fields__})
            self._sources[src.id] = src
            self._allowed_domains.add(src.domain)

        logger.info(f"Loaded {len(self._sources)} sources from registry")

    def get_source(self, source_id: str) -> SourceConfig | None:
        """Get a source by ID."""
        return self._sources.get(source_id)

    def get_enabled_sources(self) -> list[SourceConfig]:
        """Get all enabled sources, sorted by priority (1=highest)."""
        return sorted(
            [s for s in self._sources.values() if s.enabled],
            key=lambda s: s.priority
        )

    def get_all_sources(self) -> list[SourceConfig]:
        """Get all sources regardless of enabled status."""
        return list(self._sources.values())

    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is in the allowlist."""
        # Strip www. prefix for comparison
        clean = domain.lower().removeprefix("www.")
        return clean in self._allowed_domains or domain.lower() in self._allowed_domains

    def is_url_allowed(self, url: str) -> bool:
        """Check if a URL's domain is in the allowlist."""
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or ""
            return self.is_domain_allowed(domain)
        except Exception:
            return False

    def enable_source(self, source_id: str) -> bool:
        """Enable a source. Returns True if found and enabled."""
        src = self._sources.get(source_id)
        if src:
            src.enabled = True
            logger.info(f"Enabled source: {source_id}")
            return True
        return False

    def disable_source(self, source_id: str) -> bool:
        """Disable a source. Returns True if found and disabled."""
        src = self._sources.get(source_id)
        if src:
            src.enabled = False
            logger.info(f"Disabled source: {source_id}")
            return True
        return False

    def get_sources_by_tag(self, tag: str) -> list[SourceConfig]:
        """Get all enabled sources matching a tag."""
        return [s for s in self.get_enabled_sources() if tag in s.tags]
