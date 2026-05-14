"""Normalised provider status — Phase 6 shared model.

Every provider's ``test_connection()`` returns a :class:`ProviderStatus`,
and the settings test-provider endpoint returns the same shape. The
classification helpers translate vendor SDK exceptions into a small set
of stable status codes that the dashboard can render without ever seeing
the raw exception text.

Status codes
------------
``active``         Provider responded successfully to a minimal call.
``disabled``       AI is explicitly turned off (provider == "none").
``missing_key``    No API key configured for the requested provider.
``invalid_key``    Provider rejected the key (401 / 403 / "invalid key").
``quota_issue``    Rate-limit or billing/quota error from the provider.
``unreachable``    Network error or 5xx server-side issue.
``misconfigured``  Provider package not installed, or other local
                   configuration failure unrelated to the key.
``error``          Anything else. Message is a one-line summary with key
                   material scrubbed.

Design notes
------------
- The classifier never returns the raw exception text. It checks the
  exception class first, then a normalised string form, and selects a
  pre-written customer message. Any vendor message that survives the
  classifier is passed through :func:`scrub_secrets` so accidental key
  fragments cannot leak.
- The classifier is provider-agnostic by default. Each provider's
  ``test_connection()`` may pass its own type-aware classifier first
  (e.g. ``openai.AuthenticationError``) before falling back to this one.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel


Status = Literal[
    "active",
    "disabled",
    "missing_key",
    "invalid_key",
    "quota_issue",
    "unreachable",
    "misconfigured",
    "error",
]


class ProviderStatus(BaseModel):
    """Stable, schema-frozen result of a provider ``test_connection()`` call.

    Never includes raw API keys, raw SDK exception text, or HTTP request
    bodies. The ``message`` field is a one-line human summary suitable for
    display in the Settings UI. ``detail_code`` is a machine-friendly
    short token (e.g. ``"key_format"``, ``"sdk_missing"``,
    ``"rate_limit"``) that callers can use to branch on without parsing
    the message.
    """

    provider: str
    status: Status
    configured: bool
    available: bool
    model: str | None = None
    message: str
    detail_code: str | None = None
    checked_at: str  # ISO-8601 UTC

    model_config = {"frozen": True}


# ───────────────────────────────────────────────────────────────────────────
# Customer-facing messages — never reference keys, headers, or request bodies.
# ───────────────────────────────────────────────────────────────────────────

_MESSAGES: dict[Status, str] = {
    "active":        "Provider is responding.",
    "disabled":      "AI is disabled — Axion is running in standard (deterministic) mode.",
    "missing_key":   "No API key configured for this provider.",
    "invalid_key":   "The provider rejected this API key. Check that it is correct and active.",
    "quota_issue":   "The provider returned a rate-limit or quota error. Check your billing or wait a minute and retry.",
    "unreachable":   "Could not reach the provider. Check your internet connection or provider status page.",
    "misconfigured": "The provider package is not installed in this environment.",
    "error":         "The provider returned an unexpected error.",
}


def message_for(status: Status) -> str:
    """Return the canonical customer-facing message for *status*."""
    return _MESSAGES.get(status, _MESSAGES["error"])


# ───────────────────────────────────────────────────────────────────────────
# Secret scrubbing — defence-in-depth against accidental key leakage.
# ───────────────────────────────────────────────────────────────────────────

# Conservative patterns: anything that looks like a known API key gets
# replaced with ``***`` in any free-text message before it leaves the
# server. Matches the support-bundle redaction patterns so the rule set
# is centralised.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpars]-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b[0-9]{8,}:[A-Za-z0-9_\-]{30,}\b"),  # Telegram-style
]


def scrub_secrets(text: str) -> str:
    """Replace any token that looks like an API key with ``***``."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("***", out)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Generic exception classifier — used as a fallback by every provider.
# ───────────────────────────────────────────────────────────────────────────


def classify_provider_exception(exc: BaseException) -> tuple[Status, str]:
    """Return ``(status, detail_code)`` for *exc*.

    Providers should try their own type-specific classification first
    (e.g. ``isinstance(exc, openai.AuthenticationError)``); this generic
    fallback uses class name + string content as a second pass.

    The returned ``detail_code`` is a short machine-readable token that
    the UI may inspect for branching. The ``message`` for the caller is
    derived from the status (see :func:`message_for`), not from the
    exception text — that's how we guarantee no key material leaks.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    # Auth-shaped errors
    if "authentication" in name or "auth" in name or "unauthorized" in name:
        return "invalid_key", "auth_error"
    if "permission" in name:
        return "invalid_key", "auth_error"
    if "invalid" in msg and "api" in msg and "key" in msg:
        return "invalid_key", "auth_error"
    if "401" in msg or "403" in msg:
        return "invalid_key", "auth_status"

    # Rate-limit / quota
    if "ratelimit" in name or "rate_limit" in name or "rate-limit" in name:
        return "quota_issue", "rate_limit"
    if "quota" in name or "billing" in name:
        return "quota_issue", "quota_billing"
    if "429" in msg or "rate limit" in msg or "quota" in msg or "insufficient_quota" in msg:
        return "quota_issue", "rate_limit"
    if "billing" in msg or "credits" in msg:
        return "quota_issue", "quota_billing"

    # Network / connectivity
    if "connection" in name or "timeout" in name or "network" in name:
        return "unreachable", "network"
    if "dns" in msg or "resolve" in msg:
        return "unreachable", "dns"
    if "timed out" in msg or "timeout" in msg:
        return "unreachable", "timeout"

    # 5xx server-side
    if "apistatus" in name:
        # SDKs usually carry ``status_code`` on the exception.
        code = getattr(exc, "status_code", None)
        if isinstance(code, int):
            if code in (401, 403):
                return "invalid_key", f"http_{code}"
            if code == 429:
                return "quota_issue", "http_429"
            if code >= 500:
                return "unreachable", f"http_{code}"
            return "error", f"http_{code}"

    if "500" in msg or "502" in msg or "503" in msg or "504" in msg or "internal server" in msg:
        return "unreachable", "server_error"

    # Local environment problems
    if "modulenotfound" in name or "no module" in msg or "not installed" in msg:
        return "misconfigured", "sdk_missing"

    return "error", "unknown"


# ───────────────────────────────────────────────────────────────────────────
# Constructors
# ───────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_status(
    *,
    provider: str,
    status: Status,
    configured: bool,
    model: str | None = None,
    message: str | None = None,
    detail_code: str | None = None,
) -> ProviderStatus:
    """Construct a :class:`ProviderStatus`, applying default messages and
    scrubbing any free-text override.
    """
    msg = message if message is not None else message_for(status)
    return ProviderStatus(
        provider=provider,
        status=status,
        configured=configured,
        available=(status == "active"),
        model=model,
        message=scrub_secrets(msg),
        detail_code=detail_code,
        checked_at=_now_iso(),
    )


def status_from_exception(
    *,
    provider: str,
    exc: BaseException,
    configured: bool,
    model: str | None = None,
) -> ProviderStatus:
    """Map an arbitrary exception to a :class:`ProviderStatus`.

    Callers that want type-specific behaviour (e.g. catching
    ``openai.AuthenticationError`` directly) should build the status
    themselves via :func:`build_status` and only fall back to this
    helper for the generic catch-all branch.
    """
    status_code, detail = classify_provider_exception(exc)
    return build_status(
        provider=provider,
        status=status_code,
        configured=configured,
        model=model,
        detail_code=detail,
    )
