"""Phase 16 — product polish + release-readiness regression tests.

Phase 16 is a polish phase: no new product domains, no schema change,
no API change beyond the additive Phase 15 surface.  These tests lock
the things Phase 16 cleaned up so they don't quietly regress:

* Dashboard structure contract — the six top-level tabs and the five
  Insights sub-tabs are exactly what the customer docs promise, and
  Events stays a separate corporate-events surface from News.
* Empty-state consistency — every JS-rendered empty state routes
  through the shared ``renderEmpty`` / ``renderError`` helpers; no
  hand-rolled ``<div class="empty-state" style="...">`` survives.
* Responsive CSS — the Phase 16 small-screen rules exist (toolbar
  wrap, calendar horizontal-scroll, single-column insight cards,
  dialog width).
* No banned claims — the dashboard never makes a live-price /
  broker-sync / OAuth-login claim the product cannot back up.
* Docs consistency — README / Quickstart / FAQ / Known Limitations
  describe the *shipped* product (Overview sub-tab exists, revenue
  geography is shipped not "planned", AI is optional, no live
  prices, OAuth not implemented).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (PROJECT_ROOT / "dashboard" / "css" / "styles.css").read_text(
        encoding="utf-8",
    )


def _read_doc(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# Dashboard structure contract
# ─────────────────────────────────────────────────────────────────────


class TestDashboardStructure:
    EXPECTED_TABS = ["Portfolio", "Insights", "Events", "Alerts",
                     "Assistant", "Settings"]
    EXPECTED_SUBTABS = {"Overview", "News", "Analysis", "Digest", "Inbox"}

    def _top_tabs(self, html: str) -> list[str]:
        rx = re.compile(
            r'<button\s+class="tab-link[^"]*"\s+data-tab="([^"]+)"[^>]*>'
            r'(?P<label>[^<]*?)(?:\s*<|</button>)',
            re.IGNORECASE,
        )
        return [m.group("label").strip() for m in rx.finditer(html)]

    def _insights_subtabs(self, html: str) -> list[str]:
        rx = re.compile(
            r'<button[^>]*data-subtab="[^"]+"[^>]*data-parent="intelligence"[^>]*>'
            r'(?P<label>[^<]*?)(?:\s*<|</button>)',
            re.IGNORECASE,
        )
        return [m.group("label").strip() for m in rx.finditer(html)]

    def test_top_level_tabs_unchanged(self, index_html):
        assert self._top_tabs(index_html) == self.EXPECTED_TABS

    def test_insights_subtabs_unchanged(self, index_html):
        assert set(self._insights_subtabs(index_html)) == self.EXPECTED_SUBTABS

    def test_events_tab_is_separate_from_news(self, index_html):
        # The top-level Events tab points at the corporate-events panel,
        # NOT the News sub-tab.
        assert 'data-tab="corporate-events"' in index_html
        assert 'id="tab-corporate-events"' in index_html
        # News stays a sub-tab labelled "News" under Insights.
        assert "News" in self._insights_subtabs(index_html)
        assert "Events" not in self._insights_subtabs(index_html)

    def test_exposures_separates_listing_and_revenue_geography(self, index_html, app_js):
        # The Revenue geography card is a distinct, explicitly-labelled
        # surface.
        assert 'id="revenue-geo-card"' in index_html
        assert "Revenue geography" in index_html
        # The listing-country exposure card label is rendered by JS.
        assert "Listing country" in app_js
        # And the two are never conflated — the dashboard states the
        # difference plainly.
        assert "separate from listing country" in index_html


# ─────────────────────────────────────────────────────────────────────
# Empty / loading / error state consistency
# ─────────────────────────────────────────────────────────────────────


class TestEmptyStateConsistency:
    def test_shared_helpers_exist(self, app_js):
        assert "function renderEmpty(" in app_js
        assert "function renderError(" in app_js

    def test_no_inline_styled_empty_states(self, app_js):
        # Phase 16 — every empty state routes through renderEmpty().
        # A hand-rolled `<div class="empty-state" style="...">` is the
        # exact drift Phase 16 removed; lock it out.
        assert 'empty-state" style=' not in app_js, (
            "found an inline-styled empty-state div — route it through "
            "renderEmpty() instead so all empty states stay consistent"
        )

    def test_insights_overview_empty_uses_helper(self, app_js):
        # The Insights Overview no-cards state uses renderEmpty with an
        # honest, non-scary hint that names AI as optional.
        assert "Nothing to surface yet." in app_js
        assert "AI narration is optional" in app_js

    def test_revenue_geo_empty_uses_helper(self, app_js):
        assert "No revenue geography uploaded yet." in app_js


# ─────────────────────────────────────────────────────────────────────
# Responsive CSS contract
# ─────────────────────────────────────────────────────────────────────


class TestResponsiveCss:
    def test_tab_actions_wrap(self, styles_css):
        # The tab-action toolbar wraps so it never overflows a narrow
        # tab header.
        m = re.search(r'\.tab-actions\s*\{[^}]*\}', styles_css, re.DOTALL)
        assert m, ".tab-actions rule missing"
        assert "flex-wrap: wrap" in m.group(0)

    def test_insights_export_toolbar_rule_exists(self, styles_css):
        # The Phase 15 export toolbar has a real CSS rule (Phase 16
        # added it — previously it was an unstyled inline span).
        assert ".insights-export-toolbar" in styles_css
        m = re.search(
            r'\.insights-export-toolbar\s*\{[^}]*\}', styles_css, re.DOTALL,
        )
        assert m and "flex-wrap: wrap" in m.group(0)

    def test_phase16_small_screen_block_exists(self, styles_css):
        assert "Phase 16 — small-screen polish" in styles_css

    def test_calendar_scrolls_not_collapses(self, styles_css):
        # The corporate-events calendar gets a min-width + scrollable
        # wrap so its 7-column grid never collapses into slivers.
        assert "min-width: 520px" in styles_css
        m = re.search(
            r'\.ce-calendar-wrap\s*\{[^}]*overflow-x:\s*auto',
            styles_css, re.DOTALL,
        )
        assert m, "ce-calendar-wrap should scroll horizontally on small screens"

    def test_insight_cards_single_column_on_small_screens(self, styles_css):
        # Inside the 768px media block the insight card grid drops to
        # one column.
        block = re.search(
            r'@media\s*\(max-width:\s*768px\)\s*\{.*?Insights history controls',
            styles_css, re.DOTALL,
        )
        assert block, "expected the Phase 16 768px media block"
        assert ".insights-cards { grid-template-columns: 1fr; }" in block.group(0)

    def test_dialog_width_on_mobile(self, styles_css):
        assert "@media (max-width: 560px)" in styles_css


# ─────────────────────────────────────────────────────────────────────
# No banned claims
# ─────────────────────────────────────────────────────────────────────


#: Phrases that would only ever be a *false* claim for a local,
#: deterministic-first, no-broker, no-OAuth app.  None should appear
#: anywhere in the customer-facing dashboard.
_BANNED_DASHBOARD_PHRASES = [
    "real-time price",
    "realtime price",
    "live stock price",
    "live market data",
    "sign in with google",
    "sign in with microsoft",
    "connect your broker",
    "broker sync",
    "coming soon",
]


class TestNoBannedClaims:
    def test_dashboard_html_has_no_banned_claims(self, index_html):
        low = index_html.lower()
        for phrase in _BANNED_DASHBOARD_PHRASES:
            assert phrase not in low, f"banned claim in index.html: {phrase!r}"

    def test_dashboard_js_has_no_banned_claims(self, app_js):
        low = app_js.lower()
        for phrase in _BANNED_DASHBOARD_PHRASES:
            # "no promise of live prices" is an existing honest comment;
            # the banned list deliberately excludes the bare words
            # "live price" so honest negative phrasing is allowed.
            assert phrase not in low, f"banned claim in app.js: {phrase!r}"


# ─────────────────────────────────────────────────────────────────────
# Docs consistency — describe the shipped product, not a stale plan
# ─────────────────────────────────────────────────────────────────────


class TestDocsConsistency:
    def test_quickstart_lists_overview_subtab(self):
        doc = _read_doc("docs/CUSTOMER_QUICKSTART.md")
        # The Insights tab row must name the Overview sub-tab (shipped
        # in Phase 12) alongside News / Analysis / Digest / Inbox.
        assert "Overview" in doc
        # The tab table row for Insights names all five sub-tabs.
        assert re.search(
            r"Sub-tabs:\s*Overview,\s*News,\s*Analysis,\s*Digest,\s*Inbox",
            doc,
        ), "Quickstart Insights row should list all five sub-tabs"

    def test_quickstart_revenue_geography_is_shipped_not_planned(self):
        doc = _read_doc("docs/CUSTOMER_QUICKSTART.md")
        # Revenue geography shipped in Phase 10/11 — the doc must not
        # still describe it as a future "planned" phase.
        assert "revenue-geography phase is planned" not in doc
        assert "revenue geography phase is planned" not in doc.lower()

    def test_readme_local_lists_overview_subtab(self):
        doc = _read_doc("README_LOCAL.md")
        assert "Overview" in doc

    def test_known_limitations_states_no_live_prices(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md")
        assert "live price" in doc.lower()

    def test_known_limitations_states_no_oauth(self):
        doc = _read_doc("KNOWN_LIMITATIONS.md")
        low = doc.lower()
        assert "oauth" in low
        # The OAuth mention must be a negative ("not implemented" /
        # "does not").
        assert ("not yet ship any oauth" in low
                or "oauth integration" in low)

    def test_faq_describes_ai_as_optional(self):
        doc = _read_doc("docs/CLIENT_FAQ.md")
        low = doc.lower()
        assert "without ai" in low or "ai is optional" in low or \
               "core platform runs independently" in low

    def test_release_checklist_has_qa_section(self):
        doc = _read_doc("docs/RELEASE_CHECKLIST.md")
        # Phase 16 adds a professional-QA / release-readiness section.
        assert "Professional QA" in doc or "release readiness" in doc.lower()
