"""Shared LLM client facade for Axion agents.

This module is the ONLY LLM interface that the rest of Axion should import.
It delegates to the configured provider (Anthropic, OpenAI, Gemini, etc.)
via the provider abstraction in ``src.llm.providers``.

When Backup AI mode is active (settings.llm.backup_provider is set),
the system tries the primary provider first and falls back to the backup
on provider-level failures (5xx, rate-limit, timeout, auth, unavailable).

Public API (unchanged from pre-abstraction era):

- ``is_llm_available()`` — fast local check
- ``call_llm_json(prompt, ...)`` — send prompt, get parsed JSON back
- ``call_llm_text(prompt, ...)`` — send prompt, get raw text back
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
# Internal: provider helpers
# ---------------------------------------------------------------------------

def _get_provider(name: str | None = None):
    """Return a provider instance — primary if name is None."""
    from src.llm.providers import get_provider
    return get_provider(name)


def _get_backup_provider_name() -> str | None:
    """Return the backup provider name from settings, or None if not configured."""
    try:
        from src.config import get_settings
        settings = get_settings()
        backup = getattr(settings.llm, "backup_provider", "")
        if backup and backup.strip():
            return backup.strip().lower()
    except Exception:
        pass
    return None


def _is_fallback_worthy(exc: Exception) -> bool:
    """Return True if the exception warrants falling back to the backup provider.

    Fallback triggers:
    - Rate-limit errors (429 / resource_exhausted)
    - Server errors (5xx)
    - Connection / timeout errors
    - Authentication errors (invalid or revoked key)
    - Generic provider failures after retries exhausted
    """
    err = str(exc).lower()

    # Rate limit
    if "429" in err or "rate" in err or "resource_exhausted" in err:
        return True
    # Server errors
    if any(code in err for code in ("500", "502", "503", "504")):
        return True
    if "internal" in err or "server error" in err:
        return True
    # Connection / timeout
    if "connection" in err or "timeout" in err or "timed out" in err:
        return True
    # Auth errors
    if "401" in err or "403" in err or "authentication" in err or "permission" in err:
        return True
    if "invalid" in err and "key" in err:
        return True
    # Generic unavailable
    if isinstance(exc, LLMUnavailableError):
        return True

    return True  # Conservative: any provider-level failure triggers fallback


# ---------------------------------------------------------------------------
# Public API — these function signatures MUST NOT change.
# All 14+ import sites in agents, routes, and integrations depend on them.
# ---------------------------------------------------------------------------

def is_llm_available() -> bool:
    """Return ``True`` if the configured LLM provider is available.

    Fast local check (no network call).  Returns True if either the
    primary or backup provider is available.
    Returns False immediately if the primary is "none" (AI explicitly disabled).
    """
    # Check if AI is explicitly disabled
    try:
        from src.config import get_settings
        if get_settings().llm.provider.lower().strip() == "none":
            return False
    except Exception:
        pass

    try:
        if _get_provider().is_available():
            return True
    except Exception:
        pass

    # Check backup
    backup_name = _get_backup_provider_name()
    if backup_name:
        try:
            return _get_provider(backup_name).is_available()
        except Exception:
            pass

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

    If Backup AI mode is active and the primary provider fails with a
    provider-level error (after its own retries), the backup provider
    is tried automatically.

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
    primary = _get_provider()
    backup_name = _get_backup_provider_name()

    # --- Try primary provider ---
    if primary.is_available():
        try:
            result = await primary.call_json(
                prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
            return result
        except Exception as exc:
            if backup_name and _is_fallback_worthy(exc):
                logger.warning(
                    "Primary LLM provider failed (%s), falling back to backup '%s'",
                    type(exc).__name__, backup_name,
                )
            else:
                raise
    elif not backup_name:
        raise LLMUnavailableError(
            "No LLM provider available. "
            "Set an API key in Settings or ~/.axion.env."
        )
    else:
        logger.warning(
            "Primary LLM provider unavailable, falling back to backup '%s'",
            backup_name,
        )

    # --- Try backup provider ---
    try:
        backup = _get_provider(backup_name)
    except ValueError:
        raise LLMUnavailableError(
            f"Backup provider '{backup_name}' is not a valid provider. "
            f"Supported: anthropic, openai, gemini."
        )

    if not backup.is_available():
        raise LLMUnavailableError(
            f"Both primary and backup ('{backup_name}') providers are unavailable. "
            "Check your API keys in Settings."
        )

    logger.info("Using backup LLM provider: %s", backup_name)
    return await backup.call_json(
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

    If Backup AI mode is active and the primary fails, the backup is
    tried automatically.
    """
    primary = _get_provider()
    backup_name = _get_backup_provider_name()

    # --- Try primary provider ---
    if primary.is_available():
        try:
            result = await primary.call_text(
                prompt, system=system, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )
            # If the provider returned a non-error response, use it
            if result and not result.startswith("[Axion]"):
                return result
            # Provider returned an error-style string — try backup if available
            if backup_name and result.startswith("[Axion]"):
                logger.warning(
                    "Primary LLM text call returned error, trying backup '%s'",
                    backup_name,
                )
            else:
                return result
        except Exception as exc:
            if backup_name and _is_fallback_worthy(exc):
                logger.warning(
                    "Primary LLM text call failed (%s), falling back to backup '%s'",
                    type(exc).__name__, backup_name,
                )
            else:
                logger.warning("call_llm_text failed: %s", exc)
                return ""
    elif backup_name:
        logger.warning(
            "Primary LLM provider unavailable for text call, trying backup '%s'",
            backup_name,
        )
    else:
        return ""  # No provider available at all

    # --- Try backup provider ---
    try:
        backup = _get_provider(backup_name)
        if not backup.is_available():
            return ""
        return await backup.call_text(
            prompt, system=system, model=model,
            temperature=temperature, max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Backup LLM text call also failed: %s", exc)
        return ""


async def call_llm_vision_json(
    prompt: str,
    image_bytes: bytes,
    media_type: str = "image/png",
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Send *prompt* + image to the configured LLM and return parsed JSON.

    Used for extracting structured data from images (portfolio screenshots,
    scanned PDFs, etc.). Falls back to backup provider if the primary fails.

    Raises
    ------
    LLMUnavailableError
        If no provider is available.
    NotImplementedError
        If the provider does not support vision.
    ValueError
        If the response is not valid JSON after all attempts.
    """
    primary = _get_provider()
    backup_name = _get_backup_provider_name()

    # --- Try primary provider ---
    if primary.is_available():
        try:
            return await primary.call_vision_json(
                prompt, image_bytes, media_type,
                model=model, temperature=temperature,
                max_tokens=max_tokens, max_retries=max_retries,
            )
        except NotImplementedError:
            logger.warning(
                "Primary provider does not support vision, trying backup"
            )
            if not backup_name:
                raise
        except Exception as exc:
            if backup_name and _is_fallback_worthy(exc):
                logger.warning(
                    "Primary vision call failed (%s), falling back to backup '%s'",
                    type(exc).__name__, backup_name,
                )
            else:
                raise
    elif not backup_name:
        raise LLMUnavailableError(
            "No LLM provider available. "
            "Set an API key in Settings or ~/.axion.env."
        )

    # --- Try backup provider ---
    if backup_name:
        try:
            backup = _get_provider(backup_name)
        except ValueError:
            raise LLMUnavailableError(
                f"Backup provider '{backup_name}' is not a valid provider."
            )
        if not backup.is_available():
            raise LLMUnavailableError(
                f"Both primary and backup ('{backup_name}') providers unavailable."
            )
        return await backup.call_vision_json(
            prompt, image_bytes, media_type,
            model=model, temperature=temperature,
            max_tokens=max_tokens, max_retries=max_retries,
        )

    raise LLMUnavailableError("No provider available for vision extraction.")


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
