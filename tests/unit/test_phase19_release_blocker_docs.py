"""Phase 19 — release-blocker documentation-drift regression tests.

Phase 19 corrected two evidence-confirmed drift items in
``KNOWN_LIMITATIONS.md``:

1. The "Default News Sources" section claimed "6 enabled RSS sources …
   and CNBC". The real count (``config/sources.yaml``) is **7 enabled**
   and **CNBC is disabled**.
2. The "Automated Price Data" section claimed the ``price_history`` and
   ``portfolio_snapshots`` tables "exist". Neither table exists in the
   schema (v11).

These tests lock the corrections in and cross-check the customer-facing
News-source claim against ``config/sources.yaml`` so future source
toggles cannot silently re-introduce drift. They also re-assert the
honest negatives the audit must never lose (no live prices, no broker
sync, OAuth roadmap-only, ATHEX + SEC EDGAR unsupported).

All checks are deterministic and offline — they only read repo files.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _read_doc(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def _enabled_sources() -> list[dict]:
    """Parse config/sources.yaml and return the enabled source rows."""
    data = yaml.safe_load(_read_doc("config/sources.yaml"))
    return [s for s in data.get("sources", []) if s.get("enabled")]


def _known_limitations_section(heading: str) -> str:
    """Return the text of one '### <heading>' section of KNOWN_LIMITATIONS.md."""
    doc = _read_doc("KNOWN_LIMITATIONS.md")
    lines = doc.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("### ") and heading in line:
            capturing = True
            continue
        if capturing and (line.startswith("### ") or line.startswith("## ")):
            break
        if capturing:
            out.append(line)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# Drift item 1 — News-source count / CNBC
# ─────────────────────────────────────────────────────────────────────


class TestNewsSourceDrift:
    def test_no_stale_six_enabled_claim(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md")
        assert "6 enabled RSS sources" not in doc, (
            "stale '6 enabled RSS sources' claim still present"
        )

    def test_default_news_sources_section_says_seven(self):
        section = _known_limitations_section("Default News Sources")
        assert section.strip(), "Default News Sources section not found"
        assert "7 enabled" in section, (
            "Default News Sources section must state 7 enabled sources"
        )

    def test_cnbc_not_listed_as_enabled(self):
        # CNBC is disabled in config/sources.yaml. Wherever the Default
        # News Sources section mentions CNBC, it must be in a 'disabled'
        # context — never presented as a shipped/enabled feed.
        section = _known_limitations_section("Default News Sources")
        if "CNBC" in section:
            assert "disabled" in section.lower(), (
                "CNBC is mentioned but the section does not call it disabled"
            )

    def test_enabled_count_matches_config(self):
        # Lock-in: the count claimed in the doc must equal the real
        # `enabled: true` count in config/sources.yaml.
        enabled = _enabled_sources()
        section = _known_limitations_section("Default News Sources")
        assert f"{len(enabled)} enabled" in section, (
            f"config/sources.yaml has {len(enabled)} enabled sources but "
            f"KNOWN_LIMITATIONS.md does not state that count"
        )

    def test_cnbc_disabled_in_config(self):
        # Guard the premise: if CNBC ever becomes enabled, this test
        # fails so the doc must be revisited deliberately.
        enabled_ids = {s["id"] for s in _enabled_sources()}
        assert "cnbc-rss" not in enabled_ids, (
            "cnbc-rss is now enabled in config/sources.yaml — "
            "revisit the KNOWN_LIMITATIONS.md News-source paragraph"
        )

    def test_named_enabled_sources_appear_in_doc(self):
        # Each genuinely-enabled source should be recognisable in the
        # Default News Sources section by a stable keyword.
        section = _known_limitations_section("Default News Sources").lower()
        keywords = {
            "fed-rss": "federal reserve",
            "ecb-rss": "ecb",
            "google-news-business": "google news",
            "wsj-markets": "wsj",
            "marketwatch-rss": "marketwatch",
            "seekingalpha-rss": "seeking alpha",
            "investing-rss": "investing.com",
        }
        enabled_ids = {s["id"] for s in _enabled_sources()}
        for sid, kw in keywords.items():
            if sid in enabled_ids:
                assert kw in section, (
                    f"enabled source {sid} not represented in the doc "
                    f"(expected keyword {kw!r})"
                )


# ─────────────────────────────────────────────────────────────────────
# Drift item 2 — nonexistent price tables
# ─────────────────────────────────────────────────────────────────────


class TestPriceTableDrift:
    def test_does_not_claim_price_tables_exist(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md")
        # The exact stale sentence must be gone.
        assert "database tables exist" not in doc, (
            "KNOWN_LIMITATIONS.md still claims price tables 'exist'"
        )

    def test_price_table_names_only_in_negative_context(self):
        # If price_history / portfolio_snapshots are named at all, the
        # paragraph must frame them as NOT present.
        doc = _read_doc("KNOWN_LIMITATIONS.md")
        for para in doc.split("\n\n"):
            low = para.lower()
            if "price_history" in low or "portfolio_snapshots" in low:
                assert "no " in low or "not " in low or "never" in low, (
                    "price_history / portfolio_snapshots mentioned without "
                    f"a clear 'not present' framing:\n{para.strip()[:200]}"
                )

    def test_price_tables_absent_from_schema(self):
        # Guard the premise: these tables genuinely do not exist.
        models = _read_doc("src/database/models.py")
        migrations = _read_doc("src/database/migrations.py")
        assert '"price_history"' not in models
        assert '"portfolio_snapshots"' not in models
        assert "price_history" not in migrations
        assert "portfolio_snapshots" not in migrations

    def test_no_live_price_feed_statement_preserved(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "no live market price feed" in doc, (
            "the honest 'no live market price feed' statement was lost"
        )


# ─────────────────────────────────────────────────────────────────────
# Honest negatives must survive the edit
# ─────────────────────────────────────────────────────────────────────


class TestHonestNegativesPreserved:
    def test_no_live_prices(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "live price" in doc and (
            "no live" in doc or "not show live" in doc
        )

    def test_oauth_roadmap_only(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "oauth" in doc
        assert "not implemented" in doc or "does not yet ship any oauth" in doc

    def test_no_broker_sync(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "broker sync" in doc

    def test_athex_unsupported(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "athex" in doc
        assert "not enabled" in doc or "unsupported" in doc

    def test_sec_edgar_unsupported(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md").lower()
        assert "sec edgar" in doc or "edgar" in doc
        # The SEC EDGAR section frames it as not-included / not-implemented.
        assert ("not included" in doc or "not implemented" in doc
                or "unsupported" in doc)
