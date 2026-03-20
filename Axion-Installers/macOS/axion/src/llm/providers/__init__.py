"""LLM provider registry and factory.

Usage::

    from src.llm.providers import get_provider

    provider = get_provider()           # uses settings.llm.provider
    provider = get_provider("anthropic") # explicit
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Singleton cache: provider_name -> instance
_providers: dict[str, "LLMProvider"] = {}


def get_provider(name: str | None = None) -> "LLMProvider":
    """Return a cached provider instance for *name*.

    If *name* is ``None``, reads ``settings.llm.provider`` (default: ``"anthropic"``).
    """
    if name is None:
        from src.config import get_settings
        name = get_settings().llm.provider

    name = name.lower().strip()

    if name == "none":
        raise ValueError("LLM provider is set to 'none' (AI disabled). No provider to create.")

    if name in _providers:
        return _providers[name]

    if name == "anthropic":
        from src.llm.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider()
    elif name == "openai":
        from src.llm.providers.openai import OpenAIProvider
        provider = OpenAIProvider()
    elif name in ("gemini", "google"):
        from src.llm.providers.gemini import GeminiProvider
        provider = GeminiProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: '{name}'. "
            f"Supported providers: anthropic, openai, gemini. "
            f"Check settings.yaml > llm.provider."
        )

    _providers[name] = provider
    logger.info("LLM provider initialised: %s", name)
    return provider


async def close_all_providers() -> None:
    """Close every cached provider (called at app shutdown)."""
    for name, provider in list(_providers.items()):
        try:
            await provider.close()
        except Exception as exc:
            logger.warning("Error closing provider %s: %s", name, exc)
    _providers.clear()
