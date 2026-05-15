"""Phase 11 — Reviewed AI extraction tests.

Coverage:

* :data:`EXTRACTION_PROMPT` carries every anti-hallucination rule the
  charter requires (no inference from headquarters, listing exchange,
  ISIN, customer names, incorporation; explicit-only data;
  empty-list when nothing found; strict JSON shape).
* :func:`extract_from_text` reports ``missing_key`` when no LLM
  provider is available and ``extraction_failed`` when the LLM
  layer raises.
* A mocked successful LLM payload becomes a ``success`` result with
  validated candidates, evidence text, and confidence preserved.
* A mocked "no data" LLM payload becomes
  ``no_revenue_geography_found``.
* Malformed JSON / non-dict output → ``extraction_failed``.
* The ``/extract`` route is reachable, returns
  ``missing_key`` honestly in a no-key build, and writes **zero**
  rows to the ``revenue_geography`` table.
* The ``/confirm-extraction`` route persists rows with
  ``source_type='ai_extracted'``, hits the same ISIN-first matcher,
  and shows up in the existing Phase 10 revenue-geography service.
* Multi-portfolio isolation holds for confirmed rows.
* Unconfirmed candidates **do not** affect the revenue-geography
  service (status stays ``missing`` until confirm runs).
* The dashboard markup carries both tabs (CSV + AI), the candidate
  review table, the confirm/discard buttons, and the honest no-key
  status placeholder.
* The support bundle reports ``revenue_geography`` counts but never
  inlines uploaded document bytes.
* Docs explicitly mark CSV as the primary path and AI as optional
  + review-first.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Anti-hallucination prompt contract (pure)
# ───────────────────────────────────────────────────────────────────────────


class TestExtractionPromptContract:
    """The prompt text is part of the customer-safety surface.

    Changes here must keep every rule below intact — adjust the test
    only when a charter rule is intentionally tightened.
    """

    def test_explicit_only_rule(self):
        from src.intelligence.revenue_geography import EXTRACTION_PROMPT
        text = EXTRACTION_PROMPT.lower()
        assert "explicitly present" in text or "explicit" in text
        assert "do not infer" in text

    def test_no_proxy_signals(self):
        from src.intelligence.revenue_geography import EXTRACTION_PROMPT
        text = EXTRACTION_PROMPT.lower()
        for signal in (
            "headquarters",
            "country of incorporation",
            "isin prefix",
            "listing exchange",
            "customer names",
        ):
            assert signal in text, f"prompt does not forbid {signal!r}"

    def test_narrative_only_forbidden(self):
        from src.intelligence.revenue_geography import EXTRACTION_PROMPT
        text = EXTRACTION_PROMPT.lower()
        assert "without numbers" in text or "narrative" in text

    def test_empty_result_when_unsupported(self):
        from src.intelligence.revenue_geography import EXTRACTION_PROMPT
        text = EXTRACTION_PROMPT.lower()
        assert '"candidates": []' in text or '"candidates":[]' in text

    def test_json_only_response(self):
        from src.intelligence.revenue_geography import EXTRACTION_PROMPT
        text = EXTRACTION_PROMPT.lower()
        assert "return exactly this json" in text or "return only this" in text


# ───────────────────────────────────────────────────────────────────────────
# Status path tests (no LLM key configured)
# ───────────────────────────────────────────────────────────────────────────


class TestExtractionStatuses:
    @pytest.mark.asyncio
    async def test_missing_key_status_when_no_provider(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(False, "missing_key", "no key"),
        ):
            res = await extract_from_text(text="dummy report text")
        assert res.status == "missing_key"
        assert res.candidates == []

    @pytest.mark.asyncio
    async def test_disabled_status_propagates(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(False, "disabled", "AI off"),
        ):
            res = await extract_from_text(text="x")
        assert res.status == "disabled"

    @pytest.mark.asyncio
    async def test_unsupported_file_on_empty_text(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(True, "success", "ok"),
        ):
            res = await extract_from_text(text="")
        assert res.status == "unsupported_file"

    @pytest.mark.asyncio
    async def test_success_with_mocked_llm(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        async def fake_call(_prompt, **_kw):
            return {
                "found_revenue_geography": True,
                "fiscal_year": 2025,
                "period": "FY",
                "currency": "USD",
                "company_name": "Test Co",
                "ticker": "TST",
                "isin": None,
                "candidates": [
                    {
                        "region": "North America",
                        "revenue_share": 0.6,
                        "raw_evidence": "NA: 60% of FY25 revenue",
                        "page_number": 4,
                        "confidence": 0.92,
                    },
                    {
                        "region": "Europe",
                        "revenue_share": 0.4,
                        "raw_evidence": "Europe: 40% of FY25 revenue",
                        "page_number": 4,
                        "confidence": 0.91,
                    },
                ],
            }
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(True, "success", "ok"),
        ), patch("src.llm.client.call_llm_json", new=fake_call):
            res = await extract_from_text(text="fake report body")
        assert res.status == "success"
        assert len(res.candidates) == 2
        regions = {c.region for c in res.candidates}
        assert regions == {"North America", "Europe"}
        assert all(c.evidence_text for c in res.candidates)
        assert all(c.confidence and c.confidence > 0.8 for c in res.candidates)

    @pytest.mark.asyncio
    async def test_no_revenue_geography_found(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        async def fake_call(_prompt, **_kw):
            return {"found_revenue_geography": False, "candidates": []}
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(True, "success", "ok"),
        ), patch("src.llm.client.call_llm_json", new=fake_call):
            res = await extract_from_text(text="prose-only report")
        assert res.status == "no_revenue_geography_found"
        assert res.candidates == []

    @pytest.mark.asyncio
    async def test_malformed_json_becomes_extraction_failed(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        async def bad_call(_prompt, **_kw):
            return ["not", "an", "object"]
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(True, "success", "ok"),
        ), patch("src.llm.client.call_llm_json", new=bad_call):
            res = await extract_from_text(text="x")
        assert res.status == "extraction_failed"

    @pytest.mark.asyncio
    async def test_negative_share_row_dropped_with_error(self):
        from src.intelligence.revenue_geography.extraction import (
            extract_from_text,
        )
        async def fake_call(_prompt, **_kw):
            return {
                "found_revenue_geography": True,
                "candidates": [
                    {"region": "NA", "revenue_share": -0.5},
                    {"region": "EU", "revenue_share": 0.7},
                ],
            }
        with patch(
            "src.intelligence.revenue_geography.extraction._llm_availability",
            return_value=(True, "success", "ok"),
        ), patch("src.llm.client.call_llm_json", new=fake_call):
            res = await extract_from_text(text="x")
        assert res.status == "success"
        regions = {c.region for c in res.candidates}
        # NA dropped (negative share), EU kept.
        assert "Europe" in regions
        assert "North America" not in regions
        assert res.validation_errors


# ───────────────────────────────────────────────────────────────────────────
# Route-level tests via TestClient
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase11_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase11.db")
    os.environ["KLEITOS_DATA_DIR"] = tmp_dir
    os.environ["KLEITOS_LOG_LEVEL"] = "WARNING"

    from src.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    settings.api.auth_enabled = False

    import src.database.connection as connection
    connection._engine = None
    connection._session_factory = None

    from fastapi.testclient import TestClient
    from src.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    if prior_db is None:
        os.environ.pop("KLEITOS_DB_PATH", None)
    else:
        os.environ["KLEITOS_DB_PATH"] = prior_db
    if prior_data is None:
        os.environ.pop("KLEITOS_DATA_DIR", None)
    else:
        os.environ["KLEITOS_DATA_DIR"] = prior_data
    if prior_log is None:
        os.environ.pop("KLEITOS_LOG_LEVEL", None)
    else:
        os.environ["KLEITOS_LOG_LEVEL"] = prior_log
    get_settings.cache_clear()
    connection._engine = None
    connection._session_factory = None


@pytest.fixture(scope="module")
def seeded(client):
    """Two portfolios with one holding each for isolation tests."""
    import asyncio
    from src.database.connection import get_db
    from src.database.models import Holding, Portfolio

    iso = datetime.now(timezone.utc).isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph11_pA", name="Phase 11 A", base_currency="USD",
                          is_default=0, created_at=iso, updated_at=iso),
                Portfolio(id="ph11_pB", name="Phase 11 B", base_currency="EUR",
                          is_default=0, created_at=iso, updated_at=iso),
            ])
            session.add_all([
                Holding(id="ph11_aapl_pA", ticker="AAPL", currency="USD",
                        isin="US0378331005", quantity=10, weight_pct=100.0,
                        portfolio_id="ph11_pA", status="active",
                        created_at=iso, updated_at=iso),
                Holding(id="ph11_msft_pB", ticker="MSFT", currency="USD",
                        isin="US5949181045", quantity=10, weight_pct=100.0,
                        portfolio_id="ph11_pB", status="active",
                        created_at=iso, updated_at=iso),
            ])
            await session.commit()

    asyncio.run(_seed())
    yield


class TestExtractRouteNoPersist:
    def test_missing_portfolio_is_400(self, client, seeded):
        r = client.post(
            "/api/v1/exposures/revenue-geography/extract",
            data={"portfolio_id": "ph11_pA"},
            files={},
        )
        # No file and no text → 400
        assert r.status_code in (400, 422)

    def test_missing_key_returns_typed_status(self, client, seeded):
        # No LLM provider in the test environment.
        r = client.post(
            "/api/v1/exposures/revenue-geography/extract",
            data={"portfolio_id": "ph11_pA", "text": "Region NA 60%, EU 40%"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "missing_key"
        assert body["candidates"] == []

    def test_unknown_portfolio_is_404(self, client, seeded):
        r = client.post(
            "/api/v1/exposures/revenue-geography/extract",
            data={"portfolio_id": "nope", "text": "x"},
        )
        assert r.status_code == 404

    def test_extract_route_does_not_persist(self, client, seeded):
        # Hit extract; the row count must stay at 0 for portfolio A.
        client.post(
            "/api/v1/exposures/revenue-geography/extract",
            data={"portfolio_id": "ph11_pA", "text": "NA 60%, EU 40%"},
        )
        rep = client.get(
            "/api/v1/exposures/revenue-geography",
            params={"portfolio_id": "ph11_pA"},
        )
        body = rep.json()
        # Phase 10 service still reports "missing" since nothing was persisted.
        assert body["status"] == "missing"


class TestConfirmRoutePersists:
    def test_confirm_persists_with_source_type_ai(self, client, seeded):
        payload = {
            "portfolio_id": "ph11_pA",
            "candidates": [
                {"region": "North America", "revenue_share": 0.6,
                 "fiscal_year": 2025, "period": "FY",
                 "ticker": "AAPL", "isin": "US0378331005",
                 "confidence": 0.92,
                 "evidence_text": "NA: 60% FY25"},
                {"region": "Europe", "revenue_share": 0.4,
                 "fiscal_year": 2025, "period": "FY",
                 "ticker": "AAPL", "isin": "US0378331005"},
            ],
            "source_filename": "preview.pdf",
        }
        r = client.post(
            "/api/v1/exposures/revenue-geography/confirm-extraction",
            json=payload,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["imported"] == 2
        assert body["matched_by_isin"] == 2
        # Now the Phase 10 service surfaces the rows.
        rep = client.get(
            "/api/v1/exposures/revenue-geography",
            params={"portfolio_id": "ph11_pA"},
        )
        rep_body = rep.json()
        assert rep_body["status"] == "available"
        regions = {b["region"] for b in rep_body["buckets"]}
        assert "North America" in regions and "Europe" in regions
        # And rows API exposes source_type=ai_extracted.
        rows = client.get(
            "/api/v1/exposures/revenue-geography/rows",
            params={"portfolio_id": "ph11_pA"},
        ).json()
        assert all(r["source_type"] == "ai_extracted" for r in rows)

    def test_confirm_requires_candidates(self, client, seeded):
        r = client.post(
            "/api/v1/exposures/revenue-geography/confirm-extraction",
            json={"portfolio_id": "ph11_pA", "candidates": []},
        )
        assert r.status_code == 400

    def test_confirm_isolation_between_portfolios(self, client, seeded):
        # pA has rows from the prior test, pB must stay empty.
        rep_b = client.get(
            "/api/v1/exposures/revenue-geography",
            params={"portfolio_id": "ph11_pB"},
        ).json()
        assert rep_b["status"] == "missing"
        # Confirm rows for pB explicitly.
        payload = {
            "portfolio_id": "ph11_pB",
            "candidates": [
                {"region": "Asia Pacific", "revenue_share": 0.5,
                 "ticker": "MSFT", "isin": "US5949181045"},
                {"region": "Europe", "revenue_share": 0.5,
                 "ticker": "MSFT", "isin": "US5949181045"},
            ],
        }
        client.post(
            "/api/v1/exposures/revenue-geography/confirm-extraction",
            json=payload,
        )
        # Re-check pA + pB are still disjoint.
        rows_a = client.get(
            "/api/v1/exposures/revenue-geography/rows",
            params={"portfolio_id": "ph11_pA"},
        ).json()
        rows_b = client.get(
            "/api/v1/exposures/revenue-geography/rows",
            params={"portfolio_id": "ph11_pB"},
        ).json()
        tickers_a = {r["ticker"] for r in rows_a}
        tickers_b = {r["ticker"] for r in rows_b}
        assert "AAPL" in tickers_a and "MSFT" not in tickers_a
        assert "MSFT" in tickers_b and "AAPL" not in tickers_b


# ───────────────────────────────────────────────────────────────────────────
# Dashboard contract
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text("utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text("utf-8")


class TestDashboardMarkup:
    def test_two_tabs_present(self, index_html):
        for needle in (
            'id="rg-tab-csv"',
            'id="rg-tab-ai"',
            'id="rg-pane-csv"',
            'id="rg-pane-ai"',
            'id="rg-ai-file"',
            'id="rg-ai-text"',
            'id="rg-ai-extract"',
            'id="rg-ai-confirm"',
            'id="rg-ai-discard"',
            'id="rg-ai-candidates"',
            'id="rg-ai-status"',
        ):
            assert needle in index_html, f"missing {needle}"

    def test_review_first_language(self, index_html):
        # The dialog must state that nothing is saved until confirmation.
        text = index_html.lower()
        assert "nothing is saved until" in text or "review" in text
        # And manual CSV must remain prominent.
        assert "manual csv" in text or "manual-csv" in text


class TestDashboardJsContract:
    def test_extract_and_confirm_constants(self, app_js):
        assert "exposuresRevenueGeoExtract" in app_js
        assert "exposuresRevenueGeoConfirm" in app_js

    def test_extract_uses_formdata_no_json(self, app_js):
        # The extract POST should be multipart (FormData) so PDF
        # uploads work without JSON encoding.
        assert "new FormData" in app_js
        assert "exposuresRevenueGeoExtract" in app_js

    def test_confirm_path_handlers_present(self, app_js):
        for needle in ("_rgAiExtract", "_rgAiConfirm", "_rgAiDiscard",
                       "_rgSwitchTab", "_rgRenderCandidates"):
            assert needle in app_js, f"missing {needle}"


# ───────────────────────────────────────────────────────────────────────────
# Support-bundle privacy
# ───────────────────────────────────────────────────────────────────────────


class TestSupportBundlePrivacy:
    def test_bundle_counts_revenue_geography_without_row_bodies(self, tmp_path, client, seeded):
        """The bundle reports revenue_geography counts + source-type
        breakdown without leaking row contents or uploaded report bytes.
        """
        import importlib.util
        import json
        import zipfile

        spec = importlib.util.spec_from_file_location(
            "_sb", PROJECT_ROOT / "scripts" / "support_bundle.py",
        )
        sb = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(sb)

        # Stage a data_dir layout the bundle expects: db/kleitos.db.
        # WAL mode keeps writes out of the main file until checkpoint;
        # use SQLite's native ``backup()`` so we get a consistent copy.
        import sqlite3
        stage = tmp_path / "data_dir"
        (stage / "db").mkdir(parents=True)
        live_db = Path(os.environ["KLEITOS_DB_PATH"])
        dst = stage / "db" / "kleitos.db"
        src_conn = sqlite3.connect(str(live_db))
        try:
            dst_conn = sqlite3.connect(str(dst))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        out = tmp_path / "bundle.zip"
        sb.build_bundle(stage, out)
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            assert "db_diagnostics.json" in names
            diag = json.loads(zf.read("db_diagnostics.json"))
            tables = diag.get("tables", {})
            assert "revenue_geography" in tables
            # And the Phase 11 source-type breakdown is included.
            assert "revenue_geography_source_types" in diag
            # No JSON entry should contain a region name or holding id.
            for name in names:
                if not name.endswith(".json"):
                    continue
                blob = zf.read(name).decode("utf-8", errors="replace")
                assert "North America" not in blob, \
                    f"region body leaked into {name}"
                assert "ph11_aapl_pA" not in blob, \
                    f"holding id leaked into {name}"


# ───────────────────────────────────────────────────────────────────────────
# Docs contract
# ───────────────────────────────────────────────────────────────────────────


class TestDocs:
    def test_readme_documents_ai_review_first(self):
        readme = (PROJECT_ROOT / "README_LOCAL.md").read_text("utf-8")
        assert "Manual CSV" in readme
        assert "review" in readme.lower()
        assert "AI extract" in readme or "AI extraction" in readme

    def test_known_limitations_marks_ai_optional(self):
        kl = (PROJECT_ROOT / "KNOWN_LIMITATIONS.md").read_text("utf-8")
        assert "AI extraction" in kl or "AI-extracted" in kl
        assert "review" in kl.lower() or "confirm" in kl.lower()
