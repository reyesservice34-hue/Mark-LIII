"""
The live line: one WebSocket for the browser, relayed to the Realtime API.

The browser never sees the API key. It sends audio and receives audio; this
module holds the upstream socket, hands the model the same tools the chat has,
and runs their calls through the same executor — role check, approval gate,
audit trail included.
"""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, resolve_principal
from ...services import realtime
from .. import ModuleSpec

router = APIRouter(tags=["live"])


@router.get("/api/voice/live/capabilities")
async def live_capabilities(state: AppState = Depends(get_state),
                            principal: Principal = Depends(current_principal)):
    """What the live line can do right now — and if it cannot, why."""
    caps = realtime.capabilities()
    caps["tools"] = sum(1 for t in state.tools.all() if t.available and t.handler)
    return caps


@router.websocket("/api/voice/live")
async def live(ws: WebSocket):
    state: AppState = ws.app.state.jarvis
    # The cookie is on the handshake like any other request, so the same
    # resolver decides who this is. An unauthenticated socket is closed before
    # a single byte of audio moves.
    principal = resolve_principal(ws, state)  # type: ignore[arg-type]
    if principal is None or principal.kind != "user":
        await ws.close(code=4401)
        return
    from ...auth import ROLE_RANK
    if ROLE_RANK.get(principal.role, 0) < ROLE_RANK.get("operator", 99):
        await ws.close(code=4403)
        return

    await ws.accept()
    if not realtime.configured():
        await ws.send_text(json.dumps({"type": "jarvis.unavailable",
                                       "detail": realtime.unavailable_reason()}))
        await ws.close(code=1011)
        return

    instructions = state.runtime.live_instructions()
    session = realtime.RealtimeSession(
        state, principal, instructions=instructions,
        send_down=lambda ev: ws.send_text(json.dumps(ev)))

    try:
        await session.connect()
    except realtime.RealtimeError as e:
        await ws.send_text(json.dumps({"type": "jarvis.unavailable", "detail": str(e)}))
        await ws.close(code=1011)
        return

    state.log.info("realtime", f"live line opened for {principal.actor}")
    await ws.send_text(json.dumps({"type": "jarvis.ready", **realtime.capabilities()}))

    async def from_browser() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                await session.from_browser(json.loads(raw))
            except ValueError:
                continue

    pump = asyncio.create_task(session.pump())
    reader = asyncio.create_task(from_browser())
    try:
        done, pending = await asyncio.wait({pump, reader}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                task.result()
    finally:
        await session.close()
        state.log.info("realtime", f"live line closed for {principal.actor}")
        with contextlib.suppress(Exception):
            await ws.close()


MODULE = ModuleSpec(id="live", title="Live", router=router, nav=False, order=3)
