"""OpenAI (GPT) LLM provider for Axion.

Wraps the ``openai`` SDK behind the :class:`LLMProvider` interface.
All OpenAI-specific imports, error handling, and response parsing
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

# Default model for OpenAI — overridable via settings
_DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    """LLM provider backed by the OpenAI (GPT) API."""

    def __init__(self) -> None:
        self._client = None

    # -- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """Check for a valid-looking ``sk-`` API key (no network call)."""
        try:
            settings = get_settings()
            key = settings.openai_api_key.get_secret_value()
            return bool(key and key.startswith("sk-") and len(key) > 10)
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
        """Send *prompt* to GPT and return parsed JSON."""
        import openai  # Lazy import

        settings = get_settings()
        llm = settings.llm
        client = self._get_client()

        _model = model or _DEFAULT_MODEL
        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        last_error: Exception | None = None

        for attempt in range(_retries):
            try:
                response = await client.chat.completions.create(
                    model=_model,
                    temperature=_temperature,
                    max_tokens=_max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )

                choice = response.choices[0] if response.choices else None
                if not choice or not choice.message or not choice.message.content:
                    last_error = ValueError("LLM returned empty response")
                    logger.warning("Empty OpenAI response (attempt %d/%d)", attempt + 1, _retries)
                    if attempt < _retries - 1:
                        wait = backoffs[min(attempt, len(backoffs) - 1)]
                        await asyncio.sleep(wait)
                    continue

                text = choice.message.content.strip()

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
                    "OpenAI call succeeded  model=%s  tokens=%d",
                    _model,
                    response.usage.total_tokens if response.usage else 0,
                )
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"LLM returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from OpenAI (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except openai.RateLimitError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                logger.warning("OpenAI rate limited (attempt %d/%d), waiting %ds",
                               attempt + 1, _retries, wait)
                await asyncio.sleep(wait)
                continue

            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    wait = backoffs[min(attempt, len(backoffs) - 1)]
                    logger.warning("OpenAI server error %d (attempt %d/%d), retrying in %ds",
                                   exc.status_code, attempt + 1, _retries, wait)
                    await asyncio.sleep(wait)
                    continue
                raise  # 4xx (except 429) are not retryable

            except openai.APIConnectionError as exc:
                last_error = exc
                wait = backoffs[min(attempt, len(backoffs) - 1)]
                logger.warning("OpenAI connection error (attempt %d/%d), retrying in %ds",
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
        raise RuntimeError("OpenAI call failed with no captured error")

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
        """Send *prompt* + image to GPT-4o and return parsed JSON."""
        import openai
        import base64

        settings = get_settings()
        llm = settings.llm
        client = self._get_client()

        _model = model or _DEFAULT_MODEL
        _temperature = temperature if temperature is not None else llm.temperature
        _max_tokens = max_tokens or llm.max_tokens
        _retries = max_retries if max_retries is not None else llm.max_retries
        backoffs = llm.retry_backoff_seconds

        b64_data = base64.standard_b64encode(image_bytes).decode("ascii")
        data_url = f"data:{media_type};base64,{b64_data}"

        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
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
                response = await client.chat.completions.create(
                    model=_model,
                    temperature=_temperature,
                    max_tokens=_max_tokens,
                    messages=messages,
                )

                choice = response.choices[0] if response.choices else None
                if not choice or not choice.message or not choice.message.content:
                    last_error = ValueError("Vision call returned empty response")
                    logger.warning("Empty vision response (attempt %d/%d)", attempt + 1, _retries)
                    if attempt < _retries - 1:
                        await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue

                text = choice.message.content.strip()

                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()

                result = json.loads(text)
                logger.debug("Vision call succeeded  model=%s  tokens=%d",
                             _model, response.usage.total_tokens if response.usage else 0)
                return result

            except json.JSONDecodeError as exc:
                last_error = ValueError(f"Vision call returned invalid JSON: {exc}")
                logger.warning("Invalid JSON from vision (attempt %d/%d): %s",
                               attempt + 1, _retries, exc)

            except openai.RateLimitError as exc:
                last_error = exc
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                continue

            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue
                raise

            except openai.APIConnectionError as exc:
                last_error = exc
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
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
        """Send *prompt* to GPT and return raw text response."""
        import openai as _openai

        client = self._get_client()
        _model = model or _DEFAULT_MODEL
        _temperature = temperature if temperature is not None else 0.3
        _max_tokens = max_tokens or 1024

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await client.chat.completions.create(
                model=_model,
                temperature=_temperature,
                max_tokens=_max_tokens,
                messages=messages,
            )
            choice = response.choices[0] if response.choices else None
            if choice and choice.message and choice.message.content:
                logger.debug("OpenAI text call succeeded  model=%s  tokens=%d",
                             _model, response.usage.total_tokens if response.usage else 0)
                return choice.message.content.strip()
            return ""
        except _openai.RateLimitError:
            logger.warning("OpenAI rate limited during text call")
            return "[Axion] Rate limited by OpenAI. Please try again in a moment."
        except _openai.APIConnectionError:
            logger.warning("OpenAI connection error during text call")
            return "[Axion] Could not reach OpenAI. Check your connection."
        except Exception as exc:
            logger.error("OpenAI text call failed: %s", exc)
            return f"[Axion] AI response unavailable: {exc}"

    # -- provider status testing (Phase 6) ---------------------------------

    async def test_connection(
        self,
        *,
        provider_name: str = "openai",
        model: str | None = None,
    ):
        """Probe the OpenAI API with a minimal call and return a typed status.

        Uses ``openai.AuthenticationError`` / ``RateLimitError`` /
        ``APIConnectionError`` / ``APIStatusError`` for precise mapping
        before falling back to the generic classifier.
        """
        from src.llm.provider_status import build_status, status_from_exception

        if not self.is_available():
            return build_status(
                provider=provider_name,
                status="missing_key",
                configured=False,
                model=model or _DEFAULT_MODEL,
            )

        try:
            import openai
        except ModuleNotFoundError as exc:
            return status_from_exception(
                provider=provider_name,
                exc=exc,
                configured=True,
                model=model or _DEFAULT_MODEL,
            )

        try:
            client = self._get_client()
            await client.chat.completions.create(
                model=model or _DEFAULT_MODEL,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
        except openai.AuthenticationError:
            return build_status(
                provider=provider_name,
                status="invalid_key",
                configured=True,
                model=model or _DEFAULT_MODEL,
                detail_code="auth_error",
            )
        except openai.PermissionDeniedError:
            return build_status(
                provider=provider_name,
                status="invalid_key",
                configured=True,
                model=model or _DEFAULT_MODEL,
                detail_code="permission_denied",
            )
        except openai.RateLimitError:
            return build_status(
                provider=provider_name,
                status="quota_issue",
                configured=True,
                model=model or _DEFAULT_MODEL,
                detail_code="rate_limit",
            )
        except openai.APIConnectionError:
            return build_status(
                provider=provider_name,
                status="unreachable",
                configured=True,
                model=model or _DEFAULT_MODEL,
                detail_code="network",
            )
        except openai.APIStatusError as exc:
            code = getattr(exc, "status_code", None)
            if isinstance(code, int) and code >= 500:
                return build_status(
                    provider=provider_name,
                    status="unreachable",
                    configured=True,
                    model=model or _DEFAULT_MODEL,
                    detail_code=f"http_{code}",
                )
            if code == 429:
                return build_status(
                    provider=provider_name,
                    status="quota_issue",
                    configured=True,
                    model=model or _DEFAULT_MODEL,
                    detail_code="http_429",
                )
            return status_from_exception(
                provider=provider_name,
                exc=exc,
                configured=True,
                model=model or _DEFAULT_MODEL,
            )
        except Exception as exc:  # noqa: BLE001
            return status_from_exception(
                provider=provider_name,
                exc=exc,
                configured=True,
                model=model or _DEFAULT_MODEL,
            )

        return build_status(
            provider=provider_name,
            status="active",
            configured=True,
            model=model or _DEFAULT_MODEL,
            detail_code="ok",
        )

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        client = self._client
        self._client = None
        if client is not None:
            await client.close()
            logger.info("OpenAI client closed.")

    # -- internal -----------------------------------------------------------

    def _get_client(self):
        """Lazy-init the OpenAI async client."""
        if self._client is None:
            import openai
            settings = get_settings()
            self._client = openai.AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=settings.llm.timeout_seconds,
            )
            logger.info("OpenAI async client initialised (model=%s)", _DEFAULT_MODEL)
        return self._client
