"""Load prompt templates from config/prompts.yaml with hardcoded fallbacks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_PROMPT_CACHE: dict[str, str] | None = None
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "prompts.yaml"


def _load_prompts() -> dict[str, str]:
    """Load prompt templates from YAML config. Cached after first call."""
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE

    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH) as f:
                _PROMPT_CACHE = yaml.safe_load(f) or {}
            logger.info("Loaded %d prompt templates from %s", len(_PROMPT_CACHE), _CONFIG_PATH)
        else:
            logger.debug("No prompts.yaml found at %s — using hardcoded defaults", _CONFIG_PATH)
            _PROMPT_CACHE = {}
    except Exception as exc:
        logger.warning("Failed to load prompts.yaml: %s — using hardcoded defaults", exc)
        _PROMPT_CACHE = {}

    return _PROMPT_CACHE


def get_prompt(key: str, fallback: Optional[str] = None) -> str:
    """Get a prompt template by key, falling back to hardcoded default.

    Parameters
    ----------
    key:
        Prompt name (e.g. "classification", "analysis", "digest").
    fallback:
        Hardcoded default if not found in config.

    Returns
    -------
    The prompt template string.
    """
    prompts = _load_prompts()
    template = prompts.get(key)
    if template:
        return template.strip()
    if fallback:
        return fallback.strip()
    raise KeyError(f"No prompt template found for '{key}' and no fallback provided")
