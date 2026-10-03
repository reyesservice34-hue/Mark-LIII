"""Local speech transport for the same conversation/runtime as text chat."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import math
import struct
import wave
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from ...auth import Principal, ROLE_RANK
from ...deps import AppState, current_principal, get_state, resolve_principal
from ...services.voice_service import VoiceError
from .. import ModuleSpec

router = APIRouter(tags=["live"])

@router.get("/api/voice/live/capabilities")
async def live_capabilities(state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    voice = state.services["voice"]
    return {"available": voice.stt_available() and voice.tts_available(), "provider": "local",
            "model": "MIA Chat Runtime", "tools": len(state.runtime.live_tools(principal)),
            "detail": "Lokale Sprache mit gemeinsamem MIA-Gespräch", **voice.capabilities()}

@router.websocket("/api/voice/live")
async def live(ws: WebSocket):
    state: AppState = ws.app.state.jarvis
    principal = resolve_principal(ws, state)
    if principal is None:
        await ws.close(code=4401)
        return
    if ROLE_RANK.get(principal.role, 0) < ROLE_RANK.get("operator", 99):
        await ws.close(code=4403)
        return
    await ws.accept()
    conv_id = str(ws.query_params.get("conversation_id") or "").strip()
    chat = state.services["chat"]
    conv = chat.get_conversation(conv_id, principal.id) if conv_id else None
    voice = state.services["voice"]
    async def send(event):
        await ws.send_text(json.dumps(event))
    if not conv or not voice.stt_available() or not voice.tts_available():
        await send({"type": "jarvis.unavailable", "detail": "Gespräch oder lokale Sprachdienste nicht verfügbar."})
        await ws.close(code=4404 if not conv else 1011)
        return
    pcm = bytearray()
    preroll = bytearray()
    silent_samples = 0
    speaking = False
    interrupt_samples = 0
    turn: asyncio.Task | None = None
    active_run = ""

    async def respond(text):
        response_started = time.monotonic()
        response_completed = False
        nonlocal active_run
        sub = None
        try:
            from ..chat import MessageCreate, RunSubscription, send_message
            # Invoke the actual chat entry point: same memory, teaching, audit,
            # role gates, agent selection and execution as typed messages.
            sub = RunSubscription(state)
            result = await send_message(conv_id, MessageCreate(content=text, stream=False, channel="voice"), state, principal)
            active_run = result["run"]["id"]
            message_id = result["message"]["id"]
            sub.bind(active_run, message_id)
            await send({"type": "response.created"})
            async with asyncio.timeout(240):
                while True:
                    message = chat.get_message(message_id)
                    if message and message.get("status") not in ("streaming", "pending"):
                        if message.get("status") in ("failed", "cancelled", "error"):
                            raise VoiceError("MIA konnte die Antwort nicht abschließen.")
                        break
                    try:
                        event = await asyncio.wait_for(sub.queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        continue
                    if sub.matches(event) and event.type == "run.finished":
                        message = chat.get_message(message_id)
                        break
            text_out = str((message or {}).get("content") or "").strip()
            if not text_out:
                raise VoiceError("MIA hat keine vorlesbare Antwort zurückgegeben.")
            await send({"type": "response.output_audio_transcript.delta", "delta": text_out})
            chat_ms = round((time.monotonic() - response_started) * 1000, 1)
            tts_started = time.monotonic()
            audio, mime = await voice.speak(text_out[:8000])
            print("[MIA_VOICE_LATENCY] " + json.dumps({"conversation_id": conv_id, "stage": "reply", "chat_ms": chat_ms, "tts_ms": round((time.monotonic()-tts_started)*1000,1)}), flush=True)
            await send({"type": "response.audio.file", "audio": base64.b64encode(audio).decode(), "mime": mime})
            await send({"type": "response.done"})
            response_completed = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if active_run:
                await state.runtime.cancel_run(active_run, by=principal.actor)
            state.log.warning("voice", f"Local voice turn failed: {type(exc).__name__}")
            await send({"type": "error", "error": {"message": str(exc) if isinstance(exc, VoiceError) else "Der lokale Sprachkanal konnte die Antwort nicht abschließen."}})
            await send({"type": "response.done"})
        finally:
            print("[MIA_VOICE_LATENCY] " + json.dumps({"conversation_id": conv_id, "stage": "response_end", "completed": response_completed, "elapsed_ms": round((time.monotonic()-response_started)*1000,1)}), flush=True)
            if sub:
                sub.close()
            active_run = ""

    async def audio_turn(raw):
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(raw)
            stt_started = time.monotonic()
            result = await voice.transcribe(buf.getvalue(), "audio/wav")
            print("[MIA_VOICE_LATENCY] " + json.dumps({"conversation_id": conv_id, "stage": "stt", "audio_seconds": round(len(raw)/48000,2), "stt_ms": round((time.monotonic()-stt_started)*1000,1)}), flush=True)
            text = result.get("text", "").strip()
            if text:
                await send({"type": "conversation.item.input_audio_transcription.completed", "transcript": text})
                await respond(text)
        except VoiceError as exc:
            await send({"type": "error", "error": {"message": str(exc)}})

    await send({"type": "jarvis.ready", "provider": "local", "conversation_id": conv_id})
    try:
        while True:
            event = json.loads(await ws.receive_text())
            if event.get("type") == "conversation.item.create":
                if turn and not turn.done():
                    await send({"type": "error", "error": {"message": "MIA beantwortet noch die vorherige Nachricht."}})
                    continue
                text = " ".join(c.get("text", "") for c in event.get("item", {}).get("content", []))[:16000].strip()
                if text:
                    turn = asyncio.create_task(respond(text))
            elif event.get("type") == "input_audio_buffer.append":
                raw = base64.b64decode(event.get("audio", ""), validate=True)
                if len(raw) > 65536 or len(raw) % 2:
                    continue
                samples = struct.unpack(f"<{len(raw)//2}h", raw)
                rms = math.sqrt(sum(s*s for s in samples) / max(1, len(samples)))
                if turn and not turn.done():
                    # Require sustained speech before interrupting work; reject
                    # single clicks/noise while retaining the beginning of speech.
                    interrupt_samples = interrupt_samples + len(samples) if rms > 450 else 0
                    preroll.extend(raw)
                    del preroll[:-24000]
                    if interrupt_samples < 7200:
                        continue
                    run_to_cancel = active_run
                    turn.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await turn
                    if run_to_cancel:
                        await state.runtime.cancel_run(run_to_cancel, by=principal.actor)
                    turn = None
                    interrupt_samples = 0
                    # raw is already in preroll; avoid duplicating this frame.
                    raw = b""
                if rms > 450:
                    if not speaking:
                        pcm.extend(preroll)
                        speaking = True
                        await send({"type": "input_audio_buffer.speech_started"})
                    silent_samples = 0
                elif speaking:
                    silent_samples += len(samples)
                if speaking:
                    pcm.extend(raw)
                    if silent_samples > 60000 or len(pcm) > 24000 * 2 * 30:
                        # Keep the conversational pause for endpointing, but don't send
                        # its full 2.5 seconds to speech recognition. Retain 200 ms.
                        trim_bytes = max(0, silent_samples - 4800) * 2
                        utterance = bytes(pcm[:-trim_bytes]) if trim_bytes else bytes(pcm)
                        turn = asyncio.create_task(audio_turn(utterance))
                        pcm.clear()
                        preroll.clear()
                        speaking = False
                        silent_samples = 0
                else:
                    preroll.extend(raw)
                    del preroll[:-14400]
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        run_to_cancel = active_run
        if turn and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn
        if run_to_cancel:
            await state.runtime.cancel_run(run_to_cancel, by=principal.actor)

MODULE = ModuleSpec(id="live", title="Live", router=router, nav=False, order=3)
