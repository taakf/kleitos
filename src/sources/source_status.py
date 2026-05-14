"""Normalised source health status — Phase 7 shared model.

Every source health response, diagnostics summary, and support-bundle row
uses the same status vocabulary so the UI, the support engineer, and the
operator can speak the same language about a single source.

Status codes
------------
``active``        Source is enabled and last fetch succeeded.
``disabled``      Source is enabled=false in config (operator turned it off).
``missing_key``   Source requires an API key and no value is configured.
``degraded``      Last fetch returned a soft-failure (e.g. parser produced
                  zero items but didn't error). Still enabled.
``rate_limited``  Last fetch hit HTTP 429 or per-source RPM ceiling.
``unreachable``   DNS/timeout/5xx — network-side problem.
``parser_error``  Fetch succeeded, parser threw — content shape changed.
``unsupported``   Source is declared in YAML but its parser is not
                  implemented (or otherwise structurally unsupported).
                  Cannot be enabled by toggling — needs code.
``misconfigured`` Source is declared with an invalid auth_type, wrong
                  env-var, or missing required field.
``error``         Anything else — last_error_message carries a scrubbed
                  one-line summary.

Design notes
------------
- Customer-facing messages live in :data:`_MESSAGES` and never reference
  raw vendor exceptions, request bodies, or HTTP headers.
- :func:`scrub_source_error` is the single sanitiser used by the fetcher,
  the collection agent, the sources API, the diagnostics endpoint, and
  the support bundle. It strips anything that looks like an API key,
  query-string ``key=`` / ``apiKey=`` / ``token=`` parameter, or Bearer
  token from a free-text string.
- :func:`classify_fetch_outcome` maps a :class:`FetchResult` to a typed
  status. The fetcher already has good HTTP-code branching; this layer
  exists so the rest of the app (UI, support bundle, diagnostics) never
  has to know about HTTP at all.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel


Status = Literal[
    "active",
    "disabled",
    "missing_key",
    "degraded",
    "rate_limited",
    "unreachable",
    "parser_error",
    "unsupported",
    "misconfigured",
    "error",
]


class SourceHealth(BaseModel):
    """Stable, schema-frozen health snapshot for a single source.

    Never includes API keys, tokens, or raw exception text. Free-text
    fields are scrubbed via :func:`scrub_source_error` before being set.
    """

    id: str
    name: str
    source_type: str
    enabled: bool
    configured: bool
    status: Status
    parser: str | None = None
    auth_type: str | None = None
    required_env_var: str | None = None
    last_fetch_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    events_fetched_last_run: int | None = None
    supports_backfill: bool = False
    notes: str | None = None
    checked_at: str

    model_config = {"frozen": True}


# ───────────────────────────────────────────────────────────────────────────
# Customer-facing messages
# ───────────────────────────────────────────────────────────────────────────


_MESSAGES: dict[Status, str] = {
    "active":        "Source is active.",
    "disabled":      "Source is disabled in configuration.",
    "missing_key":   "Source requires an API key — set the listed environment variable and restart.",
    "degraded":      "Source responded but returned no items in the last run.",
    "rate_limited":  "Source returned HTTP 429 or hit the configured rate-limit ceiling.",
    "unreachable":   "Could not reach the source. Network error, DNS failure, or 5xx response.",
    "parser_error":  "The source replied but the parser could not extract any items.",
    "unsupported":   "This source is declared but its parser is not implemented in this build.",
    "misconfigured": "Source configuration is invalid (auth type, env var, or required field).",
    "error":         "Source returned an unexpected error. See last_error_message for the scrubbed summary.",
}


def message_for(status: Status) -> str:
    return _MESSAGES.get(status, _MESSAGES["error"])


# ───────────────────────────────────────────────────────────────────────────
# Scrubbing — never let an API key reach a UI/log/support bundle
# ───────────────────────────────────────────────────────────────────────────

# Generic patterns: NewsAPI/Finnhub keys are opaque alphanumeric strings,
# not the ``sk-…`` family. We strip anything inside a query-string parameter
# whose name is ``key``, ``apikey``, ``api_key``, ``token``, ``access_token``,
# or ``auth``. Bearer tokens in the form ``Bearer <thing>`` are masked too.
_KEY_QUERY_PARAM = re.compile(
    r"(?P<name>(?:api[_-]?)?key|token|access[_-]?token|auth(?:orization)?|secret)"
    r"=(?P<value>[^&\s\"']+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9_\-.]+", re.IGNORECASE)
# Catch the ``sk-…``, ``sk-ant-…``, ``AIza…``, ``ghp_…``, Telegram-style
# patterns too — same set the LLM scrubber uses, so leaked-vendor-key
# patterns inside any source error are also masked.
_VENDOR_TOKEN_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpars]-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b[0-9]{8,}:[A-Za-z0-9_\-]{30,}\b"),
]


def scrub_source_error(text: str) -> str:
    """Strip anything that looks like an API key, token, or auth header.

    Safe to call on any free-text string before logging, storing in the
    DB, returning over HTTP, or writing to a support bundle.
    """
    if not text:
        return text
    out = _KEY_QUERY_PARAM.sub(
        lambda m: f"{m.group('name')}=***",
        text,
    )
    out = _BEARER.sub("Bearer ***", out)
    for pat in _VENDOR_TOKEN_PATTERNS:
        out = pat.sub("***", out)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Classifier — translates fetcher outcomes into the typed vocabulary
# ───────────────────────────────────────────────────────────────────────────


def classify_fetch_outcome(
    *,
    success: bool,
    status_code: int | None,
    error: str | None,
    fetched_count: int | None = None,
) -> tuple[Status, str | None]:
    """Return ``(status, last_error_code)`` for a fetch outcome.

    ``last_error_code`` is a short machine token (e.g. ``"http_401"``,
    ``"timeout"``, ``"parser_zero_items"``) for branching in the UI;
    the customer-facing message is derived from the status code via
    :func:`message_for`.
    """
    if success:
        if fetched_count is not None and fetched_count == 0:
            return "degraded", "zero_items"
        return "active", None

    err = (error or "").lower()

    if status_code is not None:
        if status_code in (401, 403):
            # Distinguish "missing key" from "invalid key" by error text:
            # the fetcher synthesises "Missing API key: NEWSAPI_KEY" before
            # the request when no key is configured.
            if "missing api key" in err:
                return "missing_key", "missing_key"
            return "misconfigured", f"http_{status_code}"
        if status_code == 429:
            return "rate_limited", "http_429"
        if 500 <= status_code < 600:
            return "unreachable", f"http_{status_code}"
        if 400 <= status_code < 500:
            return "error", f"http_{status_code}"

    if "missing api key" in err:
        return "missing_key", "missing_key"
    if "timeout" in err or "timed out" in err:
        return "unreachable", "timeout"
    # DNS-resolution failures get their own detail_code so the support
    # engineer can tell "vendor is down" from "your DNS is broken".
    if "dns" in err or "resolve" in err:
        return "unreachable", "dns"
    if "connection" in err or "network" in err:
        return "unreachable", "network"
    if "rate" in err and "limit" in err:
        return "rate_limited", "rate_limit"
    if "parser" in err or "parse" in err:
        return "parser_error", "parse_failure"
    if "not in allowlist" in err:
        return "misconfigured", "url_not_allowed"

    return "error", "unknown"


# ───────────────────────────────────────────────────────────────────────────
# Constructors
# ───────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_health(
    *,
    id: str,
    name: str,
    source_type: str,
    enabled: bool,
    configured: bool,
    status: Status,
    parser: str | None = None,
    auth_type: str | None = None,
    required_env_var: str | None = None,
    last_fetch_at: str | None = None,
    last_success_at: str | None = None,
    last_error_at: str | None = None,
    last_error_code: str | None = None,
    last_error_message: str | None = None,
    events_fetched_last_run: int | None = None,
    notes: str | None = None,
    supports_backfill: bool = False,
) -> SourceHealth:
    """Construct a :class:`SourceHealth`, scrubbing any free-text fields."""
    return SourceHealth(
        id=id,
        name=name,
        source_type=source_type,
        enabled=enabled,
        configured=configured,
        status=status,
        parser=parser,
        auth_type=auth_type,
        required_env_var=required_env_var,
        last_fetch_at=last_fetch_at,
        last_success_at=last_success_at,
        last_error_at=last_error_at,
        last_error_code=last_error_code,
        last_error_message=scrub_source_error(last_error_message) if last_error_message else None,
        events_fetched_last_run=events_fetched_last_run,
        supports_backfill=supports_backfill,
        notes=notes,
        checked_at=_now_iso(),
    )


# ───────────────────────────────────────────────────────────────────────────
# Summary helpers
# ───────────────────────────────────────────────────────────────────────────


def summarise_by_status(healths: list[SourceHealth] | list[dict[str, Any]]) -> dict[str, int]:
    """Count sources by status. Accepts ``SourceHealth`` instances or dicts.

    Returns a dict with one entry per Status value plus a ``total`` key.
    Missing statuses default to 0 so the dashboard can render every cell.
    """
    counts: dict[str, int] = {s: 0 for s in (
        "active", "disabled", "missing_key", "degraded", "rate_limited",
        "unreachable", "parser_error", "unsupported", "misconfigured", "error",
    )}
    counts["total"] = 0
    for h in healths:
        counts["total"] += 1
        s = h.status if isinstance(h, SourceHealth) else h.get("status")
        if s in counts:
            counts[s] += 1
    return counts
