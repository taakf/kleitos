"""Phase 22 — security & privacy regression tests.

Phase 22 makes Axion's existing local-app security/privacy guarantees
*demonstrable*. The audit found one real gap — the documented
"~/.axion.env with 600 permissions" guarantee was not enforced by the
code — which Phase 22 fixes with a one-line ``os.chmod``. These tests:

* lock the ``.env`` 0600 permission fix in place;
* unit-test the four secret scrubbers the app relies on
  (support-bundle redaction, the provider-status scrubber, the source
  error scrubber, and the Phase-15 insight-export scrubber) so a
  future regression in any of them is caught;
* assert the diagnostics response model carries no secret-shaped
  field;
* assert uploaded-PDF extraction stays in memory.

Assertions that already exist elsewhere are not duplicated:
``test_phase4_support_diagnostics.py`` already covers the support
bundle excluding ``.db`` / ``.env`` and redacting secret env vars, and
the diagnostics endpoint never returning secrets;
``test_phase17_release_artifacts.py`` already covers release-zip
forbidden-file exclusion. This file focuses on the scrubber functions
themselves and the new ``.env`` permission guarantee.

All tests are deterministic and offline — no network, no real
provider calls, no browser.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_IS_WINDOWS = sys.platform.startswith("win")


def _load_script_module(name: str):
    """Load a ``scripts/*.py`` file as a module (scripts/ is not a pkg)."""
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "scripts" / f"{name}.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────────────
# The ~/.axion.env 0600 permission guarantee (Phase 22 fix)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX file modes not meaningful on Windows")
class TestEnvFilePermissions:
    def _write(self, tmp_path, monkeypatch, var: str, value: str) -> Path:
        import src.api.routes.settings as settings_mod
        env_file = tmp_path / ".axion.env"
        monkeypatch.setattr(settings_mod, "_ENV_FILE", env_file)
        settings_mod._write_env_key(var, value)
        return env_file

    def test_new_env_file_is_chmod_600(self, tmp_path, monkeypatch):
        env_file = self._write(
            tmp_path, monkeypatch, "ANTHROPIC_API_KEY", "sk-ant-phase22test",
        )
        assert env_file.exists()
        mode = env_file.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    def test_env_file_not_world_or_group_readable(self, tmp_path, monkeypatch):
        env_file = self._write(
            tmp_path, monkeypatch, "OPENAI_API_KEY", "sk-proj-phase22test",
        )
        mode = env_file.stat().st_mode
        assert not (mode & 0o077), "env file is group/world accessible"

    def test_existing_env_file_stays_600_on_update(self, tmp_path, monkeypatch):
        # First write creates it; a second write (update path) must keep 0600.
        env_file = self._write(
            tmp_path, monkeypatch, "ANTHROPIC_API_KEY", "sk-ant-first",
        )
        import src.api.routes.settings as settings_mod
        settings_mod._write_env_key("ANTHROPIC_API_KEY", "sk-ant-second")
        mode = env_file.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600 after update, got {oct(mode)}"

    def test_written_value_is_present(self, tmp_path, monkeypatch):
        # Sanity: the fix did not break the actual write behaviour.
        env_file = self._write(
            tmp_path, monkeypatch, "GEMINI_API_KEY", "AIzaphase22testvalue",
        )
        assert "GEMINI_API_KEY=AIzaphase22testvalue" in env_file.read_text()


# ─────────────────────────────────────────────────────────────────────
# Support-bundle secret scrubbers
# ─────────────────────────────────────────────────────────────────────


class TestSupportBundleScrubbers:
    def test_redact_value_by_secret_key_name(self):
        sb = _load_script_module("support_bundle")
        out = sb._redact_value("ANTHROPIC_API_KEY", "anything-at-all-here")
        assert out.startswith("<redacted")
        assert "anything-at-all-here" not in out

    def test_redact_value_by_value_pattern_even_with_benign_key(self):
        sb = _load_script_module("support_bundle")
        # A benign-looking env-var name, but the value is an Anthropic key.
        out = sb._redact_value("SOME_BENIGN_NAME", "sk-ant-abcdefghijklmnop1234")
        assert out.startswith("<redacted")
        assert "sk-ant-" not in out

    def test_redact_value_leaves_non_secret_untouched(self):
        sb = _load_script_module("support_bundle")
        assert sb._redact_value("HOME", "/Users/example") == "/Users/example"

    def test_scrub_inline_masks_url_key_and_bearer(self):
        sb = _load_script_module("support_bundle")
        scrubbed = sb._scrub_inline(
            "GET https://feed.example.com/rss?apiKey=SUPERSECRET123 -> Bearer TKN9999"
        )
        assert "SUPERSECRET123" not in scrubbed
        assert "TKN9999" not in scrubbed
        assert "apiKey=***" in scrubbed
        assert "Bearer ***" in scrubbed


# ─────────────────────────────────────────────────────────────────────
# Provider-status scrubber (test-provider message path)
# ─────────────────────────────────────────────────────────────────────


class TestProviderStatusScrubber:
    def test_scrub_secrets_masks_vendor_keys(self):
        from src.llm.provider_status import scrub_secrets
        for token in (
            "sk-ant-abcdefghijklmnop1234",
            "sk-proj-abcdefghijklmnop1234",
            "AIzaABCDEFGHIJKLMNOPQRST",
        ):
            msg = f"auth failed using {token} please retry"
            out = scrub_secrets(msg)
            assert token not in out
            assert "***" in out

    def test_scrub_secrets_leaves_plain_text(self):
        from src.llm.provider_status import scrub_secrets
        assert scrub_secrets("rate limit exceeded") == "rate limit exceeded"


# ─────────────────────────────────────────────────────────────────────
# Source-error scrubber
# ─────────────────────────────────────────────────────────────────────


class TestSourceErrorScrubber:
    def test_masks_url_query_key(self):
        from src.sources.source_status import scrub_source_error
        out = scrub_source_error(
            "fetch failed: https://api.example.com/news?token=ABC123XYZ789&category=biz"
        )
        assert "ABC123XYZ789" not in out
        assert "token=***" in out

    def test_masks_bearer_and_vendor_token(self):
        from src.sources.source_status import scrub_source_error
        out = scrub_source_error("401 Unauthorized — Bearer mytoken12345 / sk-ant-abcdefghijklmnop1234")
        assert "mytoken12345" not in out
        assert "sk-ant-abcdefghijklmnop1234" not in out

    def test_empty_input_safe(self):
        from src.sources.source_status import scrub_source_error
        assert scrub_source_error("") == ""


# ─────────────────────────────────────────────────────────────────────
# Insight-export scrubber (Phase 15 _safe_str)
# ─────────────────────────────────────────────────────────────────────


class TestInsightExportScrubber:
    def test_safe_str_redacts_forbidden_substrings(self):
        from src.api.routes.intelligence import _safe_str
        for leaked in (
            "GROUNDING CONTRACT block",
            "api_key=sk-test-123",
            "Bearer abc.def.ghi",
            "ANTHROPIC_API_KEY in text",
        ):
            assert _safe_str(leaked) == "[redacted]", leaked

    def test_safe_str_passes_normal_content(self):
        from src.api.routes.intelligence import _safe_str
        assert _safe_str("Fed signals rate hike") == "Fed signals rate hike"
        assert _safe_str(None) == ""


# ─────────────────────────────────────────────────────────────────────
# Diagnostics model carries no secret-shaped field
# ─────────────────────────────────────────────────────────────────────


class TestDiagnosticsModelNoSecrets:
    def test_no_secret_named_fields(self):
        from src.api.routes.system import DiagnosticsResponse
        fields = set(DiagnosticsResponse.model_fields.keys())
        for forbidden in ("api_key", "apikey", "secret", "password", "token"):
            leaking = [f for f in fields if forbidden in f.lower()]
            assert not leaking, f"diagnostics model field looks secret: {leaking}"

    def test_integration_fields_are_booleans(self):
        # Integration presence is exposed as booleans, never key material.
        from src.api.routes.system import DiagnosticsResponse
        ann = DiagnosticsResponse.model_fields
        assert ann["llm_configured"].annotation is bool
        assert ann["telegram_configured"].annotation is bool


# ─────────────────────────────────────────────────────────────────────
# Uploaded-PDF extraction stays in memory
# ─────────────────────────────────────────────────────────────────────


class TestPdfHandledInMemory:
    def test_extraction_uses_bytesio_not_tempfile(self):
        src = (PROJECT_ROOT / "src" / "intelligence" / "revenue_geography"
               / "extraction.py").read_text(encoding="utf-8")
        # PDF bytes are read from an in-memory buffer.
        assert "io.BytesIO" in src
        # And never staged through a named temp file on disk.
        assert "NamedTemporaryFile" not in src
        assert "mkstemp" not in src

    def test_extraction_documents_no_disk_guarantee(self):
        src = (PROJECT_ROOT / "src" / "intelligence" / "revenue_geography"
               / "extraction.py").read_text(encoding="utf-8")
        low = src.lower()
        assert "no pdf bytes touch disk" in low or "never written to disk" in low
