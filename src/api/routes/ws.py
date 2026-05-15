"""WebSocket endpoint for real-time dashboard updates."""

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# Active WebSocket connections
_connections: Set[WebSocket] = set()


@router.websocket("/api/v1/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws.accept()
    _connections.add(ws)
    logger.info("WebSocket client connected (%d total)", len(_connections))
    try:
        while True:
            # Keep connection alive — listen for pings or client messages
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await ws.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _connections.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(_connections))


async def broadcast(message: dict):
    """Broadcast a message to all connected WebSocket clients.

    Phase 9K fix: the pre-9K version had a closure-variable shadow
    bug.  It did ``_connections -= dead`` at the bottom, which is an
    augmented assignment — Python promotes ``_connections`` to a
    local for the whole function body, which made the ``if not
    _connections:`` check at the top raise ``UnboundLocalError`` on
    every call.  Phase 9J's static tests missed this because it only
    fires when the module-level set is non-empty.  Phase 9K's
    real-transport verification test catches it.

    The fix is to mutate the set in place via ``discard`` so there's
    no rebinding of ``_connections`` inside the function.
    """
    if not _connections:
        return
    payload = json.dumps(message)
    dead = []
    for ws in _connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.discard(ws)


def broadcast_sync(message: dict) -> None:
    """Fire-and-forget broadcast from sync OR async contexts.

    Phase 9M: this helper is called from FastAPI route handlers and
    from async agents that are already running inside the uvicorn
    event loop.  In that case ``asyncio.get_event_loop()`` raises
    ``DeprecationWarning`` / ``RuntimeError`` on newer Pythons, and
    ``run_until_complete`` fails because the loop is already running.

    The safe pattern is:

      1. Try ``asyncio.get_running_loop()`` — if we're inside a
         running loop (the common case for every route + agent
         caller), schedule the coroutine via ``create_task`` so the
         loop picks it up on the next tick.
      2. Otherwise fall back to ``asyncio.run()`` — the path that
         runs during synchronous unit test teardown or one-off
         scripts.

    Either path is fire-and-forget — the broadcast is best-effort
    and any delivery failure is absorbed inside ``broadcast()``.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # Inside a running event loop — schedule the coroutine.
            # We use ensure_future so pytest-asyncio's captured-loop
            # context still works.
            asyncio.ensure_future(broadcast(message))
        else:
            # No running loop — this happens during sync test paths.
            # Run the coroutine to completion synchronously.
            asyncio.run(broadcast(message))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("broadcast_sync dropped message: %s", exc)


# ---------------------------------------------------------------------------
# Typed notify helpers.  These are the ONLY public way Phase 9M code
# should emit live-update messages.  The frontend dispatcher switches
# on ``type`` and ignores anything it doesn't recognise, so adding a
# new type is a two-file change: a new notify_* here plus a new
# branch in ``_wsDispatch`` in dashboard/js/app.js.
# ---------------------------------------------------------------------------


def notify_alert(
    alert_id: str,
    title: str,
    severity: str,
    portfolio_id: str | None = None,
) -> None:
    """Notify clients of a new alert.

    Phase 9M: carries ``portfolio_id`` so the dashboard can enforce
    portfolio-safety on the refresh side (only refresh the
    intelligence overview / alerts tab if the live alert belongs to
    the currently-active portfolio).
    """
    broadcast_sync({
        "type": "alert",
        "id": alert_id,
        "title": title,
        "severity": severity,
        "portfolio_id": portfolio_id,
    })


def notify_agent_complete(agent_id: str, status: str = "success") -> None:
    """Notify clients that an agent run completed."""
    broadcast_sync({"type": "agent_complete", "agent": agent_id, "status": status})


def notify_event(
    event_id: str,
    title: str,
    linked_holding_count: int = 0,
) -> None:
    """Notify clients of a new event.

    Phase 9M: only events that linked to at least one holding should
    fire this broadcast (the caller enforces that).  ``linked_holding_count``
    is carried through so the frontend can display a small badge or
    decide whether to refresh the intelligence overview.
    """
    broadcast_sync({
        "type": "event",
        "id": event_id,
        "title": title,
        "linked_holding_count": linked_holding_count,
    })


def notify_holding_update() -> None:
    """Notify clients of a holding change."""
    broadcast_sync({"type": "holding_update"})


#: The only valid state strings for operator-action broadcasts.
#: Adding a new state is a contract break — the frontend dispatcher
#: matches on these literal strings.
_OPERATOR_ACTION_STATES = frozenset({"started", "finished", "failed"})


def notify_operator_action(
    action: str,
    state: str,
    detail: dict | None = None,
) -> None:
    """Notify clients that an operator action started / finished / failed.

    Phase 9M: this is the core of the WS-assisted operator status
    refresh.  When the operator UI is open, receiving one of these
    messages triggers an immediate ``_opPollActionsStatus()`` call
    so the busy chip flips to / from "Running…" with sub-second
    latency, without waiting for the 4-second polling interval.

    Parameters
    ----------
    action:
        ``"reconcile"`` or ``"backfill"`` — matches the lock names
        exposed by ``/api/v1/operator/actions/status``.
    state:
        ``"started"``, ``"finished"``, or ``"failed"``.  Any other
        value is coerced to ``"finished"`` for safety — the frontend
        cares about "is something running?" not the exact state.
    detail:
        Optional small dict the frontend can use for extra context.
        Phase 9M does NOT send stats here (stats still live in the
        HTTP response of the POST that triggered the action); this
        is purely a transport-level signal.
    """
    if state not in _OPERATOR_ACTION_STATES:
        state = "finished"
    broadcast_sync({
        "type": "operator_action",
        "action": action,
        "state": state,
        "detail": detail or {},
    })
