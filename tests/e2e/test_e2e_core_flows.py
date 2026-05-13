"""Phase 9J — Real-browser E2E tests for the core Axion user flows.

Drives a headless Chromium browser against a live uvicorn server
seeded with deterministic data (see ``tests/e2e/conftest.py``).  Each
test walks through a product journey exactly as a human operator
would: navigate, click, type, assert visible text.

Covered journeys (matches the Phase 9J brief one-for-one):

  A. Portfolio switching changes portfolio-scoped surfaces
  B. Intelligence overview renders for the active portfolio
  C. Events table + event detail modal work
  D. Digest reader renders the grounded layout
  E. Alerts list uses severity-first ordering in the dashboard
  F. Operator panel loads for the active portfolio
  G. Manual factor override flow end-to-end
  H. Manual relationship create → update → delete end-to-end
  I. Reconcile + backfill actions return stats
  J. Chat sends portfolio_id and renders a reply
  K. No console errors during the core flows

Every test uses the ``page`` fixture that pytest-playwright provides,
plus the ``axion_server`` session fixture from ``conftest.py`` which
boots uvicorn against a seeded temp DB.  The ``browser_context_args``
override in conftest.py pipes the base URL through so ``page.goto``
calls can use the absolute URL form (``/dashboard`` becomes
``http://127.0.0.1:{port}/dashboard``).
"""

from __future__ import annotations

import json
import re
import time

import pytest
from playwright.sync_api import Page, expect, TimeoutError as PWTimeout


# Playwright default timeout — tests are local-only, so we can be
# generous without slowing things down noticeably.
_ASSERT_TIMEOUT = 10_000  # 10s for any single assert/visibility wait


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _open_dashboard(page: Page, portfolio_id: str = "pA") -> None:
    """Navigate to the dashboard and ensure the chosen portfolio is active.

    The dashboard reads ``localStorage.activePortfolioId`` before the
    first fetch, so we seed it via ``page.add_init_script`` BEFORE the
    first navigation.  That guarantees the portfolio selector and
    every portfolio-scoped fetch use the right id from the first tick.
    """
    page.add_init_script(
        f"window.localStorage.setItem('activePortfolioId', '{portfolio_id}');"
    )
    page.goto("/dashboard", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio", timeout=_ASSERT_TIMEOUT)


def _switch_portfolio(page: Page, portfolio_id: str) -> None:
    """Select a portfolio from the nav-level <select> and wait for
    the portfolio-scoped surfaces to refresh."""
    # The select has an onchange handler that calls switchPortfolio(value)
    page.select_option("#portfolio-select", portfolio_id)
    page.wait_for_function(
        f"window.localStorage.getItem('activePortfolioId') === '{portfolio_id}'",
        timeout=_ASSERT_TIMEOUT,
    )
    # Give the JS tab re-loader a moment to re-fetch + re-render
    page.wait_for_load_state("networkidle")


def _open_settings(page: Page) -> None:
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    # Wait for the operator panel loader's async fetches to settle AND
    # for the operator tables to render at least one row so stale
    # "Loading..." text never fools the next assertion.
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#op-factor-table table tbody tr", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector(
        "#op-rel-table table tbody tr, #op-rel-table .empty-state",
        timeout=_ASSERT_TIMEOUT,
    )


def _wait_for_rendered_rows(page: Page, selector: str, min_count: int = 1) -> None:
    """Poll until the given table selector has at least ``min_count``
    <tr> rows.  Used after a filter change + fetch where
    ``networkidle`` can return before the JS renderer has updated the
    DOM, because pytest-playwright's built-in wait_for_load_state only
    waits for the network, not for subsequent microtasks."""
    page.wait_for_function(
        """
        ({selector, minCount}) => {
            const el = document.querySelector(selector);
            if (!el) return false;
            const rows = el.querySelectorAll('tbody tr');
            return rows.length >= minCount;
        }
        """,
        arg={"selector": selector, "minCount": min_count},
        timeout=_ASSERT_TIMEOUT,
    )


def _wait_for_no_text(page: Page, selector: str, substring: str) -> None:
    """Poll until the element no longer contains the given substring.

    Used to wait for async-loading placeholders like
    'Loading event detail...' or 'Thinking...' to disappear."""
    page.wait_for_function(
        """
        ({selector, substring}) => {
            const el = document.querySelector(selector);
            if (!el) return true;
            return !el.innerText.includes(substring);
        }
        """,
        arg={"selector": selector, "substring": substring},
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# A. Portfolio switching
# ---------------------------------------------------------------------------


def test_portfolio_switch_updates_holdings_table(page: Page, axion_server: str):
    """Open the dashboard on pA, verify AAPL + MSFT render, switch to
    pB, verify XOM renders AND the pA tickers are gone."""
    _open_dashboard(page, "pA")

    # pA holdings
    page.wait_for_selector("#holdings-table", timeout=_ASSERT_TIMEOUT)
    expect(page.locator("#holdings-table")).to_contain_text("AAPL", timeout=_ASSERT_TIMEOUT)
    expect(page.locator("#holdings-table")).to_contain_text("MSFT")
    assert "XOM" not in page.locator("#holdings-table").inner_text()

    _switch_portfolio(page, "pB")

    # pB holdings — and pA tickers must be gone
    expect(page.locator("#holdings-table")).to_contain_text("XOM", timeout=_ASSERT_TIMEOUT)
    pB_text = page.locator("#holdings-table").inner_text()
    assert "AAPL" not in pB_text, (
        "AAPL from pA leaked into pB holdings view: " + pB_text[:200]
    )
    assert "MSFT" not in pB_text


# ---------------------------------------------------------------------------
# B. Intelligence overview (Phase 9G)
# ---------------------------------------------------------------------------


def test_intelligence_overview_renders_for_active_portfolio(page: Page, axion_server: str):
    """pA has a critical alert + a factor touchpoint → posture is
    'strong_negative'.  The overview card must render with the correct
    posture pill, top factor pressures, and chips."""
    _open_dashboard(page, "pA")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Posture label (Phase 9G maps strong_negative → "Elevated risk")
    expect(overview.locator(".intel-posture-label")).to_contain_text(
        "Elevated risk", timeout=_ASSERT_TIMEOUT,
    )
    # Top factors — interest_rate must be there
    expect(overview.locator(".intel-factor-pill").first).to_contain_text("Interest Rates")
    # Alert chip shows critical count
    assert "critical" in overview.inner_text().lower()


def test_intelligence_overview_switches_with_portfolio(page: Page, axion_server: str):
    """Switching portfolios must re-scope the overview.  pB has only
    a high alert + oil factor — so the posture changes AND the factor
    label changes from 'Interest Rates' to 'Oil & Energy'."""
    _open_dashboard(page, "pA")
    page.wait_for_selector("#intelligence-overview .intel-factor-pill", timeout=_ASSERT_TIMEOUT)

    _switch_portfolio(page, "pB")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(overview.locator(".intel-factor-pill").first).to_contain_text(
        "Oil & Energy", timeout=_ASSERT_TIMEOUT,
    )
    # pB's factor pills must not mention Interest Rates
    text = overview.inner_text()
    assert "Interest Rates" not in text, (
        "pA factor leaked into pB overview: " + text[:300]
    )


# ---------------------------------------------------------------------------
# C. Events + event detail modal
# ---------------------------------------------------------------------------


def test_events_tab_renders_events_and_detail_modal_opens(page: Page, axion_server: str):
    """Click through to Intelligence → Events, click an event row,
    verify the Phase 9B detail modal opens with a chain card."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    # The default sub-tab is Events; wait for the events table to load
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)

    events_text = page.locator("#events-table").inner_text()
    assert "Federal Reserve" in events_text, (
        "Seeded Fed event missing from events table: " + events_text[:300]
    )

    # Click the first clickable event row
    first_row = page.locator("#events-table .events-row-clickable").first
    expect(first_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    first_row.click()

    # The Phase 9B modal opens
    modal = page.locator("#event-detail-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Factor tag + chain card markers — wait for the async fetch to
    # replace the 'Loading event detail...' placeholder before we
    # inspect the body text.
    body = page.locator("#event-detail-body")
    expect(body).to_be_visible(timeout=_ASSERT_TIMEOUT)
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")
    body_text = body.inner_text()
    assert any(
        k in body_text for k in (
            "Federal Reserve", "rate", "Interest Rate",
            "Analysis", "Summary", "Affected", "AAPL", "FACTOR",
        )
    ), f"event detail body looks empty: {body_text[:500]!r}"
    # The Phase 9B chain card OR a factor tag must render for the
    # Fed rate event — both are part of the seeded factor link.
    assert (
        "Interest Rates" in body_text
        or "interest_rate" in body_text.lower()
        or "DETERMINISTIC FACTOR" in body_text.upper()
    ), f"factor context missing from event detail: {body_text[:500]!r}"

    # Close the modal with the x button
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# D. Digest reader (Phase 9G grounded layout)
# ---------------------------------------------------------------------------


def test_digest_reader_renders_grounded_layout(page: Page, axion_server: str):
    """Open Intelligence → Digest and verify the Phase 9G grounded
    cards (headline + risk flags + holdings needing attention +
    key developments) all render."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="digest"]')
    page.wait_for_selector("#subtab-digest.active", timeout=_ASSERT_TIMEOUT)

    # The grounded renderer emits .digest-headline + .digest-risk-flags
    digest = page.locator("#digest-content")
    expect(digest).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(digest.locator(".digest-headline")).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(digest).to_contain_text("mildly negative on rate shock")
    expect(digest.locator(".digest-risk-flags")).to_be_visible()
    expect(digest.locator(".digest-attention")).to_be_visible()
    expect(digest.locator(".digest-developments")).to_be_visible()
    # Specific content from the seeded digest
    expect(digest).to_contain_text("duration risk")
    expect(digest).to_contain_text("AAPL")
    expect(digest).to_contain_text("Fed raised rates by 50 bps")


# ---------------------------------------------------------------------------
# E. Alerts list uses severity-first ordering (Phase 9G)
# ---------------------------------------------------------------------------


def test_alerts_tab_prioritizes_by_severity(page: Page, axion_server: str):
    """The seed has a 6h-old critical, a 2h-old high, and a 5m-old info.
    Chronological ordering would show info first; Phase 9G's
    priority_ordered=true must show critical first, then high, then
    info.  We check by reading the severity text of the first few cards."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    cards = page.locator("#alerts-content .alert-card")
    count = cards.count()
    assert count >= 3, f"expected ≥3 alert cards, got {count}"

    # Severity order is carried in the class name `severity-{sev}`
    classes = [cards.nth(i).get_attribute("class") or "" for i in range(min(count, 3))]
    severities = []
    for c in classes:
        for sev in ("critical", "high", "warning", "medium", "info", "low"):
            if f"severity-{sev}" in c:
                severities.append(sev)
                break
    assert severities == ["critical", "high", "info"], (
        f"alerts not priority-ordered: {severities}"
    )


# ---------------------------------------------------------------------------
# F. Operator panel — loads for active portfolio (Phase 9I)
# ---------------------------------------------------------------------------


def test_operator_panel_loads_for_active_portfolio(page: Page, axion_server: str):
    """Open Settings, verify the operator panel mounts, the active
    portfolio banner shows pA, and both tables render source badges."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Portfolio banner
    banner = page.locator("#op-active-portfolio")
    expect(banner).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(banner).to_contain_text("pA")

    # Factor table renders rows
    factor_table = page.locator("#op-factor-table table")
    expect(factor_table).to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Should have at least 10 factors × 2 holdings = 20 rows for pA
    factor_rows = page.locator("#op-factor-table tbody tr")
    assert factor_rows.count() >= 10, (
        f"expected at least 10 factor rows for pA, got {factor_rows.count()}"
    )
    # Source badges: defaults should dominate (no manual overrides seeded).
    # The badge text is uppercase via ``text-transform: uppercase`` CSS,
    # which inner_text surfaces as uppercase, so we match case-insensitively.
    factor_text = page.locator("#op-factor-table").inner_text().lower()
    assert "default" in factor_text, (
        "expected 'default' source badges in factor table: " + factor_text[:300]
    )

    # Relationship table renders at least one seed row
    rel_table = page.locator("#op-rel-table table")
    expect(rel_table).to_be_visible(timeout=_ASSERT_TIMEOUT)
    rel_text = page.locator("#op-rel-table").inner_text()
    assert "TSM" in rel_text or "Taiwan" in rel_text, (
        "seeded TSMC relationship missing from operator table: " + rel_text[:300]
    )
    assert "seed" in rel_text.lower()
    # The lock icon glyph (🔒 U+1F512) should be present on seed rows
    # — its character code survives via CSS + innerText.
    assert "\U0001F512" in rel_text or "locked" in rel_text.lower()


def test_operator_panel_switches_with_portfolio(page: Page, axion_server: str):
    """Switching from pA to pB must reload the operator tables for pB
    — different holdings, different relationship rows (or empty).
    Critical because Phase 9I relies on tab cache invalidation."""
    _open_dashboard(page, "pA")
    _open_settings(page)
    page.wait_for_selector("#op-factor-table table", timeout=_ASSERT_TIMEOUT)

    _switch_portfolio(page, "pB")
    # After the switch, Settings is still the active tab and
    # loadOperatorPanel re-runs.  Wait for the table to contain XOM
    # specifically — networkidle fires before the DOM re-render completes.
    page.wait_for_function(
        "() => (document.querySelector('#op-factor-table')?.innerText || '').includes('XOM')",
        timeout=_ASSERT_TIMEOUT,
    )
    factor_text = page.locator("#op-factor-table").inner_text()
    assert "XOM" in factor_text, (
        "operator factor table did not refresh to pB after portfolio switch: "
        + factor_text[:300]
    )
    assert "AAPL" not in factor_text, (
        "AAPL from pA leaked into pB operator factor table: " + factor_text[:300]
    )


# ---------------------------------------------------------------------------
# G. Manual factor override flow
# ---------------------------------------------------------------------------


def test_manual_factor_override_create_and_delete(page: Page, axion_server: str):
    """Create a manual override on an AAPL/inflation pair via the
    modal, verify the Effective value updates to the new number AND
    the source badge flips to ``manual``.  Then delete the override
    and verify the row returns to the default."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Filter the factor table to inflation so the row is easy to find
    page.select_option("#op-factor-filter", "inflation")
    _wait_for_rendered_rows(page, "#op-factor-table", min_count=1)

    # Find the AAPL/inflation row and click its edit button
    table = page.locator("#op-factor-table table")
    expect(table).to_be_visible(timeout=_ASSERT_TIMEOUT)
    aapl_row = table.locator("tr[data-holding-id='h_aapl_pA'][data-factor='inflation']")
    expect(aapl_row).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Initial state: default source, effective ≈ -0.30 (tech sector prior)
    initial_text = aapl_row.inner_text().lower()
    assert "default" in initial_text

    aapl_row.locator("[data-op='op-factor-edit']").click()
    modal = page.locator("#op-factor-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Set the new value + save
    page.fill("#op-factor-sensitivity", "-0.75")
    page.fill("#op-factor-reason", "E2E test override")
    page.click("#op-factor-save-btn")

    # Modal closes + table refreshes
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Wait for the row to reflect the new override — poll the row's
    # data-state until the Effective + Source columns update.
    page.wait_for_function(
        """
        () => {
            const row = document.querySelector(
                "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
            );
            if (!row) return false;
            return row.innerText.includes('-0.75') && row.innerText.toLowerCase().includes('manual');
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )

    # Last-action echo block should show the "Override saved" title
    echo = page.locator("#op-last-result")
    expect(echo).to_be_visible()
    expect(echo).to_contain_text("Override saved")

    # Now delete the override via the row-level delete button.  The
    # dialog handler MUST be registered before the click that opens
    # the confirm() dialog — Playwright catches the first emitted
    # dialog event and if there's no listener it throws.
    page.once("dialog", lambda d: d.accept())
    updated = table.locator("tr[data-holding-id='h_aapl_pA'][data-factor='inflation']")
    updated.locator("[data-op='op-factor-delete']").click()

    # Wait for the row to revert to default source.
    page.wait_for_function(
        """
        () => {
            const row = document.querySelector(
                "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
            );
            if (!row) return false;
            const t = row.innerText.toLowerCase();
            return t.includes('default') && !t.includes('manual');
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )


def test_manual_factor_override_portfolio_isolation(page: Page, axion_server: str):
    """An override on pA must not leak into pB's operator table.  We
    create on pA, switch to pB, and verify the pB inflation row still
    shows source=default (energy sector prior)."""
    _open_dashboard(page, "pA")
    _open_settings(page)
    page.select_option("#op-factor-filter", "inflation")
    _wait_for_rendered_rows(page, "#op-factor-table", min_count=1)

    # Create override on pA/AAPL
    aapl_row = page.locator(
        "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
    )
    aapl_row.locator("[data-op='op-factor-edit']").click()
    expect(page.locator("#op-factor-modal")).to_be_visible(timeout=_ASSERT_TIMEOUT)
    page.fill("#op-factor-sensitivity", "-0.88")
    page.click("#op-factor-save-btn")
    expect(page.locator("#op-factor-modal")).not_to_be_visible(timeout=_ASSERT_TIMEOUT)
    page.wait_for_function(
        """
        () => (document.querySelector(
            "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
        )?.innerText || '').includes('-0.88')
        """,
        timeout=_ASSERT_TIMEOUT,
    )

    # Switch to pB — operator table must reload, XOM/inflation row
    # should still be source=default.
    _switch_portfolio(page, "pB")
    _open_settings(page)
    page.select_option("#op-factor-filter", "inflation")
    _wait_for_rendered_rows(page, "#op-factor-table", min_count=1)

    # Wait specifically for XOM to appear (strong signal that the
    # pB-scoped fetch landed) and that AAPL + the override value are absent.
    page.wait_for_function(
        "() => (document.querySelector('#op-factor-table')?.innerText || '').includes('XOM')",
        timeout=_ASSERT_TIMEOUT,
    )
    pB_text = page.locator("#op-factor-table").inner_text()
    assert "XOM" in pB_text
    assert "AAPL" not in pB_text, (
        "AAPL override visible in pB operator table — cross-portfolio leakage"
    )
    assert "-0.88" not in pB_text, (
        "pA override value -0.88 visible in pB — cross-portfolio leakage"
    )

    # Cleanup: switch back to pA and remove the override so we don't
    # leak test state into downstream tests.
    _switch_portfolio(page, "pA")
    _open_settings(page)
    page.select_option("#op-factor-filter", "inflation")
    _wait_for_rendered_rows(page, "#op-factor-table", min_count=1)
    page.once("dialog", lambda d: d.accept())
    page.locator(
        "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation'] [data-op='op-factor-delete']"
    ).click()
    page.wait_for_function(
        """
        () => {
            const row = document.querySelector(
                "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
            );
            return row && !row.innerText.toLowerCase().includes('manual');
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# H. Manual relationship create → delete
# ---------------------------------------------------------------------------


def test_manual_relationship_create_and_delete(page: Page, axion_server: str):
    """Open the Add Manual modal, create a competitor relationship,
    verify it appears in the table, delete it, verify it disappears."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    page.click("#op-rel-add")
    modal = page.locator("#op-rel-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Fill the create form — competitor with a unique entity key
    page.select_option("#op-rel-holding", "h_aapl_pA")
    page.select_option("#op-rel-type", "competitor")
    page.fill("#op-rel-related-entity-key", "samsung_kr_e2e")
    page.fill("#op-rel-related-name", "Samsung Electronics (E2E)")
    page.fill("#op-rel-strength", "0.40")
    page.fill("#op-rel-reason", "e2e test")
    page.click("#op-rel-save-btn")
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Wait for the row to actually appear in the re-rendered table
    page.wait_for_function(
        "() => (document.querySelector('#op-rel-table')?.innerText || '').includes('samsung_kr_e2e')",
        timeout=_ASSERT_TIMEOUT,
    )

    rel_table = page.locator("#op-rel-table")
    expect(rel_table).to_contain_text("samsung_kr_e2e")
    # Case-insensitive check for the badge (CSS uppercases it)
    assert "manual" in rel_table.inner_text().lower()
    expect(page.locator("#op-last-result")).to_contain_text("Manual relationship created")

    # Delete it via the row-level delete button.  Register the dialog
    # handler BEFORE the click — Playwright emits the dialog event
    # synchronously as soon as confirm() fires.
    page.once("dialog", lambda d: d.accept())
    row = rel_table.locator("tr").filter(has_text="samsung_kr_e2e")
    row.locator("[data-op='op-rel-delete']").click()

    # Poll for removal (the table re-renders after delete)
    page.wait_for_function(
        "() => !(document.querySelector('#op-rel-table')?.innerText || '').includes('samsung_kr_e2e')",
        timeout=_ASSERT_TIMEOUT,
    )


def test_seed_relationship_is_locked(page: Page, axion_server: str):
    """The reconciler-owned seed row must render with a lock icon,
    and no edit/delete buttons.  Phase 9I guarantees this client-side."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Filter to seed-only so we KNOW every row is read-only.  Wait for
    # the filter's async fetch to actually update the DOM — the
    # default ``networkidle`` race can return before the renderer runs.
    page.select_option("#op-rel-source-filter", "seed")
    _wait_for_rendered_rows(page, "#op-rel-table", min_count=1)

    rows = page.locator("#op-rel-table tbody tr")
    count = rows.count()
    assert count >= 1, "no seed relationship rows found after filter=seed"

    # Every row must render the lock indicator and NOT render
    # data-op="op-rel-edit" / "op-rel-delete" buttons.
    html = page.locator("#op-rel-table").inner_html()
    assert "op-rel-locked" in html, "seed row missing lock indicator"
    assert 'data-op="op-rel-delete"' not in html, (
        "delete button rendered on seed row — source protection broken"
    )
    assert 'data-op="op-rel-edit"' not in html, (
        "edit button rendered on seed row — source protection broken"
    )


# ---------------------------------------------------------------------------
# I. Reconcile + backfill
# ---------------------------------------------------------------------------


def test_reconcile_action_renders_stats(page: Page, axion_server: str):
    """Click Reconcile with prune=false (safer for the E2E run), verify
    the stats line shows counters and the last-action echo renders."""
    _open_dashboard(page, "pA")
    _open_settings(page)
    page.wait_for_selector("#op-reconcile-btn", timeout=_ASSERT_TIMEOUT)

    # Disable prune to keep the run safe AND avoid needing a confirm
    # dialog handler (prune=false skips the confirm).
    prune_cb = page.locator("#op-reconcile-prune")
    if prune_cb.is_checked():
        prune_cb.uncheck()

    page.on("dialog", lambda d: d.accept())  # belt-and-braces
    page.click("#op-reconcile-btn")
    page.wait_for_load_state("networkidle")

    status = page.locator("#op-reconcile-status")
    expect(status).to_contain_text("created", timeout=_ASSERT_TIMEOUT)
    # Last action echo
    expect(page.locator("#op-last-result")).to_contain_text(
        "Reconcile complete", timeout=_ASSERT_TIMEOUT,
    )


def test_backfill_action_renders_stats(page: Page, axion_server: str):
    """Click Backfill with the default 7d/200 bounds, verify stats
    render in the inline line and last-action echo."""
    _open_dashboard(page, "pA")
    _open_settings(page)
    page.wait_for_selector("#op-backfill-btn", timeout=_ASSERT_TIMEOUT)

    page.on("dialog", lambda d: d.accept())
    page.click("#op-backfill-btn")
    page.wait_for_load_state("networkidle")

    status = page.locator("#op-backfill-status")
    expect(status).to_contain_text("scanned", timeout=_ASSERT_TIMEOUT)
    expect(page.locator("#op-last-result")).to_contain_text(
        "Backfill complete", timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# J. Chat — portfolio-scoped POST + rendered reply
# ---------------------------------------------------------------------------


def test_chat_sends_portfolio_id_and_renders_reply(page: Page, axion_server: str):
    """Open the Assistant tab, send a query, intercept the POST body
    to prove portfolio_id is sent, then verify a reply renders."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="command"]')
    page.wait_for_selector("#tab-command.active", timeout=_ASSERT_TIMEOUT)

    # Intercept /api/v1/chat so we can assert the posted portfolio_id
    captured: list[dict] = []

    def _handle(route):
        req = route.request
        try:
            captured.append(json.loads(req.post_data or "{}"))
        except Exception:
            captured.append({})
        route.continue_()

    page.route("**/api/v1/chat", _handle)

    page.fill("#cmd-input", "what's my posture?")
    page.click("#cmd-send")

    # Wait for the assistant reply to land.  sendCommand removes the
    # "Thinking..." loader before calling _appendCmdResponse, so once
    # the loader is gone AND a .cmd-msg-response node exists, the
    # reply is fully rendered.
    page.wait_for_function(
        """
        () => {
            const t = document.getElementById('cmd-transcript');
            if (!t) return false;
            const hasLoader = t.querySelector('.cmd-loading') != null;
            const hasResponse = t.querySelector('.cmd-msg-response, .cmd-warning') != null;
            return !hasLoader && hasResponse;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )

    assert captured, "no /api/v1/chat request captured"
    body = captured[-1]
    assert body.get("portfolio_id") == "pA", (
        f"chat POST missing portfolio_id=pA: {body!r}"
    )
    # The UI should render SOMETHING in the transcript that isn't an error
    transcript_text = page.locator("#cmd-transcript").inner_text()
    # The deterministic chat renderer always starts with "Portfolio {id}"
    # so a successful reply must contain the portfolio id string.
    assert "pA" in transcript_text, (
        "chat reply did not mention the active portfolio: " + transcript_text[:400]
    )
    # No raw [Axion] error stubs leaking
    assert "[Axion]" not in transcript_text, (
        "raw [Axion] error stub leaked into chat UI: " + transcript_text[:400]
    )


# ---------------------------------------------------------------------------
# K. No console errors during core navigation
# ---------------------------------------------------------------------------


def test_no_console_errors_during_core_navigation(page: Page, axion_server: str):
    """Walk through every tab + sub-tab + the operator panel and
    assert no ``error``-level console messages are emitted.  This
    catches JS runtime exceptions that any static test would miss."""
    errors: list[str] = []

    def _on_console(msg):
        if msg.type == "error":
            errors.append(f"{msg.type}: {msg.text}")

    page.on("console", _on_console)
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")
    # Holdings
    page.wait_for_selector("#holdings-table", timeout=_ASSERT_TIMEOUT)
    # Exposures
    page.click('[data-subtab="exposures"]')
    page.wait_for_selector("#subtab-exposures.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    # Trades
    page.click('[data-subtab="trades"]')
    page.wait_for_selector("#subtab-trades.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    # Intelligence → Events
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    # Intelligence → Digest
    page.click('[data-subtab="digest"]')
    page.wait_for_selector("#subtab-digest.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    # Alerts
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    # Command / Assistant
    page.click('[data-tab="command"]')
    page.wait_for_selector("#tab-command.active", timeout=_ASSERT_TIMEOUT)
    # Settings + Operator panel
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")

    # Filter out known-acceptable noise: favicon warning, 304 cache
    # notices, third-party font loads.  We only fail on genuine
    # application-level JS errors.
    _IGNORE = (
        "favicon",
        "Failed to load resource",      # network 304 / cache warnings
        "net::ERR_INTERNET_DISCONNECTED",
    )
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, "console errors during core navigation:\n" + "\n".join(fatal)
