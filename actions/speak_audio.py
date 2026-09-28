"""
Die Stimme, die der Server geschickt hat, auf diesem Rechner hörbar machen.

Der Weg: JARVIS antwortet → der Server lässt ElevenLabs daraus eine Stimme
machen → das fertige Audio kommt hierher → es wird abgespielt. Auf diesem
Rechner liegt dafür kein Schlüssel und keine Verbindung zu ElevenLabs; er
bekommt fertigen Ton.

Warum nicht einfach Text schicken und hier sprechen lassen: Dann klänge
JARVIS auf jedem Gerät anders — Windows-Stimme hier, ElevenLabs im Browser.
Eine Stimme, ein Ort, an dem sie entsteht.

Abgespielt wird über `core.speech_out.play_file`, dieselbe Stelle, die auch
die Desktop-App benutzt. WAV geht mit der Standardbibliothek; MP3 nur, wenn
`soundfile` da ist — der Server schickt deshalb WAV, wo er kann.
"""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

# Mehr als das ist kein Satz mehr, sondern ein Hörbuch — und ginge über die
# Leitung, die eigentlich für Befehle da ist.
MAX_AUDIO_BYTES = 12 * 1024 * 1024

_EXT = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
        "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/ogg": ".ogg"}


def speak_audio(parameters: dict) -> str:
    raw = str(parameters.get("audio_base64") or "")
    if not raw:
        return json.dumps({"error": "Es kam kein Audio an (audio_base64 fehlt)."})
    try:
        data = base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        return json.dumps({"error": "Das Audio war nicht sauber kodiert."})
    if len(data) > MAX_AUDIO_BYTES:
        return json.dumps({"error": f"Das Audio ist größer als {MAX_AUDIO_BYTES // (1024 * 1024)} MB."})

    mime = str(parameters.get("mime") or "audio/wav").lower()
    suffix = _EXT.get(mime, ".wav")
    tmp = Path(tempfile.gettempdir()) / f"jarvis_speak{suffix}"
    try:
        tmp.write_bytes(data)
    except OSError as e:
        return json.dumps({"error": f"Das Audio ließ sich nicht ablegen: {e.__class__.__name__}"})

    try:
        from core.speech_out import play_file
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"Die Tonausgabe fehlt auf diesem Rechner: {e.__class__.__name__}"})

    played = play_file(tmp)
    if not played:
        return json.dumps({
            "played": False,
            "error": ("Der Ton ließ sich nicht abspielen. Bei WAV fehlen meist Lautsprecher oder "
                      "sounddevice; bei MP3 zusätzlich soundfile. Nachinstallieren: python setup.py"),
            "file": str(tmp),
        })
    return json.dumps({"played": True, "bytes": len(data), "mime": mime})


TOOL = {
    "name": "speak_audio",
    "description": ("Plays audio that the server already synthesised (ElevenLabs) through this PC's "
                    "speakers. The PC needs no voice key of its own — it receives finished sound."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "audio_base64": {"type": "STRING", "description": "the audio itself, base64 encoded"},
            "mime": {"type": "STRING", "description": "audio/wav (default) or audio/mpeg"},
        },
        "required": ["audio_base64"],
    },
    "handler": speak_audio,
}
