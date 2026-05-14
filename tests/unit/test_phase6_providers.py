"""Phase 6 — AI provider layer regression tests.

Covers:
- The normalized ``ProviderStatus`` model and its constructors.
- The generic ``classify_provider_exception`` helper.
- The secret scrubbing pass.
- Per-provider ``test_connection()`` paths for missing-key / active / auth /
  rate-limit / network / 5xx outcomes — all driven by mocked SDK objects, no
  real network calls.
- The settings ``POST /api/v1/settings/test-provider`` endpoint contract:
  unknown-provider 400, disabled (no provider selected), missing-key path,
  no key material in the response, alias resolution.

All vendor SDK interactions are mocked — these tests are safe to run in CI
without internet access and without API keys.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import get_settings
from src.llm.provider_status import (
    build_status,
    classify_provider_exception,
    message_for,
    scrub_secrets,
)


# ───────────────────────────────────────────────────────────────────────────
# 6B — Status model + classifier
# ───────────────────────────────────────────────────────────────────────────


class TestProviderStatusModel:
    def test_build_status_default_message(self):
        s = build_status(
            provider="openai", status="missing_key", configured=False
        )
        assert s.status == "missing_key"
        assert s.configured is False
        assert s.available is False
        assert "No API key" in s.message
        assert s.checked_at  # ISO-8601 string

    def test_available_true_only_when_active(self):
        for status in ("active", "missing_key", "invalid_key", "quota_issue",
                       "unreachable", "misconfigured", "error", "disabled"):
            s = build_status(provider="x", status=status, configured=True)
            assert s.available == (status == "active"), f"status={status}"

    def test_message_scrubs_secrets(self):
        # If a future caller passes a custom message containing key-shaped
        # text, build_status must scrub it.
        fake_key = "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        s = build_status(
            provider="anthropic",
            status="error",
            configured=True,
            message=f"call failed for {fake_key} reason X",
        )
        assert fake_key not in s.message
        assert "***" in s.message

    def test_frozen_model(self):
        s = build_status(provider="openai", status="active", configured=True)
        with pytest.raises((ValueError, TypeError)):
            s.status = "error"  # type: ignore[misc]


class TestClassifier:
    def test_rate_limit_class_name(self):
        class RateLimitError(Exception):
            pass
        status, code = classify_provider_exception(RateLimitError("429"))
        assert status == "quota_issue"
        assert code == "rate_limit"

    def test_authentication_class_name(self):
        class AuthenticationError(Exception):
            pass
        status, code = classify_provider_exception(AuthenticationError("bad"))
        assert status == "invalid_key"
        assert code == "auth_error"

    def test_connection_class_name(self):
        class APIConnectionError(Exception):
            pass
        status, code = classify_provider_exception(APIConnectionError("network"))
        assert status == "unreachable"
        assert code == "network"

    def test_status_503_text(self):
        status, code = classify_provider_exception(Exception("503 service unavailable"))
        assert status == "unreachable"

    def test_module_not_found(self):
        status, code = classify_provider_exception(ModuleNotFoundError("openai"))
        assert status == "misconfigured"
        assert code == "sdk_missing"

    def test_apistatus_with_status_code(self):
        class APIStatusError(Exception):
            def __init__(self, status_code):
                super().__init__()
                self.status_code = status_code
        status, code = classify_provider_exception(APIStatusError(429))
        assert status == "quota_issue"
        assert code == "http_429"

        status, code = classify_provider_exception(APIStatusError(503))
        assert status == "unreachable"
        assert code.startswith("http_")

        status, code = classify_provider_exception(APIStatusError(401))
        assert status == "invalid_key"

    def test_unknown_exception(self):
        status, code = classify_provider_exception(Exception("strange new error"))
        assert status == "error"
        assert code == "unknown"


class TestSecretScrubbing:
    def test_anthropic_key_redacted(self):
        text = "trace contained sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert "sk-ant-" not in scrub_secrets(text)

    def test_openai_keys_redacted(self):
        for prefix in ("sk-proj-", "sk-"):
            text = f"saw {prefix}{'X' * 30}"
            scrubbed = scrub_secrets(text)
            assert prefix not in scrubbed
            assert "***" in scrubbed

    def test_google_key_redacted(self):
        text = "AIzaXXXXXXXXXXXXXXXXXXXXXXXX leaked"
        assert "AIza" not in scrub_secrets(text)

    def test_telegram_token_redacted(self):
        text = "token 1234567890:" + "A" * 35 + " ok"
        assert "1234567890:" not in scrub_secrets(text)

    def test_innocent_text_preserved(self):
        text = "the cat sat on the mat"
        assert scrub_secrets(text) == text

    def test_empty_string(self):
        assert scrub_secrets("") == ""


class TestMessages:
    def test_every_status_has_message(self):
        for s in (
            "active", "disabled", "missing_key", "invalid_key",
            "quota_issue", "unreachable", "misconfigured", "error",
        ):
            assert message_for(s)


# ───────────────────────────────────────────────────────────────────────────
# 6C/6D — Anthropic + OpenAI test_connection
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Isolate the settings layer from any real ``~/.axion.env`` on disk.

    The config loader calls ``dotenv.load_dotenv()`` on ``~/.axion.env``,
    ``~/.kleitos.env``, and the project-root ``.env``. The default behaviour
    is ``override=False`` — values populated by those files survive an
    ``os.environ.pop`` (because ``_build_settings`` re-runs ``load_dotenv``
    inside its body). To get a clean slate per test, we:

    1. delenv all known provider keys
    2. patch ``src.config.load_dotenv`` to a no-op so the real ``.axion.env``
       file is never read during the test
    3. clear the cached settings so the next ``get_settings()`` rebuilds
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AXION_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("KLEITOS_LLM_PROVIDER", raising=False)

    import src.config as _cfg
    monkeypatch.setattr(_cfg, "load_dotenv", lambda *a, **k: None)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def with_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKETESTKEY" + "A" * 32)
    get_settings.cache_clear()
    yield


@pytest.fixture
def with_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-FAKETESTKEY" + "A" * 32)
    get_settings.cache_clear()
    yield


@pytest.fixture
def no_provider_keys():
    """Sugar fixture — every test already runs under _isolate_settings,
    which removes provider keys. This name documents intent."""
    yield


class TestAnthropicTestConnection:
    @pytest.mark.asyncio
    async def test_missing_key(self, no_provider_keys):
        from src.llm.providers.anthropic import AnthropicProvider
        result = await AnthropicProvider().test_connection(provider_name="anthropic")
        assert result.status == "missing_key"
        assert result.configured is False
        assert result.available is False
        # The model still gets reported so the UI can show what would have been used.
        assert result.model

    @pytest.mark.asyncio
    async def test_active_mocked(self, with_anthropic_key):
        from src.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
        mock_client = MagicMock()
        # ``messages.create`` is async.
        mock_client.messages.create = AsyncMock(return_value=SimpleNamespace())
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="anthropic")
        assert result.status == "active"
        assert result.configured is True
        assert result.available is True
        assert result.detail_code == "ok"

    @pytest.mark.asyncio
    async def test_auth_error(self, with_anthropic_key):
        from src.llm.providers.anthropic import AnthropicProvider
        import anthropic

        provider = AnthropicProvider()
        mock_client = MagicMock()
        # Build an AuthenticationError without going through its constructor
        # (which requires a real httpx response).
        exc = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        Exception.__init__(exc, "bad key")
        mock_client.messages.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="anthropic")
        assert result.status == "invalid_key"
        assert result.detail_code == "auth_error"
        # The fake key must not appear in the customer-facing message.
        assert "sk-ant-" not in result.message

    @pytest.mark.asyncio
    async def test_rate_limit(self, with_anthropic_key):
        from src.llm.providers.anthropic import AnthropicProvider
        import anthropic

        provider = AnthropicProvider()
        mock_client = MagicMock()
        exc = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        Exception.__init__(exc, "rate limited")
        mock_client.messages.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="anthropic")
        assert result.status == "quota_issue"
        assert result.detail_code == "rate_limit"

    @pytest.mark.asyncio
    async def test_connection_error(self, with_anthropic_key):
        from src.llm.providers.anthropic import AnthropicProvider
        import anthropic

        provider = AnthropicProvider()
        mock_client = MagicMock()
        exc = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
        Exception.__init__(exc, "boom")
        mock_client.messages.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="anthropic")
        assert result.status == "unreachable"
        assert result.detail_code == "network"


class TestOpenAITestConnection:
    @pytest.mark.asyncio
    async def test_missing_key(self, no_provider_keys):
        from src.llm.providers.openai import OpenAIProvider
        result = await OpenAIProvider().test_connection(provider_name="openai")
        assert result.status == "missing_key"
        assert result.configured is False
        assert result.available is False
        # Sane default model surfaced.
        assert "gpt" in (result.model or "").lower()

    @pytest.mark.asyncio
    async def test_active_mocked(self, with_openai_key):
        from src.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=SimpleNamespace())
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="openai")
        assert result.status == "active"
        assert result.configured is True
        assert result.available is True

    @pytest.mark.asyncio
    async def test_auth_error(self, with_openai_key):
        from src.llm.providers.openai import OpenAIProvider
        import openai

        provider = OpenAIProvider()
        mock_client = MagicMock()
        exc = openai.AuthenticationError.__new__(openai.AuthenticationError)
        Exception.__init__(exc, "Incorrect API key")
        mock_client.chat.completions.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="openai")
        assert result.status == "invalid_key"
        assert result.detail_code == "auth_error"
        # OpenAI fake-key prefix must not appear in the customer-facing message.
        assert "sk-" not in result.message

    @pytest.mark.asyncio
    async def test_rate_limit(self, with_openai_key):
        from src.llm.providers.openai import OpenAIProvider
        import openai

        provider = OpenAIProvider()
        mock_client = MagicMock()
        exc = openai.RateLimitError.__new__(openai.RateLimitError)
        Exception.__init__(exc, "rate limited")
        mock_client.chat.completions.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="openai")
        assert result.status == "quota_issue"
        assert result.detail_code == "rate_limit"

    @pytest.mark.asyncio
    async def test_connection_error(self, with_openai_key):
        from src.llm.providers.openai import OpenAIProvider
        import openai

        provider = OpenAIProvider()
        mock_client = MagicMock()
        exc = openai.APIConnectionError.__new__(openai.APIConnectionError)
        Exception.__init__(exc, "net down")
        mock_client.chat.completions.create = AsyncMock(side_effect=exc)
        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.test_connection(provider_name="openai")
        assert result.status == "unreachable"
        assert result.detail_code == "network"


class TestGeminiTestConnection:
    @pytest.mark.asyncio
    async def test_missing_key(self, no_provider_keys):
        from src.llm.providers.gemini import GeminiProvider
        result = await GeminiProvider().test_connection(provider_name="gemini")
        assert result.status == "missing_key"
        assert result.configured is False


# ───────────────────────────────────────────────────────────────────────────
# 6G — Settings endpoint contract
# ───────────────────────────────────────────────────────────────────────────


class TestTestProviderEndpoint:
    """The settings.test_provider endpoint should be schema-stable, key-safe,
    and reject unknown providers with 400."""

    def _make_client(self):
        # Fresh FastAPI app with just the settings router so we don't fight
        # the main app's auth middleware.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes.settings import router as settings_router

        app = FastAPI()
        app.include_router(settings_router)
        return TestClient(app, raise_server_exceptions=False)

    def test_unknown_provider_returns_400(self, no_provider_keys):
        with self._make_client() as client:
            r = client.post("/api/v1/settings/test-provider?provider=fakeprovider")
        assert r.status_code == 400
        assert "fakeprovider" in r.text.lower() or "unknown" in r.text.lower()

    def test_missing_key_returns_typed_status(self, no_provider_keys):
        with self._make_client() as client:
            r = client.post("/api/v1/settings/test-provider?provider=openai")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "openai"
        assert body["status"] == "missing_key"
        assert body["configured"] is False
        assert body["available"] is False
        assert body["checked_at"]

    def test_alias_chatgpt_maps_to_openai(self, no_provider_keys):
        with self._make_client() as client:
            r = client.post("/api/v1/settings/test-provider?provider=chatgpt")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "openai"

    def test_disabled_when_no_provider_set(self, monkeypatch):
        # Force settings.llm.provider to "none" — settings UI sets this when
        # the user disables AI.
        monkeypatch.setenv("AXION_LLM_PROVIDER", "none")
        get_settings.cache_clear()
        with self._make_client() as client:
            # No ?provider= → use the configured primary.
            r = client.post("/api/v1/settings/test-provider")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "disabled"
        assert body["available"] is False

    def test_response_never_contains_api_key(self, monkeypatch):
        fake = "sk-ant-FAKETESTKEY" + ("Z" * 32)
        monkeypatch.setenv("ANTHROPIC_API_KEY", fake)
        get_settings.cache_clear()
        with self._make_client() as client:
            r = client.post("/api/v1/settings/test-provider?provider=anthropic")
        # With a fake key the response will be missing_key-or-auth-or-error
        # depending on env; the only thing we care about here is that the
        # raw key string never appears in the body.
        assert fake not in r.text

    def test_response_schema_has_required_fields(self, no_provider_keys):
        with self._make_client() as client:
            r = client.post("/api/v1/settings/test-provider?provider=anthropic")
        body = r.json()
        for key in (
            "provider", "status", "configured", "available",
            "message", "checked_at",
        ):
            assert key in body, f"missing field {key!r} in {body}"


# ───────────────────────────────────────────────────────────────────────────
# Support bundle redaction sanity (cross-phase)
# ───────────────────────────────────────────────────────────────────────────


class TestSupportBundleStillRedacts:
    """Phase 4 ships scripts/support_bundle.py with redaction. Phase 6
    explicitly relies on that redaction continuing to work for new env-var
    names like OPENAI_API_KEY. Quick sanity that nothing regressed.
    """

    def test_redactor_keeps_handling_openai_key(self, tmp_path, monkeypatch):
        # We don't run the full bundle here — just import its redact helper.
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "support_bundle",
            Path(__file__).resolve().parent.parent.parent
            / "scripts" / "support_bundle.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        env = {
            "OPENAI_API_KEY": "sk-proj-FAKE" + ("X" * 40),
            "ANTHROPIC_API_KEY": "sk-ant-FAKE" + ("Y" * 40),
            "GOOGLE_API_KEY": "AIza" + ("Z" * 36),
            "SAFE_VAR": "plain-text-value",
        }
        red = mod._redact_env(env)
        assert "redacted" in red["OPENAI_API_KEY"]
        assert "redacted" in red["ANTHROPIC_API_KEY"]
        assert "redacted" in red["GOOGLE_API_KEY"]
        assert red["SAFE_VAR"] == "plain-text-value"


# ───────────────────────────────────────────────────────────────────────────
# Dashboard contract — Phase 6H label
# ───────────────────────────────────────────────────────────────────────────


class TestDashboardLabels:
    def _index_html(self) -> str:
        from pathlib import Path
        return (
            Path(__file__).resolve().parent.parent.parent
            / "dashboard" / "index.html"
        ).read_text(encoding="utf-8")

    def test_openai_option_says_chatgpt(self):
        html = self._index_html()
        assert '<option value="openai">OpenAI / ChatGPT</option>' in html
        # The old "OpenAI (GPT)" label must be gone.
        assert "OpenAI (GPT)" not in html

    def test_test_button_exists(self):
        html = self._index_html()
        assert "testProvider('primary')" in html
        assert "testProvider('backup')" in html

    def test_env_var_hint_lists_all_three(self):
        html = self._index_html()
        assert "ANTHROPIC_API_KEY" in html
        assert "OPENAI_API_KEY" in html
        assert "GOOGLE_API_KEY" in html


# ───────────────────────────────────────────────────────────────────────────
# .env.template contract
# ───────────────────────────────────────────────────────────────────────────


class TestEnvTemplate:
    def _read(self) -> str:
        from pathlib import Path
        return (
            Path(__file__).resolve().parent.parent.parent / ".env.template"
        ).read_text(encoding="utf-8")

    def test_documents_all_three_provider_env_vars(self):
        txt = self._read()
        assert "ANTHROPIC_API_KEY" in txt
        assert "OPENAI_API_KEY" in txt
        assert "GOOGLE_API_KEY" in txt

    def test_documents_optional_model_env_vars(self):
        txt = self._read()
        assert "AXION_LLM_PROVIDER" in txt
        assert "AXION_LLM_MODEL" in txt

    def test_marks_keys_as_optional(self):
        txt = self._read()
        assert "optional" in txt.lower()


# ───────────────────────────────────────────────────────────────────────────
# OAuth roadmap doc — must be present and explicit
# ───────────────────────────────────────────────────────────────────────────


class TestOauthRoadmap:
    def _read(self) -> str:
        from pathlib import Path
        return (
            Path(__file__).resolve().parent.parent.parent
            / "docs" / "OAUTH_ROADMAP.md"
        ).read_text(encoding="utf-8")

    def test_doc_exists_and_says_not_implemented(self):
        txt = self._read()
        assert "not part of the current customer build" in txt.lower()

    def test_doc_lists_security_principles(self):
        txt = self._read().lower()
        for needle in ("pkce", "revocation", "scope"):
            assert needle in txt, f"missing OAuth principle: {needle!r}"

    def test_doc_does_not_promise_specific_integration(self):
        txt = self._read().lower()
        # The doc should say "candidate" or "speculative" or "future" — not
        # "we have OAuth working".
        assert "candidate" in txt or "speculative" in txt or "future" in txt

    def test_known_limitations_mentions_oauth_not_implemented(self):
        from pathlib import Path
        kl = (Path(__file__).resolve().parent.parent.parent
              / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
        assert "OAuth" in kl
        assert "not implemented" in kl.lower() or "not yet" in kl.lower()
