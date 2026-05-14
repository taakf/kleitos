"""Phase 5 — terminology and navigation regression tests.

These tests pin the customer-facing labels that Phase 5 renamed without
freezing the internal identifiers (HTML attributes, DOM ids, CSS classes,
API routes, DB tables, ORM models, JS variables) that intentionally stay
as ``events`` / ``intelligence``.

Coverage:
- Dashboard contract: top-level tabs are Portfolio / Insights / Alerts /
  Assistant / Settings; Insights sub-tab labels are News / Analysis /
  Digest / Inbox; the customer-facing string "Events" no longer appears
  as a sub-tab button under Insights.
- First-run welcome card: uses "news" copy, no remaining "events" copy.
- Backend ``describe_view`` and ``_SURFACE_LABELS`` / ``_SUBTAB_LABELS``:
  surface/subtab key ``events`` renders as "News".
- API route stability: ``/api/v1/events`` is still registered so the UI
  rename did not break backend compatibility.
- Doc terminology: customer-facing markdown no longer calls the news
  feed the "Events tab".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ───────────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def index_html() -> str:
    return (PROJECT_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (PROJECT_ROOT / "dashboard" / "js" / "app.js").read_text(encoding="utf-8")


# ───────────────────────────────────────────────────────────────────────────
# Top-level navigation contract
# ───────────────────────────────────────────────────────────────────────────


class TestTopLevelNavigation:
    """The 5 top-level tabs are Portfolio / Insights / Alerts / Assistant /
    Settings — no "Intelligence" surfaced as a top-level label, no extra tabs
    introduced by Phase 5.
    """

    # Phase 9 inserted a new top-level "Events" tab between "Insights"
    # (which still hosts the News sub-tab) and "Alerts" so corporate
    # / issuer events have a first-class customer surface.
    EXPECTED_TABS = ["Portfolio", "Insights", "Events", "Alerts", "Assistant", "Settings"]

    def _extract_top_tab_labels(self, html: str) -> list[str]:
        # Match <button class="tab-link …" …>Label optional<span…></span></button>
        # We only want the leading text node before any nested <span>.
        rx = re.compile(
            r'<button\s+class="tab-link[^"]*"\s+data-tab="([^"]+)"[^>]*>'
            r'(?P<label>[^<]*?)(?:\s*<|<\/button>)',
            re.IGNORECASE,
        )
        return [m.group("label").strip() for m in rx.finditer(html)]

    def test_top_tabs_match_expected_set(self, index_html):
        labels = self._extract_top_tab_labels(index_html)
        assert labels == self.EXPECTED_TABS, (
            f"top-level tab labels drifted from spec: {labels}"
        )

    def test_no_top_level_intelligence_label(self, index_html):
        labels = self._extract_top_tab_labels(index_html)
        # "Intelligence" must not be a customer-facing top tab; the
        # underlying data-tab key may stay "intelligence".
        for lab in labels:
            assert "Intelligence" not in lab, (
                f'top-tab label "{lab}" must not say "Intelligence" — '
                f'the customer label is "Insights".'
            )


# ───────────────────────────────────────────────────────────────────────────
# Insights sub-tab contract
# ───────────────────────────────────────────────────────────────────────────


class TestInsightsSubTabs:
    """Insights sub-tab labels are News / Analysis / Digest / Inbox."""

    EXPECTED_SUBTABS = {"News", "Analysis", "Digest", "Inbox"}

    def _extract_insights_subtab_labels(self, html: str) -> list[str]:
        rx = re.compile(
            r'<button[^>]*data-subtab="[^"]+"[^>]*data-parent="intelligence"[^>]*>'
            r'(?P<label>[^<]*?)(?:\s*<|<\/button>)',
            re.IGNORECASE,
        )
        return [m.group("label").strip() for m in rx.finditer(html)]

    def test_insights_subtab_labels(self, index_html):
        labels = set(self._extract_insights_subtab_labels(index_html))
        assert labels == self.EXPECTED_SUBTABS, (
            f"Insights sub-tab labels drifted: {labels}"
        )

    def test_no_events_label_under_insights(self, index_html):
        labels = self._extract_insights_subtab_labels(index_html)
        assert "Events" not in labels, (
            "Phase 5: the Insights sub-tab must be labelled 'News', not 'Events'. "
            "'Events' is reserved for a future top-level corporate-calendar tab."
        )

    def test_internal_subtab_key_stays_events(self, index_html):
        # Customer-facing label changes, but the internal data-subtab key
        # must stay "events" so backend payloads (saved views etc.) keep
        # working without a migration.
        assert 'data-subtab="events"' in index_html, (
            "data-subtab='events' is the stable internal key; do not rename it."
        )

    def test_news_panel_header_and_search(self, index_html):
        # Sub-panel header reads "News"; search placeholder reads "Search news…".
        assert "<h2>News</h2>" in index_html
        assert 'placeholder="Search news..."' in index_html


# ───────────────────────────────────────────────────────────────────────────
# Modal / detail rename
# ───────────────────────────────────────────────────────────────────────────


class TestNewsItemDetailLabel:
    def test_modal_default_title_is_news_item(self, index_html):
        # The modal's id is still event-detail-title; only the visible
        # text changes.
        assert (
            '<h3 id="event-detail-title">News item</h3>' in index_html
        ), "news-item detail modal should default to 'News item' title"

    def test_js_fallback_titles_say_news_item(self, app_js):
        # Three call sites in app.js set the modal title; all should
        # now use 'News item' / 'News item not found'.
        assert "'News item'" in app_js
        assert "'News item not found'" in app_js
        # No leftover bare 'Event' / 'Event not found' fallback strings.
        assert "= 'Event'" not in app_js
        assert "= 'Event not found'" not in app_js


# ───────────────────────────────────────────────────────────────────────────
# Empty-state copy
# ───────────────────────────────────────────────────────────────────────────


class TestEmptyStateCopy:
    def test_news_empty_state_uses_news(self, app_js):
        assert "'No news collected yet.'" in app_js
        # Old "No events collected yet." must be gone.
        assert "No events collected yet" not in app_js

    def test_digest_empty_state(self, app_js):
        assert "'No digest generated yet.'" in app_js

    def test_alerts_empty_state(self, app_js):
        # Honest copy: "No active alerts" + clarification that risk isn't zero.
        assert "'No active alerts.'" in app_js
        assert "does not mean risk is zero" in app_js.lower()

    def test_inbox_empty_state(self, app_js):
        assert "No notifications yet" in app_js

    def test_analysis_empty_state_mentions_ai_optional(self, app_js):
        # Make sure the Analysis empty state acknowledges deterministic
        # rule-based scoring works without AI.
        # Find the relevant renderEmpty('analysis', ...) call body.
        idx = app_js.find("renderEmpty('analysis',")
        assert idx != -1
        snippet = app_js[idx:idx + 600]
        assert "deterministic" in snippet.lower() or "without one" in snippet.lower()


# ───────────────────────────────────────────────────────────────────────────
# First-run welcome card (Phase 4 marker preserved, Phase 5 copy updated)
# ───────────────────────────────────────────────────────────────────────────


class TestFirstRunCard:
    def test_news_collected_hint(self, app_js):
        # The welcome step about collection now says "News collected." not
        # "Events collected.".
        assert "'News collected.'" in app_js
        assert "'Events collected'" not in app_js
        assert "'Events collected.'" not in app_js

    def test_first_run_marker_still_there(self, app_js):
        # Don't regress the Phase 4 contract.
        assert 'data-first-run="empty"' in app_js


# ───────────────────────────────────────────────────────────────────────────
# Backend navigation labels (describe_view)
# ───────────────────────────────────────────────────────────────────────────


class TestNavigationLabels:
    def test_surface_events_renders_as_news(self):
        from src.intelligence.navigation import describe_view

        assert describe_view({"surface": "events"}) == "News"
        assert (
            describe_view({"surface": "events", "subtab": "events"}) == "News"
        ), "subtab + surface both 'events' should still collapse to single 'News'"
        assert (
            describe_view(
                {"surface": "events", "subtab": "events", "filters": {"search": "fed"}}
            )
            == "News · Search: fed"
        )

    def test_event_detail_modal_label(self):
        from src.intelligence.navigation import describe_view

        r = describe_view(
            {"surface": "events", "entity_type": "event", "open_modal": True}
        )
        assert "News item detail" in r
        # Old "Event detail" must be gone from this output path.
        assert "Event detail" not in r

    def test_surface_key_events_still_valid(self):
        # The internal key must keep working — only the rendered label changed.
        from src.intelligence.navigation import _SURFACE_LABELS, _SUBTAB_LABELS

        assert "events" in _SURFACE_LABELS
        assert "events" in _SUBTAB_LABELS
        assert _SURFACE_LABELS["events"] == "News"
        assert _SUBTAB_LABELS["events"] == "News"


# ───────────────────────────────────────────────────────────────────────────
# API route stability
# ───────────────────────────────────────────────────────────────────────────


class TestApiRouteStability:
    """Phase 5 is purely a UI/label rename. The backend API must remain
    addressable at /api/v1/events so existing UI calls + saved-view payloads
    still resolve.
    """

    def test_events_route_is_registered(self):
        from src.main import app

        paths = {r.path for r in app.routes if hasattr(r, "path")}
        # The events router prefixes with /api/v1/events; at least one
        # route under that path must exist.
        events_paths = [p for p in paths if p.startswith("/api/v1/events")]
        assert events_paths, (
            f"/api/v1/events route was lost: registered paths = {sorted(paths)}"
        )

    def test_events_table_model_class_still_named_event(self):
        from src.database.models import Event

        assert Event.__tablename__ == "events"


# ───────────────────────────────────────────────────────────────────────────
# Docs terminology
# ───────────────────────────────────────────────────────────────────────────


class TestDocsTerminology:
    """Customer-facing docs must keep the News-vs-Events split clear.

    Phase 5 banned the term "Events" for the news feed; Phase 9 *introduces*
    a real top-level **Events** tab for scheduled corporate events.  The
    invariants now are:

    * The news feed is **never** called "Events sub-tab" or
      "Intelligence → Events" / "Insights → Events".
    * The phrase "Events tab" is now allowed because it refers to the new
      top-level corporate-events surface.
    """

    CUSTOMER_DOCS = [
        "README_LOCAL.md",
        "docs/CUSTOMER_QUICKSTART.md",
        "docs/DEMO_RESET.md",
        "docs/VALIDATION_CHECKLIST.md",
        "docs/CLIENT_DEMO_CARD.md",
        "docs/DEMO_RUNBOOK.md",
        "KNOWN_LIMITATIONS.md",
    ]

    #: Phrases that would (re-)conflate the news feed with the Events tab.
    BANNED_PHRASES = [
        "Events sub-tab",
        "Intelligence → Events",
        "Intelligence -> Events",
        "Insights → Events",
        "Insights -> Events",
    ]

    @pytest.mark.parametrize("doc_path", CUSTOMER_DOCS)
    def test_doc_does_not_call_news_feed_the_events_tab(self, doc_path):
        full = (PROJECT_ROOT / doc_path).read_text(encoding="utf-8")
        for phrase in self.BANNED_PHRASES:
            assert phrase not in full, (
                f"{doc_path} still uses {phrase!r} — Phase 5/9 keeps the "
                f"news feed labelled 'News' under Insights; the Events tab "
                f"is the separate top-level corporate-events surface."
            )

    def test_readme_local_has_terminology_section(self):
        readme = (PROJECT_ROOT / "README_LOCAL.md").read_text(encoding="utf-8")
        assert "## Terminology" in readme
        # All four customer-facing terms must be defined.
        assert "**News**" in readme
        assert "**Insights**" in readme
        assert "**Events**" in readme
        # Phase 9 also documents the listing-country vs revenue-geography split.
        assert "**Listing country**" in readme
        assert "**Revenue geography**" in readme

    def test_known_limitations_mentions_corporate_events(self):
        kl = (PROJECT_ROOT / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
        assert "Corporate Events calendar" in kl
        # Phase 9 shipped the foundation but ATHEX automation is intentionally
        # not enabled yet — that honest framing must stay in the doc.
        assert "Unsupported" in kl or "unsupported" in kl
        assert "ATHEX" in kl
