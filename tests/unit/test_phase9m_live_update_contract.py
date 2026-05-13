"""Phase 9M frontend/backend contract guard tests.

Locks down the Phase 9M live-update wiring so future refactors cannot
silently break:

  * the backend ``notify_*`` helpers exist and are wired at the right
    hook points (reconcile routes, backfill routes, RiskAgent alert
    commit, CollectionAgent event-with-links commit)
  * the new ``notify_operator_action`` helper exists with the expected
    state set
  * the frontend ``_wsDispatch`` central router handles every known
    message type AND ignores unknown ones safely
  * the modal-open guard + pending-refresh deferral helpers exist
  * refresh rules are scoped by active tab + active subtab
  * operator_action events drive the Phase 9L poller refresh
  * portfolio-scoped refreshes check ``msg.portfolio_id``

Static-guard pattern only — no browser, no runtime, just regex over
the source files.  The real runtime behavior is covered by the
integration test in ``tests/integration/test_phase9m_broadcast_hooks.py``
and the E2E tests in ``tests/e2e/test_e2e_phase9m_live_updates.py``.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_JS = REPO_ROOT / "dashboard" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "dashboard" / "index.html"
WS_PY = REPO_ROOT / "src" / "api" / "routes" / "ws.py"
OPERATOR_PY = REPO_ROOT / "src" / "api" / "routes" / "operator.py"
RISK_PY = REPO_ROOT / "src" / "agents" / "risk.py"
COLLECTION_PY = REPO_ROOT / "src" / "agents" / "collection.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Backend notify_* helpers exist with the expected signatures
# ---------------------------------------------------------------------------


class TestBackendNotifyHelpers:
    def test_notify_operator_action_helper_exists(self):
        ws = _read(WS_PY)
        assert "def notify_operator_action(" in ws
        # Must accept action + state + optional detail dict
        assert re.search(
            r"def notify_operator_action\([^)]*action[^)]*state[^)]*\)",
            ws,
        ), "notify_operator_action signature changed"
        # Must emit a message with type 'operator_action'
        assert '"type": "operator_action"' in ws

    def test_operator_action_states_are_frozen(self):
        ws = _read(WS_PY)
        assert "_OPERATOR_ACTION_STATES" in ws
        # The three valid states must be present in the literal set
        for state in ("started", "finished", "failed"):
            assert f'"{state}"' in ws, (
                f"operator_action state {state!r} missing from ws.py"
            )

    def test_notify_alert_carries_portfolio_id(self):
        ws = _read(WS_PY)
        # Phase 9M: portfolio_id is a first-class field so the
        # frontend dispatcher can enforce portfolio-safe refreshes.
        assert 'portfolio_id' in ws
        # The call site must include it in the payload.  We slice
        # from the def to the end of the body by looking for the
        # next top-level `def ` at column 0.
        start = ws.find("def notify_alert(")
        assert start != -1
        end = ws.find("\ndef ", start + 1)
        if end == -1:
            end = len(ws)
        body = ws[start:end]
        assert 'portfolio_id: str | None' in body
        assert '"portfolio_id": portfolio_id' in body

    def test_notify_event_carries_linked_holding_count(self):
        ws = _read(WS_PY)
        start = ws.find("def notify_event(")
        assert start != -1
        end = ws.find("\n\n", start)
        body = ws[start:end]
        assert "linked_holding_count" in body

    def test_broadcast_sync_handles_running_loop_safely(self):
        """Phase 9M: broadcast_sync must use get_running_loop + ensure_future
        when called from inside a running event loop (the common case
        for route handlers), not the legacy get_event_loop +
        run_until_complete which deprecation-warns on Python 3.12.

        We strip the docstring before inspecting the executable body —
        the docstring mentions ``run_until_complete`` as historical
        context explaining WHY we use the new pattern, and we don't
        want that mention to fool the sub-assertion.
        """
        ws = _read(WS_PY)
        start = ws.find("def broadcast_sync(")
        assert start != -1
        end = ws.find("\ndef ", start + 1)
        if end == -1:
            end = len(ws)
        body = ws[start:end]
        # Strip the docstring — take only what's after the closing `"""`.
        close_q = body.find('"""', body.find('"""') + 3)
        if close_q != -1:
            code_body = body[close_q + 3:]
        else:
            code_body = body
        assert "asyncio.get_running_loop" in code_body
        assert "ensure_future" in code_body
        # The legacy run_until_complete path is gone from the live code.
        assert "run_until_complete" not in code_body
        # And asyncio.run handles the no-running-loop branch
        assert "asyncio.run" in code_body


# ---------------------------------------------------------------------------
# 2) Backend broadcast hook points exist
# ---------------------------------------------------------------------------


class TestBackendBroadcastHooks:
    def test_reconcile_route_emits_started_and_finished(self):
        op = _read(OPERATOR_PY)
        start = op.find("async def trigger_reconcile(")
        assert start != -1
        end = op.find("\n\n\n", start)
        body = op[start:end]
        # Started event MUST fire BEFORE the in-flight guard check —
        # otherwise a running call's started event would be lost.
        assert 'notify_operator_action("reconcile", "started")' in body
        assert 'notify_operator_action("reconcile", "finished")' in body
        # Failed path emits failed
        assert 'notify_operator_action("reconcile", "failed")' in body

    def test_backfill_route_emits_started_and_finished(self):
        op = _read(OPERATOR_PY)
        start = op.find("async def trigger_backfill(")
        assert start != -1
        end = op.find("\n\n\n", start)
        body = op[start:end]
        assert 'notify_operator_action("backfill", "started")' in body
        assert 'notify_operator_action("backfill", "finished")' in body
        assert 'notify_operator_action("backfill", "failed")' in body

    def test_risk_agent_broadcasts_after_commit(self):
        risk = _read(RISK_PY)
        assert "notify_alert" in risk, "RiskAgent does not call notify_alert"
        # Broadcast loop must be OUTSIDE the `async with self._get_db()`
        # block so the commit lands before the WS fan-out.  We look
        # for the sentinel comment the implementation added.
        assert "Phase 9M" in risk
        # And the broadcast loop reads from a freshly_created list
        # populated during the commit block
        assert "freshly_created" in risk

    def test_collection_agent_broadcasts_only_when_links_nonzero(self):
        col = _read(COLLECTION_PY)
        assert "notify_event" in col, "CollectionAgent does not call notify_event"
        # Must be guarded on ``link_count > 0`` so events that touch
        # no holdings don't trigger UI refreshes.  We look in a
        # larger window — the guard lives a few lines after the
        # assignment, past the ``links +=`` accumulator.
        start = col.find("link_count = await self._link_event_to_holdings(")
        assert start != -1
        body = col[start:start + 1500]
        assert "link_count > 0" in body
        assert "notify_event(" in body


# ---------------------------------------------------------------------------
# 3) Frontend dispatcher exists and routes every known type
# ---------------------------------------------------------------------------


class TestFrontendDispatcher:
    def test_ws_dispatch_function_defined(self):
        js = _read(APP_JS)
        assert "function _wsDispatch(" in js
        # Exposed on window for E2E test access
        assert "window._wsDispatch" in js

    def test_ws_dispatch_handles_all_known_types(self):
        js = _read(APP_JS)
        start = js.find("function _wsDispatch(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        for t in (
            "ping", "alert", "event", "operator_action",
            "agent_complete", "holding_update",
        ):
            assert f"'{t}'" in body, f"_wsDispatch missing branch for {t!r}"
        # Default branch must exist
        assert "default:" in body

    def test_ws_dispatch_ignores_unknown_types(self):
        js = _read(APP_JS)
        # The default branch must not crash or call any handler — it
        # logs at debug level and returns.
        start = js.find("function _wsDispatch(")
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "console.debug" in body or "return;" in body

    def test_ws_dispatch_defends_against_malformed_messages(self):
        js = _read(APP_JS)
        start = js.find("function _wsDispatch(")
        end = js.find("\n    }\n", start)
        body = js[start:end]
        # Must check msg is an object + type is a string
        assert "typeof msg !== 'object'" in body or "!msg ||" in body
        assert "typeof t !== 'string'" in body or "typeof msg.type" in body

    def test_connect_websocket_routes_through_dispatcher(self):
        js = _read(APP_JS)
        start = js.find("function connectWebSocket(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "_wsDispatch(msg)" in body, (
            "connectWebSocket.onmessage must delegate to _wsDispatch"
        )


# ---------------------------------------------------------------------------
# 4) Modal-open guard + pending-refresh deferral
# ---------------------------------------------------------------------------


class TestModalGuard:
    def test_modal_guard_helper_exists(self):
        js = _read(APP_JS)
        assert "function _wsAnyModalOpen(" in js
        assert "dialog[open]" in js

    def test_queue_or_run_defers_when_modal_open(self):
        js = _read(APP_JS)
        start = js.find("function _wsQueueOrRun(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "_wsAnyModalOpen()" in body
        assert "_wsPendingRefreshes.add(" in body

    def test_pending_refresh_flush_on_dialog_close(self):
        js = _read(APP_JS)
        assert "_wsFlushPendingRefreshes" in js
        assert "addEventListener('close'" in js
        # Only flushes if no modal is still open
        start = js.find("function _wsFlushPendingRefreshes(")
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "_wsAnyModalOpen()" in body


# ---------------------------------------------------------------------------
# 5) Refresh rules — each handler scopes by active tab / subtab
# ---------------------------------------------------------------------------


class TestRefreshRules:
    def test_alert_handler_scopes_by_active_tab_and_portfolio(self):
        js = _read(APP_JS)
        start = js.find("function _wsHandleAlert(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        # Portfolio check
        assert "_wsMessageIsForActivePortfolio" in body
        # Refresh alerts tab if active
        assert "'alerts'" in body
        # Refresh intelligence overview if Portfolio tab is active
        assert "'portfolio'" in body
        assert "loadIntelligenceOverview" in body

    def test_event_handler_scopes_by_subtab(self):
        js = _read(APP_JS)
        start = js.find("function _wsHandleEvent(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "_wsActiveSubtab('intelligence')" in body
        assert "'events'" in body
        assert "loadIntelligenceOverview" in body

    def test_operator_action_handler_triggers_poller(self):
        js = _read(APP_JS)
        start = js.find("function _wsHandleOperatorAction(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        # Must call the Phase 9L poller directly — that's the whole
        # point of Phase 9M for operator status.
        assert "_opPollActionsStatus" in body
        # And must scope to the Settings tab
        assert "'settings'" in body
        # On "finished" state, also refresh the operator tables
        assert "'finished'" in body
        assert "loadOperatorFactorSensitivities" in body
        assert "loadOperatorRelationships" in body

    def test_holding_update_handler_scopes_by_subtab(self):
        js = _read(APP_JS)
        start = js.find("function _wsHandleHoldingUpdate(")
        assert start != -1
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "'portfolio'" in body
        assert "_wsActiveSubtab('portfolio')" in body


class TestPortfolioScopingOfRefreshes:
    def test_message_portfolio_check_exists(self):
        js = _read(APP_JS)
        assert "function _wsMessageIsForActivePortfolio(" in js
        start = js.find("function _wsMessageIsForActivePortfolio(")
        end = js.find("\n    }\n", start)
        body = js[start:end]
        assert "_activePortfolioId" in body
        # Messages without a portfolio_id are treated as global
        assert "portfolio_id == null" in body or "!msg" in body
