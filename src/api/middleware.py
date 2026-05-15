"""Axion API middleware - request logging, error handling, auth, and rate limiting."""

import hmac
import logging
import time
import traceback
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import get_settings

logger = logging.getLogger("axion.middleware")



class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and return a consistent JSON error response."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "Unhandled exception on %s %s: %s",
                request.method,
                request.url.path,
                exc,
            )
            logger.debug(traceback.format_exc())
            settings = get_settings()
            content = {"detail": "Internal server error"}
            if settings.system.environment == "development":
                content["error"] = str(exc)
            return JSONResponse(
                status_code=500,
                content=content,
            )


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate API requests using X-API-Key header."""

    # Paths that do not require authentication
    EXEMPT_PATHS = {"/api/v1/health", "/"}

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()

        # Skip auth entirely if disabled
        if not settings.api.auth_enabled:
            return await call_next(request)

        path = request.url.path

        # Only protect /api/v1/* paths (not dashboard, root, etc.)
        if not path.startswith("/api/v1/"):
            return await call_next(request)

        # Exempt specific paths
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Dashboard paths are exempt
        if path.startswith("/dashboard"):
            return await call_next(request)

        # Same-host requests are exempt (dashboard JS calling API on same machine)
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)

        # Check for API key
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing API key. Provide X-API-Key header."},
            )

        if not hmac.compare_digest(api_key, settings.api.api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key."},
            )

        return await call_next(request)


#: Loopback client IPs recognised as "the local dashboard".  A
#: request from any of these on a GET verb is classified into the
#: ``dashboard_read`` bucket regardless of path.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({
    "127.0.0.1",
    "::1",
    "localhost",
    "testclient",          # FastAPI / httpx.AsyncClient / TestClient default
})

#: HTTP verbs that count as "mutation" traffic (even from loopback).
_MUTATION_VERBS: frozenset[str] = frozenset({
    "POST", "PUT", "PATCH", "DELETE",
})


def _classify_request(method: str, client_host: str, path: str) -> str:
    """Classify a request into a rate-limit bucket.

    Returns one of:
      * ``"exempt"``           — not rate-limited at all (health, non-API)
      * ``"dashboard_read"``   — loopback GET to /api/v1/*
      * ``"mutation"``         — any write verb against /api/v1/*
      * ``"public"``           — everything else (non-loopback reads)

    Buckets are intentionally per-request-type so a normal dashboard
    session can't trip the public ceiling and a write-flood from any
    origin is bounded tightly.
    """
    # Never rate-limit health or anything outside /api/v1/*
    if not path.startswith("/api/v1/") or path == "/api/v1/health":
        return "exempt"

    upper = method.upper()
    if upper in _MUTATION_VERBS:
        return "mutation"

    # GET / HEAD / OPTIONS etc. — decide by origin.
    if client_host in _LOOPBACK_HOSTS:
        return "dashboard_read"
    return "public"


def _limit_for_bucket(bucket: str, settings) -> int:
    """Resolve the requests-per-minute ceiling for a bucket."""
    if bucket == "dashboard_read":
        return int(settings.api.rate_limit_dashboard_read_rpm)
    if bucket == "mutation":
        return int(settings.api.rate_limit_mutation_rpm)
    if bucket == "public":
        return int(settings.api.rate_limit_rpm)
    return 0  # exempt


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP, per-bucket sliding-window rate limiter.

    Phase 9K rewrite: the pre-9K middleware enforced a single
    ``rate_limit_rpm`` ceiling across every /api/v1/* request.  Phase
    9J proved that's too tight for real dashboard use — a normal tab
    cycle on a ~100 rpm limit 429'd the UI within a minute.

    The 9K design splits traffic into three buckets (dashboard_read,
    mutation, public) with independent ceilings, so:

      * local dashboard GETs can burst freely during normal browsing
      * mutations are still limited to prevent write-flood abuse
      * non-loopback reads keep the legacy ``rate_limit_rpm`` ceiling
        as a belt-and-braces guard against public abuse

    State is process-local; if Axion grows a multi-worker deployment
    later, a shared limiter (redis, in-memory broadcast) can replace
    the singleton dict without changing the bucket policy.
    """

    def __init__(self, app):
        super().__init__(app)
        # {(bucket, client_ip) -> [timestamp, timestamp, ...]}
        self._windows: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._last_prune: float = 0.0
        self._window_seconds: float = 60.0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        bucket = _classify_request(request.method, client_ip, path)

        if bucket == "exempt":
            return await call_next(request)

        settings = get_settings()
        limit = _limit_for_bucket(bucket, settings)
        if limit <= 0:
            return await call_next(request)

        now = time.time()
        window = self._window_seconds

        # Prune stale keys every 5 minutes to keep the dict bounded.
        if now - self._last_prune > 300:
            stale = [
                k for k, ts in self._windows.items()
                if not ts or now - ts[-1] > window
            ]
            for k in stale:
                del self._windows[k]
            self._last_prune = now

        key = (bucket, client_ip)
        timestamps = [t for t in self._windows[key] if now - t < window]

        if len(timestamps) >= limit:
            # Preserve the old error shape AND surface the bucket +
            # Retry-After header so clients can back off intelligently.
            oldest = timestamps[0] if timestamps else now
            retry_after = max(1, int(window - (now - oldest)) + 1)
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded for bucket {bucket!r}. "
                        f"Maximum {limit} requests per minute."
                    ),
                    "bucket": bucket,
                    "limit_per_minute": limit,
                    "retry_after_seconds": retry_after,
                },
            )
            response.headers["Retry-After"] = str(retry_after)
            self._windows[key] = timestamps  # trim
            return response

        timestamps.append(now)
        self._windows[key] = timestamps
        return await call_next(request)
