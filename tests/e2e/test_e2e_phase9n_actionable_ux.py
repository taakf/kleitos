"""Phase 9N browser E2E tests for Actionable Intelligence UX.

Drives headless Chromium against the Phase 9J live-server fixture
and validates the Phase 9N additions end-to-end:

  * intelligence overview card carries a "Recommended actions" block
    with real items for a stressed portfolio, and honest empty state
    for a portfolio with no grounded signal
  * event detail modal renders the "Why Axion flagged this" block
    (why_it_matters + suggested_action + grounded_in refs) for the
    seeded Fed rate event
  * alerts list shows a per-alert "Next step:" line for the critical
    alert, sourced from the backend suggest_next_step_for_alert
  * operator backfill action renders a "Next step:" block in the
    last-action echo after a real run — surfaced only when the real
    stats justify a hint
  * no console errors during any of these Phase 9N flows

The tests hit the SAME seeded portfolio the Phase 9J/9L/9M suite
uses (pA has a critical macro_factor alert + a high supply_chain
alert + an info alert, AAPL/MSFT factor links on interest_rate, and
an AAPL negative important analysis note).  No extra seed; Phase 9N
builds on top of the existing deterministic rows.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


_ASSERT_TIMEOUT = 10_000


# ---------------------------------------------------------------------------
# Shared helpers — matched to the Phase 9J / 9L / 9M patterns
# ---------------------------------------------------------------------------


def _open_dashboard(page: Page, portfolio_id: str = "pA") -> None:
    page.add_init_script(
        f"window.localStorage.setItem('activePortfolioId', '{portfolio_id}');"
    )
    page.goto("/dashboard", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio", timeout=_ASSERT_TIMEOUT)


def _switch_portfolio(page: Page, portfolio_id: str) -> None:
    page.select_option("#portfolio-select", portfolio_id)
    page.wait_for_function(
        f"window.localStorage.getItem('activePortfolioId') === '{portfolio_id}'",
        timeout=_ASSERT_TIMEOUT,
    )
    page.wait_for_load_state("networkidle")


def _open_settings(page: Page) -> None:
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#op-factor-table table tbody tr", timeout=_ASSERT_TIMEOUT)


def _wait_for_no_text(page: Page, selector: str, substring: str) -> None:
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
# 1) Intelligence overview carries the Phase 9N Recommended Actions block
# ---------------------------------------------------------------------------


def test_overview_renders_recommended_actions_for_stressed_portfolio(
    page: Page, axion_server: str,
):
    """pA has a critical macro_factor alert, a high supply_chain alert,
    two holdings touched by interest_rate, and a negative important
    analysis note on AAPL.  The intelligence overview card must render
    the Phase 9N ``.intel-actions-block`` with real grounded items."""
    _open_dashboard(page, "pA")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # The Phase 9N block is always present (empty state or list)
    actions_block = overview.locator(".intel-actions-block")
    expect(actions_block).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(actions_block).to_contain_text("Recommended actions")

    # Stressed portfolio → at least one real action row
    rows = actions_block.locator(".intel-action-row")
    assert rows.count() >= 1, (
        f"expected at least one recommended action for pA, got {rows.count()}"
    )

    # Every row carries a stable data-action-key that matches the
    # Phase 9N rule families.  This is the contract the backend
    # asserts against in the integration test — here we prove the
    # JSON round-trips through the dashboard and back into the DOM.
    first_key = rows.first.get_attribute("data-action-key") or ""
    assert "." in first_key, (
        f"first action row has no grounded key: {first_key!r}"
    )
    # And the key should come from one of the families we wired up
    assert first_key.split(".", 1)[0] in (
        "alerts", "holdings", "factors",
        "relationships", "freshness", "maintenance",
    ), f"unexpected action family: {first_key!r}"

    # A title + a body text (honest descriptions, not placeholders)
    expect(rows.first.locator(".intel-action-title")).to_be_visible()
    expect(rows.first.locator(".intel-action-body")).to_be_visible()

    # Priority pill renders with one of the three known classes
    priority_pill = rows.first.locator(".intel-action-priority")
    expect(priority_pill).to_be_visible()
    row_class = rows.first.get_attribute("class") or ""
    assert re.search(
        r"intel-action-(high|medium|low)", row_class
    ), f"row priority class missing: {row_class!r}"


def test_overview_recommended_actions_switch_with_portfolio(
    page: Page, axion_server: str,
):
    """Switching from pA to pB must re-scope the recommended actions
    block — different holdings produce different grounded keys, and
    pA's tickers (AAPL/MSFT) must not leak into pB's rendering."""
    _open_dashboard(page, "pA")
    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(overview.locator(".intel-actions-block")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    # Capture pA's rendered action text for the leak assertion
    pa_actions_text = overview.locator(".intel-actions-block").inner_text()

    _switch_portfolio(page, "pB")

    # After the portfolio switch, the overview re-fetches and the
    # actions block must still render (empty-state or list).  The
    # exact content depends on the seeded pB rows — a single XOM
    # holding with an oil factor link and one high oil alert — so
    # we assert that pA's AAPL/MSFT tickers are not present inside
    # the Phase 9N block.
    actions_block = overview.locator(".intel-actions-block")
    expect(actions_block).to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Wait for the block text to stabilise on the pB rendering by
    # looking for any content that is NOT the pA snapshot.
    page.wait_for_function(
        """
        (prior) => {
            const el = document.querySelector('#intelligence-overview .intel-actions-block');
            if (!el) return false;
            return el.innerText && el.innerText !== prior;
        }
        """,
        arg=pa_actions_text,
        timeout=_ASSERT_TIMEOUT,
    )

    pb_text = actions_block.inner_text()
    # pA tickers must not leak into pB's actions
    assert "MSFT" not in pb_text, (
        "MSFT from pA leaked into pB recommended actions: " + pb_text[:300]
    )
    # AAPL is only allowed if pB somehow had an AAPL-scoped row — our
    # seed has no AAPL in pB, so this is a hard leakage check.
    assert "AAPL" not in pb_text, (
        "AAPL from pA leaked into pB recommended actions: " + pb_text[:300]
    )


def test_overview_recommended_actions_empty_state_on_default_portfolio(
    page: Page, axion_server: str,
):
    """The seeded 'default' portfolio has a single GOOGL holding, no
    alerts, no factor links, no analysis notes.  The Phase 9N block
    must render the honest empty state — not filler."""
    _open_dashboard(page, "default")

    overview = page.locator("#intelligence-overview")
    expect(overview).to_be_visible(timeout=_ASSERT_TIMEOUT)

    actions_block = overview.locator(".intel-actions-block")
    expect(actions_block).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Either the explicit empty state renders, or the list is empty.
    # We accept both rather than assume the rule engine won't ever
    # fire a low-priority freshness hint here — but the rendered
    # text must match one of these two grounded outcomes.
    empty_state = actions_block.locator(".intel-actions-empty")
    row_count = actions_block.locator(".intel-action-row").count()
    if row_count == 0:
        expect(empty_state).to_be_visible(timeout=_ASSERT_TIMEOUT)
        expect(empty_state).to_contain_text(
            "No immediate actions from current signals",
        )
    else:
        # If any row renders, it must still be a grounded row with
        # a valid family key — no free-text filler.
        first_key = (
            actions_block.locator(".intel-action-row").first
            .get_attribute("data-action-key") or ""
        )
        assert "." in first_key, (
            f"default portfolio row has no grounded key: {first_key!r}"
        )


# ---------------------------------------------------------------------------
# 2) Event detail modal carries the Phase 9N explanation block
# ---------------------------------------------------------------------------


def test_event_detail_modal_renders_why_axion_flagged_this(
    page: Page, axion_server: str,
):
    """Open the Fed rate event detail modal and verify the Phase 9N
    explanation block renders with why_it_matters + suggested_action
    + grounded refs pointing at the interest_rate factor."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)

    # Click the Fed event row specifically so we know the modal will
    # have a factor-backed explanation — identified by title match.
    fed_row = page.locator(
        "#events-table .events-row-clickable", has_text="Federal Reserve"
    ).first
    expect(fed_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    fed_row.click()

    modal = page.locator("#event-detail-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")

    # Phase 9N explanation block
    why_block = page.locator("#event-detail-body .event-why-block")
    expect(why_block).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(why_block).to_contain_text("Why Axion flagged this")

    # why_it_matters names the Interest Rates factor
    why_text = why_block.locator(".event-why-text")
    expect(why_text).to_be_visible()
    why_text_content = why_text.inner_text()
    assert (
        "Interest Rates" in why_text_content
        or "interest rate" in why_text_content.lower()
    ), f"why text did not name the rate factor: {why_text_content!r}"

    # suggested next step names at least one of the affected tickers
    action_block = why_block.locator(".event-why-action")
    expect(action_block).to_be_visible()
    action_text = action_block.inner_text()
    assert "Suggested next step" in action_text
    assert "AAPL" in action_text or "MSFT" in action_text, (
        f"suggested action did not name the affected holdings: {action_text!r}"
    )

    # Grounding chips render with at least the factor ref.
    # Phase 9O rewired the event-detail block to use the shared
    # ``.evidence-refs-grouped`` container, which in turn renders
    # either a flat chip row or per-category sub-rows.  We match on
    # the shared class so the test stays stable across the 9N/9O
    # refactor.
    grounding = why_block.locator(".evidence-refs-grouped")
    expect(grounding).to_be_visible()
    grounding_text = grounding.inner_text()
    assert "factor:interest_rate" in grounding_text, (
        f"grounding did not name the factor key: {grounding_text!r}"
    )

    # Close the modal to clean up for the next test
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)


def test_event_detail_modal_silent_for_event_without_evidence(
    page: Page, axion_server: str,
):
    """An event with no factor tags, no chains, no affected holdings
    must NOT render the explanation block — it's evidence-gated.  We
    exercise this by intercepting the event-detail API response and
    returning a skeleton payload with no evidence fields."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)

    def _handle_detail(route, request):
        import json
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "id": "evt_bare",
                "title": "Bare event (no evidence)",
                "summary": "A minimal event for the no-explanation branch.",
                "event_type": "general",
                "fetched_at": "2026-04-05T00:00:00+00:00",
                "factor_tags": [],
                "affected_holdings": [],
                "links": [],
                "related_analyses": [],
                "related_alerts": [],
                # Phase 9N fields explicitly absent / null
                "why_it_matters": None,
                "suggested_action": None,
                "explanation_grounded_in": [],
            }),
        )
    page.route("**/api/v1/events/*", _handle_detail)

    # Click any event row to trigger the intercepted fetch
    first_row = page.locator("#events-table .events-row-clickable").first
    expect(first_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    first_row.click()

    modal = page.locator("#event-detail-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")

    # The explanation block must not render — Phase 9N is silent
    # when the backend provided no evidence
    expect(
        page.locator("#event-detail-body .event-why-block")
    ).to_have_count(0)

    # Clean up
    page.unroute("**/api/v1/events/*")
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 3) Alerts page renders per-alert suggested_action line
# ---------------------------------------------------------------------------


def test_alerts_list_renders_suggested_action_on_critical_card(
    page: Page, axion_server: str,
):
    """The critical alert has related_holdings=['AAPL'] → the backend
    should emit a 'Review now and inspect the related holding(s).'
    hint, and the dashboard should render it inside the alert card."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # The critical card is first because of priority_ordered=true
    critical_card = page.locator(
        "#alerts-content .alert-card.severity-critical"
    ).first
    expect(critical_card).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Phase 9N adds a ``.alert-suggested-action`` inside the card
    next_step = critical_card.locator(".alert-suggested-action")
    expect(next_step).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(next_step).to_contain_text("Next step:")
    # Critical + related_holdings → "Review now and inspect..."
    next_step_text = next_step.inner_text()
    assert "Review now" in next_step_text, (
        f"critical alert suggested action wrong: {next_step_text!r}"
    )


def test_alerts_list_has_suggested_action_on_high_supply_chain_card(
    page: Page, axion_server: str,
):
    """The high supply_chain alert should carry the macro/supply-chain
    specific hint 'Inspect the causal chain before acknowledging.'"""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    high_card = page.locator(
        "#alerts-content .alert-card.severity-high"
    ).first
    expect(high_card).to_be_visible(timeout=_ASSERT_TIMEOUT)

    next_step = high_card.locator(".alert-suggested-action")
    expect(next_step).to_be_visible(timeout=_ASSERT_TIMEOUT)
    next_step_text = next_step.inner_text()
    assert "Inspect" in next_step_text, (
        f"high supply-chain alert suggested action wrong: {next_step_text!r}"
    )


def test_alerts_info_card_has_no_suggested_action(
    page: Page, axion_server: str,
):
    """Info alerts don't get a suggested action — the Phase 9N helper
    returns None for info severity, so the ``.alert-suggested-action``
    element must be absent from those cards."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    info_card = page.locator(
        "#alerts-content .alert-card.severity-info"
    ).first
    expect(info_card).to_be_visible(timeout=_ASSERT_TIMEOUT)
    # No Phase 9N next-step line on info alerts
    expect(info_card.locator(".alert-suggested-action")).to_have_count(0)


# ---------------------------------------------------------------------------
# 4) Operator backfill action renders a Phase 9N "Next step" hint
# ---------------------------------------------------------------------------


def test_operator_backfill_renders_next_step_hint(
    page: Page, axion_server: str,
):
    """Run a real backfill against the seeded DB and verify the
    last-action echo surfaces the Phase 9N ``.op-last-result-next-step``
    block.  The hint text is content-dependent (depends on whether
    the backfill actually added links or not) but the block itself
    must render any time the helper returns a non-null string — and
    for a fresh seeded DB the backfill builder always returns SOME
    stats that justify a hint (either 'Open the intelligence overview'
    or 'No new links landed')."""
    _open_dashboard(page, "pA")
    page.on("dialog", lambda d: d.accept())
    _open_settings(page)

    page.click("#op-backfill-btn")
    # Wait for the real backfill to complete
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Backfill complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )

    echo = page.locator("#op-last-result")
    expect(echo).to_have_class(re.compile(r"op-last-result-ok"))

    # Phase 9N next-step block
    next_step = echo.locator(".op-last-result-next-step")
    expect(next_step).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(next_step).to_contain_text("Next step:")

    # The backfill hint must be one of the two grounded outcomes
    # described in _opMaintenanceHint: either "Open the intelligence
    # overview" (when links landed) or "No new links landed" (noop).
    next_step_text = next_step.inner_text()
    assert (
        "intelligence overview" in next_step_text.lower()
        or "no new links landed" in next_step_text.lower()
    ), f"backfill next-step hint unexpected: {next_step_text!r}"


def test_operator_reconcile_renders_next_step_when_changes_exist(
    page: Page, axion_server: str,
):
    """Run a real reconcile against the seeded DB.  The seeded DB
    already has the TSMC relationship row so a plain reconcile with
    prune=off may be a noop, in which case _opMaintenanceHint returns
    null and the next-step block is absent.  Either outcome is valid;
    we just assert that WHEN the block renders, it names backfill as
    the follow-up step."""
    _open_dashboard(page, "pA")
    page.on("dialog", lambda d: d.accept())
    _open_settings(page)

    # Uncheck prune to avoid the second confirm dialog
    prune_cb = page.locator("#op-reconcile-prune")
    if prune_cb.is_checked():
        prune_cb.uncheck()

    page.click("#op-reconcile-btn")
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Reconcile complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )

    echo = page.locator("#op-last-result")
    expect(echo).to_have_class(re.compile(r"op-last-result-ok"))

    # If the block rendered, its text must mention "backfill" (the
    # grounded follow-up for a reconcile that touched seed rows).
    next_step_count = echo.locator(".op-last-result-next-step").count()
    if next_step_count:
        next_step_text = echo.locator(
            ".op-last-result-next-step"
        ).inner_text()
        assert "backfill" in next_step_text.lower(), (
            f"reconcile next-step hint wrong: {next_step_text!r}"
        )


# ---------------------------------------------------------------------------
# 5) No console errors during any of the Phase 9N flows
# ---------------------------------------------------------------------------


def test_no_console_errors_during_phase9n_flows(
    page: Page, axion_server: str,
):
    """Walk through every Phase 9N surface in one go — overview,
    event detail modal, alerts page — and assert no console errors
    fired anywhere."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # Overview with Phase 9N block
    overview = page.locator("#intelligence-overview")
    expect(overview.locator(".intel-actions-block")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    # Event detail modal with Phase 9N explanation block
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
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")
    # Close the modal
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(page.locator("#event-detail-modal")).not_to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    # Alerts page with Phase 9N per-row next step
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)
    # Make sure at least one suggested-action line rendered
    expect(
        page.locator("#alerts-content .alert-suggested-action").first
    ).to_be_visible(timeout=_ASSERT_TIMEOUT)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9N flows:\n" + "\n".join(fatal)
    )
