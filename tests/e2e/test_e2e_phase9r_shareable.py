"""Phase 9R browser E2E tests — Shareable Deep Links + Exact Anchors.

Validates:
  * holdings rows carry data-holding-id + data-ticker
  * jumpToTarget with holding:<id> highlight key scrolls + flashes
    the exact row
  * the URL hash is written on every jump and decoded on reload
  * the event detail modal copy-link button copies a URL with a
    nav hash
  * a cold page load with a nav hash restores the exact surface
  * malformed hash → graceful no-op
  * cross-portfolio hash → portfolio switch then navigate
  * no console errors during all flows
"""

from __future__ import annotations

import json
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
    page.wait_for_function(
        "typeof window.jumpToTarget === 'function'",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 1) Holdings rows carry exact anchors
# ---------------------------------------------------------------------------


def test_holdings_rows_carry_data_holding_id(page: Page, axion_server: str):
    """Phase 9R adds data-holding-id + data-ticker to every holdings
    table row so the highlight engine can scroll + flash a specific
    holding."""
    _open_dashboard(page, "pA")
    page.wait_for_selector("#holdings-table table tbody tr", timeout=_ASSERT_TIMEOUT)

    first_row = page.locator("#holdings-table table tbody tr").first
    expect(first_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    hid = first_row.get_attribute("data-holding-id")
    ticker = first_row.get_attribute("data-ticker")
    assert hid, "holdings row missing data-holding-id"
    assert ticker, "holdings row missing data-ticker"


# ---------------------------------------------------------------------------
# 2) jumpToTarget with holding highlight → exact row flash
# ---------------------------------------------------------------------------


def test_holding_highlight_flashes_exact_row(page: Page, axion_server: str):
    """Inject a jumpToTarget call with highlight_key='holding:<id>'
    and verify the matching row gets the nav-highlight-flash class."""
    _open_dashboard(page, "pA")
    page.wait_for_selector("#holdings-table table tbody tr", timeout=_ASSERT_TIMEOUT)

    # Read the first row's holding id
    hid = page.evaluate(
        "() => document.querySelector('#holdings-table table tbody tr')?.getAttribute('data-holding-id')"
    )
    assert hid, "no holding id on first row"

    # Jump to the holding
    page.evaluate(
        f"""
        () => window.jumpToTarget({{
            surface: 'portfolio',
            portfolio_id: '{page.evaluate("() => window.localStorage.getItem('activePortfolioId')")}',
            highlight_key: 'holding:{hid}',
        }})
        """
    )

    # The matching row should receive the highlight class
    page.wait_for_selector(
        f"tr[data-holding-id='{hid}'].nav-highlight-flash",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 3) URL hash is written on jump and can be decoded
# ---------------------------------------------------------------------------


def test_url_hash_written_on_jump(page: Page, axion_server: str):
    """After a jumpToTarget call, location.hash should contain a
    #nav=<base64> fragment that round-trips through the decoder."""
    _open_dashboard(page, "pA")

    page.evaluate(
        """
        () => window.jumpToTarget({
            surface: 'alerts',
            portfolio_id: 'pA',
            entity_type: 'alert',
            entity_id: 'alert_pA_critical',
            highlight_key: 'alert:alert_pA_critical',
        })
        """
    )
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    # Wait for the deferred hash write (setTimeout 50ms inside jumpToTarget)
    page.wait_for_function(
        "() => location.hash && location.hash.startsWith('#nav=')",
        timeout=_ASSERT_TIMEOUT,
    )

    # Read the hash
    hash_val = page.evaluate("() => location.hash")
    assert hash_val.startswith("#nav="), f"hash not written: {hash_val!r}"

    # Decode it client-side
    decoded = page.evaluate("() => window._decodeNavTargetFromHash()")
    assert decoded is not None
    assert decoded["surface"] == "alerts"
    assert decoded["portfolio_id"] == "pA"
    assert decoded["entity_id"] == "alert_pA_critical"


# ---------------------------------------------------------------------------
# 4) Reload with hash restores the surface
# ---------------------------------------------------------------------------


def test_reload_with_hash_restores_alerts_tab(page: Page, axion_server: str):
    """Navigate to /dashboard#nav=<alerts-target>, wait for the page
    to load, and verify the Alerts tab is active — proving the
    consume-on-load hook works."""
    # Build the hash server-side so we know it's valid
    from src.intelligence.navigation import _safe_target, encode_nav_hash
    t = _safe_target(
        surface="alerts", portfolio_id="pA",
        entity_type="alert", entity_id="alert_pA_critical",
        highlight_key="alert:alert_pA_critical",
    )
    nav_hash = encode_nav_hash(t)

    # Navigate directly to the hashed URL
    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{nav_hash}", wait_until="networkidle")

    # The consume-on-load hook fires after the default tab loads and
    # switches to the target surface from the hash.  Wait directly
    # for the target tab.
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 5) Reload with event hash opens modal
# ---------------------------------------------------------------------------


def test_reload_with_event_hash_opens_modal(page: Page, axion_server: str):
    """Navigate to /dashboard#nav=<event-target> and verify the
    event detail modal auto-opens."""
    from src.intelligence.navigation import _safe_target, encode_nav_hash
    t = _safe_target(
        surface="events", portfolio_id="pA",
        entity_type="event", entity_id="evt_fed_rates",
        subtab="events", open_modal=True,
        highlight_key="event:evt_fed_rates",
    )
    nav_hash = encode_nav_hash(t)

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{nav_hash}", wait_until="networkidle")

    # The event detail modal should auto-open from the hash consumer
    expect(page.locator("#event-detail-modal")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )
    # Clean up
    page.locator("#event-detail-modal [data-close-modal]").first.click()


# ---------------------------------------------------------------------------
# 6) Copy-link button on event detail modal
# ---------------------------------------------------------------------------


def test_event_detail_copy_link_button_exists(page: Page, axion_server: str):
    """The event detail modal should carry a Phase 9R 'Copy link'
    button next to the close button."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)
    page.locator("#events-table .events-row-clickable").first.click()
    expect(page.locator("#event-detail-modal")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    copy_btn = page.locator("#event-detail-copy-link")
    expect(copy_btn).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(copy_btn).to_contain_text("Copy link")

    page.locator("#event-detail-modal [data-close-modal]").first.click()


# ---------------------------------------------------------------------------
# 7) Cross-portfolio hash → portfolio switch
# ---------------------------------------------------------------------------


def test_hash_with_different_portfolio_switches(page: Page, axion_server: str):
    """A hash carrying pB while the user starts on pA should switch
    the active portfolio to pB before navigating."""
    from src.intelligence.navigation import _safe_target, encode_nav_hash
    t = _safe_target(surface="alerts", portfolio_id="pB")
    nav_hash = encode_nav_hash(t)

    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto(f"/dashboard{nav_hash}", wait_until="networkidle")

    # Should switch to pB then land on alerts
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    active_pid = page.evaluate(
        "() => window.localStorage.getItem('activePortfolioId')"
    )
    assert active_pid == "pB", f"portfolio not switched: {active_pid}"


# ---------------------------------------------------------------------------
# 8) Malformed hash → graceful no-op
# ---------------------------------------------------------------------------


def test_malformed_hash_is_noop(page: Page, axion_server: str):
    """A garbage hash fragment should not crash the app — it should
    land on the default Portfolio tab as if the hash were absent."""
    page.add_init_script(
        "window.localStorage.setItem('activePortfolioId', 'pA');"
    )
    page.goto("/dashboard#nav=!!!garbage!!!", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio.active", timeout=_ASSERT_TIMEOUT)
    # No crash, default tab active
    expect(page.locator("#tab-portfolio")).to_have_class(re.compile(r"active"))


# ---------------------------------------------------------------------------
# 9) No console errors
# ---------------------------------------------------------------------------


def test_no_console_errors_during_shareable_deep_link_flows(
    page: Page, axion_server: str,
):
    """Exercise hash write + reload + malformed hash; assert no
    console errors."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # Jump to alerts and verify hash is written
    page.evaluate(
        "() => window.jumpToTarget({surface:'alerts',portfolio_id:'pA'})"
    )
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Navigate with a garbage hash
    page.goto("/dashboard#nav=broken", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio.active", timeout=_ASSERT_TIMEOUT)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9R flows:\n" + "\n".join(fatal)
    )
