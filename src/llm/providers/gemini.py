"""Google Gemini LLM provider for Axion.

Wraps the ``google-generativeai`` SDK behind the :class:`LLMProvider`
interface. All Gemini-specific imports, error handling, and response
parsing are isolated here.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any

from src.config import get_settings
from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# Default model for Gemini — overridable via settings
_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(LLMProvider):
    """LLM provider backed by the Google Gemini API."""

    def __init__(self) -> None:
        self._client = None

    # -- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """Check for a valid-looking ``AIza`` API key (no network call)."""
        try:
            settings = get_settings()
            key = settings.google_api_key.get_secret_value()
            return bool(key and key.startswith("AIza") and len(key) > 10)
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
        """Send *prompt* to Gemini and return parsed JSON."""
        settings = get_settings()
        llm = settings.llm
        client = self._get_client(model or _DEFAULT_MODEL)

        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        generation_config = {
            "temperature": _temperature,
            "max_output_tokens": _max_tokens,
        }

        last_error: Exception | None = None

        for attempt in range(_retries):
            try:
                # google-generativeai is sync — run in executor
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: client.generate_content(
                        prompt,
                        generation_config=generation_config,
                    ),
                )

                if not response.text:
                    last_error = ValueError("Gemini returned empty response")
                    logger.warning("Empty Gemini response (attempt %d/%d)", attempt + 1, _retries)
                    if attempt < _retries - 1:
                        wait = backoffs[min(attempt, len(backoffs) - 1)]
                        await asyncio.sleep(wait)
                    continue

                text = response.text.strip()

                # Strip markdown fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

                result = json.loads(text)
                logger.debug("Gemini call succeeded  model=%s", model or _DEFAULT_MODEL)
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"Gemini returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from Gemini (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except Exception as exc:
                error_str = str(exc).lower()
                if "429" in error_str or "resource_exhausted" in error_str:
                    last_error = exc
                    wait = backoffs[min(attempt, len(backoffs) - 1)]
                    logger.warning("Gemini rate limited (attempt %d/%d), waiting %ds",
                                   attempt + 1, _retries, wait)
                    await asyncio.sleep(wait)
                    continue
                if "500" in error_str or "503" in error_str or "internal" in error_str:
                    last_error = exc
                    wait = backoffs[min(attempt, len(backoffs) - 1)]
                    logger.warning("Gemini server error (attempt %d/%d), retrying in %ds",
                                   attempt + 1, _retries, wait)
                    await asyncio.sleep(wait)
                    continue
                # Non-retryable error
                raise

            # Back-off before JSON retry
            if attempt < _retries - 1:
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                await asyncio.sleep(wait)

        if isinstance(last_error, ValueError):
            raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini call failed with no captured error")

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
        """Send *prompt* + image to Gemini and return parsed JSON."""
        settings = get_settings()
        llm = settings.llm
        client = self._get_client(model or _DEFAULT_MODEL)

        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        generation_config = {
            "temperature": _temperature,
            "max_output_tokens": _max_tokens,
        }

        # Build multimodal content: image + text prompt
        import PIL.Image
        image = PIL.Image.open(io.BytesIO(image_bytes))
        content_parts = [image, prompt]

        last_error: Exception | None = None

        for attempt in range(_retries):
            try:
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: client.generate_content(
                        content_parts,
                        generation_config=generation_config,
                    ),
                )

                if not response.text:
                    last_error = ValueError("Vision call returned empty response")
                    logger.warning("Empty Gemini vision response (attempt %d/%d)",
                                   attempt + 1, _retries)
                    if attempt < _retries - 1:
                        await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue

                text = response.text.strip()

                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

                result = json.loads(text)
                logger.debug("Gemini vision call succeeded  model=%s",
                             model or _DEFAULT_MODEL)
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"Gemini vision returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from Gemini vision (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except Exception as exc:
                error_str = str(exc).lower()
                if "429" in error_str or "resource_exhausted" in error_str:
                    last_error = exc
                    await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue
                if "500" in error_str or "503" in error_str or "internal" in error_str:
                    last_error = exc
                    await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue
                raise

            if attempt < _retries - 1:
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])

        if isinstance(last_error, ValueError):
            raise last_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini vision call failed with no captured error")

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
        """Send *prompt* to Gemini and return raw text response."""
        client = self._get_client(model or _DEFAULT_MODEL, system_instruction=system)
        _temperature = temperature if temperature is not None else 0.3
        _max_tokens = max_tokens or 1024

        generation_config = {
            "temperature": _temperature,
            "max_output_tokens": _max_tokens,
        }

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.generate_content(
                    prompt,
                    generation_config=generation_config,
                ),
            )
            if response.text:
                logger.debug("Gemini text call succeeded  model=%s", model or _DEFAULT_MODEL)
                return response.text.strip()
            return ""
        except Exception as exc:
            error_str = str(exc).lower()
            if "429" in error_str or "resource_exhausted" in error_str:
                logger.warning("Gemini rate limited during text call")
                return "[Axion] Rate limited by Google. Please try again in a moment."
            logger.error("Gemini text call failed: %s", exc)
            return f"[Axion] AI response unavailable: {exc}"

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """No persistent connection to close for google-generativeai."""
        self._client = None
        logger.info("Gemini provider released.")

    # -- internal -----------------------------------------------------------

    def _get_client(self, model_name: str, *, system_instruction: str | None = None):
        """Return a GenerativeModel instance.

        google-generativeai uses a module-level configure + per-call model
        rather than a persistent async client. We configure on first use
        and cache the model object.
        """
        import google.generativeai as genai

        settings = get_settings()
        key = settings.google_api_key.get_secret_value()
        genai.configure(api_key=key)

        kwargs = {}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction

        # For text calls with system instruction, always create fresh model
        # For JSON calls (no system instruction), cache is fine
        if system_instruction or self._client is None:
            self._client = genai.GenerativeModel(model_name, **kwargs)
            if not system_instruction:
                logger.info("Gemini model initialised: %s", model_name)

        return self._client
