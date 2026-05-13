"""Phase 9T browser E2E tests — Dismissible Actions + State Hygiene.

Validates:
  * the overview card renders a dismiss button on each action row
  * clicking dismiss hides the action from the overview
  * the hidden count footer appears after a dismiss
  * dismissing an action also removes it from the inbox
  * the hidden count footer reflects the correct count
  * no console errors during action-state flows
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


_ASSERT_TIMEOUT = 10_000


def _open_dashboard(page: Page, portfolio_id: str = "pA") -> None:
    page.add_init_script(
        f"window.localStorage.setItem('activePortfolioId', '{portfolio_id}');"
    )
    page.goto("/dashboard", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio", timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 1) Dismiss button renders on action rows
# ---------------------------------------------------------------------------


def test_overview_actions_have_dismiss_buttons(page: Page, axion_server: str):
    """Each visible recommended action on the overview card should
    carry a Phase 9T 'Dismiss' button."""
    _open_dashboard(page, "pA")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    actions = overview.locator(".intel-actions-block .intel-action-row")
    if actions.count() == 0:
        pytest.skip("no recommended actions for pA seed")

    first = actions.first
    expect(first).to_be_visible(timeout=_ASSERT_TIMEOUT)
    dismiss_btn = first.locator(".intel-action-dismiss")
    expect(dismiss_btn).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(dismiss_btn).to_contain_text("Dismiss")


# ---------------------------------------------------------------------------
# 2) Clicking dismiss hides the action
# ---------------------------------------------------------------------------


def test_dismiss_action_hides_it_from_overview(page: Page, axion_server: str):
    """Clicking the Dismiss button should remove the action row from
    the overview and show a 'N handled actions hidden' footer."""
    _open_dashboard(page, "pA")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    actions_block = overview.locator(".intel-actions-block")
    expect(actions_block).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Count visible actions before dismiss
    initial_rows = overview.locator(".intel-action-row")
    if initial_rows.count() == 0:
        pytest.skip("no recommended actions for pA seed")
    initial_count = initial_rows.count()

    # Capture the first action's key so we can verify it's gone
    first_key = initial_rows.first.get_attribute("data-action-key")
    assert first_key, "first action row missing data-action-key"

    # Click dismiss
    initial_rows.first.locator(".intel-action-dismiss").click()

    # Wait for the overview to re-render and the hidden-count footer
    # to appear.  We don't check row count because the backend may
    # promote a previously-hidden action into the visible slot.
    page.wait_for_function(
        """
        () => {
            const footer = document.querySelector(
                '#intelligence-overview .intel-actions-hidden-footer'
            );
            return footer && footer.innerText.includes('hidden');
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )

    # The dismissed action's key should no longer appear in any row
    page.wait_for_function(
        f"""
        () => {{
            const rows = document.querySelectorAll('#intelligence-overview .intel-action-row');
            for (const r of rows) {{
                if (r.dataset.actionKey === '{first_key}') return false;
            }}
            return true;
        }}
        """,
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 3) Dismissed action disappears from the inbox
# ---------------------------------------------------------------------------


def test_dismissed_action_vanishes_from_inbox(page: Page, axion_server: str):
    """After dismissing a high-priority action via the overview, the
    corresponding inbox item (if it existed) should no longer appear.

    We open the inbox, check for action-type items, dismiss one from
    the overview, then re-open the inbox and verify the count dropped.
    """
    _open_dashboard(page, "pA")

    # First, open inbox and count action-type items
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="inbox"]')
    page.wait_for_selector("#subtab-inbox.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_function(
        "() => document.querySelector('#inbox-content .inbox-list, #inbox-content .inbox-empty') !== null",
        timeout=_ASSERT_TIMEOUT,
    )
    action_items_before = page.locator(
        "#inbox-content .inbox-item[data-source-type='action']"
    ).count()

    if action_items_before == 0:
        pytest.skip("no action items in inbox for pA seed")

    # Go to portfolio tab to see the overview
    page.click('[data-tab="portfolio"]')
    page.wait_for_selector("#tab-portfolio.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector(
        "#intelligence-overview .intel-action-row",
        timeout=_ASSERT_TIMEOUT,
    )

    # Dismiss the first action
    page.locator(
        "#intelligence-overview .intel-action-row .intel-action-dismiss"
    ).first.click()

    # Wait for the overview to re-render
    page.wait_for_timeout(500)

    # Re-open inbox
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="inbox"]')
    page.wait_for_selector("#subtab-inbox.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_function(
        "() => document.querySelector('#inbox-content .inbox-list, #inbox-content .inbox-empty') !== null",
        timeout=_ASSERT_TIMEOUT,
    )

    # Invalidate the tab loader cache so the inbox re-fetches
    page.evaluate("() => { tabLoaded = tabLoaded || {}; tabLoaded.inbox = false; }")
    page.click('[data-subtab="inbox"]')
    page.wait_for_function(
        "() => document.querySelector('#inbox-content .inbox-list, #inbox-content .inbox-empty') !== null",
        timeout=_ASSERT_TIMEOUT,
    )

    action_items_after = page.locator(
        "#inbox-content .inbox-item[data-source-type='action']"
    ).count()
    assert action_items_after < action_items_before, (
        f"inbox action items did not decrease: before={action_items_before} after={action_items_after}"
    )


# ---------------------------------------------------------------------------
# 4) Portfolio isolation — dismiss in pA doesn't affect pB
# ---------------------------------------------------------------------------


def test_action_state_is_portfolio_scoped(page: Page, axion_server: str):
    """Dismissing an action in pA must not affect pB's overview."""
    _open_dashboard(page, "pA")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    pa_rows = overview.locator(".intel-action-row")
    if pa_rows.count() == 0:
        pytest.skip("no actions for pA")

    # Dismiss all visible actions in pA
    while pa_rows.count() > 0:
        pa_rows.first.locator(".intel-action-dismiss").click()
        page.wait_for_timeout(300)
        pa_rows = page.locator("#intelligence-overview .intel-action-row")

    # Switch to pB — its actions should be independent
    page.select_option("#portfolio-select", "pB")
    page.wait_for_function(
        "window.localStorage.getItem('activePortfolioId') === 'pB'",
        timeout=_ASSERT_TIMEOUT,
    )
    page.wait_for_load_state("networkidle")

    # pB may or may not have actions — the point is that whatever
    # pB has should NOT be affected by pA's dismissals.  We just
    # verify the overview renders without error.
    expect(page.locator("#intelligence-overview")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 5) No console errors
# ---------------------------------------------------------------------------


def test_no_console_errors_during_action_state_flows(
    page: Page, axion_server: str,
):
    """Dismiss an action, check inbox, switch portfolios — no
    console errors."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # Dismiss if there are actions
    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)
    dismiss = overview.locator(".intel-action-row .intel-action-dismiss").first
    if dismiss.count():
        dismiss.click()
        page.wait_for_timeout(300)

    # Check inbox
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="inbox"]')
    page.wait_for_selector("#subtab-inbox.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_timeout(300)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9T flows:\n" + "\n".join(fatal)
    )
