"""Phase 17 — release-artefact + customer-handoff regression tests.

Phase 17 is the final pre-handoff hardening pass: a single version
module, a release-zip manifest, and customer docs that describe the
*shipped* product honestly.  These tests lock that surface:

* Version identity — ``src/version.py`` is the single source of truth;
  ``src.__version__`` re-exports it; ``release_identity()`` reports the
  schema version pulled from the migrations module.
* Release manifest — a built zip carries ``axion/RELEASE_MANIFEST.json``
  with the expected keys and explicit no-secrets / no-database
  guarantees.
* Zip contents — required runtime files are present; forbidden
  artefacts (``.env``, ``*.db``, caches, ``.git/``, stale ``Axion/``)
  are absent.
* Docs — no forbidden positive claims (live prices, broker sync, OAuth
  supported, paid-vendor bundled, ATHEX fully automatic); the required
  customer concepts are all present.
* Support bundle metadata carries the release identity.
* Diagnostics endpoint surfaces version metadata.

All tests are deterministic, offline, and make no real provider calls.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _load_script_module(name: str):
    """Load a ``scripts/*.py`` file as a module (scripts/ is not a pkg)."""
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "scripts" / f"{name}.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_doc(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


#: Customer-facing docs Phase 17 must keep honest.
_CUSTOMER_DOCS = [
    "README_LOCAL.md",
    "INSTALL.md",
    "KNOWN_LIMITATIONS.md",
    "docs/CUSTOMER_QUICKSTART.md",
    "docs/CLIENT_FAQ.md",
    "docs/DEMO_RUNBOOK.md",
    "docs/FINAL_CUSTOMER_HANDOFF.md",
]


# ─────────────────────────────────────────────────────────────────────
# Version metadata contract
# ─────────────────────────────────────────────────────────────────────


class TestVersionMetadata:
    def test_version_module_constants(self):
        from src.version import APP_NAME, APP_VERSION, RELEASE_CHANNEL
        assert APP_NAME == "Axion"
        assert isinstance(APP_VERSION, str) and APP_VERSION
        # Honest non-commercial channel marker.
        assert RELEASE_CHANNEL == "local"

    def test_src_dunder_version_reexports_app_version(self):
        import src
        from src.version import APP_VERSION
        assert src.__version__ == APP_VERSION

    def test_config_default_version_matches_module(self):
        # The single source of truth must flow into the config default.
        from src.config import SystemSettings
        from src.version import APP_VERSION
        assert SystemSettings().version == APP_VERSION

    def test_release_identity_shape(self):
        from src.version import release_identity
        ident = release_identity()
        for key in ("app_name", "app_version", "release_channel",
                    "release_tag", "schema_version"):
            assert key in ident, f"release_identity missing {key}"

    def test_schema_version_matches_migrations(self):
        from src.version import schema_version
        from src.database.migrations import CURRENT_SCHEMA_VERSION
        # Schema number is read from migrations — never duplicated.
        assert schema_version() == CURRENT_SCHEMA_VERSION

    def test_version_is_not_a_bare_unverified_claim(self):
        # Guard: the version string should be a real dotted version,
        # not a placeholder.
        from src.version import APP_VERSION
        assert re.match(r"^\d+\.\d+\.\d+", APP_VERSION), APP_VERSION


# ─────────────────────────────────────────────────────────────────────
# Release-zip manifest + contents
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def built_zip(tmp_path_factory) -> Path:
    """Build the macOS release zip once into a temp dir and yield it."""
    out_dir = tmp_path_factory.mktemp("phase17_release")
    builder = _load_script_module("build_release_zip")
    zip_path = builder.build_zip("macos", out_dir)
    assert zip_path.exists()
    return zip_path


@pytest.fixture(scope="module")
def zip_names(built_zip) -> list[str]:
    with zipfile.ZipFile(built_zip) as zf:
        return zf.namelist()


@pytest.fixture(scope="module")
def manifest(built_zip) -> dict:
    with zipfile.ZipFile(built_zip) as zf:
        return json.loads(zf.read("axion/RELEASE_MANIFEST.json").decode("utf-8"))


class TestReleaseManifest:
    def test_manifest_member_exists(self, zip_names):
        assert "axion/RELEASE_MANIFEST.json" in zip_names

    def test_manifest_required_keys(self, manifest):
        for key in ("app_name", "app_version", "release_channel",
                    "platform_package", "git_commit", "build_timestamp",
                    "file_count", "included_top_level", "guarantees"):
            assert key in manifest, f"manifest missing {key}"

    def test_manifest_identity_matches_version_module(self, manifest):
        from src.version import APP_NAME, APP_VERSION, RELEASE_CHANNEL
        assert manifest["app_name"] == APP_NAME
        assert manifest["app_version"] == APP_VERSION
        assert manifest["release_channel"] == RELEASE_CHANNEL

    def test_manifest_platform_is_macos(self, manifest):
        assert manifest["platform_package"] == "macos"

    def test_manifest_guarantees_mention_no_db_no_keys(self, manifest):
        joined = " ".join(manifest["guarantees"]).lower()
        assert "database" in joined
        assert "api key" in joined or "keys" in joined
        assert ".env" in joined

    def test_manifest_lists_core_top_level_dirs(self, manifest):
        top = set(manifest["included_top_level"])
        for entry in ("src", "dashboard", "config", "docs"):
            assert entry in top, f"manifest top-level missing {entry}"


class TestZipRequiredFiles:
    REQUIRED = [
        "axion/src/main.py",
        "axion/src/version.py",
        "axion/dashboard/index.html",
        "axion/config/sources.yaml",
        "axion/config/relationships.yaml",
        "axion/requirements.txt",
        "axion/scripts/migrate.py",
        "axion/scripts/rotate_logs.py",
        "axion/scripts/support_bundle.py",
        "axion/scripts/smoke_local.py",
        "axion/scripts/run_local.sh",
        "axion/scripts/run_local.ps1",
        "axion/src/intelligence/insights/generator.py",
        "axion/src/intelligence/navigation.py",
        "axion/src/corporate_events/manual_import.py",
        "axion/src/intelligence/revenue_geography/__init__.py",
        "axion/README_LOCAL.md",
        "axion/INSTALL.md",
        "axion/KNOWN_LIMITATIONS.md",
        "axion/docs/FINAL_CUSTOMER_HANDOFF.md",
    ]

    def test_all_required_runtime_files_present(self, zip_names):
        missing = [r for r in self.REQUIRED if r not in zip_names]
        assert not missing, f"release zip is missing: {missing}"


class TestZipForbiddenFiles:
    def test_no_env_secrets_file(self, zip_names):
        # The real local secrets file must never ship; .env.template may.
        bad = [n for n in zip_names
               if n.split("/")[-1] in (".env", ".kleitos.env", ".axion.env")]
        assert not bad, f"secrets file leaked into zip: {bad}"

    def test_no_database_files(self, zip_names):
        bad = [n for n in zip_names
               if n.endswith((".db", ".db-wal", ".db-shm"))]
        assert not bad, f"database files leaked into zip: {bad}"

    def test_no_caches_or_vcs(self, zip_names):
        for pattern in ("__pycache__/", ".git/", ".pytest_cache/",
                        ".mypy_cache/", ".ruff_cache/", ".venv/"):
            bad = [n for n in zip_names if pattern in n]
            assert not bad, f"{pattern} leaked into zip: {bad[:3]}"

    def test_no_stale_duplicate_dirs(self, zip_names):
        # The stale top-level Axion/ and Axion-Installers/ dirs never ship.
        bad = [n for n in zip_names
               if n.startswith(("axion/Axion/", "axion/Axion-Installers/"))]
        assert not bad, f"stale duplicate dir leaked: {bad[:3]}"

    def test_no_runtime_data_dirs(self, zip_names):
        for pattern in ("axion-data/", "kleitos-data/", "/support/",
                        "test-results/", "/dist/"):
            bad = [n for n in zip_names if pattern in n]
            assert not bad, f"runtime data {pattern} leaked into zip: {bad[:3]}"

    def test_no_obvious_secret_values_in_member_names(self, zip_names):
        # Defensive: no zip member name looks like an embedded key.
        for n in zip_names:
            low = n.lower()
            assert "sk-ant-" not in low
            assert "sk-proj-" not in low
            assert "bearer " not in low


# ─────────────────────────────────────────────────────────────────────
# Docs — no forbidden claims
# ─────────────────────────────────────────────────────────────────────


#: Positive-assertion phrases that are inherently a *false claim* for
#: this product.  Each is written in assertion form so an honest
#: negated sentence ("OAuth is *not* supported") does not contain it as
#: a substring — they may never appear verbatim in any customer doc.
_NEVER_PHRASES = [
    "oauth is supported",
    "oauth is implemented",
    "oauth is available",
    "broker sync is supported",
    "broker sync is available",
    "bloomberg is included",
    "bloomberg data is included",
    "factset is included",
    "refinitiv is included",
    "live price feed is included",
    "real-time prices are included",
    "athex automation is fully supported",
]

#: Sensitive terms that are acceptable *only* inside an honest
#: disclaimer.  Checked at paragraph granularity so soft-wrapped
#: markdown lines don't split a negation away from the term.
_CONTEXT_SENSITIVE_TERMS = (
    "live price", "live market", "real-time price",
    "broker sync", "broker connection",
)

#: Negation / roadmap tokens that make a sensitive mention honest.
_NEGATION_TOKENS = (
    "no ", "not ", "never", "without", "n't", "does not", "do not",
    "isn't", "aren't", "roadmap", "unsupported", "not yet",
    "not implemented", "not a substitute", "cannot", "requires ",
)


class TestDocsNoForbiddenClaims:
    def test_no_never_phrases(self):
        for rel in _CUSTOMER_DOCS:
            low = _read_doc(rel).lower()
            for phrase in _NEVER_PHRASES:
                assert phrase not in low, (
                    f"{rel} contains forbidden claim: {phrase!r}"
                )

    def test_sensitive_terms_only_in_disclaimer_paragraphs(self):
        # Markdown paragraphs are blank-line separated; check each
        # paragraph that mentions a sensitive term carries a negation.
        for rel in _CUSTOMER_DOCS:
            paragraphs = _read_doc(rel).split("\n\n")
            for para in paragraphs:
                low = para.lower()
                for term in _CONTEXT_SENSITIVE_TERMS:
                    if term in low:
                        assert any(tok in low for tok in _NEGATION_TOKENS), (
                            f"{rel}: {term!r} appears without a clear "
                            f"disclaimer in:\n{para.strip()[:200]}"
                        )

    def test_oauth_is_documented_as_not_implemented(self):
        # OAuth must be present in the docs *and* always framed as
        # roadmap / not-implemented — never as a shipped capability.
        faq = _read_doc("docs/CLIENT_FAQ.md").lower()
        known = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "oauth" in faq and "oauth" in known
        # The FAQ answer + Known Limitations both say it is not shipped.
        assert "does not yet integrate" in faq or "no. axion" in faq
        assert "does not yet ship any oauth" in known \
            or "oauth integration" in known


# ─────────────────────────────────────────────────────────────────────
# Docs — required final concepts present
# ─────────────────────────────────────────────────────────────────────


class TestDocsRequiredConcepts:
    def test_handoff_doc_exists(self):
        assert (PROJECT_ROOT / "docs" / "FINAL_CUSTOMER_HANDOFF.md").exists()

    def test_handoff_covers_news_vs_events(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "news" in doc and "events" in doc
        # The distinction is stated explicitly.
        assert "corporate-events calendar" in doc or "corporate events" in doc

    def test_handoff_covers_listing_vs_revenue_geography(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "listing country" in doc
        assert "revenue geography" in doc

    def test_handoff_states_ai_optional(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "without any ai" in doc or "ai is optional" in doc \
            or "works without" in doc or "deterministic" in doc

    def test_handoff_covers_support_bundle(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "support_bundle.py" in doc or "support bundle" in doc

    def test_handoff_covers_backups(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "backup" in doc

    def test_handoff_states_no_live_prices(self):
        doc = _read_doc("docs/FINAL_CUSTOMER_HANDOFF.md").lower()
        assert "no live market-price feed" in doc or "no live price" in doc

    def test_known_limitations_still_honest(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "oauth" in doc          # OAuth limitation documented
        assert "live price" in doc     # no-live-prices documented


# ─────────────────────────────────────────────────────────────────────
# Support bundle + diagnostics carry the release identity
# ─────────────────────────────────────────────────────────────────────


class TestSupportBundleVersion:
    def test_bundle_metadata_carries_release_identity(self, tmp_path):
        data_dir = tmp_path / "data"
        (data_dir / "db").mkdir(parents=True)
        env = {
            **os.environ,
            "AXION_DATA_DIR": str(data_dir),
            "AXION_DB_PATH": str(data_dir / "db" / "kleitos.db"),
            "KLEITOS_DATA_DIR": str(data_dir),
            "KLEITOS_DB_PATH": str(data_dir / "db" / "kleitos.db"),
        }
        proc = subprocess.run(
            [sys.executable, "scripts/support_bundle.py"],
            cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        bundles = sorted((data_dir / "support").glob("axion-support-*.zip"))
        assert bundles, "no support bundle produced"
        with zipfile.ZipFile(bundles[-1]) as zf:
            meta = json.loads(zf.read("metadata.json").decode("utf-8"))
            names = zf.namelist()
        from src.version import APP_VERSION, RELEASE_CHANNEL
        assert meta.get("app_version") == APP_VERSION
        assert meta.get("release_channel") == RELEASE_CHANNEL
        # And it still excludes the database itself.
        assert not [n for n in names if n.endswith(".db")]


class TestDiagnosticsVersion:
    def test_diagnostics_includes_version_metadata(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        (data_dir / "db").mkdir(parents=True)
        monkeypatch.setenv("AXION_DATA_DIR", str(data_dir))
        monkeypatch.setenv("AXION_DB_PATH", str(data_dir / "db" / "kleitos.db"))
        monkeypatch.setenv("KLEITOS_DATA_DIR", str(data_dir))
        monkeypatch.setenv("KLEITOS_DB_PATH", str(data_dir / "db" / "kleitos.db"))

        from src.config import get_settings
        import src.database.connection as connection
        get_settings.cache_clear()
        settings = get_settings()
        settings.api.auth_enabled = False
        connection.reset_connection_state()

        from fastapi.testclient import TestClient
        from src.main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/v1/system/diagnostics")
        assert r.status_code == 200, r.text
        body = r.json()
        from src.version import APP_VERSION
        assert body.get("app_version") == APP_VERSION
        assert body.get("release_channel") == "local"

        get_settings.cache_clear()
        connection.reset_connection_state()
