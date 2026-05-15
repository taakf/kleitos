"""Abstract base class for LLM providers.

All providers (Anthropic, OpenAI, Gemini, etc.) implement this interface.
The rest of Axion interacts with LLM providers only through this contract,
ensuring that swapping or adding providers requires no changes to agents,
analysis, or other core code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Provider-agnostic LLM interface."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this provider is configured and likely callable.

        Should be a fast local check (e.g. API key format), not a network call.
        """

    @abstractmethod
    async def call_json(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Send *prompt* and return the model response parsed as JSON.

        Implementations must handle retries, rate-limit back-off, response
        parsing, and markdown-fence stripping internally.

        Raises
        ------
        ValueError
            If the response is not valid JSON after all retries.
        RuntimeError
            On unrecoverable provider errors.
        """

    @abstractmethod
    async def call_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send *prompt* and return the raw text response (no JSON parsing).

        Used for conversational / chat-style queries where the response
        is natural language rather than structured JSON.

        Parameters
        ----------
        system:
            Optional system prompt to set context/persona.
        """

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
        """Send *prompt* with an image to the model and return parsed JSON.

        Used for extracting structured data from images (portfolio screenshots,
        scanned PDFs, etc.). Not all providers may support this — unsupported
        providers raise ``NotImplementedError``.

        Parameters
        ----------
        prompt:
            Instruction text sent alongside the image.
        image_bytes:
            Raw bytes of the image file.
        media_type:
            MIME type of the image (e.g. ``"image/png"``, ``"image/jpeg"``).
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support vision calls."
        )

    @abstractmethod
    async def close(self) -> None:
        """Release any HTTP connections held by the provider."""

    # ── Phase 6 — provider status testing ────────────────────────────────
    # Concrete method (not abstract) so existing providers keep working
    # without being forced to override. The default implementation:
    #
    #   1. Reports ``missing_key`` if ``is_available()`` is False.
    #   2. Otherwise calls ``call_text("ping", max_tokens=5)`` once and
    #      maps the result / any exception to a ``ProviderStatus``.
    #
    # Providers may override to use SDK-typed exceptions (e.g.
    # ``openai.AuthenticationError``) for higher-precision classification.
    async def test_connection(
        self,
        *,
        provider_name: str,
        model: str | None = None,
    ):
        """Probe the provider with a minimal call and return a typed status.

        Imports the status module lazily so this base class stays free of
        Pydantic dependencies at definition time.
        """
        from src.llm.provider_status import (
            build_status,
            status_from_exception,
        )

        if not self.is_available():
            return build_status(
                provider=provider_name,
                status="missing_key",
                configured=False,
                model=model,
            )

        try:
            result = await self.call_text("ping", max_tokens=5)
        except ModuleNotFoundError as exc:
            return status_from_exception(
                provider=provider_name,
                exc=exc,
                configured=True,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — classifier handles every kind
            return status_from_exception(
                provider=provider_name,
                exc=exc,
                configured=True,
                model=model,
            )

        # Some provider ``call_text`` paths swallow errors and return a
        # sentinel "[Axion] ..." string instead of raising. Treat that as
        # a non-active result so the UI doesn't say "OK" when it isn't.
        if isinstance(result, str) and result.startswith("[Axion]"):
            # Map the sentinel to a best-guess status. The two known
            # sentinels are "Rate limited" and "Could not reach".
            low = result.lower()
            if "rate" in low:
                return build_status(
                    provider=provider_name,
                    status="quota_issue",
                    configured=True,
                    model=model,
                    detail_code="rate_limit",
                )
            if "could not reach" in low or "connection" in low:
                return build_status(
                    provider=provider_name,
                    status="unreachable",
                    configured=True,
                    model=model,
                    detail_code="network",
                )
            return build_status(
                provider=provider_name,
                status="error",
                configured=True,
                model=model,
                detail_code="sentinel",
            )

        return build_status(
            provider=provider_name,
            status="active",
            configured=True,
            model=model,
            detail_code="ok",
        )
