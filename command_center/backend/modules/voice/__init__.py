"""
Voice — capability discovery only.

The frontend has a microphone/voice component that asks this endpoint what
the server can do. Nothing is faked: until a speech-to-text / text-to-speech
backend is wired in (env `JARVIS_CC_STT_URL`, `JARVIS_CC_TTS_URL`), the
component shows "voice not configured" and stays disabled.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import current_principal
from .. import ModuleSpec

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/capabilities")
async def capabilities(_: Principal = Depends(current_principal)):
    stt = bool(os.environ.get("JARVIS_CC_STT_URL"))
    tts = bool(os.environ.get("JARVIS_CC_TTS_URL"))
    return {
        "speech_to_text": {"available": False, "configured": stt,
                           "detail": "STT endpoint configured but adapter not implemented" if stt
                           else "no speech-to-text backend configured (JARVIS_CC_STT_URL)"},
        "text_to_speech": {"available": False, "configured": tts,
                           "detail": "TTS endpoint configured but adapter not implemented" if tts
                           else "no text-to-speech backend configured (JARVIS_CC_TTS_URL)"},
        "browser_fallback": {"detail": "The desktop app (main.py) keeps handling live voice; the web voice "
                                       "abstraction activates once a server-side backend exists."},
    }


MODULE = ModuleSpec(id="voice", title="Voice", router=router, nav=False, order=210)
