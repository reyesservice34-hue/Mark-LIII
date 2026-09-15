"""
Speech that leaves the machine — one voice, decided in one place.

core/tts.py speaks *to the room*: it synthesises and plays. This module writes a
file instead, because a voice note has to be handed to something else (WhatsApp,
an upload, an archive) rather than pushed at a speaker.

The voice question is the whole point. The live session speaks with a Gemini
prebuilt voice (Charon, Puck, Kore, Fenrir, Aoede) chosen in the settings, so a
voice note produced by a *different* engine would arrive sounding like a
stranger. Gemini's TTS accepts those same voice names, so the primary path asks
it for exactly the voice ``get_voice()`` returns — the same single source the
Live session reads. There is no second setting to drift out of sync.

Both paths are free: Gemini's TTS runs on the key already in api_keys.json, and
the EdgeTTS fallback needs no key at all. The fallback cannot reproduce a Gemini
voice — nothing outside Google can — so it is pinned to one configured voice and
says so, which is the honest version of "always the same voice": the same one
every time, and a named second-best when the first is unreachable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional

from memory.config_manager import get_voice, load_api_keys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR   = get_base_dir()
OUT_DIR    = BASE_DIR / "memory" / "voice_notes"

# Gemini TTS returns raw little-endian 16-bit PCM at 24 kHz, mono.
PCM_RATE   = 24000
PCM_WIDTH  = 2
PCM_CHANNELS = 1

DEFAULT_TTS_MODEL     = "gemini-2.5-flash-preview-tts"
DEFAULT_FALLBACK_VOICE = "de-DE-ConradNeural"   # free, no key, stable across runs
MAX_CHARS = 1500     # a voice note nobody listens to is a voice note wasted


class SpeechError(Exception):
    """Synthesis failed on every available path."""


def _setting(key: str, default: str) -> str:
    try:
        return (load_api_keys().get(key) or "").strip() or default
    except Exception:
        return default


def _pcm_to_wav(pcm: bytes, path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(PCM_CHANNELS)
        w.setsampwidth(PCM_WIDTH)
        w.setframerate(PCM_RATE)
        w.writeframes(pcm)


def _gemini_tts(text: str, path: Path) -> Path:
    """The live session's own voice. Raises if the key, the library or the model
    is unavailable — the caller falls back rather than failing the whole task."""
    key = (load_api_keys().get("gemini_api_key") or "").strip()
    if not key:
        raise SpeechError("no Gemini API key configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=_setting("tts_model", DEFAULT_TTS_MODEL),
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=get_voice()      # the one source, shared with Live
                    )
                )
            ),
        ),
    )

    pcm = None
    for cand in (getattr(resp, "candidates", None) or []):
        for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                pcm = data
                break
        if pcm:
            break
    if not pcm:
        raise SpeechError("Gemini returned no audio")

    wav_path = path.with_suffix(".wav")
    _pcm_to_wav(pcm, wav_path)
    return wav_path


def _edge_tts(text: str, path: Path) -> Path:
    """Free fallback, no key. A different voice from the live session — stated
    plainly rather than hidden, and pinned so it is at least always this one."""
    import asyncio
    import edge_tts

    voice = _setting("tts_fallback_voice", DEFAULT_FALLBACK_VOICE)

    async def _synth() -> bytes:
        comm = edge_tts.Communicate(text, voice)
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    loop = asyncio.new_event_loop()
    try:
        audio = loop.run_until_complete(_synth())
    finally:
        loop.close()

    if not audio:
        raise SpeechError("EdgeTTS returned no audio")
    mp3_path = path.with_suffix(".mp3")
    mp3_path.write_bytes(audio)
    return mp3_path


def _to_opus(src: Path) -> Path:
    """WhatsApp shows an OGG/Opus file as a voice bubble rather than a file card.
    ffmpeg is not a dependency of this project, so its absence is normal: without
    it the original is sent and still plays, just with a different-looking
    attachment. Never let a cosmetic step fail the send."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return src
    dst = src.with_suffix(".ogg")
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-c:a", "libopus", "-b:a", "32k",
             "-ar", "48000", "-ac", "1", str(dst)],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    return src


def describe_voice() -> str:
    """What the next voice note will sound like, in one line."""
    key = (load_api_keys().get("gemini_api_key") or "").strip()
    if key:
        return f"{get_voice()} (same voice as the live session)"
    return f"{_setting('tts_fallback_voice', DEFAULT_FALLBACK_VOICE)} (fallback — no API key configured)"


def synthesize(text: str, out_dir: Optional[Path] = None,
               name: str = "note", as_opus: bool = True) -> Path:
    """
    Text to an audio file on disk. Returns its path.

    Tries the live session's own voice first and the free fallback second; only
    when BOTH fail does it raise, because a voice note that silently does not
    exist is worse than an error the user can read.
    """
    text = (text or "").strip()
    if not text:
        raise SpeechError("nothing to say")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] + "…"

    target_dir = Path(out_dir) if out_dir else OUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = target_dir / name

    errors = []
    for attempt in (_gemini_tts, _edge_tts):
        try:
            produced = attempt(text, stem)
            return _to_opus(produced) if as_opus else produced
        except Exception as e:
            errors.append(f"{attempt.__name__.strip('_')}: {e}")

    raise SpeechError("could not produce audio — " + "; ".join(errors))
