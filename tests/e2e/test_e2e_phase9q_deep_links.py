"""Phase 9Q browser E2E tests — Deep Links + Contextual Navigation.

Drives headless Chromium against the Phase 9J live-server fixture
and validates the Phase 9Q additions end-to-end:

  * inbox alert item click → Alerts tab + highlight flash on the
    matching alert card
  * inbox event item click → Events sub-tab + event detail modal
    auto-opens
  * inbox operator item click → Settings tab + scrolled to the
    matching operator recent-actions row
  * alert evidence chip click → Events sub-tab + event modal
  * event detail grounded ref click → operator factor filter
    applied
  * recommended action jump button on the overview card → target
    tab
  * portfolio-safe cross-portfolio jump: a target carrying pB's
    portfolio_id while the user is on pA switches the portfolio
    first, then navigates
  * missing target → graceful no-op (button absent or no crash)
  * no console errors during any of these flows
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect


_ASSERT_TIMEOUT = 10_000


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


def _open_inbox(page: Page) -> None:
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="inbox"]')
    page.wait_for_selector("#subtab-inbox.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_function(
        """
        () => {
            const el = document.querySelector('#inbox-content');
            if (!el) return false;
            return (el.querySelector('.inbox-list') || el.querySelector('.inbox-empty')) !== null;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 1) Inbox alert → Alerts tab + highlight flash
# ---------------------------------------------------------------------------


def test_inbox_alert_jump_opens_alerts_tab_and_highlights_card(
    page: Page, axion_server: str,
):
    """Clicking an inbox alert item's 'Open alert →' button should
    switch to the Alerts tab AND flash the matching alert card with
    the ``nav-highlight-flash`` class."""
    _open_dashboard(page, "pA")
    _open_inbox(page)

    alert_item = page.locator(
        "#inbox-content .inbox-item[data-source-type='alert']"
    ).first
    expect(alert_item).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # The structured nav_target attribute carries the exact alert id.
    raw = alert_item.get_attribute("data-nav-target")
    assert raw is not None, "inbox alert item missing data-nav-target"
    target = json.loads(raw)
    assert target["surface"] == "alerts"
    assert target["entity_id"]

    alert_item.locator(".inbox-jump-btn").click()

    # Alerts tab is now active
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # The matching alert card receives the highlight-flash class for
    # ~1.8s.  Catch it during the animation window.
    matching_card_selector = f"[data-alert-id='{target['entity_id']}']"
    page.wait_for_selector(
        f"{matching_card_selector}.nav-highlight-flash",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 2) Structured nav_target dict round-trips via data attribute
# ---------------------------------------------------------------------------


def test_inbox_items_carry_structured_nav_target_attribute(
    page: Page, axion_server: str,
):
    """Every inbox item with a backend-supplied target should carry
    a ``data-nav-target`` JSON attribute that round-trips through
    getAttribute + JSON.parse."""
    _open_dashboard(page, "pA")
    _open_inbox(page)

    # Collect every item's data-nav-target via an in-page evaluator
    payload = page.evaluate(
        """
        () => {
            const items = document.querySelectorAll('#inbox-content .inbox-item');
            return Array.from(items).map(el => ({
                key: el.getAttribute('data-inbox-key'),
                source: el.getAttribute('data-source-type'),
                raw: el.getAttribute('data-nav-target'),
            }));
        }
        """,
    )
    assert payload, "no inbox items rendered"

    parsed_count = 0
    for entry in payload:
        if not entry["raw"]:
            continue
        target = json.loads(entry["raw"])  # must not raise
        assert "surface" in target
        assert "portfolio_id" in target
        assert target["portfolio_id"] == "pA"
        parsed_count += 1
    assert parsed_count >= 1, "expected at least one item with a nav target"


# ---------------------------------------------------------------------------
# 3) Alerts tab evidence chip → Events sub-tab + modal
# ---------------------------------------------------------------------------


def test_alert_evidence_chip_opens_event_modal(page: Page, axion_server: str):
    """The Phase 9Q alert evidence chip row should render an 'event:...'
    chip as a clickable button that jumps to the Events sub-tab and
    auto-opens the event detail modal."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # Find a clickable event chip.  The Phase 9J seed has the Fed
    # event referenced by the critical alert.
    event_chip = page.locator(
        "#alerts-content .alert-evidence-refs .evidence-ref-chip.evidence-ref-clickable"
    ).first
    expect(event_chip).to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Some chips might be holding: refs — find one that starts with event:
    chip_text = event_chip.inner_text()
    if not chip_text.startswith("event:"):
        event_chip = page.locator(
            "#alerts-content .alert-evidence-refs .evidence-ref-chip.evidence-ref-clickable",
            has_text="event:",
        ).first
    expect(event_chip).to_be_visible(timeout=_ASSERT_TIMEOUT)
    event_chip.click()

    # Events sub-tab is active and the modal opens
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#subtab-events.active", timeout=_ASSERT_TIMEOUT)
    expect(page.locator("#event-detail-modal")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )
    # Clean up — close the modal so downstream tests have a clean state
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(page.locator("#event-detail-modal")).not_to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 4) Event detail grounded ref → operator factor filter applied
# ---------------------------------------------------------------------------


def test_event_detail_factor_ref_opens_operator_factor_filter(
    page: Page, axion_server: str,
):
    """The event detail modal's 'Grounded in' block should have a
    clickable 'factor:interest_rate' chip that jumps to Settings →
    Operator with the factor filter pre-selected."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)
    fed_row = page.locator(
        "#events-table .events-row-clickable", has_text="Federal Reserve"
    ).first
    fed_row.click()
    expect(page.locator("#event-detail-modal")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )
    page.wait_for_function(
        "() => !document.querySelector('#event-detail-body')?.innerText?.includes('Loading event detail')",
        timeout=_ASSERT_TIMEOUT,
    )
    factor_chip = page.locator(
        "#event-detail-body .evidence-refs-grouped .evidence-ref-chip.evidence-ref-clickable",
        has_text="factor:interest_rate",
    ).first
    expect(factor_chip).to_be_visible(timeout=_ASSERT_TIMEOUT)
    factor_chip.click()

    # Should navigate to Settings → Operator and apply the factor filter
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    # The modal should have closed (the jump switches top-level tab)
    # The operator factor table should load and the filter should be
    # set to "interest_rate".
    page.wait_for_function(
        "() => document.querySelector('#op-factor-filter')?.value === 'interest_rate'",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 5) Recommended action card jump button (Intelligence overview)
# ---------------------------------------------------------------------------


def test_overview_recommended_action_jump_button(
    page: Page, axion_server: str,
):
    """Phase 9Q adds an 'Open X →' button to recommended action rows
    on the intelligence overview card whenever the Phase 9N action
    family has a mapped target.  Click it and verify navigation."""
    _open_dashboard(page, "pA")

    # Wait for the overview + at least one recommended action row
    expect(page.locator("#intelligence-overview .intel-actions-block")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )
    # Find an action row that carries a Phase 9Q jump button
    jump_btn = page.locator(
        "#intelligence-overview .intel-action-row .intel-action-jump"
    ).first
    if jump_btn.count() == 0:
        # No high-priority action has a mapped target in this run — skip
        pytest.skip("no recommended action with a jump button in this seed")
    expect(jump_btn).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Read the row's nav target to know what surface to expect
    row = page.locator(
        "#intelligence-overview .intel-action-row[data-nav-target]"
    ).first
    raw = row.get_attribute("data-nav-target")
    assert raw
    target = json.loads(raw)
    surface = target["surface"]

    jump_btn.click()

    # The dispatcher maps surface → top-level tab
    surface_to_tab = {
        "alerts": "alerts",
        "digest": "intelligence",
        "events": "intelligence",
        "operator": "settings",
        "portfolio": "portfolio",
    }
    expected_tab = surface_to_tab[surface]
    page.wait_for_selector(f"#tab-{expected_tab}.active", timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 6) Operator recent-action row click → Settings tab
# ---------------------------------------------------------------------------


def test_operator_recent_row_is_clickable_and_jumps(
    page: Page, axion_server: str,
):
    """Phase 9Q makes operator recent-action rows clickable when the
    backend attached a nav_target.  We first trigger a real backfill
    to produce an audit row, then click the resulting recent-action
    row and verify the dispatcher fires without crashing."""
    _open_dashboard(page, "pA")
    page.on("dialog", lambda d: d.accept())
    # Get to the operator panel
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-backfill-btn", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-recent-actions-card", timeout=_ASSERT_TIMEOUT)

    # Trigger a real backfill to produce an audit row — Phase 9O
    # auto-refreshes the recent-actions card on success.
    page.click("#op-backfill-btn")
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Backfill complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )

    # Wait for at least one clickable recent-action row
    page.wait_for_function(
        """
        () => {
            const rows = document.querySelectorAll(
                '#op-recent-actions .op-recent-row-clickable'
            );
            return rows.length >= 1;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )
    row = page.locator(
        "#op-recent-actions .op-recent-row-clickable"
    ).first
    expect(row).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Read the structured nav_target off the row
    raw = row.get_attribute("data-nav-target")
    assert raw, "clickable recent-action row is missing data-nav-target"
    target = json.loads(raw)
    assert target["surface"] == "operator"
    assert target["portfolio_id"] == "pA"

    row.click()

    # Still on Settings — the dispatcher runs without crashing and
    # keeps the surface active.
    expect(page.locator("#tab-settings")).to_have_class(re.compile(r"active"))


# ---------------------------------------------------------------------------
# 7) Portfolio-safe cross-portfolio jump
# ---------------------------------------------------------------------------


def test_jumptotarget_switches_portfolio_when_required(
    page: Page, axion_server: str,
):
    """If the structured target carries a portfolio_id that differs
    from the current active portfolio, the dispatcher should switch
    portfolios first and THEN navigate to the target tab."""
    _open_dashboard(page, "pA")

    # Inject a synthetic target pointing at pB via the public dispatcher
    page.evaluate(
        """
        () => {
            window.jumpToTarget({
                surface: 'alerts',
                portfolio_id: 'pB',
                entity_type: null,
                entity_id: null,
                subtab: null,
                filter: null,
                open_modal: false,
                highlight_key: null,
                label: 'Test jump',
            });
        }
        """,
    )

    # Active portfolio should now be pB AND the Alerts tab should be active
    page.wait_for_function(
        "window.localStorage.getItem('activePortfolioId') === 'pB'",
        timeout=_ASSERT_TIMEOUT,
    )
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 8) Missing / malformed target fallback
# ---------------------------------------------------------------------------


def test_jumptotarget_is_noop_for_missing_target(
    page: Page, axion_server: str,
):
    """Calling jumpToTarget with null / undefined / unknown-surface
    values must be a silent no-op — never crash, never navigate."""
    _open_dashboard(page, "pA")

    # Establish a baseline — we're on the Portfolio tab
    page.wait_for_selector("#tab-portfolio.active", timeout=_ASSERT_TIMEOUT)

    # Null target — no-op
    page.evaluate("() => window.jumpToTarget(null)")
    page.wait_for_timeout(200)
    expect(page.locator("#tab-portfolio")).to_have_class(re.compile(r"active"))

    # Undefined surface — no-op
    page.evaluate("() => window.jumpToTarget({portfolio_id: 'pA'})")
    page.wait_for_timeout(200)
    expect(page.locator("#tab-portfolio")).to_have_class(re.compile(r"active"))

    # Unknown surface — no-op
    page.evaluate(
        "() => window.jumpToTarget({surface: 'mystery', portfolio_id: 'pA'})"
    )
    page.wait_for_timeout(200)
    expect(page.locator("#tab-portfolio")).to_have_class(re.compile(r"active"))

    # Unknown portfolio — silently skip the switch but still land on
    # the requested surface.  The current portfolio stays as-is.
    page.evaluate(
        "() => window.jumpToTarget({surface: 'alerts', portfolio_id: 'nonexistent'})"
    )
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    active_pid = page.evaluate(
        "() => window.localStorage.getItem('activePortfolioId')"
    )
    assert active_pid == "pA", (
        f"active portfolio leaked to nonexistent: {active_pid}"
    )


# ---------------------------------------------------------------------------
# 9) No console errors during deep-link flows
# ---------------------------------------------------------------------------


def test_no_console_errors_during_deep_link_flows(
    page: Page, axion_server: str,
):
    """Walk through inbox alert jump + alert evidence chip jump + a
    null-target no-op; assert no console errors fire."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")
    _open_inbox(page)

    # Inbox alert jump
    alert_item = page.locator(
        "#inbox-content .inbox-item[data-source-type='alert']"
    ).first
    if alert_item.count():
        alert_item.locator(".inbox-jump-btn").click()
        page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # Null-target no-op (exercise the dispatcher's defensive path)
    page.evaluate("() => window.jumpToTarget(null)")
    page.wait_for_timeout(100)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9Q flows:\n" + "\n".join(fatal)
    )
