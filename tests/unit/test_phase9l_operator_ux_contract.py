"""Phase 9L frontend/backend contract guard tests.

Locks down the Phase 9L operator-UX hardening contract so future
refactors cannot silently break:

  * the structured ApiError + 429/409 parsing in the fetch layer
  * the operator status poller lifecycle (start on Settings open,
    stop on leave, poll /api/v1/operator/actions/status)
  * the busy/idle chip + disabled button wiring
  * the 409 "in progress" / 429 "rate limit" UX branches
  * the audit-trail readback + timestamp in the last-action block

Same static-guard pattern as Phase 9B and Phase 9I — no browser, no
JS runtime, just regex + substring checks against the source files.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_JS = REPO_ROOT / "dashboard" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "dashboard" / "index.html"
STYLES_CSS = REPO_ROOT / "dashboard" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Phase 9L HTML markers — status chips exist inside the operator panel
# ---------------------------------------------------------------------------


_PHASE9L_REQUIRED_IDS = frozenset({
    "op-reconcile-chip",
    "op-backfill-chip",
})


def test_every_phase9l_id_exists_in_html():
    html = _read(INDEX_HTML)
    missing = [
        i for i in sorted(_PHASE9L_REQUIRED_IDS)
        if f'id="{i}"' not in html
    ]
    assert not missing, f"Phase 9L ids missing from index.html: {missing}"


def test_every_phase9l_id_is_referenced_by_js():
    js = _read(APP_JS)
    unused = [i for i in sorted(_PHASE9L_REQUIRED_IDS) if i not in js]
    assert not unused, (
        f"Phase 9L ids declared in HTML but never read by JS: {unused}"
    )


def test_status_chips_are_inside_the_operator_actions_card():
    html = _read(INDEX_HTML)
    start = html.find('id="op-actions-card"')
    assert start != -1, "op-actions-card anchor missing"
    # grab a window large enough to contain both chips
    block = html[start:start + 4000]
    assert 'id="op-reconcile-chip"' in block, (
        "reconcile chip must live inside the maintenance-actions card"
    )
    assert 'id="op-backfill-chip"' in block, (
        "backfill chip must live inside the maintenance-actions card"
    )


# ---------------------------------------------------------------------------
# 2) Phase 9L CSS classes all have matching rules
# ---------------------------------------------------------------------------


_PHASE9L_JS_CLASSES = frozenset({
    "op-action-chip",
    "op-action-chip-idle",
    "op-action-chip-running",
    "op-btn-busy",
    "op-last-result-body",
    "op-last-result-footer",
    "op-audit-hint",
})

_PHASE9L_HTML_CLASSES = frozenset({
    "op-action-head",
    "op-action-dot",
    "op-action-chip-label",
})

# Dynamically constructed: 'op-last-result-' + tone.  Phase 9L adds
# 'busy' as a new tone on top of the Phase 9I ok/err/info set.
_PHASE9L_DYNAMIC_CLASSES = frozenset({
    "op-last-result-busy",
})


def test_every_phase9l_class_has_a_css_rule():
    css = _read(STYLES_CSS)
    missing = []
    all_classes = (
        _PHASE9L_JS_CLASSES | _PHASE9L_HTML_CLASSES | _PHASE9L_DYNAMIC_CLASSES
    )
    for cls in sorted(all_classes):
        pattern = re.compile(rf"\.{re.escape(cls)}(?![a-zA-Z0-9_-])")
        if not pattern.search(css):
            missing.append(cls)
    assert not missing, (
        f"Phase 9L references {len(missing)} CSS class(es) with no rule: {missing}"
    )


def test_every_phase9l_js_class_still_emitted():
    js = _read(APP_JS)
    unused = [cls for cls in sorted(_PHASE9L_JS_CLASSES) if cls not in js]
    assert not unused, f"Phase 9L JS classes no longer emitted: {unused}"


def test_every_phase9l_html_class_still_declared():
    html = _read(INDEX_HTML)
    unused = [cls for cls in sorted(_PHASE9L_HTML_CLASSES) if cls not in html]
    assert not unused, f"Phase 9L HTML classes no longer declared: {unused}"


# ---------------------------------------------------------------------------
# 3) Structured fetch error — the ApiError class is the single source
#    of 429 / 409 metadata for every caller
# ---------------------------------------------------------------------------


def test_api_error_class_is_defined():
    js = _read(APP_JS)
    assert "class ApiError extends Error" in js, (
        "Phase 9L ApiError class missing from dashboard/js/app.js"
    )


def test_api_error_surfaces_429_metadata():
    js = _read(APP_JS)
    # The ApiError constructor must set isRateLimit + bucket + limit + retry.
    # We look for each field assignment by name — any refactor that
    # loses one of these fields breaks the 429 UX branch.
    for field in (
        "this.isRateLimit = status === 429",
        "this.bucket = this.body.bucket",
        "this.limitPerMinute = this.body.limit_per_minute",
        "this.retryAfter = ",
    ):
        assert field in js, f"ApiError missing 429 field assignment: {field!r}"


def test_api_error_surfaces_409_in_progress_metadata():
    js = _read(APP_JS)
    # FastAPI wraps HTTPException(detail={...}) under a top-level
    # "detail" key, so the client unwraps inner.in_progress.
    assert "this.isInProgress = true" in js
    assert "this.action = inner.action" in js
    # The primary .message must be human-readable (pulled from the
    # inner detail string) so generic error toasts still work.
    assert "this.message = String(inner.detail)" in js


def test_throw_for_status_helper_exists_and_is_used():
    js = _read(APP_JS)
    assert "async function _throwForStatus(" in js
    # fetchJSON / postJSON / putJSON / deleteJSON must all route errors
    # through the helper so the ApiError metadata is consistent.
    for fn_name in ("fetchJSON", "postJSON", "putJSON", "deleteJSON"):
        # Grab the function body and assert it calls _throwForStatus
        start = js.find(f"async function {fn_name}(")
        assert start != -1, f"{fn_name} not found"
        body = js[start:start + 600]
        assert "_throwForStatus" in body, (
            f"{fn_name} does not route errors through _throwForStatus"
        )


# ---------------------------------------------------------------------------
# 4) Status poller lifecycle + consumption of
#    /api/v1/operator/actions/status
# ---------------------------------------------------------------------------


def test_actions_status_api_constant_defined_in_js():
    js = _read(APP_JS)
    assert "opActionsStatus:" in js
    assert "/api/v1/operator/actions/status" in js


def test_actions_status_api_constant_hits_real_backend_route():
    from src.main import app
    registered = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/api/v1/operator/actions/status" in registered, (
        "Phase 9K /api/v1/operator/actions/status route is not "
        "registered — Phase 9L UI will crash on startup."
    )


def test_status_poller_start_stop_helpers_exist():
    js = _read(APP_JS)
    assert "function _opStartStatusPolling(" in js
    assert "function _opStopStatusPolling(" in js
    assert "async function _opPollActionsStatus(" in js
    # Poller runs on a setInterval loop
    assert "setInterval(_opPollActionsStatus" in js
    # And fires a single immediate poll on start so the chip is never
    # stuck on 'Idle' for 4 seconds after the panel opens.
    assert "_opPollActionsStatus()" in js


def test_load_operator_panel_starts_the_poller():
    js = _read(APP_JS)
    # loadOperatorPanel is the entry point — the last thing it does
    # must be to kick off the status poller.
    start = js.find("async function loadOperatorPanel(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "_opStartStatusPolling()" in body, (
        "loadOperatorPanel must start the status poller"
    )


def test_switch_tab_stops_the_poller_when_leaving_settings():
    js = _read(APP_JS)
    # switchTab is the leave hook — it stops the poller for any tab
    # other than 'settings'.
    assert "function switchTab(name)" in js
    start = js.find("function switchTab(name)")
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "_opStopStatusPolling" in body, (
        "switchTab must stop the operator status poller on leave"
    )
    assert "name !== 'settings'" in body, (
        "switchTab must only stop the poller for non-Settings tabs"
    )


# ---------------------------------------------------------------------------
# 5) Busy state + chip/button renderer
# ---------------------------------------------------------------------------


def test_chip_state_renderer_updates_both_chips_and_both_buttons():
    js = _read(APP_JS)
    start = js.find("function _opUpdateActionChipUI(")
    assert start != -1, "_opUpdateActionChipUI missing"
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # Both chips are updated
    assert "#op-reconcile-chip" in body
    assert "#op-backfill-chip" in body
    # Both buttons are flipped between idle / disabled+busy
    assert "#op-reconcile-btn" in body
    assert "#op-backfill-btn" in body
    # Busy state applies the op-btn-busy class + disables the button
    assert "btn-busy" in body or "op-btn-busy" in body
    assert ".disabled = true" in body
    assert ".disabled = false" in body
    assert "Running" in body  # the busy label text
    assert "Run Reconcile" in body
    assert "Run Backfill" in body


def test_local_in_flight_flags_exist_and_are_flipped_by_actions():
    js = _read(APP_JS)
    # The local flags let the chip flip instantly on click before the
    # next status poll arrives.
    assert "_opReconcileLocalRunning" in js
    assert "_opBackfillLocalRunning" in js
    # Both action handlers flip the matching flag
    start = js.find("async function _opRunReconcile(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "_opReconcileLocalRunning = true" in body
    assert "_opReconcileLocalRunning = false" in body

    start = js.find("async function _opRunBackfill(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "_opBackfillLocalRunning = true" in body
    assert "_opBackfillLocalRunning = false" in body


def test_action_handlers_guard_against_concurrent_local_runs():
    """The pre-9L handlers only relied on server-side 409.  Phase 9L
    adds a local fail-fast: if the chip already says running, the
    click is treated as a friendly ``already running`` state without
    hitting the network."""
    js = _read(APP_JS)
    for fn in ("_opRunReconcile", "_opRunBackfill"):
        start = js.find(f"async function {fn}(")
        assert start != -1, f"{fn} missing"
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "already running" in body.lower(), (
            f"{fn} missing local concurrent-run guard message"
        )


# ---------------------------------------------------------------------------
# 6) 409 + 429 UX branches
# ---------------------------------------------------------------------------


def test_operator_error_handler_has_in_progress_branch():
    js = _read(APP_JS)
    start = js.find("function _opHandleOperatorError(")
    assert start != -1, "_opHandleOperatorError missing"
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # 409 branch — uses the structured ApiError metadata
    assert "e.isInProgress" in body
    assert "already running" in body.lower()
    # The 409 branch must NOT surface as a red/error state — use the
    # 'busy' tone to distinguish from genuine failures.
    assert "'busy'" in body
    # The chip state must stay "Running" until the next poll reports idle.
    assert "_opReconcileServerRunning" in body or "_opBackfillServerRunning" in body


def test_operator_error_handler_has_rate_limit_branch():
    js = _read(APP_JS)
    start = js.find("function _opHandleOperatorError(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # 429 branch — uses the structured ApiError metadata
    assert "e.isRateLimit" in body
    assert "retryAfter" in body or "Retry in" in body
    assert "rate-limit" in body.lower() or "rate limit" in body.lower()
    # Fires the global throttle toast
    assert "_surfaceRateLimitToast" in body


def test_rate_limit_toast_helper_exists_and_is_throttled():
    js = _read(APP_JS)
    assert "function _surfaceRateLimitToast(" in js
    # Throttled per-bucket window so a burst of 429s doesn't spam.
    assert "_rateLimitLastToast" in js
    assert "_rateLimitToastWindowMs" in js


# ---------------------------------------------------------------------------
# 7) Trust readback — timestamp + audit hint on successful mutations
# ---------------------------------------------------------------------------


def test_last_result_renders_timestamp():
    js = _read(APP_JS)
    start = js.find("function _opShowLastResult(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # Timestamp is built from a local Date and rendered in the footer
    assert "toLocaleTimeString" in body
    assert "op-last-result-footer" in body


def test_last_result_renders_audit_hint_on_ok():
    js = _read(APP_JS)
    start = js.find("function _opShowLastResult(")
    assert start != -1
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # The audit hint is conditional on ``tone === 'ok'`` so busy/err
    # states don't lie about being audited.  (They ARE audited —
    # every action writes an AuditLog row — but the visual hint is
    # kept to the success path for clarity.)
    assert "tone === 'ok'" in body
    assert "audit trail" in body.lower() or "op-audit-hint" in body


# ---------------------------------------------------------------------------
# 8) Integration sanity — the new API constants point to real routes
# ---------------------------------------------------------------------------


def test_audit_api_constant_defined():
    js = _read(APP_JS)
    assert "auditEntries:" in js
    assert "/api/v1/audit" in js


def test_audit_route_registered_in_backend():
    from src.main import app
    registered = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/api/v1/audit" in registered
