"""
Live voice — Gemini Live instead of OpenAI's Realtime API.

Same browser-facing protocol as services/realtime.py (the OpenAI-shaped
event names live.ts / VoiceConsole.tsx already speak), so neither needs to
change. Only the upstream connection is different: this project standardized
on free Gemini models throughout (see the "Kostenlos ist Pflicht" commit) —
the OpenAI Realtime API needs a paid key this deployment was never meant to
carry.

The browser sends/receives PCM16 mono at 24 kHz either way (services/
realtime.py's own comment: getting the sample rate wrong sounds like a
broken microphone, not a config mistake). Gemini Live wants 16 kHz on the
way in and produces 24 kHz on the way out, so only the input direction needs
resampling here — audioop.ratecv carries its filter state across calls,
which matters: resetting it every chunk reintroduced exactly the periodic
block-boundary artifact that measurably hurt recognition in Mark-LIII's own
phone-mic pipeline earlier this project (RMSE 0.79 vs. 0.0 against an
unchunked reference, verified in Node before that fix shipped).

Tool calls go through the same ToolExecutor as the chat and the OpenAI live
line — role check, approval gate, audit trail included.
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import contextlib
import json
import os
import re
import time
from typing import Any, Awaitable, Callable

from ..ai.base import ToolNameMap
from ..orchestrator.tool_registry import sanitize_schema

DEFAULT_MODEL = "models/gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Puck"
SEND_RATE    = 16000   # what Gemini Live wants on input
RECEIVE_RATE = 24000   # what Gemini Live produces — matches what the browser already expects
# Mirrors realtime.py's TOOL_TIMEOUT: a tool that stops for approval would
# otherwise hold the line open in silence.
TOOL_TIMEOUT = 25.0

# ── ambient listening: on all the time, only reacts once addressed ─────────
# The microphone here is never opened by hand — see modules/live/__init__.py
# and the frontend's liveStore, which open it once on page load and keep it
# open across navigation. That only works as an "ambient" line rather than an
# always-answering one if something decides which speech was actually meant
# for Jarvis. Mark-LIII's desktop app solves this by never sending audio
# upstream at all until a local wake-word model fires; a browser microphone
# can't run that same model, so the gate has to live here instead, on the
# transcript Gemini already produces — everything still reaches Gemini (it
# has to, to be transcribed at all), but nothing reaches the browser, and no
# tool call is allowed to run, until "Jarvis" actually appears in what was
# said. WAKE_SLEEP_TIMEOUT mirrors main.py's own constant of the same name.
WAKE_WORD = re.compile(r"\bjarvis\b", re.IGNORECASE)
WAKE_SLEEP_TIMEOUT = 120.0


class RealtimeError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _gemini_key() -> str:
    return _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")


def configured() -> bool:
    return bool(_gemini_key())


def unavailable_reason() -> str:
    if not configured():
        return ("GEMINI_API_KEY is not set. The live line runs on Gemini Live; "
                "the key stays on this server.")
    return ""


def settings() -> dict:
    return {
        "model": _env("JARVIS_CC_REALTIME_MODEL") or DEFAULT_MODEL,
        "voice": _env("JARVIS_CC_REALTIME_VOICE") or DEFAULT_VOICE,
    }


def capabilities() -> dict:
    s = settings()
    return {
        "available": configured(),
        "detail": (f"live line via {s['model']} ({s['voice']})" if configured()
                   else unavailable_reason()),
        "model": s["model"] if configured() else "",
        "voice": s["voice"] if configured() else "",
        "audio": {"format": "pcm16", "sample_rate": RECEIVE_RATE, "channels": 1},
        "machine_tokens": True,
        # The mic is meant to stay open all the time (see modules/live's
        # auto-connect); this tells the frontend it doesn't need its own
        # "open the line" button, and what the mic is actually waiting for.
        "ambient": True,
        "wake_word": "Jarvis",
    }


def _tool_declarations(state, principal) -> tuple[list[dict], ToolNameMap]:
    """The same tools the chat has, in the shape Gemini's function-calling expects.

    Role-scoped via runtime.live_tools() — the same state.tools.for_agent()
    call chat's master agent goes through — not a bare state.tools.all().
    A lower-privileged caller must see exactly what chat would have shown
    them, never more just because they came in by voice instead of typing.

    Deliberately `parameters_json_schema`, not `parameters`: google-genai
    validates `parameters` against its own `Schema` type, a strict subset of
    JSON Schema with extra="forbid" — real tool schemas here (n8n's node
    definitions especially) carry plain JSON-Schema-2020-12 keys like `$schema`
    and a numeric `exclusiveMinimum` that `Schema` rejects outright, one bad
    tool taking the whole live line down with it (every declaration is
    validated together). `parameters_json_schema` is untyped (`Any`) on the
    google-genai side and passed through to the API as real JSON Schema, so
    the same sanitize_schema() output every other provider already gets here
    works unchanged."""
    tools = state.runtime.live_tools(principal)
    names = ToolNameMap((t.name for t in tools), limit=64)
    decls = [{
        "name": names.wire(t.name),
        "description": t.description,
        "parameters_json_schema": sanitize_schema(t.input_schema),
    } for t in tools]
    return decls, names


class RealtimeSession:
    """One live conversation. Owns the upstream Gemini session and the tool loop.

    Same public shape as realtime.RealtimeSession (connect/from_browser/pump/
    close), so modules/live/__init__.py can use either interchangeably."""

    def __init__(self, state, principal, *, instructions: str = "",
                 send_down: Callable[[dict], Awaitable[None]]) -> None:
        self.state = state
        self.principal = principal
        self.instructions = instructions
        self.send_down = send_down
        self.session: Any = None
        self._live_cm: Any = None
        self._names: ToolNameMap | None = None
        self._closed = False
        self._resample_state: Any = None      # audioop.ratecv's carried filter state
        self._turn_open = False                # whether response.created was already sent this turn
        self._said = ""
        self._heard: list[str] = []
        # Ambient wake-word gate — see the module docstring above.
        self._awake = False                    # starts asleep: nothing plays until addressed
        self._awake_until = 0.0                # monotonic deadline; past it, back to asleep
        self._turn_heard = ""                  # this turn's input transcript so far, for the wake check
        self._turn_pending: list[dict] = []    # this turn's downstream events, held until awake or dropped

    # ── upstream ─────────────────────────────────────────────────────────
    async def connect(self) -> None:
        if not configured():
            raise RealtimeError(unavailable_reason())
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover - dependency is pinned
            raise RealtimeError("The 'google-genai' package is missing on the server.") from e

        self._types = types
        s = settings()
        decls, names = _tool_declarations(self.state, self.principal)
        self._names = names

        # This code enforces the wake-word gate regardless of what the model
        # does (see pump()/_emit below) — but telling the model about it too
        # means it doesn't spend a turn narrating small talk it will never be
        # allowed to say out loud.
        ambient_note = (
            "\n\nAmbient listening: this microphone is left open all the time, not "
            "pushed to talk, so you hear everything nearby — not all of it addressed "
            "to you. Only answer, or use a tool, once the person has actually said "
            "your name (\"Jarvis\") in what they just said. For anything else, stay "
            "completely silent: no spoken reply, no filler, no tool call. Once "
            "addressed, keep responding naturally for the rest of that exchange "
            "without needing your name repeated every sentence."
        )
        client = genai.Client(api_key=_gemini_key())
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=self.instructions + ambient_note,
            tools=[{"function_declarations": decls}] if decls else None,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=s["voice"])
                )
            ),
        )
        try:
            self._live_cm = client.aio.live.connect(model=s["model"], config=config)
            self.session = await self._live_cm.__aenter__()
        except Exception as e:  # noqa: BLE001
            self._live_cm = None
            raise RealtimeError(f"Could not open the live line: {e.__class__.__name__}: {e}") from e

    async def from_browser(self, event: dict) -> None:
        """Relay a client event — only the ones a browser is allowed to send,
        translated from the OpenAI-shaped wire protocol into Gemini's calls."""
        if self.session is None or self._closed:
            return
        t = event.get("type", "")
        if t == "input_audio_buffer.append":
            b64 = event.get("audio", "")
            if not b64:
                return
            try:
                raw24 = base64.b64decode(b64)
            except Exception:
                return
            # 24 kHz -> 16 kHz, 16-bit mono, carrying filter state across calls.
            raw16, self._resample_state = audioop.ratecv(
                raw24, 2, 1, 24000, SEND_RATE, self._resample_state)
            if raw16:
                await self.session.send_realtime_input(
                    audio=self._types.Blob(data=raw16, mime_type="audio/pcm"))
        elif t == "conversation.item.create":
            item = event.get("item") or {}
            content = item.get("content") or []
            text = "".join(
                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "input_text"
            ).strip()
            if text:
                # Typing is already an explicit, deliberate address to Jarvis —
                # the wake-word gate exists for ambient speech, not for this.
                self._awake = True
                self._awake_until = time.monotonic() + WAKE_SLEEP_TIMEOUT
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": text}]},
                    turn_complete=True,
                )
        elif t == "input_audio_buffer.clear":
            self._resample_state = None
        # response.create / response.cancel / input_audio_buffer.commit: Gemini
        # Live decides turn boundaries itself from the audio stream — nothing
        # to relay for these.

    # ── ambient wake-word gate ───────────────────────────────────────────
    # Every downstream event Gemini's turn produces goes through _emit rather
    # than send_down directly: while asleep it is held (not dropped yet —
    # the wake word can still turn up later in the same turn, since the
    # transcript and the spoken reply stream in roughly together), and only
    # actually reaches the browser once "Jarvis" has been heard. Nothing here
    # can make Gemini answer faster than it decides to; it only ever holds
    # back what Gemini already produced.
    async def _emit(self, ev: dict) -> None:
        if self._awake:
            self._awake_until = time.monotonic() + WAKE_SLEEP_TIMEOUT
            await self.send_down(ev)
        else:
            self._turn_pending.append(ev)

    async def _wake(self) -> None:
        self._awake = True
        self._awake_until = time.monotonic() + WAKE_SLEEP_TIMEOUT
        await self.send_down({"type": "jarvis.awake"})
        pending, self._turn_pending = self._turn_pending, []
        for ev in pending:
            await self.send_down(ev)

    async def _maybe_sleep(self) -> None:
        if self._awake and time.monotonic() > self._awake_until:
            self._awake = False
            await self.send_down({"type": "jarvis.asleep"})

    async def _turn_started(self) -> None:
        if not self._turn_open:
            self._turn_open = True
            self._said = ""
            await self._emit({"type": "response.created"})

    def _end_turn(self) -> None:
        self._turn_open = False
        self._turn_heard = ""
        self._turn_pending = []   # never woken into during this turn — ambient, discard

    # ── downstream ───────────────────────────────────────────────────────
    async def pump(self) -> None:
        """Read from Gemini until the line closes, translating each event into
        the same shape the OpenAI-backed line already sends the browser."""
        assert self.session is not None
        async for response in self.session.receive():
            if self._closed:
                return
            await self._maybe_sleep()

            sc = getattr(response, "server_content", None)
            if sc and sc.interrupted:
                await self._emit({"type": "input_audio_buffer.speech_started"})

            if response.data:
                await self._turn_started()
                await self._emit({
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(response.data).decode("ascii"),
                })

            if sc:
                if sc.output_transcription and sc.output_transcription.text:
                    await self._turn_started()
                    self._said += sc.output_transcription.text
                    await self._emit({
                        "type": "response.output_audio_transcript.delta",
                        "delta": sc.output_transcription.text,
                    })
                if sc.input_transcription and sc.input_transcription.text:
                    self._heard.append(sc.input_transcription.text)
                    self._turn_heard += sc.input_transcription.text
                    if not self._awake and WAKE_WORD.search(self._turn_heard):
                        await self._wake()
                if sc.turn_complete:
                    full_in = " ".join(self._heard).strip()
                    self._heard = []
                    if full_in:
                        await self._emit({
                            "type": "conversation.item.input_audio_transcription.completed",
                            "transcript": full_in,
                        })
                    await self._emit({"type": "response.done"})
                    self._end_turn()

            if getattr(response, "tool_call", None):
                for fc in response.tool_call.function_calls:
                    if not self._awake and WAKE_WORD.search(self._turn_heard):
                        await self._wake()
                    if self._awake:
                        asyncio.create_task(self._run_tool(fc))
                    else:
                        asyncio.create_task(self._decline_tool(fc))

    # ── tools ────────────────────────────────────────────────────────────
    async def _run_tool(self, fc) -> None:
        from ..orchestrator.tool_registry import ToolContext

        wire = fc.name
        name = self._names.real(wire) if self._names else wire
        args = dict(fc.args or {})

        ctx = ToolContext(state=self.state, principal=self.principal, agent_id="jarvis",
                          emit=lambda kind, data: None)
        executor = self.state.runtime.executor
        try:
            text, ok = await asyncio.wait_for(executor.execute(ctx, name, args), timeout=TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            text, ok = ("That needs your approval first — it is waiting in the dashboard "
                        "under Freigaben."), False
        except Exception as e:  # noqa: BLE001
            text, ok = f"{name} failed: {e}", False

        if self.session is not None and not self._closed:
            with contextlib.suppress(Exception):
                await self.session.send_tool_response(function_responses=[
                    self._types.FunctionResponse(id=fc.id, name=wire,
                                                 response={"ok": ok, "result": str(text)[:6000]})
                ])
        await self.send_down({"type": "jarvis.tool", "name": name, "ok": ok,
                              "result": str(text)[:400]})

    async def _decline_tool(self, fc) -> None:
        """A tool call attempted while nobody had addressed Jarvis yet. This
        never reaches the executor — no role check or approval gate would make
        an unaddressed action safe, the action itself must not run at all —
        and it never reaches the browser either, same as any other ambient
        chatter. Gemini still needs an answer for the call it made, or the
        turn is left hanging."""
        if self.session is not None and not self._closed:
            with contextlib.suppress(Exception):
                await self.session.send_tool_response(function_responses=[
                    self._types.FunctionResponse(id=fc.id, name=fc.name, response={
                        "ok": False,
                        "result": "Not addressed — this is ambient listening; say \"Jarvis\" first.",
                    })
                ])

    async def close(self) -> None:
        self._closed = True
        if self._live_cm is not None:
            with contextlib.suppress(Exception):
                await self._live_cm.__aexit__(None, None, None)
            self._live_cm = None
            self.session = None


__all__ = ["RealtimeSession", "RealtimeError", "capabilities", "configured",
           "unavailable_reason", "settings"]
