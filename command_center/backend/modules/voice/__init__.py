"""
Voice — transcription and speech, only as real as the configured backend.

`GET /api/voice/capabilities` is the honest answer the browser asks for before
enabling the microphone: with no `JARVIS_CC_STT_URL` the control stays disabled
and shows the reason rather than pretending to listen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.voice_service import MAX_AUDIO_BYTES, VoiceError
from .. import ModuleSpec

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    voice: str = ""


@router.get("/capabilities")
async def capabilities(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return state.services["voice"].capabilities()


@router.post("/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...), language: str = Form(""),
                     state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    voice = state.services["voice"]
    if not voice.stt_available():
        raise HTTPException(status_code=503, detail=voice.capabilities()["speech_to_text"]["detail"])
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"Recording larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    try:
        result = await voice.transcribe(b"".join(chunks), file.content_type or "audio/webm", language)
    except VoiceError as e:
        state.log.warning("voice", f"Transcription failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    state.log.audit(actor_type=principal.kind, actor_id=principal.actor, action="voice.transcribe",
                    status="ok", target=f"{size} bytes", result=result["text"][:200])
    return result


@router.post("/speak")
async def speak(body: SpeakBody, state: AppState = Depends(get_state),
                principal: Principal = Depends(require_role("operator"))):
    voice = state.services["voice"]
    if not voice.tts_available():
        raise HTTPException(status_code=503, detail=voice.capabilities()["text_to_speech"]["detail"])
    try:
        audio, media_type = await voice.speak(body.text, body.voice)
    except VoiceError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=audio, media_type=media_type,
                    headers={"Cache-Control": "no-store", "Content-Disposition": 'inline; filename="speech.mp3"'})


MODULE = ModuleSpec(id="voice", title="Voice", router=router, nav=False, order=210)
