"""Real-time transport: Server-Sent Events (primary) and WebSocket (mirror)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, resolve_principal
from .. import ModuleSpec

router = APIRouter(prefix="/api/events", tags=["events"])
HEARTBEAT = 15.0


@router.get("/stream")
async def stream(request: Request, state: AppState = Depends(get_state),
                 principal: Principal = Depends(current_principal),
                 types: str = Query("", description="comma-separated event types, supports trailing *")):
    last_id = 0
    raw = request.headers.get("last-event-id") or request.query_params.get("last_id", "")
    try:
        last_id = int(raw) if raw else 0
    except ValueError:
        last_id = 0
    wanted = {t.strip() for t in types.split(",") if t.strip()} or None

    async def gen():
        yield f": connected as {principal.actor}\nretry: 3000\n\n"
        sub = state.bus.subscribe(last_id=last_id, user_id=principal.id, types=wanted)
        it = sub.__aiter__()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(it.__anext__(), timeout=HEARTBEAT)
                    yield ev.sse()
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except StopAsyncIteration:
                    break
        finally:
            await sub.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.websocket("/ws")
async def websocket(ws: WebSocket):
    state: AppState = ws.app.state.jarvis
    principal = resolve_principal(ws, state)
    if principal is None:
        await ws.close(code=4001)
        return
    await ws.accept()
    last_id = 0
    try:
        last_id = int(ws.query_params.get("last_id", "0") or 0)
    except ValueError:
        pass
    sub = state.bus.subscribe(last_id=last_id, user_id=principal.id)
    it = sub.__aiter__()

    async def reader():
        try:
            while True:
                await ws.receive_text()   # client pings / ignored
        except WebSocketDisconnect:
            pass

    reader_task = asyncio.create_task(reader())
    try:
        while not reader_task.done():
            try:
                ev = await asyncio.wait_for(it.__anext__(), timeout=HEARTBEAT)
                await ws.send_text(json.dumps({"id": ev.id, "type": ev.type, "ts": ev.ts, "data": ev.data},
                                              default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader_task.cancel()
        await sub.aclose()


MODULE = ModuleSpec(id="events", title="Ereignisse", router=router, nav=False, order=2)
