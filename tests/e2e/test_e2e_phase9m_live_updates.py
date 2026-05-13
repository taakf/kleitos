"""Phase 9M browser E2E tests for live updates.

Drives headless Chromium against the Phase 9J live-server fixture
and validates the Phase 9M dispatcher end-to-end by INJECTING
websocket messages via ``window._wsDispatch`` from the test thread.
This bypasses the real WS transport (which is covered by Phase 9K's
``test_ws_connect_and_broadcast_receive``) and lets us deterministically
drive every dispatcher branch without timing races on the real
uvicorn broadcast path.

Covers:

  * operator_action "started" event flips the chip immediately
    (faster than the 4s Phase 9L polling interval)
  * alert event refreshes the Alerts tab when it's active
  * alert event refreshes the intelligence overview when the
    Portfolio tab is active
  * alert event is SKIPPED when the portfolio_id doesn't match
    the active portfolio
  * event event refreshes Intelligence → Events subtab
  * unknown message type does not break the app
  * malformed / missing-type messages do not crash the dispatcher
  * modal-open guard defers refreshes while a dialog is open, then
    flushes when the dialog closes
  * dispatcher survives a burst of messages without console errors
"""

from __future__ import annotations

import json
import re
import time

import pytest
from playwright.sync_api import Page, expect


_ASSERT_TIMEOUT = 10_000


def _open_dashboard(page: Page, portfolio_id: str = "pA") -> None:
    page.add_init_script(
        f"window.localStorage.setItem('activePortfolioId', '{portfolio_id}');"
    )
    page.goto("/dashboard", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio", timeout=_ASSERT_TIMEOUT)
    # Wait for _wsDispatch to be exposed on window — it's defined
    # inside the module closure and re-exported synchronously at the
    # bottom of the IIFE, so it should land on first paint.
    page.wait_for_function(
        "typeof window._wsDispatch === 'function'",
        timeout=_ASSERT_TIMEOUT,
    )


def _dispatch(page: Page, msg: dict) -> None:
    """Inject a websocket payload as if it arrived over the wire."""
    page.evaluate("(m) => window._wsDispatch(m)", msg)


# ---------------------------------------------------------------------------
# 1) operator_action "started" flips the chip with sub-poll latency
# ---------------------------------------------------------------------------


def test_operator_action_started_flips_chip_immediately(
    page: Page, axion_server: str,
):
    """Phase 9L polls /operator/actions/status every 4s.  Phase 9M
    must flip the chip sooner when an operator_action event arrives.
    We intercept the status poll to keep the server reporting idle,
    then inject a synthetic operator_action event and verify the chip
    flips to running inside 2 seconds (well under the 4s poll)."""
    _open_dashboard(page, "pA")

    # Intercept the status poll so it says reconcile is running AFTER
    # the event lands.  The dispatcher triggers an immediate poll on
    # the operator_action event, so the poll response is what actually
    # flips the chip.
    reconcile_running = {"value": False}

    def _handle_status(route, request):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "reconcile": {"in_progress": reconcile_running["value"]},
                "backfill": {"in_progress": False},
            }),
        )
    page.route("**/api/v1/operator/actions/status", _handle_status)

    # Open Settings — this kicks off the initial poll, which reports idle.
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-reconcile-chip", timeout=_ASSERT_TIMEOUT)

    # Initial state: idle
    expect(page.locator("#op-reconcile-chip")).to_have_class(
        re.compile(r"op-action-chip-idle"),
    )

    # Flip the server state THEN dispatch the event.  The dispatcher's
    # _wsHandleOperatorAction will trigger _opPollActionsStatus() which
    # now sees the updated server state.
    reconcile_running["value"] = True
    t0 = time.time()
    _dispatch(page, {"type": "operator_action", "action": "reconcile", "state": "started"})

    # Wait for the chip to flip — must happen much faster than 4s
    page.wait_for_function(
        "() => document.querySelector('#op-reconcile-chip')?.classList.contains('op-action-chip-running')",
        timeout=2500,
    )
    elapsed = time.time() - t0
    assert elapsed < 2.0, (
        f"chip flip took {elapsed:.2f}s — Phase 9M should be much "
        f"faster than the 4s poll interval"
    )
    expect(page.locator("#op-reconcile-btn")).to_be_disabled()


def test_operator_action_finished_refreshes_operator_tables(
    page: Page, axion_server: str,
):
    """When the dispatcher receives an operator_action finished event
    with no modal open, it refreshes the operator factor + relationship
    tables (they may have drifted during the run)."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-factor-table table tbody tr", timeout=_ASSERT_TIMEOUT)

    # Track relationship fetch calls so we can prove the refresh fired
    rel_fetches = {"count": 0}

    def _handle_rels(route, request):
        rel_fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/operator/relationships**", _handle_rels)

    before = rel_fetches["count"]
    _dispatch(page, {
        "type": "operator_action",
        "action": "reconcile",
        "state": "finished",
    })
    # Give the handler a moment to fire both refreshes
    page.wait_for_function(
        f"() => {rel_fetches}['count']",  # no-op — just wait a tick
        timeout=500,
    ) if False else page.wait_for_timeout(300)
    assert rel_fetches["count"] > before, (
        "finished event did not trigger the relationship table refresh"
    )


# ---------------------------------------------------------------------------
# 2) Alert event refreshes Alerts tab
# ---------------------------------------------------------------------------


def test_alert_event_refreshes_alerts_tab_when_active(
    page: Page, axion_server: str,
):
    """With the Alerts tab active, receiving an alert event must
    trigger a refresh of the alerts list."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # Count how many times the alerts API is fetched.
    # Phase 9W changed loadAlerts to call /api/v1/alerts (the
    # ``list_alerts`` route with server-side filter params) instead
    # of the old /api/v1/alerts/active path.
    fetches = {"count": 0}

    def _handle_alerts(route, request):
        fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/alerts?**", _handle_alerts)

    before = fetches["count"]
    _dispatch(page, {
        "type": "alert",
        "id": "new-alert-1",
        "title": "phase9m live alert",
        "severity": "high",
        "portfolio_id": "pA",
    })
    # Poll for the refresh
    page.wait_for_function(
        f"() => true",  # just yield
        timeout=50,
    ) if False else page.wait_for_timeout(400)
    assert fetches["count"] > before, (
        "alert event did not trigger Alerts tab refresh"
    )


# ---------------------------------------------------------------------------
# 3) Alert event refreshes intelligence overview on Portfolio tab
# ---------------------------------------------------------------------------


def test_alert_event_refreshes_intelligence_overview_on_portfolio_tab(
    page: Page, axion_server: str,
):
    """When the user is on the Portfolio tab (where the Phase 9G
    intelligence overview lives), an alert event must refresh the
    overview via loadIntelligenceOverview()."""
    _open_dashboard(page, "pA")
    page.wait_for_selector("#intelligence-overview .intel-overview-card", timeout=_ASSERT_TIMEOUT)

    fetches = {"count": 0}

    def _handle(route, request):
        fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/intelligence/summary**", _handle)

    before = fetches["count"]
    _dispatch(page, {
        "type": "alert",
        "id": "new-alert-2",
        "title": "overview refresh alert",
        "severity": "critical",
        "portfolio_id": "pA",
    })
    page.wait_for_timeout(400)
    assert fetches["count"] > before, (
        "alert event did not trigger intelligence overview refresh"
    )


# ---------------------------------------------------------------------------
# 4) Cross-portfolio message is skipped
# ---------------------------------------------------------------------------


def test_cross_portfolio_alert_event_is_skipped(
    page: Page, axion_server: str,
):
    """The active portfolio is pA.  An alert event carrying
    portfolio_id='pB' must NOT trigger any refresh — portfolio safety
    in the live-update layer."""
    _open_dashboard(page, "pA")
    page.wait_for_selector("#intelligence-overview .intel-overview-card", timeout=_ASSERT_TIMEOUT)

    fetches = {"count": 0}

    def _handle(route, request):
        fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/intelligence/summary**", _handle)
    page.route("**/api/v1/alerts?**", _handle)

    before = fetches["count"]
    _dispatch(page, {
        "type": "alert",
        "id": "cross-portfolio-alert",
        "title": "should be ignored",
        "severity": "high",
        "portfolio_id": "pB",   # NOT the active portfolio
    })
    page.wait_for_timeout(400)
    assert fetches["count"] == before, (
        f"cross-portfolio alert wrongly triggered refreshes: "
        f"{fetches['count']} vs {before}"
    )


# ---------------------------------------------------------------------------
# 5) Event event refreshes Intelligence → Events subtab
# ---------------------------------------------------------------------------


def test_event_event_refreshes_events_subtab(page: Page, axion_server: str):
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    # Events is the default subtab
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)

    fetches = {"count": 0}

    def _handle(route, request):
        fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/events**", _handle)

    before = fetches["count"]
    _dispatch(page, {
        "type": "event",
        "id": "new-event-1",
        "title": "phase9m live event",
        "linked_holding_count": 2,
    })
    page.wait_for_timeout(400)
    assert fetches["count"] > before, (
        "event event did not refresh the Events subtab"
    )


# ---------------------------------------------------------------------------
# 6) Unknown message type does not break the app
# ---------------------------------------------------------------------------


def test_unknown_message_type_is_ignored(page: Page, axion_server: str):
    """An unknown ``type`` string must not crash the dispatcher or
    pollute the console with errors."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    _dispatch(page, {"type": "totally_unknown_type", "payload": "whatever"})
    _dispatch(page, {"type": "phase9m_future_event", "x": 1})

    # And the app must still respond to known messages afterwards.
    # Navigate to Alerts tab and inject a valid alert event to prove
    # the dispatcher is still alive.
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    fetches = {"count": 0}

    def _handle(route, request):
        fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/alerts?**", _handle)

    before = fetches["count"]
    _dispatch(page, {
        "type": "alert",
        "id": "after-unknown",
        "title": "still alive",
        "severity": "info",
        "portfolio_id": "pA",
    })
    page.wait_for_timeout(300)
    assert fetches["count"] > before

    # No fatal console errors
    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, "console errors during unknown-type test:\n" + "\n".join(fatal)


def test_malformed_message_does_not_crash_dispatcher(
    page: Page, axion_server: str,
):
    """Missing type, non-string type, non-object payloads, and null
    all have to be handled defensively."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # These all should no-op silently
    page.evaluate("() => window._wsDispatch(null)")
    page.evaluate("() => window._wsDispatch(undefined)")
    page.evaluate("() => window._wsDispatch({})")
    page.evaluate("() => window._wsDispatch({type: 42})")
    page.evaluate("() => window._wsDispatch({type: null})")
    page.evaluate("() => window._wsDispatch('not an object')")

    # And the app still works
    assert page.locator("#tab-portfolio.active").is_visible()
    assert not errors, "pageerror during malformed dispatch:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# 7) Modal-open guard defers refreshes
# ---------------------------------------------------------------------------


def test_refresh_is_deferred_while_operator_modal_is_open(
    page: Page, axion_server: str,
):
    """Open the operator factor-override modal, dispatch an event
    that would normally refresh the operator tables, verify NO
    refresh happens while the modal is open, then close the modal
    and verify the deferred refresh fires."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-factor-table table tbody tr", timeout=_ASSERT_TIMEOUT)

    # Open the factor override modal for the first row
    page.select_option("#op-factor-filter", "interest_rate")
    page.wait_for_function(
        "() => document.querySelectorAll('#op-factor-table tbody tr').length > 0",
        timeout=_ASSERT_TIMEOUT,
    )
    # Click the edit button on the first row
    page.click("#op-factor-table tbody tr:first-child [data-op='op-factor-edit']")
    page.wait_for_selector("#op-factor-modal[open]", timeout=_ASSERT_TIMEOUT)

    # Track relationship fetches — a finished operator_action should
    # refresh them, but with a modal open it should DEFER.
    rel_fetches = {"count": 0}

    def _handle(route, request):
        rel_fetches["count"] += 1
        route.continue_()
    page.route("**/api/v1/operator/relationships**", _handle)

    before = rel_fetches["count"]
    _dispatch(page, {
        "type": "operator_action",
        "action": "reconcile",
        "state": "finished",
    })
    page.wait_for_timeout(400)
    # With the modal open, no immediate refresh fires
    assert rel_fetches["count"] == before, (
        f"modal-open guard failed: refresh fired while modal was open "
        f"({rel_fetches['count']} vs {before})"
    )

    # Close the modal and confirm the deferred refresh fires
    page.click("#op-factor-modal [data-close-modal]")
    page.wait_for_function(
        "() => !document.querySelector('#op-factor-modal[open]')",
        timeout=_ASSERT_TIMEOUT,
    )
    # Flush happens on the dialog close event via setTimeout(50), so
    # give it a moment.
    page.wait_for_timeout(500)
    assert rel_fetches["count"] > before, (
        f"deferred refresh did not flush after modal close: "
        f"{rel_fetches['count']} vs {before}"
    )


# ---------------------------------------------------------------------------
# 8) Burst of messages — dispatcher survives under load
# ---------------------------------------------------------------------------


def test_burst_of_messages_does_not_spam_console(page: Page, axion_server: str):
    """Fire 50 mixed messages in a tight loop and verify the dispatcher
    handles them without errors or runaway refreshes."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None,
    )

    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)

    # 50 messages covering every known type + a few unknowns.  The
    # dispatcher should absorb them cleanly.
    page.evaluate("""
        (() => {
            const types = [
                { type: 'ping' },
                { type: 'alert', id: 'a', title: 't', severity: 'info', portfolio_id: 'pA' },
                { type: 'event', id: 'e', title: 't', linked_holding_count: 1 },
                { type: 'operator_action', action: 'reconcile', state: 'started' },
                { type: 'operator_action', action: 'reconcile', state: 'finished' },
                { type: 'holding_update' },
                { type: 'agent_complete', agent: 'risk', status: 'success' },
                { type: 'unknown_type_a' },
                { type: 'unknown_type_b' },
                {},
            ];
            for (let i = 0; i < 50; i++) {
                window._wsDispatch(types[i % types.length]);
            }
        })();
    """)
    page.wait_for_timeout(500)

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, "burst-of-messages console errors:\n" + "\n".join(fatal)
    # And the app is still responsive
    assert page.locator("#tab-alerts.active").is_visible()
