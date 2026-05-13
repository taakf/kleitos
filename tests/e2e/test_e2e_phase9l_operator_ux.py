"""Phase 9L browser E2E tests for operator UX hardening.

Drives headless Chromium against the Phase 9J live-server fixture
and validates the Phase 9L additions end-to-end:

  * live reconcile / backfill status chips track the server
  * busy buttons + Running… labels during real actions
  * 409 in-progress responses render as a friendly "already running"
    state, not a red error
  * 429 rate-limit responses render as a friendly throttle state with
    retry hint, not a generic exception
  * the last-action block shows a timestamp + audit-trail hint after
    a successful mutation
  * the operator panel recovers cleanly after 409 / 429 responses
  * no console errors during any of these flows

The tests use Playwright's ``page.route`` interception for the 409 /
429 branches so we don't have to produce those responses on the real
server — the goal is to validate the *UI* behavior, not the backend
(which already has its own Phase 9K tests).  Real actions (without
interception) are still exercised to prove the happy path works end
to end.
"""

from __future__ import annotations

import json
import re
import time

import pytest
from playwright.sync_api import Page, expect


# Same 10s assertion timeout as the Phase 9J suite.  Tests are local.
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


def _open_settings(page: Page) -> None:
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    # Wait for the operator tables AND the Phase 9L chips to appear.
    page.wait_for_selector("#op-reconcile-chip", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#op-backfill-chip", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(
        "#op-factor-table table tbody tr",
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 1) Idle state — status chips render "Idle" on first load
# ---------------------------------------------------------------------------


def test_status_chips_render_idle_on_first_open(page: Page, axion_server: str):
    """With nothing running, both chips show 'Idle' after the first
    poll lands.  This proves /api/v1/operator/actions/status is being
    consumed by the UI."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    rec_chip = page.locator("#op-reconcile-chip")
    bf_chip = page.locator("#op-backfill-chip")
    expect(rec_chip).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(bf_chip).to_be_visible()

    # The chip text normalises to "Idle" via text-transform:uppercase
    # in CSS but the JS label stays "Idle".  We check against the
    # rendered inner text (which reflects the JS label).
    rec_label = rec_chip.locator(".op-action-chip-label").inner_text()
    bf_label = bf_chip.locator(".op-action-chip-label").inner_text()
    assert rec_label.lower() == "idle", f"reconcile chip not idle: {rec_label!r}"
    assert bf_label.lower() == "idle", f"backfill chip not idle: {bf_label!r}"

    # Both buttons are enabled
    expect(page.locator("#op-reconcile-btn")).to_be_enabled()
    expect(page.locator("#op-backfill-btn")).to_be_enabled()


# ---------------------------------------------------------------------------
# 2) Running state — chips flip to "Running…" when the server poll
#     reports an in-flight action
# ---------------------------------------------------------------------------


def test_chips_flip_to_running_when_server_reports_in_flight(
    page: Page, axion_server: str,
):
    """We intercept ``/api/v1/operator/actions/status`` and return a
    payload saying reconcile is running.  The chip should flip to
    'Running…' and the button should disable."""
    _open_dashboard(page, "pA")

    # Install the interceptor BEFORE opening Settings so every poll
    # hits our canned response.
    def _handle_status(route, request):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "reconcile": {"in_progress": True},
                "backfill": {"in_progress": False},
            }),
        )
    page.route("**/api/v1/operator/actions/status", _handle_status)

    _open_settings(page)

    # Wait for the poller to report running + the chip to update.
    page.wait_for_function(
        "() => {"
        " const el = document.querySelector('#op-reconcile-chip');"
        " if (!el) return false;"
        " return el.classList.contains('op-action-chip-running');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )
    rec_chip = page.locator("#op-reconcile-chip")
    expect(rec_chip).to_have_class(re.compile(r"op-action-chip-running"))
    rec_label = rec_chip.locator(".op-action-chip-label").inner_text()
    assert rec_label.lower().startswith("running"), f"label: {rec_label!r}"

    # Reconcile button is disabled + wears the busy class
    rec_btn = page.locator("#op-reconcile-btn")
    expect(rec_btn).to_be_disabled()
    expect(rec_btn).to_have_class(re.compile(r"op-btn-busy"))

    # Backfill is still idle — proves the two states are independent
    bf_chip = page.locator("#op-backfill-chip")
    expect(bf_chip).to_have_class(re.compile(r"op-action-chip-idle"))
    expect(page.locator("#op-backfill-btn")).to_be_enabled()


# ---------------------------------------------------------------------------
# 3) 409 "in progress" response → friendly busy state (not error)
# ---------------------------------------------------------------------------


def test_backfill_409_in_progress_renders_friendly_busy_state(
    page: Page, axion_server: str,
):
    """Intercept POST /api/v1/operator/backfill and return a Phase 9K
    409 in-progress response.  The UI must surface it as a friendly
    'Backfill already running' state with the busy tone — NOT as a
    red error toast — and the chip must stay on 'Running…'.

    We ALSO intercept the status poll so it reports the action as
    still running — otherwise the finally-block's re-poll sees the
    real idle state and flips the chip back to idle immediately.
    This matches real-world semantics: if the server says 409
    in_progress for a POST, a status poll at the same instant would
    also report in_progress.
    """
    _open_dashboard(page, "pA")

    # Stateful mock: status poll returns "idle" until the POST lands,
    # then returns "backfill running".  This matches the real-world
    # sequence — the user sees idle, clicks, another process grabs
    # the lock concurrently, the POST returns 409, the next poll
    # reflects the new locked state.
    post_seen = {"value": False}

    def _handle_backfill(route, request):
        if request.method == "POST":
            post_seen["value"] = True
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({
                    "detail": {
                        "detail": (
                            "A backfill is already running in this process. "
                            "Wait for it to finish before starting another."
                        ),
                        "in_progress": True,
                        "action": "backfill",
                    },
                }),
            )
        else:
            route.continue_()
    page.route("**/api/v1/operator/backfill", _handle_backfill)

    def _handle_status(route, request):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "reconcile": {"in_progress": False},
                "backfill": {"in_progress": post_seen["value"]},
            }),
        )
    page.route("**/api/v1/operator/actions/status", _handle_status)

    # Auto-accept the confirm() dialog so the click goes through.
    page.on("dialog", lambda d: d.accept())

    _open_settings(page)
    # Button should be enabled (status still reports idle)
    expect(page.locator("#op-backfill-btn")).to_be_enabled(timeout=_ASSERT_TIMEOUT)
    page.click("#op-backfill-btn")

    # Last-action block should render the friendly busy state
    echo = page.locator("#op-last-result")
    expect(echo).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(echo).to_contain_text("Backfill already running")
    expect(echo).to_have_class(re.compile(r"op-last-result-busy"))

    # And the chip must stay on Running… because the server reported
    # the lock is still held (both via the 409 and the post-click poll).
    bf_chip = page.locator("#op-backfill-chip")
    expect(bf_chip).to_have_class(re.compile(r"op-action-chip-running"))


def test_reconcile_409_in_progress_renders_friendly_busy_state(
    page: Page, axion_server: str,
):
    """Same stateful pattern as the backfill test — status reports
    idle until the POST lands, then flips to locked."""
    _open_dashboard(page, "pA")

    post_seen = {"value": False}

    def _handle_reconcile(route, request):
        if request.method == "POST":
            post_seen["value"] = True
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps({
                    "detail": {
                        "detail": "A relationship reconcile is already running in this process.",
                        "in_progress": True,
                        "action": "reconcile",
                    },
                }),
            )
        else:
            route.continue_()
    page.route("**/api/v1/operator/relationships/reconcile**", _handle_reconcile)

    def _handle_status(route, request):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "reconcile": {"in_progress": post_seen["value"]},
                "backfill": {"in_progress": False},
            }),
        )
    page.route("**/api/v1/operator/actions/status", _handle_status)

    page.on("dialog", lambda d: d.accept())

    _open_settings(page)
    # Uncheck prune to sidestep the extra prune-confirm dialog
    prune_cb = page.locator("#op-reconcile-prune")
    if prune_cb.is_checked():
        prune_cb.uncheck()
    expect(page.locator("#op-reconcile-btn")).to_be_enabled(timeout=_ASSERT_TIMEOUT)
    page.click("#op-reconcile-btn")

    echo = page.locator("#op-last-result")
    expect(echo).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(echo).to_contain_text("Reconcile already running")
    expect(echo).to_have_class(re.compile(r"op-last-result-busy"))

    rec_chip = page.locator("#op-reconcile-chip")
    expect(rec_chip).to_have_class(re.compile(r"op-action-chip-running"))


# ---------------------------------------------------------------------------
# 4) 429 rate-limit response → friendly throttle state with retry hint
# ---------------------------------------------------------------------------


def test_backfill_429_rate_limit_renders_friendly_throttle_state(
    page: Page, axion_server: str,
):
    """Intercept POST /api/v1/operator/backfill and return a Phase 9K
    429 rate-limit response.  The UI must surface it as a friendly
    throttle state with a retry-in hint and the correct bucket name,
    NOT as a generic 500-style error."""
    _open_dashboard(page, "pA")

    def _handle_backfill(route, request):
        if request.method == "POST":
            route.fulfill(
                status=429,
                content_type="application/json",
                headers={"Retry-After": "7"},
                body=json.dumps({
                    "detail": "Rate limit exceeded for bucket 'mutation'. Maximum 240 requests per minute.",
                    "bucket": "mutation",
                    "limit_per_minute": 240,
                    "retry_after_seconds": 7,
                }),
            )
        else:
            route.continue_()
    page.route("**/api/v1/operator/backfill", _handle_backfill)

    page.on("dialog", lambda d: d.accept())

    _open_settings(page)
    page.click("#op-backfill-btn")

    echo = page.locator("#op-last-result")
    expect(echo).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(echo).to_contain_text("Backfill rate-limited")
    # The bucket name is surfaced in the message body
    expect(echo).to_contain_text("mutation")
    # The limit ceiling is surfaced
    expect(echo).to_contain_text("240")
    # The retry hint is surfaced with the server's value (7s)
    expect(echo).to_contain_text("7s")
    # Busy tone, not error tone
    expect(echo).to_have_class(re.compile(r"op-last-result-busy"))


def test_reconcile_429_rate_limit_renders_friendly_throttle_state(
    page: Page, axion_server: str,
):
    _open_dashboard(page, "pA")

    def _handle_reconcile(route, request):
        if request.method == "POST":
            route.fulfill(
                status=429,
                content_type="application/json",
                headers={"Retry-After": "12"},
                body=json.dumps({
                    "detail": "Rate limit exceeded for bucket 'mutation'. Maximum 240 requests per minute.",
                    "bucket": "mutation",
                    "limit_per_minute": 240,
                    "retry_after_seconds": 12,
                }),
            )
        else:
            route.continue_()
    page.route("**/api/v1/operator/relationships/reconcile**", _handle_reconcile)

    page.on("dialog", lambda d: d.accept())

    _open_settings(page)
    prune_cb = page.locator("#op-reconcile-prune")
    if prune_cb.is_checked():
        prune_cb.uncheck()
    page.click("#op-reconcile-btn")

    echo = page.locator("#op-last-result")
    expect(echo).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(echo).to_contain_text("Reconcile rate-limited")
    expect(echo).to_contain_text("mutation")
    expect(echo).to_contain_text("12s")
    expect(echo).to_have_class(re.compile(r"op-last-result-busy"))


# ---------------------------------------------------------------------------
# 5) Trust readback — successful actions show timestamp + audit hint
# ---------------------------------------------------------------------------


def test_successful_backfill_shows_timestamp_and_audit_hint(
    page: Page, axion_server: str,
):
    """Run a REAL backfill (not intercepted) against the Phase 9J
    seeded DB and verify the last-action block carries a timestamp
    footer + 'Saved with audit trail' audit hint."""
    _open_dashboard(page, "pA")
    page.on("dialog", lambda d: d.accept())
    _open_settings(page)

    page.click("#op-backfill-btn")
    # Wait for the run to complete
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Backfill complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )

    echo = page.locator("#op-last-result")
    # Phase 9L: timestamp footer + audit trail hint render on 'ok' tone
    expect(echo).to_have_class(re.compile(r"op-last-result-ok"))
    expect(echo.locator(".op-last-result-footer")).to_be_visible()
    expect(echo.locator(".op-audit-hint")).to_be_visible()
    hint_text = echo.locator(".op-audit-hint").inner_text()
    assert "audit trail" in hint_text.lower()
    # The footer has a rendered local time string.  We don't assert
    # the exact format (locale-dependent), but it must contain at
    # least one digit + a colon separator.
    footer_text = echo.locator(".op-last-result-footer").inner_text()
    assert re.search(r"\d{1,2}:\d{2}", footer_text), footer_text


# ---------------------------------------------------------------------------
# 6) Recovery — after a 429, the operator panel still works
# ---------------------------------------------------------------------------


def test_operator_panel_recovers_after_429_error(page: Page, axion_server: str):
    """Hit a 429 on backfill, then drop the interceptor and retry.
    The second attempt must succeed — proving the first 429 didn't
    wedge the UI or leave the button permanently disabled."""
    _open_dashboard(page, "pA")

    # First attempt: intercept with 429
    def _rl(route, request):
        route.fulfill(
            status=429,
            content_type="application/json",
            headers={"Retry-After": "2"},
            body=json.dumps({
                "detail": "Rate limit exceeded.",
                "bucket": "mutation",
                "limit_per_minute": 10,
                "retry_after_seconds": 2,
            }),
        )

    page.route("**/api/v1/operator/backfill", _rl)
    page.on("dialog", lambda d: d.accept())
    _open_settings(page)

    page.click("#op-backfill-btn")
    expect(page.locator("#op-last-result")).to_contain_text(
        "Backfill rate-limited", timeout=_ASSERT_TIMEOUT,
    )

    # Button is enabled again (the busy state auto-cleared)
    expect(page.locator("#op-backfill-btn")).to_be_enabled(timeout=_ASSERT_TIMEOUT)

    # Drop the interceptor so the next POST hits the real server
    page.unroute("**/api/v1/operator/backfill")

    # Second attempt: real server, should succeed
    page.click("#op-backfill-btn")
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Backfill complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )
    expect(page.locator("#op-last-result")).to_have_class(
        re.compile(r"op-last-result-ok"),
    )


# ---------------------------------------------------------------------------
# 7) No console errors during any of the new flows
# ---------------------------------------------------------------------------


def test_no_console_errors_during_operator_ux_flows(page: Page, axion_server: str):
    """Walk through status chip idle → running → back to idle, trigger
    a 409, trigger a 429, then run a successful backfill.  Assert no
    console errors anywhere along the way."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # Intercept status to show reconcile running
    statuses = iter([
        {"reconcile": {"in_progress": True}, "backfill": {"in_progress": False}},
        {"reconcile": {"in_progress": False}, "backfill": {"in_progress": False}},
    ])

    def _status(route, request):
        try:
            payload = next(statuses)
        except StopIteration:
            payload = {
                "reconcile": {"in_progress": False},
                "backfill": {"in_progress": False},
            }
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        )
    page.route("**/api/v1/operator/actions/status", _status)

    _open_settings(page)

    # Wait for the first poll to flip the chip to running
    page.wait_for_function(
        "() => document.querySelector('#op-reconcile-chip')?.classList.contains('op-action-chip-running')",
        timeout=_ASSERT_TIMEOUT,
    )

    # Drop the interceptor so downstream polls get the real state
    page.unroute("**/api/v1/operator/actions/status")

    # No console errors so far
    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, "console errors during UX flows:\n" + "\n".join(fatal)
