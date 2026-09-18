"""
Live voice — an open line, not a walkie-talkie.

The browser holds one WebSocket to this server; this server holds one to
OpenAI's Realtime API. Audio flows both ways continuously, the model decides
itself when the user has stopped speaking, and the user can talk over it.

Why the relay instead of letting the browser connect directly: the API key
would otherwise have to reach the browser. It never does. The same reason the
rest of this server exists.

Tool calls from the live session go through the *same* ToolExecutor as the
chat — role check, approval gate, audit trail. A high-risk call does not
quietly run because it arrived by voice.

Protocol (verified against the published documentation, not from memory):
  wss://api.openai.com/v1/realtime?model=…      header Authorization: Bearer …
  client → session.update, input_audio_buffer.append, conversation.item.create,
           response.create, response.cancel
  server → response.output_audio.delta, response.output_audio_transcript.delta,
           conversation.item.input_audio_transcription.completed, response.done
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any, Awaitable, Callable

from ..ai.base import ToolNameMap

REALTIME_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1"
DEFAULT_VOICE = "cedar"
# The live session is a conversation, not a batch job: a tool that stops to ask
# for approval would otherwise hold the line open in silence. After this it
# says so out loud and the approval is decided in the dashboard.
TOOL_TIMEOUT = 25.0


class RealtimeError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def configured() -> bool:
    return bool(_env("OPENAI_API_KEY"))


def unavailable_reason() -> str:
    if not configured():
        return ("OPENAI_API_KEY is not set. The live line runs on OpenAI's Realtime API; "
                "the key stays on this server.")
    return ""


def settings() -> dict:
    return {
        "model": _env("JARVIS_CC_REALTIME_MODEL") or DEFAULT_MODEL,
        "voice": _env("JARVIS_CC_REALTIME_VOICE") or DEFAULT_VOICE,
        "url": _env("JARVIS_CC_REALTIME_URL") or REALTIME_URL,
    }


def capabilities() -> dict:
    s = settings()
    return {
        "available": configured(),
        "detail": (f"live line via {s['model']} ({s['voice']})" if configured()
                   else unavailable_reason()),
        "model": s["model"] if configured() else "",
        "voice": s["voice"] if configured() else "",
        # What the browser must send and will receive. Stated rather than
        # assumed, because getting the sample rate wrong sounds like a broken
        # microphone rather than a configuration mistake.
        "audio": {"format": "pcm16", "sample_rate": 24000, "channels": 1},
    }


def _tool_declarations(state) -> tuple[list[dict], ToolNameMap]:
    """The same tools the chat has, in the shape the Realtime API expects."""
    tools = [t for t in state.tools.all() if t.available and t.handler]
    names = ToolNameMap((t.name for t in tools), limit=64)
    decls = [{
        "type": "function",
        "name": names.wire(t.name),
        "description": t.description,
        "parameters": t.input_schema,
    } for t in tools]
    return decls, names


class RealtimeSession:
    """One live conversation. Owns the upstream socket and the tool loop."""

    def __init__(self, state, principal, *, instructions: str = "",
                 send_down: Callable[[dict], Awaitable[None]]) -> None:
        self.state = state
        self.principal = principal
        self.instructions = instructions
        self.send_down = send_down
        self.ws: Any = None
        self._names: ToolNameMap | None = None
        self._closed = False

    # ── upstream ─────────────────────────────────────────────────────────
    async def connect(self) -> None:
        if not configured():
            raise RealtimeError(unavailable_reason())
        try:
            import websockets
        except ImportError as e:      # pragma: no cover - dependency is pinned
            raise RealtimeError("The 'websockets' package is missing on the server.") from e

        s = settings()
        url = f"{s['url']}?model={s['model']}"
        headers = {"Authorization": f"Bearer {_env('OPENAI_API_KEY')}"}
        try:
            # websockets renamed this argument; support both rather than
            # pinning the caller to one release.
            try:
                self.ws = await websockets.connect(url, additional_headers=headers,
                                                   max_size=16 * 1024 * 1024)
            except TypeError:
                self.ws = await websockets.connect(url, extra_headers=headers,
                                                   max_size=16 * 1024 * 1024)
        except Exception as e:  # noqa: BLE001
            raise RealtimeError(f"Could not open the live line: {e.__class__.__name__}: {e}") from e

        decls, names = _tool_declarations(self.state)
        self._names = names
        await self._up({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self.instructions,
                "tools": decls,
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        # The model decides when a turn ended. That is what
                        # makes it a conversation instead of push-to-talk,
                        # and it is also what allows talking over it.
                        "turn_detection": {"type": "semantic_vad", "interrupt_response": True},
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {"format": {"type": "audio/pcm", "rate": 24000},
                               "voice": settings()["voice"]},
                },
            },
        })

    async def _up(self, event: dict) -> None:
        if self.ws is not None and not self._closed:
            await self.ws.send(json.dumps(event))

    async def from_browser(self, event: dict) -> None:
        """Relay a client event, but only the ones a browser is allowed to send."""
        t = event.get("type", "")
        if t not in ("input_audio_buffer.append", "input_audio_buffer.commit",
                     "input_audio_buffer.clear", "conversation.item.create",
                     "conversation.item.truncate", "response.create", "response.cancel"):
            return
        await self._up(event)

    # ── downstream ───────────────────────────────────────────────────────
    async def pump(self) -> None:
        """Read from OpenAI until the line closes, acting on what needs acting on."""
        assert self.ws is not None
        async for raw in self.ws:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type", "")
            if t == "response.done":
                # Tool calls arrive here, alongside the finished answer.
                for item in ((ev.get("response") or {}).get("output") or []):
                    if item.get("type") == "function_call":
                        asyncio.create_task(self._run_tool(item))
            if t == "error":
                detail = (ev.get("error") or {}).get("message") or "unknown error"
                self.state.log.warning("realtime", f"live line: {detail}")
            await self.send_down(ev)

    # ── tools ────────────────────────────────────────────────────────────
    async def _run_tool(self, item: dict) -> None:
        from ..orchestrator.tool_registry import ToolContext

        wire = item.get("name", "")
        name = self._names.real(wire) if self._names else wire
        call_id = item.get("call_id", "")
        try:
            args = json.loads(item.get("arguments") or "{}")
        except ValueError:
            args = {}

        ctx = ToolContext(state=self.state, principal=self.principal, agent_id="jarvis",
                          emit=lambda kind, data: None)
        executor = self.state.runtime.executor
        try:
            text, ok = await asyncio.wait_for(executor.execute(ctx, name, args), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            # Almost always the approval gate. Say so instead of leaving a
            # silence the user cannot interpret.
            text, ok = ("That needs your approval first — it is waiting in the dashboard "
                        "under Freigaben."), False
        except Exception as e:  # noqa: BLE001
            text, ok = f"{name} failed: {e}", False

        await self._up({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id,
                     "output": json.dumps({"ok": ok, "result": str(text)[:6000]})},
        })
        # The model does not continue by itself after a tool result.
        await self._up({"type": "response.create"})
        await self.send_down({"type": "jarvis.tool", "name": name, "ok": ok,
                              "result": str(text)[:400]})

    async def close(self) -> None:
        self._closed = True
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
            self.ws = None


__all__ = ["RealtimeSession", "RealtimeError", "capabilities", "configured",
           "unavailable_reason", "settings"]
