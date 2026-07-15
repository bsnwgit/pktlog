"""
WebSocket endpoint for live dashboard/alert updates.

Auth: JWT passed as ?token= query param — browsers can't set custom headers
on a WebSocket handshake, so this can't reuse the Authorization-header
dependency the REST API uses. Same secret_key/algorithm, via
app.auth.local.decode_access_token.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.local import decode_access_token

log = logging.getLogger("pktlog.ws")
router = APIRouter()

_connections: set[WebSocket] = set()
_PING_INTERVAL = 25.0  # seconds — comfortably under typical LB/proxy idle timeouts


@router.websocket("/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    if not decode_access_token(token):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    _connections.add(websocket)
    log.info("WS client connected (%d total)", len(_connections))

    ping_task = asyncio.create_task(_keepalive(websocket))
    try:
        while True:
            # Drain client messages (e.g. "ping" replies) — nothing to act on
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.debug("WS connection error: %s", e)
    finally:
        ping_task.cancel()
        _connections.discard(websocket)
        log.info("WS client disconnected (%d total)", len(_connections))


async def _keepalive(websocket: WebSocket) -> None:
    try:
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except (asyncio.CancelledError, Exception):
        pass


async def broadcast_alert_fired(
    event_id: int, rule_name: str, severity: str, message: str, details: dict,
) -> None:
    """Called by AlertEngine._fire() — pushes a live toast to connected clients."""
    if not _connections:
        return
    payload = json.dumps({
        "type": "alert_fired",
        "data": {
            "event_id": event_id,
            "rule_name": rule_name,
            "severity": severity,
            "message": message,
            "details": details,
            "fired_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    })
    dead = set()
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _connections.difference_update(dead)
