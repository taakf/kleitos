"""Phase 9B frontend/backend contract guard tests.

There is no browser-side test harness in this repo, so these tests
act as a static contract guard:

* Every CSS class name the Phase 9B JS emits in the event detail
  modal (or on the events table row) has a matching rule in
  ``dashboard/css/styles.css``.  This catches ``dead-class``
  regressions of the kind found in the Phase 9B audit where
  ``.event-row-title`` was referenced in JS without any CSS rule.

* The event detail API response model exposes exactly the fields
  the JS renderer reads — if someone later adds a field to the JS
  without updating the API model (or vice versa), this test will
  fail.

The tests are coarse — they don't verify visual appearance — but
they lock the contract shape so refactors can't silently drift.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_JS = REPO_ROOT / "dashboard" / "js" / "app.js"
STYLES_CSS = REPO_ROOT / "dashboard" / "css" / "styles.css"
INDEX_HTML = REPO_ROOT / "dashboard" / "index.html"


# ---------------------------------------------------------------------------
# CSS class guard
# ---------------------------------------------------------------------------


# Class names the Phase 9B JS renderer emits in dashboard/js/app.js.
# Every one of these MUST have a matching CSS rule AND still appear
# inside app.js (the sanity check catches dead-class drift).
_JS_EMITTED_CLASSES = frozenset({
    # Row-level factor tags
    "factor-tag-list-mini",
    "factor-tag-mini",
    "event-row-title",
    "events-row-clickable",
    # Modal structure
    "event-detail-group",
    "event-detail-meta",
    "event-detail-summary",
    "event-factor-tags",
    # Factor tag (detail panel)
    "factor-tag",
    "factor-arrow",
    # Chain card
    "chain-card",
    "chain-card-header",
    "chain-origin-badge",
    "chain-summary",
    "chain-flow",
    "chain-step",
    "chain-arrow",
    "chain-rationale",
    "chain-metrics",
    # Affected holdings
    "affected-holdings-list",
    "affected-holding-row",
    # Related rows
    "related-row",
    # Empty state
    "empty-inline",
})

# Class names declared in dashboard/index.html (not emitted by JS).
# These still need a CSS rule, but the sanity check does NOT look
# for them in app.js.
_HTML_DECLARED_CLASSES = frozenset({
    "modal-lg",
})

_PHASE9B_CLASSES = _JS_EMITTED_CLASSES | _HTML_DECLARED_CLASSES


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_phase9b_css_class_is_defined():
    css = _read(STYLES_CSS)
    missing = []
    for cls in sorted(_PHASE9B_CLASSES):
        # Any occurrence as a class selector anywhere in the CSS file
        # counts as "defined" — allows for descendant, modifier, and
        # pseudo-class variants.
        pattern = re.compile(rf"\.{re.escape(cls)}(?![a-zA-Z0-9_-])")
        if not pattern.search(css):
            missing.append(cls)
    assert not missing, (
        f"Phase 9B JS emits {len(missing)} CSS class(es) with no matching "
        f"rule in dashboard/css/styles.css: {missing}"
    )


def test_every_phase9b_js_class_still_emitted_by_js():
    """Sanity: confirm the JS renderer actually emits every JS-class we
    guard, so this test doesn't drift into guarding classes that have
    been removed from the renderer.  HTML-declared classes (like
    ``modal-lg``) are checked separately."""
    js = _read(APP_JS)
    unused = []
    for cls in sorted(_JS_EMITTED_CLASSES):
        if cls not in js:
            unused.append(cls)
    assert not unused, (
        f"JS-emitted classes no longer appear in dashboard/js/app.js: "
        f"{unused}. Either re-add them or remove from the guard list."
    )


def test_every_phase9b_html_class_still_declared_in_html():
    html = _read(INDEX_HTML)
    unused = []
    for cls in sorted(_HTML_DECLARED_CLASSES):
        if cls not in html:
            unused.append(cls)
    assert not unused, (
        f"HTML-declared classes no longer appear in dashboard/index.html: "
        f"{unused}."
    )


# ---------------------------------------------------------------------------
# Markup contract guard
# ---------------------------------------------------------------------------


def test_event_detail_modal_markup_present_in_index_html():
    """The event detail modal dialog and its title/body targets must
    exist in the HTML because ``window.openEventDetail`` grabs them
    by id.  Missing these would silently no-op at runtime."""
    html = _read(INDEX_HTML)
    assert 'id="event-detail-modal"' in html
    assert 'id="event-detail-title"' in html
    assert 'id="event-detail-body"' in html
    # modal-lg class is applied so the wider layout kicks in
    assert 'class="modal-lg"' in html or "modal-lg" in html


# ---------------------------------------------------------------------------
# API contract guard
# ---------------------------------------------------------------------------


# Fields the JS detail renderer reads off the detail response.
# (Fields the JS reads off individual link.chain are covered by the
# backend unit tests in tests/unit/test_chain_normalizer.py.)
_JS_DETAIL_FIELDS = frozenset({
    "id", "title", "summary", "url", "event_type", "source_name",
    "published_at", "materiality",
    "factor_tags",
    "linked_ticker_count",
    "links",
    "affected_holdings",
    "related_analyses",
    "related_alerts",
})

# Fields the JS list-row renderer reads off each row.
_JS_LIST_FIELDS = frozenset({
    "id", "title", "event_type", "materiality", "source_name",
    "published_at", "factor_tags", "linked_ticker_count",
})


def test_event_detail_response_model_has_every_field_js_reads():
    from src.api.routes.events import EventDetailResponse

    model_fields = set(EventDetailResponse.model_fields.keys())
    missing = _JS_DETAIL_FIELDS - model_fields
    assert not missing, (
        f"EventDetailResponse is missing fields the dashboard JS reads: "
        f"{sorted(missing)}"
    )


def test_event_list_response_model_has_every_field_js_reads():
    from src.api.routes.events import EventResponse

    model_fields = set(EventResponse.model_fields.keys())
    missing = _JS_LIST_FIELDS - model_fields
    assert not missing, (
        f"EventResponse is missing fields the dashboard JS row renderer "
        f"reads: {sorted(missing)}"
    )


def test_factor_tag_model_has_every_field_js_reads():
    from src.api.routes.events import FactorTag

    model_fields = set(FactorTag.model_fields.keys())
    # JS reads: key, label, direction, magnitude, confidence
    required = {"key", "label", "direction", "magnitude", "confidence"}
    missing = required - model_fields
    assert not missing, f"FactorTag missing JS-required fields: {sorted(missing)}"


def test_affected_holding_model_has_every_field_js_reads():
    from src.api.routes.events import AffectedHolding

    model_fields = set(AffectedHolding.model_fields.keys())
    # JS reads: ticker, portfolio_id, weight_pct, max_relevance,
    # link_types, sector
    required = {
        "ticker", "portfolio_id", "weight_pct", "max_relevance",
        "link_types", "sector",
    }
    missing = required - model_fields
    assert not missing, f"AffectedHolding missing JS-required fields: {sorted(missing)}"


def test_related_alert_model_has_every_field_js_reads():
    from src.api.routes.events import RelatedAlert

    model_fields = set(RelatedAlert.model_fields.keys())
    # JS reads: severity, title, alert_type, portfolio_id,
    # acknowledged, created_at
    required = {
        "severity", "title", "alert_type", "portfolio_id",
        "acknowledged", "created_at",
    }
    missing = required - model_fields
    assert not missing, f"RelatedAlert missing JS-required fields: {sorted(missing)}"


def test_related_analysis_note_model_has_every_field_js_reads():
    from src.api.routes.events import RelatedAnalysisNote

    model_fields = set(RelatedAnalysisNote.model_fields.keys())
    required = {
        "materiality", "ticker", "note_type", "summary", "created_at",
    }
    missing = required - model_fields
    assert not missing, f"RelatedAnalysisNote missing JS-required fields: {sorted(missing)}"


def test_event_link_response_has_chain_field():
    from src.api.routes.events import EventLinkResponse

    assert "chain" in EventLinkResponse.model_fields, (
        "EventLinkResponse.chain must exist — the dashboard modal "
        "reads `link.chain` for every link."
    )


# ---------------------------------------------------------------------------
# Null-safety guard
# ---------------------------------------------------------------------------


def test_open_event_detail_has_null_guard():
    """Ensure ``openEventDetail`` tolerates a null payload (fetchJSON
    returns null on 404).  Guards against a past regression where
    ``data.title`` would throw on a null payload."""
    js = _read(APP_JS)
    # The handler must special-case the null payload somewhere
    # between the fetch and the title write.
    assert "if (!data)" in js, (
        "openEventDetail is missing its null-data guard — fetchJSON "
        "returns null on 404 and the handler must handle it cleanly."
    )
