"""Phase 25 — source-reliability transparency lock-in tests.

The Phase 25 audit confirmed the source pipeline is already honest:

* ``config/sources.yaml`` ships exactly 7 enabled, keyless RSS
  sources; the rest are disabled duplicates / dead feeds, two
  ``unsupported: true`` rows (SEC EDGAR, ATHEX corporate events), and
  two optional API-key sources (NewsAPI, Finnhub).
* ``_resolve_source_status()`` checks ``cfg.unsupported`` *first*, so
  an ``unsupported`` source can never be presented as working — even
  if its DB row's ``enabled`` flag is toggled on.
* ``requires_auth`` sources surface ``missing_key`` (not a crash)
  when their key env var is unset.
* ``scrub_source_error()`` masks API keys / tokens in error text.

These tests lock those invariants in so a future config or code
change cannot silently regress source transparency. They also pin the
parser registry's real contents (see ``TestParserRegistry`` — the
``finnhub`` parser file exists but is intentionally *not* registered;
Phase 25 reports that gap rather than wiring it).

All checks are deterministic and offline.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES_YAML = PROJECT_ROOT / "config" / "sources.yaml"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _registry():
    from src.sources.registry import SourceRegistry
    return SourceRegistry(SOURCES_YAML)


def _fake_db_row(enabled: int, last_status: str | None = None):
    """A minimal stand-in for a SourceModel row — _resolve_source_status
    only reads ``.enabled`` and ``.last_status``."""
    return types.SimpleNamespace(enabled=enabled, last_status=last_status)


#: The 7 sources that ship enabled + keyless.
EXPECTED_ENABLED = {
    "fed-rss", "ecb-rss", "google-news-business", "wsj-markets",
    "marketwatch-rss", "seekingalpha-rss", "investing-rss",
}
#: The two structurally-unsupported sources.
EXPECTED_UNSUPPORTED = {"sec-edgar", "athex-corporate-events"}
#: The two optional-key sources.
EXPECTED_KEY_SOURCES = {"newsapi-general", "finnhub-news"}


# ─────────────────────────────────────────────────────────────────────
# Enabled-source count matches the customer doc
# ─────────────────────────────────────────────────────────────────────


class TestEnabledSourceCount:
    def test_exactly_seven_enabled_sources(self):
        reg = _registry()
        enabled = {s.id for s in reg.get_enabled_sources()}
        assert enabled == EXPECTED_ENABLED, (
            f"enabled source set drifted: {sorted(enabled)}"
        )

    def test_enabled_count_matches_known_limitations_doc(self):
        # KNOWN_LIMITATIONS.md states the enabled count explicitly
        # (Phase 19). If sources.yaml changes, the doc must change too.
        reg = _registry()
        count = len(reg.get_enabled_sources())
        doc = (PROJECT_ROOT / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
        assert f"{count} enabled" in doc, (
            f"config has {count} enabled sources but KNOWN_LIMITATIONS.md "
            f"does not state that count"
        )

    def test_all_enabled_sources_are_keyless(self):
        # The 7 default sources must need no API key.
        reg = _registry()
        for s in reg.get_enabled_sources():
            assert not s.requires_auth, (
                f"enabled source {s.id} unexpectedly requires auth"
            )

    def test_enabled_sources_are_not_unsupported(self):
        reg = _registry()
        for s in reg.get_enabled_sources():
            assert not s.unsupported, (
                f"enabled source {s.id} is also marked unsupported"
            )


# ─────────────────────────────────────────────────────────────────────
# Unsupported sources resolve to "unsupported" and cannot be toggled on
# ─────────────────────────────────────────────────────────────────────


class TestUnsupportedSourcesGated:
    def test_unsupported_sources_present_in_config(self):
        reg = _registry()
        unsupported = {s.id for s in reg.get_all_sources() if s.unsupported}
        assert unsupported == EXPECTED_UNSUPPORTED, (
            f"unsupported source set drifted: {sorted(unsupported)}"
        )

    @pytest.mark.parametrize("source_id", sorted(EXPECTED_UNSUPPORTED))
    def test_unsupported_resolves_to_unsupported(self, source_id):
        from src.api.routes.sources import _resolve_source_status
        cfg = _registry().get_source(source_id)
        assert cfg is not None
        status, _code, _msg = _resolve_source_status(cfg, None)
        assert status == "unsupported", (
            f"{source_id} resolved to {status!r}, expected 'unsupported'"
        )

    @pytest.mark.parametrize("source_id", sorted(EXPECTED_UNSUPPORTED))
    def test_unsupported_cannot_be_toggled_into_working(self, source_id):
        # Even with a DB row that says enabled=1 AND last_status="ok",
        # an unsupported source must still resolve to "unsupported" —
        # the cfg.unsupported check runs before any enabled/status logic.
        from src.api.routes.sources import _resolve_source_status
        cfg = _registry().get_source(source_id)
        toggled_on = _fake_db_row(enabled=1, last_status="ok")
        status, _code, _msg = _resolve_source_status(cfg, toggled_on)
        assert status == "unsupported", (
            f"{source_id} was toggled enabled and reported {status!r} — "
            f"an unsupported source must never present as working"
        )


# ─────────────────────────────────────────────────────────────────────
# Optional-key sources surface missing_key without crashing
# ─────────────────────────────────────────────────────────────────────


class TestOptionalKeySources:
    def test_key_sources_present_and_disabled_by_default(self):
        reg = _registry()
        for sid in EXPECTED_KEY_SOURCES:
            cfg = reg.get_source(sid)
            assert cfg is not None, f"{sid} missing from config"
            assert cfg.requires_auth, f"{sid} should require auth"
            assert not cfg.enabled, f"{sid} should be disabled by default"
            assert not cfg.unsupported, f"{sid} should not be unsupported"

    @pytest.mark.parametrize("source_id", sorted(EXPECTED_KEY_SOURCES))
    def test_enabled_key_source_without_key_is_missing_key(
        self, source_id, monkeypatch,
    ):
        # Simulate the operator toggling the source on (DB row
        # enabled=1) while the API key env var is absent. The status
        # must be the typed "missing_key" — never a crash.
        from src.api.routes.sources import _resolve_source_status
        cfg = _registry().get_source(source_id)
        if cfg.auth_env_var:
            monkeypatch.delenv(cfg.auth_env_var, raising=False)
        toggled_on = _fake_db_row(enabled=1, last_status=None)
        status, code, _msg = _resolve_source_status(cfg, toggled_on)
        assert status == "missing_key", (
            f"{source_id} without a key resolved to {status!r}"
        )
        assert code == "missing_key"


# ─────────────────────────────────────────────────────────────────────
# Parser registry — pins the real, audited contents
# ─────────────────────────────────────────────────────────────────────


class TestParserRegistry:
    def test_implemented_parsers_resolve(self):
        from src.sources.parsers import get_parser
        for pid in ("rss_generic", "newsapi"):
            parser = get_parser(pid)
            assert parser is not None

    def test_unsupported_source_parsers_are_absent(self):
        # The two unsupported sources' parsers must NOT resolve —
        # that is what makes "unsupported" honest.
        from src.sources.parsers import get_parser
        for pid in ("sec_edgar", "athex_corporate_events"):
            with pytest.raises(ValueError):
                get_parser(pid)

    def test_finnhub_parser_is_not_registered(self):
        # AUDIT FINDING (Phase 25): src/sources/parsers/finnhub.py
        # defines FinnhubParser, but it is not registered in
        # get_parser()'s _build_registry(). The finnhub-news source is
        # disabled by default so nothing breaks out of the box. This
        # test pins the current reality; if finnhub is later wired in,
        # this test fails and must be updated deliberately.
        from src.sources.parsers import get_parser
        with pytest.raises(ValueError):
            get_parser("finnhub")


# ─────────────────────────────────────────────────────────────────────
# Source-error scrubbing
# ─────────────────────────────────────────────────────────────────────


class TestSourceErrorScrubbing:
    def test_scrubs_url_query_key(self):
        from src.sources.source_status import scrub_source_error
        out = scrub_source_error(
            "fetch failed: https://api.example.com/news?apiKey=secret123abc",
        )
        assert "secret123abc" not in out
        assert "apiKey=***" in out

    def test_scrubs_bearer_token(self):
        from src.sources.source_status import scrub_source_error
        out = scrub_source_error("401 Unauthorized: Bearer abc123def456ghi")
        assert "abc123def456ghi" not in out
        assert "Bearer ***" in out

    def test_scrubs_vendor_key_patterns(self):
        from src.sources.source_status import scrub_source_error
        leaked = "error with key sk-ant-aaaaaaaaaaaaaaaaaaaa in request"
        out = scrub_source_error(leaked)
        assert "sk-ant-aaaaaaaaaaaaaaaaaaaa" not in out

    def test_plain_error_text_is_untouched(self):
        from src.sources.source_status import scrub_source_error
        msg = "connection timed out after 30s"
        assert scrub_source_error(msg) == msg

    def test_handles_empty_and_none(self):
        from src.sources.source_status import scrub_source_error
        assert scrub_source_error("") == ""
        assert scrub_source_error(None) is None
