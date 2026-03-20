"""HTTP Fetcher — rate-limited, retry-capable HTTP client for source fetching."""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx

from src.sources.registry import SourceConfig, SourceRegistry

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Result of a fetch attempt."""
    source_id: str
    success: bool
    status_code: int | None = None
    content: str | None = None
    content_type: str | None = None
    content_hash: str | None = None
    error: str | None = None
    fetch_duration_ms: int = 0


class RateLimiter:
    """Simple per-source rate limiter."""

    def __init__(self):
        self._last_fetch: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait_if_needed(self, source_id: str, rpm: int) -> None:
        """Wait if we need to respect rate limits."""
        async with self._lock:
            min_interval = 60.0 / max(rpm, 1)
            last = self._last_fetch.get(source_id, 0)
            elapsed = time.time() - last
            if elapsed < min_interval:
                wait = min_interval - elapsed
                logger.debug(f"Rate limit: waiting {wait:.1f}s for {source_id}")
                await asyncio.sleep(wait)
            self._last_fetch[source_id] = time.time()


class SourceFetcher:
    """Fetches content from approved sources with rate limiting and retry."""

    def __init__(self, registry: SourceRegistry, timeout: float = 30.0, max_retries: int = 3):
        self._registry = registry
        self._timeout = timeout
        self._max_retries = max_retries
        self._rate_limiter = RateLimiter()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                headers={
                    "User-Agent": "Axion/1.0 Portfolio Intelligence System",
                    "Accept": "application/json, application/xml, text/xml, text/html, */*",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_source(self, source: SourceConfig, api_keys: dict[str, str] | None = None) -> FetchResult:
        """Fetch content from a single source.

        Args:
            source: Source configuration
            api_keys: Dict of env_var_name -> key value for authenticated sources
        """
        # Validate URL against allowlist
        if not self._registry.is_url_allowed(source.url):
            logger.error(f"BLOCKED: URL not in allowlist: {source.url}")
            return FetchResult(
                source_id=source.id,
                success=False,
                error=f"URL domain not in allowlist: {source.url}",
            )

        # Apply rate limiting
        await self._rate_limiter.wait_if_needed(source.id, source.rate_limit_rpm)

        # Build request
        headers = {}
        params = dict(source.params) if source.params else {}

        if source.requires_auth:
            if not api_keys:
                return FetchResult(
                    source_id=source.id,
                    success=False,
                    error=f"Source requires auth but no api_keys provided ({source.auth_env_var})",
                )
            key = api_keys.get(source.auth_env_var or "", "")
            if not key:
                return FetchResult(
                    source_id=source.id,
                    success=False,
                    error=f"Missing API key: {source.auth_env_var}",
                )
            if source.auth_type == "api_key":
                # Use source-specific param name, defaulting to "apiKey"
                param_name = getattr(source, "auth_param_name", None) or "apiKey"
                params[param_name] = key
            elif source.auth_type == "bearer":
                headers["Authorization"] = f"Bearer {key}"

        # Fetch with retry
        client = await self._get_client()
        last_error = None

        for attempt in range(self._max_retries):
            start = time.time()
            try:
                response = await client.get(source.url, headers=headers, params=params)
                duration_ms = int((time.time() - start) * 1000)

                content = response.text
                content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]

                if response.status_code == 200:
                    logger.info(
                        f"Fetched {source.id}: {response.status_code} "
                        f"({len(content)} bytes, {duration_ms}ms)"
                    )
                    return FetchResult(
                        source_id=source.id,
                        success=True,
                        status_code=response.status_code,
                        content=content,
                        content_type=response.headers.get("content-type", ""),
                        content_hash=content_hash,
                        fetch_duration_ms=duration_ms,
                    )
                elif response.status_code == 429:
                    # Rate limited — respect Retry-After header or use backoff
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = min(int(retry_after), 120)
                        except ValueError:
                            wait = min(2 ** attempt * 5, 60)
                    else:
                        wait = min(2 ** attempt * 5, 60)
                    logger.warning(f"Rate limited by {source.id}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    last_error = f"HTTP {response.status_code}: Rate limited"
                    continue  # skip the generic backoff below
                elif 400 <= response.status_code < 500:
                    # Client errors (except 429) are deterministic — don't retry
                    return FetchResult(
                        source_id=source.id,
                        success=False,
                        status_code=response.status_code,
                        error=f"HTTP {response.status_code}: Client error (not retried)",
                        fetch_duration_ms=duration_ms,
                    )
                else:
                    # 5xx server errors — retry with backoff
                    last_error = f"HTTP {response.status_code}"
                    logger.warning(f"Fetch failed for {source.id}: {last_error}")

            except httpx.TimeoutException:
                duration_ms = int((time.time() - start) * 1000)
                last_error = "Timeout"
                logger.warning(f"Timeout fetching {source.id} ({duration_ms}ms)")
            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(f"Request error for {source.id}: {last_error}")

            # Backoff before retry
            if attempt < self._max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        return FetchResult(
            source_id=source.id,
            success=False,
            error=f"Failed after {self._max_retries} attempts: {last_error}",
        )

    async def fetch_all_enabled(self, api_keys: dict[str, str] | None = None) -> list[FetchResult]:
        """Fetch from all enabled sources sequentially (respecting rate limits)."""
        sources = self._registry.get_enabled_sources()
        results = []
        for source in sources:
            result = await self.fetch_source(source, api_keys)
            results.append(result)
        return results
