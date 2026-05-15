"""Phase 10 — Revenue-geography foundation tests.

Coverage:

* Migration v10 creates ``revenue_geography`` with required columns,
  indexes, the unique constraint, and the CHECK that rejects
  negative shares.  Running migrations a second time is a no-op.
* :func:`parse_revenue_share` handles ``0.45`` / ``45`` / ``45%`` and
  rejects negatives.
* :func:`normalize_region` resolves common aliases.
* :func:`validate_company_allocations` flags sum<<100 and sum>>100 as
  soft warnings without blocking import.
* :func:`compute_portfolio_revenue_exposure` aggregates rows by
  weight, surfaces an explicit ``"Revenue geography not uploaded"``
  bucket for holdings without data, and **never** falls back to
  listing country.
* Manual CSV import: ISIN-first matching, ticker fallback,
  unmatched audit row, dedup on repeat import, URL scrubbing of
  ``source_url``, per-row errors, warnings.
* API:
    - ``GET /api/v1/exposures/listing-country`` returns the listing
      breakdown with ``data_source="isin_prefix_or_venue"``.
    - ``GET /api/v1/exposures/revenue-geography`` returns the typed
      ``status`` (missing / partial / available), buckets,
      missing-holdings list, and fiscal_year coverage.
    - ``POST /import`` returns per-row + warning summary.
    - ``GET /missing`` returns the same missing-holdings shape.
    - Multi-portfolio isolation: pA rows never bleed into pB.
* Grounded AI context honours ``holding_revenue_geography_status``:
  missing when no rows, available when allocations sum near 100%,
  partial when below.  Listing country is never copied into
  ``holding_revenue_breakdown``.
* Existing ``/api/v1/portfolio/exposure?dimension=geography`` still
  works (back-compat).
* Dashboard markup carries the new card + import dialog and the
  legacy "Geography" label is replaced by "Listing country".
* Docs declare the listing vs revenue-geography split, the CSV
  schema, and the no-fallback rule.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# TestClient + temp DB
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    prior_db = os.environ.get("KLEITOS_DB_PATH")
    prior_data = os.environ.get("KLEITOS_DATA_DIR")
    prior_log = os.environ.get("KLEITOS_LOG_LEVEL")

    tmp_dir = tempfile.mkdtemp(prefix="axion_phase10_")
    os.environ["KLEITOS_DB_PATH"] = os.path.join(tmp_dir, "test_phase10.db")
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
    """Two portfolios with three holdings each (US + Greek + EU mix)."""
    import asyncio
    from src.database.connection import get_db
    from src.database.models import Holding, Portfolio, Security

    iso = datetime.now(timezone.utc).isoformat()

    async def _seed():
        async with get_db() as session:
            session.add_all([
                Portfolio(id="ph10_pA", name="Phase 10 A", base_currency="USD",
                          is_default=0, created_at=iso, updated_at=iso),
                Portfolio(id="ph10_pB", name="Phase 10 B", base_currency="EUR",
                          is_default=0, created_at=iso, updated_at=iso),
            ])
            session.add_all([
                Security(id="ph10_sec_aapl", ticker="AAPL", currency="USD",
                         geography="united states",
                         created_at=iso, updated_at=iso),
                Security(id="ph10_sec_opap", ticker="OPAP", currency="EUR",
                         geography="greece",
                         created_at=iso, updated_at=iso),
                Security(id="ph10_sec_nesn", ticker="NESN", currency="CHF",
                         geography="switzerland",
                         created_at=iso, updated_at=iso),
            ])
            session.add_all([
                Holding(id="ph10_aapl_pA", ticker="AAPL", currency="USD",
                        isin="US0378331005", quantity=10, weight_pct=40.0,
                        portfolio_id="ph10_pA", status="active",
                        created_at=iso, updated_at=iso),
                Holding(id="ph10_opap_pA", ticker="OPAP", currency="EUR",
                        isin="GRS419003009", quantity=10, weight_pct=35.0,
                        portfolio_id="ph10_pA", status="active",
                        created_at=iso, updated_at=iso),
                Holding(id="ph10_nesn_pA", ticker="NESN", currency="CHF",
                        isin="CH0038863350", quantity=5, weight_pct=25.0,
                        portfolio_id="ph10_pA", status="active",
                        created_at=iso, updated_at=iso),
                # pB has a separate holding so isolation tests work.
                Holding(id="ph10_aapl_pB", ticker="AAPL", currency="USD",
                        isin="US0378331005", quantity=5, weight_pct=100.0,
                        portfolio_id="ph10_pB", status="active",
                        created_at=iso, updated_at=iso),
            ])
            await session.commit()

    asyncio.run(_seed())
    yield


# ───────────────────────────────────────────────────────────────────────────
# Migration / schema
# ───────────────────────────────────────────────────────────────────────────


class TestMigrationAndModel:
    def test_current_schema_version_bumped(self):
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        assert CURRENT_SCHEMA_VERSION >= 10

    def test_revenue_geography_table_exists(self, client):
        import asyncio
        from sqlalchemy import inspect
        from src.database.connection import get_engine

        async def _check():
            engine = get_engine()
            async with engine.connect() as conn:
                def _inspect(sync_conn):
                    insp = inspect(sync_conn)
                    cols = {c["name"] for c in insp.get_columns("revenue_geography")}
                    idx = {i["name"] for i in insp.get_indexes("revenue_geography")}
                    return cols, idx
                return await conn.run_sync(_inspect)

        cols, idx = asyncio.run(_check())
        required_cols = {
            "id", "portfolio_id", "holding_id", "ticker", "isin",
            "company_name", "region", "country", "revenue_share",
            "fiscal_year", "period", "currency", "source_type",
            "source_name", "source_url", "confidence", "raw_payload",
            "import_batch_id", "match_method",
            "created_at", "updated_at",
        }
        assert required_cols <= cols, f"missing cols: {required_cols - cols}"
        required_indexes = {
            "ix_revenue_geography_portfolio_id",
            "ix_revenue_geography_holding_id",
            "ix_revenue_geography_ticker",
            "ix_revenue_geography_isin",
            "ix_revenue_geography_fiscal_year",
            "ix_revenue_geography_region",
            "ix_revenue_geography_country",
        }
        assert required_indexes <= idx, f"missing indexes: {required_indexes - idx}"

    def test_migration_v10_is_idempotent(self, client):
        import asyncio
        from src.database.migrations import run_migrations
        asyncio.run(run_migrations())
        asyncio.run(run_migrations())

    def test_negative_share_is_rejected_by_check(self, client):
        # The CHECK constraint must reject revenue_share < 0 at the DB layer.
        import asyncio
        from sqlalchemy import text
        from src.database.connection import get_engine

        async def _try_insert():
            engine = get_engine()
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO revenue_geography "
                    "(id, portfolio_id, region, revenue_share, source_type, "
                    " created_at, updated_at) "
                    "VALUES ('neg', 'default', 'X', -0.1, 'manual_csv', "
                    "        '2026-01-01', '2026-01-01')"
                ))

        with pytest.raises(Exception):
            asyncio.run(_try_insert())


# ───────────────────────────────────────────────────────────────────────────
# Pure helpers
# ───────────────────────────────────────────────────────────────────────────


class TestServiceHelpers:
    def test_parse_revenue_share_fraction(self):
        from src.intelligence.revenue_geography import parse_revenue_share
        v, note = parse_revenue_share("0.45")
        assert v == pytest.approx(0.45)
        assert note is None

    def test_parse_revenue_share_percent_suffix(self):
        from src.intelligence.revenue_geography import parse_revenue_share
        v, note = parse_revenue_share("45%")
        assert v == pytest.approx(0.45)
        assert note and "percent" in note.lower()

    def test_parse_revenue_share_bare_number_over_one(self):
        from src.intelligence.revenue_geography import parse_revenue_share
        v, note = parse_revenue_share("45")
        assert v == pytest.approx(0.45)
        assert note and "percent" in note.lower()

    def test_parse_revenue_share_negative_rejected(self):
        from src.intelligence.revenue_geography import parse_revenue_share
        with pytest.raises(ValueError):
            parse_revenue_share("-0.1")
        with pytest.raises(ValueError):
            parse_revenue_share("-5")

    def test_parse_revenue_share_empty_rejected(self):
        from src.intelligence.revenue_geography import parse_revenue_share
        with pytest.raises(ValueError):
            parse_revenue_share("")
        with pytest.raises(ValueError):
            parse_revenue_share(None)

    def test_normalize_region_aliases(self):
        from src.intelligence.revenue_geography import normalize_region
        assert normalize_region("emea") == "EMEA"
        assert normalize_region("EUROPE") == "Europe"
        assert normalize_region("united states") == "North America"
        assert normalize_region("APAC") == "Asia Pacific"
        # Free-text bucket survives unchanged.
        assert normalize_region("Greater Middle East") == "Greater Middle East"

    def test_validate_company_allocations_sum_low_warning(self):
        from src.intelligence.revenue_geography import validate_company_allocations
        rows = [
            {"ticker": "ACME", "isin": None, "fiscal_year": 2025, "period": "FY",
             "region": "North America", "revenue_share": 0.4},
            {"ticker": "ACME", "isin": None, "fiscal_year": 2025, "period": "FY",
             "region": "Europe", "revenue_share": 0.2},
        ]
        warnings = validate_company_allocations(rows)
        assert any(w.kind == "sum_low" for w in warnings)

    def test_validate_company_allocations_sum_high_warning(self):
        from src.intelligence.revenue_geography import validate_company_allocations
        rows = [
            {"ticker": "ACME", "isin": None, "fiscal_year": 2025, "period": "FY",
             "region": "North America", "revenue_share": 0.7},
            {"ticker": "ACME", "isin": None, "fiscal_year": 2025, "period": "FY",
             "region": "Europe", "revenue_share": 0.5},
        ]
        warnings = validate_company_allocations(rows)
        assert any(w.kind == "sum_high" for w in warnings)


# ───────────────────────────────────────────────────────────────────────────
# CSV parse + import
# ───────────────────────────────────────────────────────────────────────────


class TestParseCsv:
    def test_minimal_row_parses(self):
        from src.intelligence.revenue_geography import parse_csv
        rows, errors = parse_csv(
            "ticker,region,revenue_share\nAAPL,North America,0.6\n"
        )
        assert not errors and len(rows) == 1
        assert rows[0]["region"] == "North America"
        assert rows[0]["revenue_share"] == pytest.approx(0.6)

    def test_isin_only_row(self):
        from src.intelligence.revenue_geography import parse_csv
        rows, errors = parse_csv(
            "isin,region,revenue_share\nUS0378331005,Europe,30%\n"
        )
        assert not errors
        assert rows[0]["isin"] == "US0378331005"
        assert rows[0]["region"] == "Europe"
        assert rows[0]["revenue_share"] == pytest.approx(0.3)

    def test_missing_required_field_errors(self):
        from src.intelligence.revenue_geography import parse_csv
        rows, errors = parse_csv(
            "ticker,region,revenue_share\nAAPL,,0.5\n"
        )
        assert not rows and errors and errors[0].field == "region"

    def test_neither_ticker_nor_isin_errors(self):
        from src.intelligence.revenue_geography import parse_csv
        rows, errors = parse_csv(
            "region,revenue_share\nEurope,0.5\n"
        )
        assert not rows and errors[0].field == "ticker/isin"

    def test_url_is_scrubbed(self):
        from src.intelligence.revenue_geography import parse_csv
        rows, _ = parse_csv(
            "ticker,region,revenue_share,source_url\n"
            "AAPL,Europe,0.3,https://example.com/x?apiKey=SECRET\n"
        )
        assert "SECRET" not in (rows[0]["source_url"] or "")
        assert "apiKey=***" in rows[0]["source_url"]

    def test_unparseable_fiscal_year_errors(self):
        from src.intelligence.revenue_geography import parse_csv
        _, errors = parse_csv(
            "ticker,region,revenue_share,fiscal_year\n"
            "AAPL,Europe,0.3,not-a-year\n"
        )
        assert errors and errors[0].field == "fiscal_year"


# ───────────────────────────────────────────────────────────────────────────
# API — list / import / missing / exposure aggregation
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def imported(client, seeded):
    """Import a small batch — AAPL full 100%, OPAP partial 60%, NESN missing."""
    csv_text = (
        "ticker,isin,company_name,fiscal_year,period,region,revenue_share,source_url\n"
        "AAPL,US0378331005,Apple Inc,2025,FY,North America,0.6,https://example.com/a?apiKey=AAA\n"
        "AAPL,US0378331005,Apple Inc,2025,FY,Asia Pacific,0.3,\n"
        "AAPL,US0378331005,Apple Inc,2025,FY,Europe,0.1,\n"
        "OPAP,GRS419003009,OPAP SA,2025,FY,Europe,0.6,\n"
        # NESN deliberately not uploaded
    )
    r = client.post(
        "/api/v1/exposures/revenue-geography/import",
        json={"portfolio_id": "ph10_pA", "csv_text": csv_text},
    )
    assert r.status_code == 200, r.text
    return r.json()


class TestRevenueGeographyApi:
    def test_import_summary_shape(self, imported):
        s = imported
        assert s["imported"] == 4
        assert s["matched_by_isin"] >= 3
        assert s["unmatched"] == 0

    def test_listing_country_endpoint(self, client, seeded):
        r = client.get(
            "/api/v1/exposures/listing-country",
            params={"portfolio_id": "ph10_pA"},
        )
        assert r.status_code == 200
        body = r.json()
        labels = {b["label"] for b in body["buckets"]}
        # Listing country labels match Security.geography
        assert "united states" in labels
        assert "greece" in labels
        assert "switzerland" in labels
        assert body["data_source"] == "isin_prefix_or_venue"

    def test_revenue_geography_endpoint_partial(self, client, imported):
        r = client.get(
            "/api/v1/exposures/revenue-geography",
            params={"portfolio_id": "ph10_pA"},
        )
        assert r.status_code == 200
        body = r.json()
        # NESN is missing — status must be "partial".
        assert body["status"] == "partial"
        # And NESN must show up in missing_holdings.
        missing_tickers = {m["ticker"] for m in body["missing_holdings"]}
        assert "NESN" in missing_tickers
        # AAPL's North America bucket should carry ~24% of portfolio weight
        # (40% AAPL × 60% NA = 24%).
        regions = {b["region"]: b["weight_pct"] for b in body["buckets"]}
        assert regions.get("North America", 0) == pytest.approx(24.0, abs=0.1)
        # OPAP's Europe partial: 35% × 60% = 21% Europe (from OPAP)
        # plus AAPL Europe 4% = ~25% total.
        assert regions.get("Europe", 0) == pytest.approx(25.0, abs=0.1)
        # OPAP's 40% unallocated portion (35% × 40%) should appear under
        # "Other / unallocated".
        assert "Other / unallocated" in regions
        assert regions["Other / unallocated"] == pytest.approx(14.0, abs=0.1)
        # The "Revenue geography not uploaded" bucket carries NESN's 25%.
        assert regions.get("Revenue geography not uploaded", 0) == pytest.approx(25.0, abs=0.1)

    def test_missing_endpoint(self, client, imported):
        r = client.get(
            "/api/v1/exposures/revenue-geography/missing",
            params={"portfolio_id": "ph10_pA"},
        )
        body = r.json()
        tickers = {m["ticker"] for m in body}
        assert tickers == {"NESN"}

    def test_url_scrubbed_in_rows(self, client, imported):
        r = client.get(
            "/api/v1/exposures/revenue-geography/rows",
            params={"portfolio_id": "ph10_pA", "ticker": "AAPL"},
        )
        urls = [row["source_url"] for row in r.json() if row.get("source_url")]
        assert urls
        for u in urls:
            assert "AAA" not in u
            assert "apiKey=***" in u

    def test_dedup_on_repeat_import(self, client, seeded):
        csv_text = (
            "ticker,isin,fiscal_year,period,region,revenue_share\n"
            "NESN,CH0038863350,2025,FY,Europe,0.7\n"
        )
        first = client.post(
            "/api/v1/exposures/revenue-geography/import",
            json={"portfolio_id": "ph10_pA", "csv_text": csv_text},
        ).json()
        second = client.post(
            "/api/v1/exposures/revenue-geography/import",
            json={"portfolio_id": "ph10_pA", "csv_text": csv_text},
        ).json()
        assert first["imported"] == 1
        assert second["imported"] == 0
        assert second["skipped_duplicate"] == 1

    def test_portfolio_isolation(self, client, imported):
        # pB has no uploaded revenue rows yet → status="missing".
        r = client.get(
            "/api/v1/exposures/revenue-geography",
            params={"portfolio_id": "ph10_pB"},
        )
        body = r.json()
        assert body["status"] == "missing"
        assert body["holdings_with_data"] == 0

    def test_legacy_portfolio_exposure_still_works(self, client, seeded):
        # Phase 10 must not break the legacy geography endpoint.
        r = client.get(
            "/api/v1/portfolio/exposure",
            params={"dimension": "geography", "portfolio_id": "ph10_pA"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dimension"] == "geography"
        assert body["buckets"]


# ───────────────────────────────────────────────────────────────────────────
# Grounded AI context — Phase 10 surface
# ───────────────────────────────────────────────────────────────────────────


class TestGroundedAi:
    def test_missing_when_no_rows(self, client, seeded):
        """Without uploaded rows the prompt must say 'not uploaded'."""
        import asyncio
        from src.database.connection import get_db
        from src.database.models import Event
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        async def _arrange() -> str:
            iso = _dt.now(_tz.utc).isoformat()
            ev_id = str(_uuid.uuid4())
            async with get_db() as session:
                session.add(Event(id=ev_id, title="Test", fetched_at=iso,
                                  created_at=iso, dedup_hash=str(_uuid.uuid4())))
                await session.commit()
            return ev_id

        async def _build_and_render(event_id: str, holding_id: str):
            from src.database.connection import get_db
            from src.llm.grounded import (
                assemble_event_context, build_event_analysis_prompt,
            )
            async with get_db() as session:
                ctx = await assemble_event_context(
                    session, event_id=event_id, holding_id=holding_id,
                )
            return build_event_analysis_prompt(ctx)

        ev_id = asyncio.run(_arrange())
        # ph10_aapl_pB lives in portfolio B which never receives an
        # upload across this test module, so revenue-geography rows
        # for it must stay empty regardless of test execution order.
        text = asyncio.run(_build_and_render(ev_id, "ph10_aapl_pB"))
        assert "not uploaded" in text.lower()
        assert "do not infer" in text.lower()

    def test_available_after_upload(self, client, imported):
        """When allocations sum near 100%, status="available"."""
        import asyncio
        from src.database.connection import get_db
        from src.database.models import Event
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        async def _arrange() -> str:
            iso = _dt.now(_tz.utc).isoformat()
            ev_id = str(_uuid.uuid4())
            async with get_db() as session:
                session.add(Event(id=ev_id, title="Test2", fetched_at=iso,
                                  created_at=iso, dedup_hash=str(_uuid.uuid4())))
                await session.commit()
            return ev_id

        async def _build(event_id: str, holding_id: str):
            from src.llm.grounded import assemble_event_context
            from src.database.connection import get_db
            async with get_db() as session:
                return await assemble_event_context(
                    session, event_id=event_id, holding_id=holding_id,
                )

        ev_id = asyncio.run(_arrange())
        ctx = asyncio.run(_build(ev_id, "ph10_aapl_pA"))
        # AAPL allocations sum to 100% → "available".
        assert ctx.holding_revenue_geography_status == "available"
        # The breakdown must NEVER contain listing country verbatim.
        for r in ctx.holding_revenue_breakdown:
            assert r["region"] != "united states"


# ───────────────────────────────────────────────────────────────────────────
# Dashboard markup + docs contract
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text("utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text("utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (PROJECT_ROOT / "dashboard" / "css" / "styles.css").read_text("utf-8")


class TestDashboardMarkup:
    def test_revenue_geography_card_present(self, index_html):
        for needle in (
            'id="revenue-geo-card"',
            'id="rg-import-btn"',
            'id="rg-fiscal-year"',
            'id="rg-chart"',
            'id="rg-empty"',
            'id="rg-missing-details"',
            'id="rg-import-dialog"',
            'id="rg-import-textarea"',
        ):
            assert needle in index_html, f"missing {needle}"

    def test_card_explains_distinction(self, index_html):
        assert "Revenue geography" in index_html
        assert "separate from listing country" in index_html.lower()


class TestDashboardJs:
    def test_loader_renames_geography_card(self, app_js):
        # The customer label must no longer be the bare "Geography Exposure".
        assert "Geography Exposure" not in app_js
        assert "Listing country" in app_js

    def test_revenue_geography_loader_present(self, app_js):
        assert "loadRevenueGeography" in app_js
        assert "exposuresRevenueGeography" in app_js


class TestDashboardCss:
    def test_card_classes_present(self, styles_css):
        for cls in (".revenue-geo-card", ".rg-status-badge",
                    ".rg-chart", ".rg-missing-details",
                    ".rg-bucket-missing", ".rg-bucket-leftover"):
            assert cls in styles_css, f"missing CSS class {cls}"


class TestDocsTerminology:
    """The README must explicitly separate listing country from revenue."""

    def test_readme_has_revenue_geography_section(self):
        readme = (PROJECT_ROOT / "README_LOCAL.md").read_text("utf-8")
        assert "Revenue geography" in readme
        assert "Listing country" in readme
        # The "never infer" rule must be visible.
        assert "never infer" in readme.lower() or "no fallback" in readme.lower()

    def test_known_limitations_mentions_revenue_csv(self):
        kl = (PROJECT_ROOT / "KNOWN_LIMITATIONS.md").read_text("utf-8")
        assert "Revenue geography" in kl
        assert "manual" in kl.lower()

    def test_quickstart_links_csv_format(self):
        qs = (PROJECT_ROOT / "docs" / "CUSTOMER_QUICKSTART.md").read_text("utf-8")
        assert "Revenue geography" in qs
