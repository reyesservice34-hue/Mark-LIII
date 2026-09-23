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
from ...services import realtime as realtime_openai
from ...services import realtime_gemini
from .. import ModuleSpec

router = APIRouter(tags=["live"])


def _provider():
    """Which upstream backs the live line. This project standardized on free
    Gemini models throughout (see the "Kostenlos ist Pflicht" commit) — OpenAI's
    Realtime API needs a paid key this deployment was never meant to carry, so
    it's only a fallback for a server that has an OPENAI_API_KEY but no Gemini
    one. Both modules share the same RealtimeSession/capabilities()/configured()
    shape, so callers never need to know which one they got."""
    if realtime_gemini.configured():
        return realtime_gemini
    return realtime_openai


@router.get("/api/voice/live/capabilities")
async def live_capabilities(state: AppState = Depends(get_state),
                            principal: Principal = Depends(current_principal)):
    """What the live line can do right now — and if it cannot, why."""
    caps = _provider().capabilities()
    # Same count the line will actually offer (state.runtime.live_tools is
    # role-scoped) — a bare state.tools.all() would show a viewer a number
    # bigger than what they can really reach.
    caps["tools"] = len(state.runtime.live_tools(principal))
    return caps


@router.websocket("/api/voice/live")
async def live(ws: WebSocket):
    state: AppState = ws.app.state.jarvis
    # Der Handshake trägt dieselben Merkmale wie jede andere Anfrage, also
    # entscheidet derselbe Resolver. Ein Browser kommt über sein Cookie, ein
    # Rechner über X-Jarvis-Token — beides ist recht, denn die Rolle
    # entscheidet, nicht die Art des Nachweises. Ohne Nachweis wird geschlossen,
    # bevor ein einziges Byte Audio fließt.
    principal = resolve_principal(ws, state)  # type: ignore[arg-type]
    if principal is None:
        await ws.close(code=4401)
        return
    from ...auth import ROLE_RANK
    if ROLE_RANK.get(principal.role, 0) < ROLE_RANK.get("operator", 99):
        await ws.close(code=4403)
        return

    await ws.accept()
    conversation_id = str(ws.query_params.get("conversation_id") or "").strip()
    if conversation_id:
        conv = state.services["chat"].get_conversation(conversation_id, principal.id)
        if not conv:
            await ws.send_text(json.dumps({"type": "jarvis.unavailable", "detail": "Sitzung nicht gefunden."}))
            await ws.close(code=4404)
            return
    realtime = _provider()
    if not realtime.configured():
        await ws.send_text(json.dumps({"type": "jarvis.unavailable",
                                       "detail": realtime.unavailable_reason()}))
        await ws.close(code=1011)
        return

    instructions = state.runtime.live_instructions(principal)
    if conversation_id:
        history = state.services["chat"].messages(conversation_id, limit=30)
        if history:
            context = "\n".join(
                f"{'NUTZER' if m.get('role') == 'user' else 'MIA'}: {m.get('content','')[:1200]}"
                for m in history
                if m.get("role") in ("user", "assistant") and m.get("content")
            )
            instructions += (
                "\n\nDIESE LIVE-LEITUNG GEHÖRT ZUR GESPEICHERTEN MIA-SITZUNG. "
                "Sprache und getippter Text sind dieselbe Sitzung. "
                "Nutze diesen bisherigen Verlauf als Kontext:\n" + context[-12000:]
            )
    session = realtime.RealtimeSession(
        state, principal, instructions=instructions, conversation_id=conversation_id,
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
