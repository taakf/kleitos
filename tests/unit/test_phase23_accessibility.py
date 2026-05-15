"""Phase 23 — dashboard accessibility & keyboard-UX regression tests.

Phase 23 added a focused, additive set of accessibility fixes to the
static dashboard:

* every icon-only close button (``&times;``) carries ``aria-label``;
* every ``<dialog>`` carries a label (``aria-label`` /
  ``aria-labelledby``);
* a ``:focus-visible`` block restores a keyboard focus ring for the
  input classes whose ``:focus`` rule sets ``outline: none``;
* the holdings / news / insights content containers are ``aria-live``;
* the customer data tables (holdings / news / trades + the review
  modal) emit ``<th scope="col">``;
* a small additive ``setupDialogFocusReturn`` helper returns focus to
  the trigger element when a dialog closes.

These tests are markup-contract checks — deterministic, offline, and
read only the three dashboard source files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD = PROJECT_ROOT / "dashboard"


@pytest.fixture(scope="module")
def index_html() -> str:
    return (DASHBOARD / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (DASHBOARD / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (DASHBOARD / "css" / "styles.css").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# Icon-only close buttons must be labelled
# ─────────────────────────────────────────────────────────────────────


class TestCloseButtonsLabelled:
    def test_every_times_button_has_aria_label(self, index_html):
        # Every line that renders a `&times;` close glyph in a button
        # must also carry an aria-label so a screen reader announces
        # something better than "times".
        offenders = []
        for i, line in enumerate(index_html.splitlines(), 1):
            if "&times;" in line and "<button" in line:
                if "aria-label" not in line:
                    offenders.append((i, line.strip()))
        assert not offenders, f"unlabelled close buttons: {offenders}"

    def test_close_buttons_are_actually_present(self, index_html):
        # Guard the premise — if the close-button markup is refactored
        # away, this test should be revisited rather than silently pass.
        count = index_html.count("&times;")
        assert count >= 10, f"expected the close-button glyphs, found {count}"


# ─────────────────────────────────────────────────────────────────────
# Every dialog carries an accessible name
# ─────────────────────────────────────────────────────────────────────


class TestDialogsLabelled:
    def test_every_dialog_has_a_label(self, index_html):
        dialog_tags = re.findall(r"<dialog\b[^>]*>", index_html)
        assert dialog_tags, "no <dialog> elements found"
        for tag in dialog_tags:
            assert ("aria-label=" in tag or "aria-labelledby=" in tag), (
                f"dialog without an accessible name: {tag}"
            )


# ─────────────────────────────────────────────────────────────────────
# Visible keyboard focus ring
# ─────────────────────────────────────────────────────────────────────


class TestFocusVisible:
    def test_focus_visible_block_present(self, styles_css):
        assert ":focus-visible" in styles_css

    def test_outline_none_inputs_have_focus_visible_counterpart(self, styles_css):
        # Each input class whose :focus rule removes the outline must
        # have a :focus-visible rule restoring a keyboard ring.
        for selector in (".input", ".cmd-input", ".portfolio-select"):
            assert f"{selector}:focus-visible" in styles_css, (
                f"{selector} has no :focus-visible focus ring"
            )
        assert ".review-table input" in styles_css
        assert 'review-table input[type="text"]:focus-visible' in styles_css

    def test_focus_visible_uses_a_real_outline(self, styles_css):
        # The Phase 23 block restores an actual `outline` (works in
        # forced-colors / high-contrast mode, unlike box-shadow).
        block = styles_css.split(".input:focus-visible", 1)[-1][:400]
        assert "outline:" in block and "solid" in block


# ─────────────────────────────────────────────────────────────────────
# Live regions for async content
# ─────────────────────────────────────────────────────────────────────


class TestAriaLiveContainers:
    @pytest.mark.parametrize("container_id", [
        "holdings-table", "events-table", "insights-cards",
    ])
    def test_container_is_aria_live(self, index_html, container_id):
        m = re.search(
            rf'<div id="{container_id}"[^>]*>', index_html,
        )
        assert m, f"container #{container_id} not found"
        assert 'aria-live="polite"' in m.group(0), (
            f"#{container_id} is not an aria-live region"
        )


# ─────────────────────────────────────────────────────────────────────
# Table headers carry scope
# ─────────────────────────────────────────────────────────────────────


class TestTableHeaderScope:
    def test_holdings_header_has_scope(self, app_js):
        # The holdings table header renderer emits scoped <th>.
        assert '<th scope="col" class="sortable" data-sort="ticker">' in app_js

    def test_news_header_has_scope(self, app_js):
        assert '<th scope="col" class="sortable" data-sort="title">' in app_js
        assert '<th scope="col">Holdings</th>' in app_js

    def test_trades_header_has_scope(self, app_js):
        assert '<th scope="col">Date</th>' in app_js

    def test_review_modal_header_has_scope(self, index_html):
        assert '<th scope="col">Ticker</th>' in index_html

    def test_no_unscoped_header_in_customer_tables(self, app_js):
        # Within the holdings/news/trades renderers every non-empty
        # <th> should be scoped. We check the count is plausible.
        scoped = app_js.count('<th scope="col"')
        assert scoped >= 20, f"expected scoped customer-table headers, found {scoped}"


# ─────────────────────────────────────────────────────────────────────
# Dialog focus-return helper
# ─────────────────────────────────────────────────────────────────────


class TestFocusReturnHelper:
    def test_helper_present(self, app_js):
        assert "setupDialogFocusReturn" in app_js

    def test_helper_records_trigger_on_open(self, app_js):
        # It wraps showModal to remember document.activeElement.
        assert "_axReturnFocus" in app_js
        assert "document.activeElement" in app_js
        assert "proto.showModal" in app_js

    def test_helper_listens_for_close_in_capture(self, app_js):
        # The dialog 'close' event does not bubble — the listener must
        # use the capture phase (the `, true)` flag on addEventListener).
        block = app_js.split("setupDialogFocusReturn", 1)[-1][:2000]
        assert "addEventListener('close'" in block
        assert "}, true)" in block  # capture flag on addEventListener

    def test_helper_restores_focus_only_when_safe(self, app_js):
        # Focus is restored only if the trigger is still in the DOM
        # and focusable.
        block = app_js.split("setupDialogFocusReturn", 1)[-1][:1200]
        assert "document.contains" in block
        assert ".focus()" in block

    def test_helper_does_not_rewrite_modal_system(self, app_js):
        # Phase 23 constraint: the helper is additive only. The
        # existing Escape handler and data-close-modal delegation
        # must still be present and untouched.
        assert "$$('dialog[open]').forEach(d => d.close())" in app_js
        assert "[data-close-modal]" in app_js
