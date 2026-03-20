"""Shared LLM client facade for Axion agents.

This module is the ONLY LLM interface that the rest of Axion should import.
It delegates to the configured provider (Anthropic, OpenAI, Gemini, etc.)
via the provider abstraction in ``src.llm.providers``.

Public API (unchanged from pre-abstraction era):

- ``is_llm_available()`` — fast local check
- ``call_llm_json(prompt, ...)`` — send prompt, get parsed JSON back
- ``get_llm_client()`` — low-level client access (Anthropic-specific, deprecated)
- ``close_llm_client()`` — shutdown hook
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class LLMUnavailableError(RuntimeError):
    """Raised when an LLM call is attempted but no valid API key is configured."""


# ---------------------------------------------------------------------------
# Internal: get the configured provider
# ---------------------------------------------------------------------------
def _get_provider():
    """Return the singleton provider instance for the configured provider."""
    from src.llm.providers import get_provider
    return get_provider()


# ---------------------------------------------------------------------------
# Public API — these function signatures MUST NOT change.
# All 14+ import sites in agents, routes, and integrations depend on them.
# ---------------------------------------------------------------------------

def is_llm_available() -> bool:
    """Return ``True`` if the configured LLM provider is available.

    Fast local check (no network call).
    """
    try:
        return _get_provider().is_available()
    except Exception:
        return False


async def call_llm_json(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Send *prompt* to the configured LLM provider and return parsed JSON.

    Retries on rate-limit and transient errors with exponential back-off.
    Re-raises after all retries are exhausted.

    Parameters
    ----------
    prompt:
        The full user-message to send.
    model, temperature, max_tokens:
        Overrides for the defaults in ``settings.llm``.
    max_retries:
        Override the default retry count.

    Returns
    -------
    dict
        The parsed JSON object from the model's response.

    Raises
    ------
    ValueError
        If the model response is not valid JSON after all attempts.
    LLMUnavailableError
        If no provider is available.
    """
    provider = _get_provider()
    if not provider.is_available():
        raise LLMUnavailableError(
            "No LLM provider available. "
            "Set an API key in Settings or ~/.axion.env."
        )
    return await provider.call_json(
        prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
    )


async def call_llm_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send *prompt* to the configured LLM and return raw text response.

    Used for conversational / chat-style queries. Returns an error message
    string (not an exception) if the provider is unavailable or fails,
    so callers always get a displayable response.
    """
    try:
        provider = _get_provider()
        if not provider.is_available():
            return ""  # Caller should handle unavailable mode
        return await provider.call_text(
            prompt, system=system, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("call_llm_text failed: %s", exc)
        return ""


def get_llm_client():
    """Return the low-level provider client (Anthropic-specific).

    .. deprecated::
        Prefer ``call_llm_json()`` which is provider-agnostic.
        This function exists for backward compatibility with code that
        uses the Anthropic SDK directly (e.g. Telegram bot chat handler).
    """
    provider = _get_provider()
    if hasattr(provider, "_get_client"):
        return provider._get_client()
    raise LLMUnavailableError("Direct client access not supported for this provider.")


async def close_llm_client() -> None:
    """Gracefully close all LLM provider connections (shutdown hook)."""
    from src.llm.providers import close_all_providers
    await close_all_providers()
