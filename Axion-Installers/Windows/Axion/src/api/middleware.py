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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP rate limiting for API endpoints."""

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_prune: float = 0.0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only rate-limit /api/v1/* paths, excluding health and dashboard
        if not path.startswith("/api/v1/") or path == "/api/v1/health":
            return await call_next(request)

        settings = get_settings()
        rpm = settings.api.rate_limit_rpm
        now = time.time()
        window = 60.0  # 1 minute

        # Prune stale IPs every 5 minutes to prevent memory leak
        if now - self._last_prune > 300:
            stale = [ip for ip, ts in self._requests.items() if not ts or now - ts[-1] > window]
            for ip in stale:
                del self._requests[ip]
            self._last_prune = now

        # Use client IP as the key
        client_ip = request.client.host if request.client else "unknown"

        # Clean up old timestamps outside the window
        timestamps = self._requests[client_ip]
        self._requests[client_ip] = [t for t in timestamps if now - t < window]

        if len(self._requests[client_ip]) >= rpm:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Maximum {rpm} requests per minute."},
            )

        self._requests[client_ip].append(now)
        return await call_next(request)
