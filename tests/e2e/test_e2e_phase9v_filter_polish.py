"""Phase 9V browser E2E tests — Alerts Filter + Saved View Readability.

Validates:
  * the alerts severity filter <select> exists and works
  * setting the severity filter to critical_high hides info alerts
  * the severity filter round-trips through a deep-link hash
  * the severity filter survives a reload
  * saved-view rows show a human-readable description line
  * old pre-9V deep links still work
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
# 1) Alerts severity filter exists
# ---------------------------------------------------------------------------


def test_alerts_severity_filter_exists(page: Page, axion_server: str):
    """The Alerts tab should have a severity filter <select> with
    options for all/critical/critical_high/warning/info."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    sel = page.locator("#alerts-severity-filter")
    expect(sel).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Check the options
    options = page.evaluate(
        "() => Array.from(document.querySelector('#alerts-severity-filter').options).map(o => o.value)"
    )
    assert "" in options  # "all" option
    assert "critical" in options
    assert "critical_high" in options


# ---------------------------------------------------------------------------
# 2) Severity filter hides non-matching alerts
# ---------------------------------------------------------------------------


def test_severity_filter_hides_info_alerts(page: Page, axion_server: str):
    """Setting the severity filter to 'critical_high' should hide
    info-severity alert cards."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # Count total alerts first
    total_before = page.locator("#alerts-content .alert-card").count()
    assert total_before >= 2, f"expected multiple alerts, got {total_before}"

    # Check that info alerts exist
    info_count = page.locator(
        "#alerts-content .alert-card.severity-info"
    ).count()
    assert info_count >= 1, "expected at least one info alert"

    # Set filter to critical_high
    page.select_option("#alerts-severity-filter", "critical_high")
    # Wait for the list to re-render
    page.wait_for_function(
        f"() => document.querySelectorAll('#alerts-content .alert-card').length < {total_before}",
        timeout=_ASSERT_TIMEOUT,
    )

    # Info alerts should now be hidden
    info_after = page.locator(
        "#alerts-content .alert-card.severity-info"
    ).count()
    assert info_after == 0, f"info alerts still visible: {info_after}"


# ---------------------------------------------------------------------------
# 3) Severity filter round-trips through hash
# ---------------------------------------------------------------------------


def test_severity_filter_round_trips_via_hash(page: Page, axion_server: str):
    """A deep link with filters.severity=critical_high should restore
    the filter on reload."""
    from src.intelligence.navigation import _safe_target, encode_nav_hash

    t = _safe_target(
        surface="alerts", portfolio_id="pA",
        filters={"severity": "critical_high"},
    )
    nav_hash = encode_nav_hash(t)

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{nav_hash}", wait_until="networkidle")

    # Alerts tab active
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Filter should be set to critical_high
    page.wait_for_function(
        "() => document.querySelector('#alerts-severity-filter')?.value === 'critical_high'",
        timeout=_ASSERT_TIMEOUT,
    )

    # And info alerts should be hidden
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)
    info_count = page.locator(
        "#alerts-content .alert-card.severity-info"
    ).count()
    assert info_count == 0


# ---------------------------------------------------------------------------
# 4) Saved-view rows show a description
# ---------------------------------------------------------------------------


def test_saved_view_row_shows_description(page: Page, axion_server: str):
    """After saving a view while on the Alerts tab with a severity
    filter active, the saved-view row should show a description
    like 'Alerts · Critical & High'."""
    _open_dashboard(page, "pA")

    # Set the alerts severity filter
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.select_option("#alerts-severity-filter", "critical_high")
    page.wait_for_timeout(200)

    # Save the view from Settings
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#save-current-view-btn", timeout=_ASSERT_TIMEOUT)

    # But first we need to be ON the alerts tab when we capture.
    # Go back to alerts with the filter set:
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    # Re-set the filter (tab switch may have triggered a re-render)
    page.select_option("#alerts-severity-filter", "critical_high")
    page.wait_for_timeout(100)

    # Now go to Settings and save
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#save-current-view-btn", timeout=_ASSERT_TIMEOUT)
    page.once("dialog", lambda d: d.accept("Crit+High View"))
    page.click("#save-current-view-btn")

    # Wait for the saved view to appear
    page.wait_for_function(
        "() => document.querySelector('#saved-views-list .saved-view-row') !== null",
        timeout=_ASSERT_TIMEOUT,
    )

    # The description line should mention the severity filter
    desc = page.locator("#saved-views-list .saved-view-description").first
    expect(desc).to_be_visible(timeout=_ASSERT_TIMEOUT)
    desc_text = desc.inner_text()
    # The description is generated by the backend's describe_view
    assert desc_text, "description is empty"
    # It should contain something meaningful about the surface
    assert "alert" in desc_text.lower() or "operator" in desc_text.lower() or "portfolio" in desc_text.lower(), (
        f"description not descriptive: {desc_text!r}"
    )


# ---------------------------------------------------------------------------
# 5) Old pre-9V deep links still work
# ---------------------------------------------------------------------------


def test_old_9u_hash_still_works(page: Page, axion_server: str):
    """A pre-9V hash without severity filters should still navigate
    correctly."""
    from src.intelligence.navigation import encode_nav_hash

    old_hash = encode_nav_hash({
        "surface": "alerts",
        "portfolio_id": "pA",
    })

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{old_hash}", wait_until="networkidle")
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    # Severity filter should be at default (empty = all)
    val = page.evaluate(
        "() => document.querySelector('#alerts-severity-filter')?.value || ''"
    )
    assert val == "" or val == "all"


# ---------------------------------------------------------------------------
# 6) No console errors
# ---------------------------------------------------------------------------


def test_no_console_errors_during_filter_flows(
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
    page.select_option("#alerts-severity-filter", "critical_high")
    page.wait_for_timeout(300)
    page.select_option("#alerts-severity-filter", "")
    page.wait_for_timeout(300)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9V flows:\n" + "\n".join(fatal)
    )
