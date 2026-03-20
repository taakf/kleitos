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
    """Broadcast a message to all connected WebSocket clients."""
    if not _connections:
        return
    payload = json.dumps(message)
    dead = set()
    for ws in _connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _connections -= dead


def broadcast_sync(message: dict):
    """Fire-and-forget broadcast from synchronous code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast(message))
        else:
            loop.run_until_complete(broadcast(message))
    except RuntimeError:
        pass


def notify_alert(alert_id: str, title: str, severity: str):
    """Notify clients of a new alert."""
    broadcast_sync({"type": "alert", "id": alert_id, "title": title, "severity": severity})


def notify_agent_complete(agent_id: str, status: str = "success"):
    """Notify clients that an agent run completed."""
    broadcast_sync({"type": "agent_complete", "agent": agent_id, "status": status})


def notify_event(event_id: str, title: str):
    """Notify clients of a new event."""
    broadcast_sync({"type": "event", "id": event_id, "title": title})


def notify_holding_update():
    """Notify clients of a holding change."""
    broadcast_sync({"type": "holding_update"})
