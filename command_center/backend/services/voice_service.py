"""
Voice — speech to text and text to speech, both optional and both real.

There is no built-in model here. The service talks to an OpenAI-compatible
audio endpoint, which is what nearly every self-hostable option already
exposes: `faster-whisper-server`, `whisper.cpp`'s server, Speaches, LocalAI,
Kokoro-FastAPI and Piper wrappers, as well as OpenAI itself.

  JARVIS_CC_STT_URL   e.g. http://whisper:8000        → POST /v1/audio/transcriptions
  JARVIS_CC_TTS_URL   e.g. http://kokoro:8880         → POST /v1/audio/speech

Für die Stimme kommt ElevenLabs hinzu und geht vor, wenn ein Schlüssel
hinterlegt ist:

  ELEVENLABS_API_KEY    der Schlüssel
  ELEVENLABS_VOICE_ID   welche Stimme (aus der Stimmenbibliothek)
  ELEVENLABS_MODEL_ID   voreingestellt eleven_multilingual_v2

Warum ElevenLabs bevorzugt wird und nicht als weitere Option danebensteht:
Zwei Stimmen, die je nach Konfiguration wechseln, sind keine Stimme. Wer
einen Schlüssel einträgt, will diese Stimme hören.

Zurückgegeben wird nach Möglichkeit WAV: ElevenLabs liefert rohes PCM, und
daraus hier einen WAV-Kopf zu bauen kostet nichts und erspart dem Empfänger
einen MP3-Dekodierer, den er vielleicht nicht hat.

Nothing is faked: with neither variable set, `capabilities()` reports the
feature as unavailable *and says which variable is missing*, and the
microphone control in the browser stays disabled with that reason on it.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import wave

import httpx

_log = logging.getLogger("jarvis.cc")

# Gemini-TTS erzeugt die ganze Datei, bevor sie zurückkommt (~0,5 s je Sekunde Sprache).
# Lange Texte lesen deshalb Piper vor, damit niemand lange auf den ersten Ton wartet.
GEMINI_TTS_MAX_CHARS = 1500
GEMINI_TTS_TIMEOUT = 25.0
GEMINI_TTS_RATE = 24000
# Gratis-Gemini erlaubt nur 10 Stimm-Anfragen am Tag. Nach einem Ausfall wird es eine
# Weile übersprungen, statt bei jedem Satz erneut eine halbe Sekunde auf das 429 zu warten.
GEMINI_TTS_PAUSE_S = 600.0

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_TIMEOUT = 30.0
DEFAULT_OPENAI_TTS_INSTRUCTIONS = ("Sprich natürliches Hochdeutsch, warm, freundlich und klug, "
                                   "in ruhigem Gesprächstempo.")

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"
# 24 kHz mono: klein genug für die Leitung, gut genug für eine Stimme.
ELEVEN_PCM = "pcm_24000"
ELEVEN_PCM_RATE = 24000

MAX_AUDIO_BYTES = 25 * 1024 * 1024
ALLOWED_AUDIO = {
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav",
    "audio/wave", "audio/flac", "audio/m4a", "audio/mp3", "video/webm",
}
_EXT = {"audio/webm": "webm", "video/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3",
        "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/wav": "wav",
        "audio/x-wav": "wav", "audio/wave": "wav", "audio/flac": "flac"}


DEFAULT_STT_PROMPT = ("Hey Mia, Master, Reyes Service, Lexware, Plancraft, Umsatzsteuer-Voranmeldung, Beleg, Baustelle, "
                      "Google Kalender, Kalenderbrücke, Herzschlag, Dashboard, Command Center, Ollama, n8n, Gedächtnis, "
                      "Verhaltensgedächtnis, Neuberg, Karben.")
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

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


_SPOKEN = [(r"\bz\. ?B\.", "zum Beispiel"), (r"\bca\.", "circa"), (r"\bbzw\.", "beziehungsweise"), (r"\busw\.", "und so weiter"),
           (r"\bd\. ?h\.", "das heißt"), (r"\bggf\.", "gegebenenfalls"), (r"\bevtl\.", "eventuell"), (r"\bInkl\.", "inklusive"),
           (r"m²|\bqm\b", " Quadratmeter"), (r"(\d)\s?%", r"\1 Prozent"), (r"(\d)\s?(€|EUR)\b", r"\1 Euro"), (r"€", " Euro"),
           (r"&", " und "), (r"(\d{1,2}):00\b(?: Uhr)?", r"\1 Uhr"), (r"(\d{1,2}):(\d{2})\b(?: Uhr)?", r"\1 Uhr \2"),
           (r"(\d),(\d{2})\s?Euro", r"\1 Euro \2"), (r"\s{2,}", " ")]


def _speakable(text: str) -> str:
    """Was man schreibt, ist nicht, was man sagt: Abkürzungen, Euro-Zeichen, Uhrzeiten und Markdown aussprechbar machen."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # Links: nur der Text
    text = re.sub(r"```.*?```", " ", text, flags=re.S)          # Codeblöcke werden nicht vorgelesen
    text = re.sub(r"[*_`#>|]+", "", text)                       # Markdown-Zeichen
    for pat, rep in _SPOKEN:
        text = re.sub(pat, rep, text)
    return text.strip()


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
        # Most OpenAI-compatible endpoints accept mp3. Some (e.g. Google's TTS
        # models via OpenRouter) only speak raw pcm — set this to "pcm" for
        # those and the response is wrapped into a WAV header below.
        self.tts_format = _env("JARVIS_CC_TTS_FORMAT", "mp3")
        self.tts_speed = _env("JARVIS_CC_TTS_SPEED")
        self.language = _env("JARVIS_CC_STT_LANGUAGE")
        # JARVIS_CC_STT_PROVIDER=openai: Erkennung über OpenAI (~0,5–1 s statt 3–4 s lokal), lokal bleibt Ersatz.
        self.openai_stt_on = bool(_env("OPENAI_API_KEY")) and _env("JARVIS_CC_STT_PROVIDER", "local").lower() == "openai"
        self.openai_stt_model = _env("JARVIS_CC_OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        self.eleven_key = _env("ELEVENLABS_API_KEY")
        self.eleven_voice = _env("ELEVENLABS_VOICE_ID")
        self.eleven_model = _env("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        # Gemini-TTS ist nur der Mund: es liest den Text vor, den der Chat liefert. Das
        # Gehirn bleibt der Chat. Ausfall oder JARVIS_CC_TTS_PROVIDER=local → Piper.
        self.gemini_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        self.gemini_tts_model = _env("JARVIS_CC_GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
        self.gemini_tts_voice = _env("JARVIS_CC_GEMINI_TTS_VOICE") or _env("JARVIS_CC_REALTIME_VOICE") or "Aoede"
        provider = _env("JARVIS_CC_TTS_PROVIDER", "local").lower()
        # OpenAI ist bezahlt und ohne Tageslimit; Gemini bleibt dahinter als Ersatz, dann Piper.
        self.openai_tts_key = _env("OPENAI_API_KEY")
        self.openai_tts_model = _env("JARVIS_CC_OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.openai_tts_voice = _env("JARVIS_CC_OPENAI_TTS_VOICE", "marin")
        self.openai_tts_instructions = _env("JARVIS_CC_OPENAI_TTS_INSTRUCTIONS", DEFAULT_OPENAI_TTS_INSTRUCTIONS)
        self.openai_tts_on = bool(self.openai_tts_key) and provider == "openai"
        self.gemini_tts_on = bool(self.gemini_key) and provider in ("gemini", "openai")
        self._gemini_client = None
        self._gemini_pause_until = 0.0

    # ── capabilities ─────────────────────────────────────────────────────
    def stt_available(self) -> bool:
        return bool(self.stt_url or self.openai_stt_on)

    def eleven_available(self) -> bool:
        """Ein Schlüssel ohne Stimme nützt nichts — beides oder keins."""
        return bool(self.eleven_key and self.eleven_voice)

    def tts_available(self) -> bool:
        return bool(self.eleven_available() or self.openai_tts_on or self.gemini_tts_on or self.tts_url)

    def tts_provider(self) -> str:
        if self.eleven_available():
            return "elevenlabs"
        if self.openai_tts_on:
            return "openai"
        if self.gemini_tts_on:
            return "gemini"
        return "openai-compatible" if self.tts_url else ""

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
            "text_to_speech": self._tts_capabilities(),
            "note": ("The desktop app keeps its own live voice session; this is the browser's path, "
                     "and it only works against a backend you run."),
        }

    def _tts_capabilities(self) -> dict:
        """Welche Stimme wirklich spricht — und wenn keine, welche Variable fehlt."""
        if self.eleven_available():
            return {"available": True, "configured": True, "provider": "elevenlabs",
                    "model": self.eleven_model, "voice": self.eleven_voice,
                    "detail": f"Stimme über ElevenLabs ({self.eleven_model})"}
        if self.openai_tts_on:
            return {"available": True, "configured": True, "provider": "openai",
                    "model": self.openai_tts_model, "voice": self.openai_tts_voice,
                    "detail": f"Stimme über OpenAI ({self.openai_tts_model}/{self.openai_tts_voice})"
                              + (", bei Ausfall Gemini" if self.gemini_tts_on else "")
                              + (", dann Piper" if self.tts_url else "")}
        if self.gemini_tts_on:
            return {"available": True, "configured": True, "provider": "gemini",
                    "model": self.gemini_tts_model, "voice": self.gemini_tts_voice,
                    "detail": f"Stimme über Gemini ({self.gemini_tts_model}/{self.gemini_tts_voice})"
                              + (", bei Ausfall Piper" if self.tts_url else "")}
        if self.eleven_key and not self.eleven_voice:
            return {"available": bool(self.tts_url), "configured": bool(self.tts_url),
                    "provider": "openai-compatible" if self.tts_url else "",
                    "model": self.tts_model, "voice": self.tts_voice,
                    "detail": "ELEVENLABS_API_KEY ist gesetzt, aber ELEVENLABS_VOICE_ID fehlt — "
                              "ohne Stimmen-Kennung weiß ElevenLabs nicht, wer sprechen soll."}
        if self.tts_url:
            return {"available": True, "configured": True, "provider": "openai-compatible",
                    "model": self.tts_model, "voice": self.tts_voice,
                    "detail": f"speech via {self.tts_url} ({self.tts_model}/{self.tts_voice})"}
        return {"available": False, "configured": False, "provider": "", "model": "", "voice": "",
                "detail": "keine Stimme eingerichtet — ELEVENLABS_API_KEY und ELEVENLABS_VOICE_ID "
                          "setzen, oder JARVIS_CC_TTS_URL für einen eigenen Dienst"}

    @staticmethod
    def _wav(pcm: bytes, rate: int = ELEVEN_PCM_RATE) -> bytes:
        """Rohes PCM in eine WAV-Datei fassen.

        Der Empfänger ist am Ende ein Windows-PC. WAV spielt dort die
        Standardbibliothek; für MP3 bräuchte er einen Dekodierer, den er
        vielleicht nicht hat. Der Kopf kostet 44 Byte.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()

    async def _speak_eleven(self, text: str, voice: str = "") -> tuple[bytes, str]:
        voice_id = voice or self.eleven_voice
        headers = {"xi-api-key": self.eleven_key, "accept": "audio/*"}
        body = {"text": text, "model_id": self.eleven_model}
        url = f"{ELEVEN_URL}/{voice_id}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(url, json=body, headers=headers,
                                 params={"output_format": ELEVEN_PCM})
                # Rohes PCM ist je nach Tarif gesperrt. Dann eben MP3 — das
                # kann jeder Tarif, und der Empfänger bekommt gesagt, was es ist.
                if r.status_code in (401, 403) and b"output_format" in r.content.lower():
                    r = await c.post(url, json=body, headers=headers,
                                     params={"output_format": "mp3_44100_128"})
                    if r.status_code < 400:
                        return r.content, "audio/mpeg"
        except httpx.HTTPError as e:
            raise VoiceError(f"ElevenLabs ist nicht erreichbar ({e.__class__.__name__}).")
        if r.status_code == 401:
            raise VoiceError("ElevenLabs weist den Schlüssel ab (401). ELEVENLABS_API_KEY prüfen.")
        if r.status_code == 404:
            raise VoiceError(f"ElevenLabs kennt die Stimme '{voice_id}' nicht (404). "
                             "ELEVENLABS_VOICE_ID prüfen.")
        if r.status_code == 429:
            raise VoiceError("ElevenLabs: Kontingent aufgebraucht oder zu viele Anfragen (429).")
        if r.status_code >= 400:
            raise VoiceError(f"ElevenLabs antwortete mit HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            raise VoiceError("ElevenLabs lieferte kein Audio zurück.")
        ctype = r.headers.get("content-type", "")
        if "mpeg" in ctype or "mp3" in ctype:
            return r.content, "audio/mpeg"
        return self._wav(r.content), "audio/wav"

    # ── speech to text ───────────────────────────────────────────────────
    async def transcribe(self, audio: bytes, content_type: str = "audio/webm",
                         language: str = "") -> dict:
        if not self.stt_available():
            raise VoiceError("Auf diesem Server ist keine Spracherkennung eingerichtet "
                             "(JARVIS_CC_STT_URL setzen).")
        if not audio:
            raise VoiceError("Die Aufnahme war leer.")
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
        hint = os.environ.get("JARVIS_CC_STT_PROMPT")
        hint = DEFAULT_STT_PROMPT if hint is None else hint.strip()
        if self.openai_stt_on:
            try:
                return await self._transcribe_openai(files, lang, hint)
            except Exception as e:  # noqa: BLE001 - jeder Ausfall führt zur lokalen Erkennung
                if isinstance(e, VoiceError) and "nichts verstanden" in str(e):
                    raise
                _log.warning("OpenAI-Erkennung fehlgeschlagen, lokal weiter: %s", e)
                if not self.stt_url:
                    raise VoiceError("Die Spracherkennung ist gerade nicht erreichbar.")
        # Ein Wortvorrat als Hinweis: Das kleine Erkennungsmodell versteht „Jarvis“ sonst als „ja, Wiss“ und „Lexware“ als
        # „Lex wer“. Gemessen: mit Hinweis kamen Jarvis, Voranmeldung, Lexware und Neuberg richtig an. Abschalten oder ändern:
        # JARVIS_CC_STT_PROMPT (leer = aus).
        if hint:
            data["prompt"] = hint
        headers = {"Authorization": f"Bearer {self.stt_key}"} if self.stt_key else {}
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(_endpoint(self.stt_url, "/audio/transcriptions"),
                                 files=files, data=data, headers=headers)
        except httpx.HTTPError as e:
            raise VoiceError(f"The transcription service could not be reached ({e.__class__.__name__}).")
        if r.status_code == 401:
            raise VoiceError("Die Spracherkennung hat den API-Schlüssel abgelehnt.")
        if r.status_code >= 400:
            raise VoiceError(f"The transcription service answered HTTP {r.status_code}: {r.text[:200]}")
        try:
            payload = r.json()
        except ValueError:
            payload = {"text": r.text}
        text = (payload.get("text") or "").strip()
        if not text:
            raise VoiceError("Ich habe in der Aufnahme nichts verstanden. Bitte noch einmal etwas länger sprechen.")
        return {"text": text, "language": payload.get("language", lang), "model": self.stt_model}

    async def _transcribe_openai(self, files: dict, lang: str, hint: str) -> dict:
        data = {"model": self.openai_stt_model, "response_format": "json"}
        if lang:
            data["language"] = lang
        if hint:
            data["prompt"] = hint
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(OPENAI_STT_URL, files=files, data=data,
                             headers={"Authorization": f"Bearer {_env('OPENAI_API_KEY')}"})
        if r.status_code >= 400:
            raise VoiceError(f"OpenAI-Erkennung antwortete mit HTTP {r.status_code}: {r.text[:200]}")
        text = (r.json().get("text") or "").strip()
        if not text:
            raise VoiceError("Ich habe in der Aufnahme nichts verstanden. Bitte noch einmal etwas länger sprechen.")
        return {"text": text, "language": lang, "model": self.openai_stt_model}

    # ── text to speech ───────────────────────────────────────────────────
    async def _speak_gemini(self, text: str) -> tuple[bytes, str]:
        """Liest `text` mit Gemini-TTS vor. Nur die Stimme, kein eigenes Denken."""
        from google import genai
        from google.genai import types
        if self._gemini_client is None:
            self._gemini_client = genai.Client(api_key=self.gemini_key)
        # Markdown-Zeichen würden mitgesprochen oder verschluckt.
        spoken = re.sub(r"[*`]+|^#+\s*", "", text, flags=re.M).strip()
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.gemini_tts_voice))))
        r = await asyncio.wait_for(
            self._gemini_client.aio.models.generate_content(model=self.gemini_tts_model, contents=spoken, config=cfg),
            timeout=GEMINI_TTS_TIMEOUT)
        part = r.candidates[0].content.parts[0].inline_data
        data = part.data if part else b""
        if not data:
            raise VoiceError("Gemini hat kein Audio geliefert.")
        # Gemini liefert eine fertige WAV; rohes PCM wird nur zur Sicherheit eingepackt.
        if data[:4] == b"RIFF" or "wav" in (part.mime_type or "").lower():
            return data, "audio/wav"
        return self._wav(data, rate=GEMINI_TTS_RATE), "audio/wav"

    async def _speak_openai(self, text: str) -> tuple[bytes, str]:
        """Liest `text` mit OpenAI-TTS vor. Nur die Stimme, kein eigenes Denken."""
        spoken = re.sub(r"[*`]+|^#+\s*", "", text, flags=re.M).strip()
        body = {"model": self.openai_tts_model, "voice": self.openai_tts_voice, "input": spoken,
                "response_format": "mp3"}
        if self.openai_tts_instructions:
            body["instructions"] = self.openai_tts_instructions
        async with httpx.AsyncClient(timeout=OPENAI_TTS_TIMEOUT) as c:
            r = await c.post(OPENAI_TTS_URL, json=body,
                             headers={"Authorization": f"Bearer {self.openai_tts_key}"})
        if r.status_code >= 400:
            raise VoiceError(f"OpenAI-Stimme antwortete mit HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            raise VoiceError("OpenAI hat kein Audio geliefert.")
        return r.content, "audio/mpeg"

    async def speak(self, text: str, voice: str = "") -> tuple[bytes, str]:
        if not self.tts_available():
            raise VoiceError(self._tts_capabilities()["detail"])
        text = (text or "").strip()
        if not text:
            raise VoiceError("Es gibt nichts vorzulesen.")
        if len(text) > 8000:
            text = text[:8000]
        text = _speakable(text) or text
        # ElevenLabs geht vor, wenn es eingerichtet ist: eine Stimme, nicht zwei.
        if self.eleven_available():
            return await self._speak_eleven(text, voice)
        if self.openai_tts_on:
            try:
                return await self._speak_openai(text)
            except Exception as e:  # noqa: BLE001 - jeder Ausfall führt zum Ersatz, nie zu Stille
                _log.warning("OpenAI-TTS fehlgeschlagen, Ersatzstimme springt ein: %s", e)
        loop_now = asyncio.get_running_loop().time()
        if self.gemini_tts_on and len(text) <= GEMINI_TTS_MAX_CHARS and loop_now >= self._gemini_pause_until:
            try:
                return await self._speak_gemini(text)
            except Exception as e:  # noqa: BLE001 - jeder Ausfall führt zu Piper, nie zu Stille
                self._gemini_pause_until = loop_now + GEMINI_TTS_PAUSE_S
                _log.warning("Gemini-TTS fehlgeschlagen, Piper springt ein (Pause %d s): %s",
                             GEMINI_TTS_PAUSE_S, e.__class__.__name__)
                if not self.tts_url:
                    raise VoiceError("Die Gemini-Stimme ist nicht erreichbar und es ist kein lokaler Rückfall eingerichtet.")
        if not self.tts_url:
            raise VoiceError("Dieser Text ist für die Gemini-Stimme zu lang und es ist kein lokaler Rückfall eingerichtet.")
        headers = {"Authorization": f"Bearer {self.tts_key}"} if self.tts_key else {}
        body = {"model": self.tts_model, "voice": voice or self.tts_voice, "input": text,
                "response_format": self.tts_format}
        if self.tts_speed:
            body["speed"] = float(self.tts_speed)
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(_endpoint(self.tts_url, "/audio/speech"), json=body, headers=headers)
        except httpx.HTTPError as e:
            raise VoiceError(f"The speech service could not be reached ({e.__class__.__name__}).")
        if r.status_code == 401:
            raise VoiceError("Die Sprachausgabe hat den API-Schlüssel abgelehnt.")
        if r.status_code >= 400:
            raise VoiceError(f"The speech service answered HTTP {r.status_code}: {r.text[:200]}")
        audio = r.content
        if not audio:
            raise VoiceError("Die Sprachausgabe hat kein Audio geliefert.")
        if self.tts_format == "pcm":
            return self._wav(audio, rate=24000), "audio/wav"
        return audio, r.headers.get("content-type", "audio/mpeg")

    # ── health ───────────────────────────────────────────────────────────
    async def health(self) -> dict:
        if not (self.stt_available() or self.tts_available()):
            return {"status": "not_configured",
                    "detail": "weder ElevenLabs noch JARVIS_CC_STT_URL / JARVIS_CC_TTS_URL gesetzt"}
        if self.eleven_available() and not self.stt_url:
            # Ein Schlüsselcheck ohne Sprachausgabe: die günstigste Frage, die
            # ElevenLabs beantwortet, und sie kostet kein Kontingent.
            try:
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get("https://api.elevenlabs.io/v1/user/subscription",
                                    headers={"xi-api-key": self.eleven_key})
                if r.status_code == 401:
                    return {"status": "offline", "detail": "ElevenLabs weist den Schlüssel ab (401)"}
                if r.status_code >= 400:
                    return {"status": "degraded", "detail": f"ElevenLabs antwortet mit {r.status_code}"}
                return {"status": "healthy", "detail": f"ElevenLabs, Stimme {self.eleven_voice}"}
            except httpx.HTTPError as e:
                return {"status": "offline", "detail": f"ElevenLabs nicht erreichbar ({e.__class__.__name__})"}
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
