"""
Voice — speech to text and text to speech, both optional and both real.

There is no built-in model here. The service talks to an OpenAI-compatible
audio endpoint, which is what nearly every self-hostable option already
exposes: `faster-whisper-server`, `whisper.cpp`'s server, Speaches, LocalAI,
Kokoro-FastAPI and Piper wrappers, as well as OpenAI itself.

  JARVIS_CC_STT_URL   e.g. http://whisper:8000        → POST /v1/audio/transcriptions
  JARVIS_CC_TTS_URL   e.g. http://kokoro:8880         → POST /v1/audio/speech

Nothing is faked: with neither variable set, `capabilities()` reports the
feature as unavailable *and says which variable is missing*, and the
microphone control in the browser stays disabled with that reason on it.
"""
from __future__ import annotations

import os

import httpx

MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO = {
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav",
    "audio/wave", "audio/flac", "audio/m4a", "audio/mp3", "video/webm",
}
_EXT = {"audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3",
        "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/wav": "wav",
        "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac"}


class VoiceError(Exception):
    """Something the user needs to hear, phrased plainly."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _endpoint(base: str, path: str) -> str:
    """Accept both a server root and a full endpoint URL."""
    base = base.rstrip("/")
    if base.endswith(path) or "/audio/" in base:
        return base
    if base.endswith("/v1"):
        return f"{base}{path}"
    return f"{base}/v1{path}"


class VoiceService:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.stt_url = _env("JARVIS_CC_STT_URL")
        self.tts_url = _env("JARVIS_CC_TTS_URL")
        self.stt_key = _env("JARVIS_CC_STT_API_KEY") or _env("OPENAI_API_KEY")
        self.tts_key = _env("JARVIS_CC_TTS_API_KEY") or _env("OPENAI_API_KEY")
        self.stt_model = _env("JARVIS_CC_STT_MODEL", "whisper-1")
        self.tts_model = _env("JARVIS_CC_TTS_MODEL", "tts-1")
        self.tts_voice = _env("JARVIS_CC_TTS_VOICE", "alloy")
        self.language = _env("JARVIS_CC_STT_LANGUAGE")

    # ── capabilities ─────────────────────────────────────────────────────
    def stt_available(self) -> bool:
        return bool(self.stt_url)

    def tts_available(self) -> bool:
        return bool(self.tts_url)

    def capabilities(self) -> dict:
        return {
            "speech_to_text": {
                "available": self.stt_available(),
                "configured": self.stt_available(),
                "model": self.stt_model if self.stt_available() else "",
                "detail": (f"transcription via {self.stt_url} ({self.stt_model})" if self.stt_available()
                           else "no speech-to-text backend configured (set JARVIS_CC_STT_URL to an "
                                "OpenAI-compatible endpoint, e.g. a faster-whisper-server)"),
                "accepts": sorted(ALLOWED_AUDIO),
                "max_bytes": MAX_AUDIO_BYTES,
            },
            "text_to_speech": {
                "available": self.tts_available(),
                "configured": self.tts_available(),
                "model": self.tts_model if self.tts_available() else "",
                "voice": self.tts_voice if self.tts_available() else "",
                "detail": (f"speech via {self.tts_url} ({self.tts_model}/{self.tts_voice})" if self.tts_available()
                           else "no text-to-speech backend configured (set JARVIS_CC_TTS_URL)"),
            },
            "note": ("The desktop app keeps its own live voice session; this is the browser's path, "
                     "and it only works against a backend you run."),
        }

    # ── speech to text ───────────────────────────────────────────────────
    async def transcribe(self, audio: bytes, content_type: str = "audio/webm",
                         language: str = "") -> dict:
        if not self.stt_available():
            raise VoiceError("No speech-to-text backend is configured on this server "
                             "(set JARVIS_CC_STT_URL).")
        if not audio:
            raise VoiceError("The recording was empty.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise VoiceError(f"The recording is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
        base_type = (content_type or "").split(";")[0].strip().lower()
        if base_type not in ALLOWED_AUDIO:
            raise VoiceError(f"'{base_type or 'unknown'}' is not an audio format this server accepts.")
        files = {"file": (f"speech.{_EXT.get(base_type, 'webm')}", audio, base_type)}
        data = {"model": self.stt_model, "response_format": "json"}
        lang = language or self.language
        if lang:
            data["language"] = lang
        headers = {"Authorization": f"Bearer {self.stt_key}"} if self.stt_key else {}
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(_endpoint(self.stt_url, "/audio/transcriptions"),
                                 files=files, data=data, headers=headers)
        except httpx.HTTPError as e:
            raise VoiceError(f"The transcription service could not be reached ({e.__class__.__name__}).")
        if r.status_code == 401:
            raise VoiceError("The transcription service rejected the API key.")
        if r.status_code >= 400:
            raise VoiceError(f"The transcription service answered HTTP {r.status_code}: {r.text[:200]}")
        try:
            payload = r.json()
        except ValueError:
            payload = {"text": r.text}
        text = (payload.get("text") or "").strip()
        if not text:
            raise VoiceError("Nothing recognisable was in that recording.")
        return {"text": text, "language": payload.get("language", lang), "model": self.stt_model}

    # ── text to speech ───────────────────────────────────────────────────
    async def speak(self, text: str, voice: str = "") -> tuple[bytes, str]:
        if not self.tts_available():
            raise VoiceError("No text-to-speech backend is configured on this server "
                             "(set JARVIS_CC_TTS_URL).")
        text = (text or "").strip()
        if not text:
            raise VoiceError("There is nothing to say.")
        if len(text) > 8000:
            text = text[:8000]
        headers = {"Authorization": f"Bearer {self.tts_key}"} if self.tts_key else {}
        body = {"model": self.tts_model, "voice": voice or self.tts_voice, "input": text,
                "response_format": "mp3"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(_endpoint(self.tts_url, "/audio/speech"), json=body, headers=headers)
        except httpx.HTTPError as e:
            raise VoiceError(f"The speech service could not be reached ({e.__class__.__name__}).")
        if r.status_code == 401:
            raise VoiceError("The speech service rejected the API key.")
        if r.status_code >= 400:
            raise VoiceError(f"The speech service answered HTTP {r.status_code}: {r.text[:200]}")
        audio = r.content
        if not audio:
            raise VoiceError("The speech service returned no audio.")
        return audio, r.headers.get("content-type", "audio/mpeg")

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> dict:
        if not (self.stt_available() or self.tts_available()):
            return {"status": "not_configured",
                    "detail": "JARVIS_CC_STT_URL / JARVIS_CC_TTS_URL not set"}
        parts, worst = [], "healthy"
        for label, url, key in (("speech-to-text", self.stt_url, self.stt_key),
                                ("text-to-speech", self.tts_url, self.tts_key)):
            if not url:
                continue
            probe = url.rstrip("/")
            probe = probe if probe.endswith("/v1") else f"{probe}/v1"
            try:
                async with httpx.AsyncClient(timeout=8.0) as c:
                    r = await c.get(f"{probe}/models",
                                    headers={"Authorization": f"Bearer {key}"} if key else {})
                if r.status_code < 400:
                    parts.append(f"{label} reachable")
                else:
                    parts.append(f"{label} answered HTTP {r.status_code}")
                    worst = "degraded"
            except httpx.HTTPError:
                parts.append(f"{label} unreachable")
                worst = "offline"
        return {"status": worst, "detail": "; ".join(parts) or "configured"}
