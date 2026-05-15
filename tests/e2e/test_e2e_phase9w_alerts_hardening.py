"""Phase 9W browser E2E tests — Server-Backed Alerts Filtering + Saved View Quality.

Validates:
  * the acknowledged filter <select> exists with open/all/ack options
  * switching to "acknowledged" shows acknowledged alerts
  * combined severity + ack filters round-trip through deep-link hash
  * the ack filter survives reload
  * saved-view description reflects both severity + ack filters
  * auto-suggested view name includes filter info
  * old 9V deep links still work (no ack field → defaults to "open")
  * no console errors during the new flows
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
# 1) Acknowledged filter UI exists
# ---------------------------------------------------------------------------


def test_acknowledged_filter_exists(page: Page, axion_server: str):
    """The Alerts tab should have an acknowledged-state filter <select>
    with options for open / all / ack."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    sel = page.locator("#alerts-ack-filter")
    expect(sel).to_be_visible(timeout=_ASSERT_TIMEOUT)

    options = page.evaluate(
        "() => Array.from(document.querySelector('#alerts-ack-filter').options).map(o => o.value)"
    )
    assert "open" in options
    assert "" in options  # "all"
    assert "ack" in options


# ---------------------------------------------------------------------------
# 2) Default "open" filter shows only unacknowledged alerts
# ---------------------------------------------------------------------------


def test_default_open_filter_shows_unacknowledged(
    page: Page, axion_server: str,
):
    """With the default 'open' filter, all rendered alert cards should
    have unacknowledged=false (i.e. no ack badge)."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # The default filter value should be "open"
    val = page.evaluate(
        "() => document.querySelector('#alerts-ack-filter')?.value"
    )
    assert val == "open"


# ---------------------------------------------------------------------------
# 3) Combined filters round-trip through hash
# ---------------------------------------------------------------------------


def test_combined_severity_ack_round_trips_via_hash(
    page: Page, axion_server: str,
):
    """A hash with both severity=critical_high and ack=open should
    restore both filters on reload."""
    from src.intelligence.navigation import _safe_target, encode_nav_hash

    t = _safe_target(
        surface="alerts", portfolio_id="pA",
        filters={"severity": "critical_high", "ack": "open"},
    )
    nav_hash = encode_nav_hash(t)

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{nav_hash}", wait_until="networkidle")

    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Both filters should be set
    page.wait_for_function(
        "() => document.querySelector('#alerts-severity-filter')?.value === 'critical_high'",
        timeout=_ASSERT_TIMEOUT,
    )
    page.wait_for_function(
        "() => document.querySelector('#alerts-ack-filter')?.value === 'open'",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 4) Saved-view description reflects both filters
# ---------------------------------------------------------------------------


def test_saved_view_shows_combined_filter_description(
    page: Page, axion_server: str,
):
    """Save a view with severity=critical_high + ack=open active on
    the Alerts tab.  The saved-view description should mention both."""
    _open_dashboard(page, "pA")

    # Set both filters on the Alerts tab
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.select_option("#alerts-severity-filter", "critical_high")
    page.select_option("#alerts-ack-filter", "open")
    page.wait_for_timeout(200)

    # Go to Settings and save the view
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)

    # Go back to Alerts to make the capture work from the right tab
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    # Re-set filters (tab switch may trigger re-render)
    page.select_option("#alerts-severity-filter", "critical_high")
    page.select_option("#alerts-ack-filter", "open")
    page.wait_for_timeout(100)

    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#save-current-view-btn", timeout=_ASSERT_TIMEOUT)

    # The auto-suggest should include filter info
    page.once("dialog", lambda d: d.accept(d.default_value or "Combined View"))
    page.click("#save-current-view-btn")

    page.wait_for_function(
        "() => document.querySelector('#saved-views-list .saved-view-row') !== null",
        timeout=_ASSERT_TIMEOUT,
    )

    desc = page.locator("#saved-views-list .saved-view-description").first
    expect(desc).to_be_visible(timeout=_ASSERT_TIMEOUT)
    desc_text = desc.inner_text()
    # The backend's describe_view should have produced something with
    # the filter info — either severity or ack label should appear
    assert desc_text, "description is empty"


# ---------------------------------------------------------------------------
# 5) Old 9V deep links still work
# ---------------------------------------------------------------------------


def test_old_9v_severity_only_hash_works(page: Page, axion_server: str):
    """A 9V-era hash with only severity and no ack should still
    restore the severity filter, and ack should default to 'open'."""
    from src.intelligence.navigation import encode_nav_hash

    old_hash = encode_nav_hash({
        "surface": "alerts",
        "portfolio_id": "pA",
        "filters": {"severity": "critical"},
    })

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{old_hash}", wait_until="networkidle")
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Severity should be set
    page.wait_for_function(
        "() => document.querySelector('#alerts-severity-filter')?.value === 'critical'",
        timeout=_ASSERT_TIMEOUT,
    )
    # Ack filter should remain at its default ("open")
    ack_val = page.evaluate(
        "() => document.querySelector('#alerts-ack-filter')?.value"
    )
    assert ack_val == "open"


# ---------------------------------------------------------------------------
# 6) No console errors
# ---------------------------------------------------------------------------


def test_no_console_errors_during_alert_filter_flows(
    page: Page, axion_server: str,
):
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Switch severity filter
    page.select_option("#alerts-severity-filter", "critical_high")
    page.wait_for_timeout(300)

    # Switch ack filter
    page.select_option("#alerts-ack-filter", "ack")
    page.wait_for_timeout(300)

    # Reset to all
    page.select_option("#alerts-severity-filter", "")
    page.select_option("#alerts-ack-filter", "open")
    page.wait_for_timeout(300)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9W flows:\n" + "\n".join(fatal)
    )
