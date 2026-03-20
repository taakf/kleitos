"""Anthropic (Claude) LLM provider for Axion.

Wraps the ``anthropic`` SDK behind the :class:`LLMProvider` interface.
All Anthropic-specific imports, error handling, and response parsing
are isolated here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src.config import get_settings
from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """LLM provider backed by the Anthropic (Claude) API."""

    def __init__(self) -> None:
        self._client = None

    # -- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """Check for a valid-looking ``sk-ant-`` API key (no network call)."""
        try:
            settings = get_settings()
            key = settings.anthropic_api_key.get_secret_value()
            return bool(key and key.startswith("sk-ant-"))
        except Exception:
            return False

    # -- core call ----------------------------------------------------------

    async def call_json(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Send *prompt* to Claude and return parsed JSON."""
        import anthropic  # Lazy import — only needed when actually calling

        settings = get_settings()
        llm = settings.llm
        client = self._get_client()

        _model = model or llm.model
        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        last_error: Exception | None = None

        for attempt in range(_retries):
            try:
                response = await client.messages.create(
                    model=_model,
                    max_tokens=_max_tokens,
                    temperature=_temperature,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Guard against empty or non-text responses
                if not response.content or not hasattr(response.content[0], "text"):
                    last_error = ValueError("LLM returned empty or non-text response")
                    logger.warning("Empty LLM response (attempt %d/%d)", attempt + 1, _retries)
                    if attempt < _retries - 1:
                        wait = backoffs[min(attempt, len(backoffs) - 1)]
                        await asyncio.sleep(wait)
                    continue

                text = response.content[0].text.strip()

                # Strip markdown fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

                result = json.loads(text)
                logger.debug(
                    "LLM call succeeded  model=%s  tokens_in=%d  tokens_out=%d",
                    _model,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"LLM returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from LLM (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except anthropic.RateLimitError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                logger.warning("Rate limited (attempt %d/%d), waiting %ds",
                               attempt + 1, _retries, wait)
                await asyncio.sleep(wait)
                continue

            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    wait = backoffs[min(attempt, len(backoffs) - 1)]
                    logger.warning("API server error %d (attempt %d/%d), retrying in %ds",
                                   exc.status_code, attempt + 1, _retries, wait)
                    await asyncio.sleep(wait)
                    continue
                raise  # 4xx (except 429) are not retryable

            except anthropic.APIConnectionError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                logger.warning("Connection error (attempt %d/%d), retrying in %ds",
                               attempt + 1, _retries, wait)
                await asyncio.sleep(wait)
                continue

            # Back-off before JSON retry
            if attempt < _retries - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                await asyncio.sleep(wait)

        if isinstance(last_error, ValueError):
            raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM call failed with no captured error")

    # -- vision call --------------------------------------------------------

    async def call_vision_json(
        self,
        prompt: str,
        image_bytes: bytes,
        media_type: str = "image/png",
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Send *prompt* + image to Claude and return parsed JSON."""
        import anthropic
        import base64

        settings = get_settings()
        llm = settings.llm
        client = self._get_client()

        _model = model or llm.model
        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        b64_data = base64.standard_b64encode(image_bytes).decode("ascii")

        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
                {
                    "type": "text",
                    "text": prompt,
                },
            ],
        }]

        last_error: Exception | None = None

        for attempt in range(_retries):
            try:
                response = await client.messages.create(
                    model=_model,
                    max_tokens=_max_tokens,
                    temperature=_temperature,
                    messages=messages,
                )

                if not response.content or not hasattr(response.content[0], "text"):
                    last_error = ValueError("Vision call returned empty response")
                    logger.warning("Empty vision response (attempt %d/%d)", attempt + 1, _retries)
                    if attempt < _retries - 1:
                        await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue

                text = response.content[0].text.strip()

                # Strip markdown fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

                result = json.loads(text)
                logger.debug(
                    "Vision call succeeded  model=%s  tokens_in=%d  tokens_out=%d",
                    _model, response.usage.input_tokens, response.usage.output_tokens,
                )
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"Vision call returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from vision (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except anthropic.RateLimitError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                logger.warning("Rate limited on vision (attempt %d/%d), waiting %ds",
                               attempt + 1, _retries, wait)
                await asyncio.sleep(wait)
                continue

            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    wait = backoffs[min(attempt, len(backoffs) - 1)]
                    await asyncio.sleep(wait)
                    continue
                raise

            except anthropic.APIConnectionError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                await asyncio.sleep(wait)
                continue

            if attempt < _retries - 1:
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])

        if isinstance(last_error, ValueError):
            raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Vision call failed with no captured error")

    # -- text call (conversational) -----------------------------------------

    async def call_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send *prompt* to Claude and return raw text response."""
        import anthropic as _anthropic

        settings = get_settings()
        llm = settings.llm
        client = self._get_client()

        _model = model or llm.model
        _temperature = temperature if temperature is not None else 0.3  # Slightly warmer for chat
        _max_tokens = max_tokens or 1024

        kwargs: dict = {
            "model": _model,
            "max_tokens": _max_tokens,
            "temperature": _temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            response = await client.messages.create(**kwargs)
            if response.content and hasattr(response.content[0], "text"):
                logger.debug("LLM text call succeeded  model=%s  tokens_in=%d  tokens_out=%d",
                             _model, response.usage.input_tokens, response.usage.output_tokens)
                return response.content[0].text.strip()
            return ""
        except _anthropic.RateLimitError:
            logger.warning("Rate limited during text call")
            return "[Axion] Rate limited by the AI provider. Please try again in a moment."
        except _anthropic.APIConnectionError:
            logger.warning("Connection error during text call")
            return "[Axion] Could not reach the AI provider. Check your connection."
        except Exception as exc:
            logger.error("LLM text call failed: %s", exc)
            return f"[Axion] AI response unavailable: {exc}"

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        client = self._client
        self._client = None
        if client is not None:
            await client.close()
            logger.info("Anthropic client closed.")

    # -- internal -----------------------------------------------------------

    def _get_client(self):
        """Lazy-init the Anthropic async client."""
        if self._client is None:
            import anthropic
            settings = get_settings()
            self._client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                timeout=settings.llm.timeout_seconds,
            )
            logger.info("Anthropic async client initialised (model=%s)", settings.llm.model)
        return self._client
