"""Phase 7 — source health, API-key sources, and degraded-state tests.

Covers:
- The normalized ``SourceHealth`` model + status vocabulary.
- ``scrub_source_error()`` strips URL-embedded keys, Bearer tokens, and
  vendor-token patterns from any free-text string.
- ``classify_fetch_outcome()`` maps fetcher outcomes to the typed
  vocabulary (auth split between missing_key / misconfigured, 429 →
  rate_limited, 5xx → unreachable, parse failure → parser_error,
  zero-items success → degraded).
- ``config/sources.yaml`` source-by-source contract: every entry has
  required fields, ``sec-edgar`` is ``unsupported``, both API-key
  sources declare ``auth_env_var`` + ``auth_param_name``.
- The Finnhub parser correctly handles valid arrays, empty arrays,
  error dicts, missing fields, and bad JSON — all without leaking
  the API key (which never reaches the parser anyway).
- ``GET /api/v1/sources/health`` returns the normalized list with
  the per-status summary; reports ``Unsupported`` for sec-edgar with
  a disabled toggle; reports ``Missing key`` when an API-key source
  is enabled but the env var is empty. Never contains a fake key.
- ``GET /api/v1/system/diagnostics`` includes ``sources_by_status``.
- ``scripts/support_bundle.py`` inline-scrubs URL-embedded API keys
  inside log tails and includes a ``sources_health.json`` summary.
- The Sources UI markup uses the Phase 7 status vocabulary + the
  Auth env var column + the optional-keys intro paragraph.
- Customer docs name ``NEWSAPI_KEY`` / ``FINNHUB_KEY`` and never claim
  Bloomberg / FactSet / ATHEX corporate events.

All HTTP / SDK interactions are mocked. No real network calls.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from src.sources.registry import SourceRegistry
from src.sources.source_status import (
    build_health,
    classify_fetch_outcome,
    message_for,
    scrub_source_error,
    summarise_by_status,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip provider + source keys per test so missing-key paths fire."""
    for k in (
        "NEWSAPI_KEY", "FINNHUB_KEY", "ALPHAVANTAGE_KEY",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


# ───────────────────────────────────────────────────────────────────────────
# 7B — Status model + scrubber + classifier
# ───────────────────────────────────────────────────────────────────────────


class TestSourceHealthModel:
    def test_build_health_round_trips_required_fields(self):
        h = build_health(
            id="x",
            name="X",
            source_type="rss",
            enabled=True,
            configured=True,
            status="active",
            parser="rss_generic",
        )
        assert h.status == "active"
        assert h.id == "x"
        assert h.checked_at
        assert h.last_error_message is None

    def test_build_health_scrubs_free_text(self):
        h = build_health(
            id="x", name="X", source_type="api",
            enabled=False, configured=False, status="missing_key",
            last_error_message="GET https://newsapi.org/v2/top-headlines?apiKey=ABC123XYZSECRET failed",
        )
        assert "ABC123XYZSECRET" not in (h.last_error_message or "")
        assert "***" in (h.last_error_message or "")

    def test_frozen(self):
        h = build_health(
            id="x", name="X", source_type="rss",
            enabled=True, configured=True, status="active",
        )
        with pytest.raises((ValueError, TypeError)):
            h.status = "error"  # type: ignore[misc]

    def test_message_for_every_status(self):
        for s in (
            "active", "disabled", "missing_key", "degraded", "rate_limited",
            "unreachable", "parser_error", "unsupported", "misconfigured", "error",
        ):
            assert message_for(s)


class TestScrubSourceError:
    @pytest.mark.parametrize("name", ["apiKey", "api_key", "apikey", "key",
                                       "token", "access_token", "auth", "secret"])
    def test_url_query_param_keys_masked(self, name):
        text = f"GET https://x.test/path?{name}=ABCDEFG12345MAGIC&q=test failed"
        out = scrub_source_error(text)
        assert "ABCDEFG12345MAGIC" not in out
        assert f"{name}=***" in out

    def test_bearer_masked(self):
        text = "Authorization: Bearer xyz.abc.123 — denied"
        out = scrub_source_error(text)
        assert "xyz.abc.123" not in out
        assert "Bearer ***" in out

    def test_anthropic_pattern_masked(self):
        text = "leaked sk-ant-" + "A" * 32
        assert "sk-ant-" not in scrub_source_error(text)

    def test_openai_pattern_masked(self):
        for prefix in ("sk-proj-", "sk-"):
            text = f"saw {prefix}{'X' * 30} in trace"
            assert prefix not in scrub_source_error(text)

    def test_telegram_pattern_masked(self):
        text = "token 1234567890:" + "A" * 35 + " ok"
        assert "1234567890:" not in scrub_source_error(text)

    def test_empty(self):
        assert scrub_source_error("") == ""

    def test_innocent_text_preserved(self):
        text = "the cat sat on the mat"
        assert scrub_source_error(text) == text


class TestClassifyFetchOutcome:
    def test_success_with_items_is_active(self):
        s, c = classify_fetch_outcome(success=True, status_code=200, error=None, fetched_count=5)
        assert s == "active"
        assert c is None

    def test_success_with_zero_items_is_degraded(self):
        s, c = classify_fetch_outcome(success=True, status_code=200, error=None, fetched_count=0)
        assert s == "degraded"
        assert c == "zero_items"

    def test_401_with_missing_key_text(self):
        s, c = classify_fetch_outcome(success=False, status_code=401, error="Missing API key: NEWSAPI_KEY")
        assert s == "missing_key"

    def test_401_with_other_text(self):
        s, c = classify_fetch_outcome(success=False, status_code=401, error="Unauthorized")
        assert s == "misconfigured"
        assert c == "http_401"

    def test_403(self):
        s, c = classify_fetch_outcome(success=False, status_code=403, error="forbidden")
        assert s == "misconfigured"

    def test_429(self):
        s, c = classify_fetch_outcome(success=False, status_code=429, error="rate limited")
        assert s == "rate_limited"
        assert c == "http_429"

    def test_500(self):
        s, c = classify_fetch_outcome(success=False, status_code=503, error="service unavailable")
        assert s == "unreachable"

    def test_timeout_no_code(self):
        s, c = classify_fetch_outcome(success=False, status_code=None, error="Timeout")
        assert s == "unreachable"
        assert c == "timeout"

    def test_dns_no_code(self):
        s, c = classify_fetch_outcome(success=False, status_code=None, error="dns resolution failed")
        assert s == "unreachable"
        assert c == "dns"

    def test_parse_failure_no_code(self):
        s, c = classify_fetch_outcome(success=False, status_code=None, error="parser failed: bad json")
        assert s == "parser_error"


class TestSummariseByStatus:
    def test_counts_match_health_list(self):
        healths = [
            build_health(id="a", name="A", source_type="rss", enabled=True, configured=True, status="active"),
            build_health(id="b", name="B", source_type="api", enabled=True, configured=False, status="missing_key"),
            build_health(id="c", name="C", source_type="rss", enabled=False, configured=True, status="disabled"),
            build_health(id="d", name="D", source_type="api", enabled=False, configured=False, status="unsupported"),
        ]
        s = summarise_by_status(healths)
        assert s["total"] == 4
        assert s["active"] == 1
        assert s["missing_key"] == 1
        assert s["disabled"] == 1
        assert s["unsupported"] == 1
        # Unfilled buckets default to zero so the UI can render every cell.
        assert s["rate_limited"] == 0


# ───────────────────────────────────────────────────────────────────────────
# 7C — sources.yaml contract
# ───────────────────────────────────────────────────────────────────────────


class TestSourcesYamlContract:
    @pytest.fixture(scope="class")
    def yaml_data(self):
        return yaml.safe_load(
            (PROJECT_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
        )

    REQUIRED_FIELDS = ("id", "name", "domain", "type", "url", "parser", "enabled")

    def test_every_source_has_required_fields(self, yaml_data):
        for src in yaml_data["sources"]:
            for field in self.REQUIRED_FIELDS:
                assert field in src, f"{src.get('id', '?')} missing {field}"

    def test_sec_edgar_is_unsupported(self, yaml_data):
        sec = next(s for s in yaml_data["sources"] if s["id"] == "sec-edgar")
        assert sec.get("unsupported") is True
        assert sec["enabled"] is False
        assert "notes" in sec

    def test_newsapi_declares_env_and_param(self, yaml_data):
        n = next(s for s in yaml_data["sources"] if s["id"] == "newsapi-general")
        assert n["auth_env_var"] == "NEWSAPI_KEY"
        assert n["auth_param_name"] == "apiKey"
        assert n["enabled"] is False
        assert n["parser"] == "newsapi"

    def test_finnhub_declares_env_and_param(self, yaml_data):
        f = next(s for s in yaml_data["sources"] if s["id"] == "finnhub-news")
        assert f["auth_env_var"] == "FINNHUB_KEY"
        assert f["auth_param_name"] == "token"
        assert f["enabled"] is False
        assert f["parser"] == "finnhub"

    def test_no_paid_vendors_declared_as_active(self, yaml_data):
        banned = {"bloomberg", "factset", "refinitiv", "capital iq", "s&p capital"}
        for src in yaml_data["sources"]:
            name = (src.get("name") or "").lower()
            assert not any(b in name for b in banned), (
                f"YAML must not ship paid-vendor source as bundled: {src['id']}"
            )

    def test_registry_loads_new_fields(self):
        reg = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
        sec = reg.get_source("sec-edgar")
        assert sec is not None
        assert sec.unsupported is True
        finnhub = reg.get_source("finnhub-news")
        assert finnhub is not None
        assert finnhub.auth_param_name == "token"
        assert finnhub.notes


# ───────────────────────────────────────────────────────────────────────────
# 7D — Finnhub parser
# ───────────────────────────────────────────────────────────────────────────


class TestFinnhubParser:
    def _parser(self):
        from src.sources.parsers.finnhub import FinnhubParser
        return FinnhubParser()

    def test_valid_array(self):
        payload = json.dumps([
            {
                "category": "general",
                "datetime": 1707840000,
                "headline": "Apple beats Q4 ...",
                "id": 119551392,
                "image": "https://example/img.jpg",
                "related": "AAPL,MSFT",
                "source": "Reuters",
                "summary": "Apple reported strong Q4 ...",
                "url": "https://example/article-1",
            },
            {
                "category": "general",
                "datetime": 1707843600,
                "headline": "Fed signals pause",
                "id": 119551393,
                "image": "",
                "related": "",
                "source": "Bloomberg",
                "summary": "FOMC minutes ...",
                "url": "https://example/article-2",
            },
        ])
        events = self._parser().parse(payload, "finnhub-news")
        assert len(events) == 2
        assert events[0].title.startswith("Apple")
        assert events[0].url == "https://example/article-1"
        assert "AAPL" in events[0].tags
        assert "MSFT" in events[0].tags
        # raw_data must NOT contain the image URL (we strip it for size).
        assert "image" not in events[0].raw_data

    def test_empty_array(self):
        assert self._parser().parse("[]", "finnhub-news") == []

    def test_error_dict(self):
        payload = json.dumps({"error": "Invalid API key"})
        events = self._parser().parse(payload, "finnhub-news")
        assert events == []

    def test_bad_json(self):
        assert self._parser().parse("not json", "finnhub-news") == []

    def test_missing_url_is_skipped(self):
        payload = json.dumps([
            {"headline": "no url", "datetime": 1707840000},  # missing url -> skip
            {"headline": "ok",     "datetime": 1707840000, "url": "https://x/y"},
        ])
        events = self._parser().parse(payload, "finnhub-news")
        assert len(events) == 1
        assert events[0].url == "https://x/y"

    def test_unparseable_datetime(self):
        payload = json.dumps([
            {"headline": "ok", "datetime": "not a number", "url": "https://x/y"},
        ])
        events = self._parser().parse(payload, "finnhub-news")
        assert len(events) == 1
        assert events[0].published_at == ""


# ───────────────────────────────────────────────────────────────────────────
# 7F — /api/v1/sources/health endpoint
# ───────────────────────────────────────────────────────────────────────────


class TestSourcesHealthEndpoint:
    def _make_client(self, db_rows: list | None = None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.deps import get_session
        from src.api.routes.sources import router

        async def fake_session():
            class _S:
                async def execute(self, *a, **k):
                    class _R:
                        def scalars(self): return self
                        def all(self): return db_rows or []
                    return _R()
                async def get(self, *a, **k): return None
            yield _S()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = fake_session
        return TestClient(app)

    def test_returns_normalized_health_for_every_yaml_source(self):
        with self._make_client() as client:
            r = client.get("/api/v1/sources/health")
        assert r.status_code == 200, r.text
        body = r.json()
        ids = {s["id"] for s in body["sources"]}
        for required in ("fed-rss", "ecb-rss", "sec-edgar",
                         "newsapi-general", "finnhub-news"):
            assert required in ids, f"missing {required} in health response"

    def test_sec_edgar_status_unsupported(self):
        with self._make_client() as client:
            r = client.get("/api/v1/sources/health")
        body = r.json()
        sec = next(s for s in body["sources"] if s["id"] == "sec-edgar")
        assert sec["status"] == "unsupported"
        assert sec["last_error_code"] == "parser_missing"

    def test_newsapi_missing_key_when_env_empty(self):
        # newsapi-general is enabled:false in YAML, but the user might
        # toggle it on. Simulate that via a DB row with enabled=1, then
        # confirm the status is missing_key (key env var not set).
        class _Row:
            id = "newsapi-general"
            name = "NewsAPI Business"
            domain = "newsapi.org"
            url = "https://newsapi.org/v2/top-headlines"
            source_type = "api"
            parser_id = "newsapi"
            priority = 3
            trust_level = "standard"
            enabled = 1
            rate_limit_rpm = 2
            requires_auth = 1
            auth_type = "api_key"
            auth_env_var = "NEWSAPI_KEY"
            last_fetched_at = None
            last_status = None
            created_at = "2026-01-01T00:00:00Z"

        with self._make_client(db_rows=[_Row()]) as client:
            r = client.get("/api/v1/sources/health")
        body = r.json()
        newsapi = next(s for s in body["sources"] if s["id"] == "newsapi-general")
        assert newsapi["status"] == "missing_key"
        assert newsapi["required_env_var"] == "NEWSAPI_KEY"
        assert newsapi["configured"] is False

    def test_newsapi_active_when_env_set(self, monkeypatch):
        monkeypatch.setenv("NEWSAPI_KEY", "fake-test-key-do-not-leak")
        class _Row:
            id = "newsapi-general"
            name = "NewsAPI Business"
            domain = "newsapi.org"
            url = "https://newsapi.org/v2/top-headlines"
            source_type = "api"
            parser_id = "newsapi"
            priority = 3
            trust_level = "standard"
            enabled = 1
            rate_limit_rpm = 2
            requires_auth = 1
            auth_type = "api_key"
            auth_env_var = "NEWSAPI_KEY"
            last_fetched_at = "2026-05-14T00:00:00Z"
            last_status = "ok"
            created_at = "2026-01-01T00:00:00Z"

        with self._make_client(db_rows=[_Row()]) as client:
            r = client.get("/api/v1/sources/health")
        body = r.json()
        newsapi = next(s for s in body["sources"] if s["id"] == "newsapi-general")
        assert newsapi["status"] == "active"
        assert newsapi["configured"] is True
        # The fake key must NEVER appear anywhere in the response body.
        assert "fake-test-key-do-not-leak" not in r.text

    def test_response_summary_counts(self):
        with self._make_client() as client:
            r = client.get("/api/v1/sources/health")
        body = r.json()
        summary = body["summary"]
        # Phase 9 added the ATHEX corporate-events row (also unsupported)
        # so the total floats with the YAML. The contract that matters is:
        # at least the original 15 news sources are present, sec-edgar +
        # athex-corporate-events are unsupported, and the default RSS feeds
        # are active.
        assert summary["total"] >= 15
        assert summary["unsupported"] >= 2  # sec-edgar + athex-corporate-events
        assert summary["active"] >= 5

    def test_response_no_raw_url_query_keys(self):
        # Even if a future YAML embeds a key in the URL, the health
        # response only carries id/name/parser/required_env_var, not the
        # raw URL — so this is a structural guarantee.
        with self._make_client() as client:
            r = client.get("/api/v1/sources/health")
        # No source field named "url" in the response.
        body = r.json()
        for s in body["sources"]:
            assert "url" not in s


# ───────────────────────────────────────────────────────────────────────────
# Existing /api/v1/sources route remains backward compatible
# ───────────────────────────────────────────────────────────────────────────


class TestLegacySourcesRoute:
    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.deps import get_session
        from src.api.routes.sources import router

        async def fake_session():
            class _S:
                async def execute(self, *a, **k):
                    class _R:
                        def scalars(self): return self
                        def all(self): return []
                    return _R()
                async def get(self, *a, **k): return None
            yield _S()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = fake_session
        return TestClient(app)

    def test_list_sources_returns_200(self):
        with self._make_client() as client:
            r = client.get("/api/v1/sources")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_unknown_source_returns_404(self):
        with self._make_client() as client:
            r = client.get("/api/v1/sources/nonexistent/health")
        assert r.status_code == 404


# ───────────────────────────────────────────────────────────────────────────
# 7H — diagnostics endpoint sources_by_status
# ───────────────────────────────────────────────────────────────────────────


class TestDiagnosticsSourceCounts:
    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api.routes.system import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_diagnostics_includes_sources_by_status(self):
        with self._make_client() as client:
            r = client.get("/api/v1/system/diagnostics")
        body = r.json()
        assert "sources_by_status" in body
        s = body["sources_by_status"]
        # Every normalized status key plus 'total' must be present.
        for k in ("active", "disabled", "missing_key", "degraded",
                  "rate_limited", "unreachable", "parser_error",
                  "unsupported", "misconfigured", "error", "total"):
            assert k in s, f"missing {k!r} in sources_by_status"
        # Phase 9 added athex-corporate-events to the YAML; the floor is
        # still 15 (the Phase 7 news lineup).
        assert s["total"] >= 15


# ───────────────────────────────────────────────────────────────────────────
# 7H — support_bundle redaction + source health summary
# ───────────────────────────────────────────────────────────────────────────


class TestSupportBundleRedaction:
    def _import_bundle(self):
        spec = importlib.util.spec_from_file_location(
            "support_bundle", PROJECT_ROOT / "scripts" / "support_bundle.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_inline_scrub_handles_url_keys(self):
        mod = self._import_bundle()
        text = "GET https://newsapi.org/v2/top-headlines?apiKey=ABCDEFG12345&q=x failed"
        out = mod._scrub_inline(text)
        assert "ABCDEFG12345" not in out
        assert "apiKey=***" in out

    def test_inline_scrub_handles_bearer(self):
        mod = self._import_bundle()
        out = mod._scrub_inline("Authorization: Bearer abc.def.ghi")
        assert "abc.def.ghi" not in out
        assert "Bearer ***" in out

    def test_inline_scrub_innocent_text(self):
        mod = self._import_bundle()
        text = "no secrets here"
        assert mod._scrub_inline(text) == text


# ───────────────────────────────────────────────────────────────────────────
# 7G — Sources UI markup contract
# ───────────────────────────────────────────────────────────────────────────


class TestSourcesUiContract:
    @pytest.fixture(scope="class")
    def app_js(self) -> str:
        return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(encoding="utf-8")

    def test_renderer_uses_phase7_status_vocabulary(self, app_js):
        for label in (
            "'Missing key'", "'Degraded'", "'Rate limited'",
            "'Unreachable'", "'Parser error'", "'Unsupported'",
            "'Misconfigured'",
        ):
            assert label in app_js, f"Sources renderer missing status label {label}"

    def test_renderer_has_auth_env_var_column(self, app_js):
        assert "Auth env var" in app_js

    def test_renderer_has_optional_keys_intro(self, app_js):
        assert "Core RSS sources work without keys" in app_js
        assert "Paid / subscription sources are not bundled" in app_js

    def test_renderer_disables_toggle_for_unsupported(self, app_js):
        # The toggle is rendered ``disabled`` when status === 'unsupported'.
        assert "status === 'unsupported' ? 'disabled' : ''" in app_js

    def test_renderer_consumes_sources_health_endpoint(self, app_js):
        assert "/api/v1/sources/health" in app_js


# ───────────────────────────────────────────────────────────────────────────
# 7I — env template + docs language
# ───────────────────────────────────────────────────────────────────────────


class TestEnvTemplateSources:
    def _read(self) -> str:
        return (PROJECT_ROOT / ".env.template").read_text(encoding="utf-8")

    def test_documents_news_api_keys(self):
        txt = self._read()
        assert "NEWSAPI_KEY" in txt
        assert "FINNHUB_KEY" in txt

    def test_marks_news_keys_optional(self):
        txt = self._read().lower()
        assert "optional" in txt

    def test_does_not_promise_paid_vendors(self):
        txt = self._read().lower()
        # Free-text "Bloomberg, FactSet, ..." may appear under
        # "NOT included in this build". Ensure that's the framing.
        if "bloomberg" in txt:
            assert "not included" in txt or "not yet" in txt


class TestDocsLanguage:
    DOCS = [
        "README_LOCAL.md",
        "docs/CUSTOMER_QUICKSTART.md",
        "KNOWN_LIMITATIONS.md",
        "docs/CLIENT_FAQ.md",
        "docs/RELEASE_CHECKLIST.md",
    ]

    @pytest.mark.parametrize("path", DOCS)
    def test_doc_mentions_news_api_keys(self, path):
        txt = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        # At least one of the docs in this set must reference NEWSAPI_KEY.
        # We assert per-file only if the doc is a customer-onboarding doc.
        if path in ("README_LOCAL.md", "docs/CUSTOMER_QUICKSTART.md", "docs/CLIENT_FAQ.md"):
            assert "NEWSAPI_KEY" in txt or "Finnhub" in txt or "newsapi" in txt.lower()

    @pytest.mark.parametrize("path", DOCS)
    def test_doc_does_not_promise_athex_corporate_events(self, path):
        txt = (PROJECT_ROOT / path).read_text(encoding="utf-8")
        low = txt.lower()
        if "athex" not in low and "corporate event" not in low:
            return
        flagged = any(needle in low for needle in (
            "not implemented", "not yet", "future", "roadmap",
            "not part of this build", "is not yet part", "is **not** part",
            "**not**", "never claim", "never marketed", "not bundled",
        ))
        assert flagged, f"{path} mentions ATHEX / corporate events without flagging future"

    @pytest.mark.parametrize("path", DOCS)
    def test_doc_does_not_promise_paid_vendors_as_active(self, path):
        txt = (PROJECT_ROOT / path).read_text(encoding="utf-8").lower()
        for vendor in ("bloomberg", "factset", "refinitiv", "capital iq"):
            if vendor in txt:
                assert any(needle in txt for needle in (
                    "not included", "not bundled", "roadmap", "not yet",
                    "never claim",
                )), f"{path} mentions {vendor} without 'not included' framing"


# ───────────────────────────────────────────────────────────────────────────
# 7E — fetcher hardening: missing key + auth_param_name resolution
# ───────────────────────────────────────────────────────────────────────────


class TestFetcherKeyHandling:
    @pytest.mark.asyncio
    async def test_missing_key_returns_missing_api_key_error(self):
        from src.sources.fetcher import SourceFetcher
        reg = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
        fetcher = SourceFetcher(registry=reg)
        try:
            cfg = reg.get_source("newsapi-general")
            # The fetcher has two paths for "no key configured":
            # (a) api_keys is falsy → "Source requires auth but no api_keys provided"
            # (b) api_keys is set but missing the requested env var → "Missing API key: NEWSAPI_KEY"
            # Both must classify as missing_key — verify (b) directly since
            # that's the path the collection agent actually takes (it always
            # passes at least an empty dict).
            result = await fetcher.fetch_source(cfg, api_keys={"UNRELATED": "x"})
            assert result.success is False
            assert "Missing API key" in (result.error or "")
            # And the source_status classifier maps it to missing_key.
            from src.sources.source_status import classify_fetch_outcome
            status, code = classify_fetch_outcome(
                success=False, status_code=None, error=result.error,
            )
            assert status == "missing_key"
        finally:
            await fetcher.close()

    @pytest.mark.asyncio
    async def test_finnhub_uses_token_param(self):
        from src.sources.fetcher import SourceFetcher
        reg = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
        cfg = reg.get_source("finnhub-news")
        assert cfg.auth_param_name == "token"
        # Build the fetcher and patch the httpx client. Inspect the
        # request that goes out to confirm ``token`` not ``apiKey``.
        fetcher = SourceFetcher(registry=reg)
        try:
            class _FakeClient:
                last_params = None
                async def get(self, url, headers=None, params=None):
                    _FakeClient.last_params = dict(params or {})
                    class R:
                        status_code = 200
                        text = "[]"
                        headers = {"content-type": "application/json"}
                    return R()
                async def aclose(self): pass
            fetcher._client = _FakeClient()
            result = await fetcher.fetch_source(cfg, api_keys={"FINNHUB_KEY": "fake-finnhub-key"})
            assert result.success is True
            assert "token" in _FakeClient.last_params
            assert _FakeClient.last_params["token"] == "fake-finnhub-key"
            assert "apiKey" not in _FakeClient.last_params
        finally:
            await fetcher.close()

    @pytest.mark.asyncio
    async def test_error_message_scrubs_url_key(self, monkeypatch):
        from src.sources.fetcher import SourceFetcher
        import httpx
        reg = SourceRegistry(PROJECT_ROOT / "config" / "sources.yaml")
        cfg = reg.get_source("newsapi-general")
        fetcher = SourceFetcher(registry=reg)
        try:
            class _FakeClient:
                async def get(self, url, headers=None, params=None):
                    # Simulate httpx including the URL in the error.
                    raise httpx.RequestError(
                        "connection failed for https://newsapi.org/v2/top-headlines?apiKey=ABCDEFG12345SECRET"
                    )
                async def aclose(self): pass
            fetcher._client = _FakeClient()
            result = await fetcher.fetch_source(cfg, api_keys={"NEWSAPI_KEY": "ABCDEFG12345SECRET"})
            assert result.success is False
            assert "ABCDEFG12345SECRET" not in (result.error or "")
            assert "apiKey=***" in (result.error or "")
        finally:
            await fetcher.close()
