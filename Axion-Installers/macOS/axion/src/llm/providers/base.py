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
