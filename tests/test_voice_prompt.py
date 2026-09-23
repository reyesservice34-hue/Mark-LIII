"""Spracherkennung: der Wortvorrat-Hinweis geht mit, ist einstellbar und abschaltbar — offline."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["JARVIS_CC_STT_URL"] = "http://speaches:8000"
os.environ["JARVIS_CC_STT_MODEL"] = "Systran/faster-whisper-base"

import httpx  # noqa: E402

from command_center.backend.services import voice_service as vs  # noqa: E402

FAILS = []
SENT = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


def handler(request: httpx.Request) -> httpx.Response:
    body = request.content.decode("latin-1")
    SENT.append({"prompt": 'name="prompt"' in body, "body": body})
    return httpx.Response(200, json={"text": "Hallo Jarvis", "language": "de"})


_orig = httpx.AsyncClient


class Client(_orig):
    def __init__(self, *a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        super().__init__(*a, **kw)


vs.httpx.AsyncClient = Client


async def main():
    v = vs.VoiceService()
    os.environ.pop("JARVIS_CC_STT_PROMPT", None)
    r = await v.transcribe(b"x" * 3000, "audio/mp4", "de")
    check("Standard: der Wortvorrat (Jarvis, Lexware, Voranmeldung …) wird mitgeschickt", SENT[-1]["prompt"] and "Jarvis" in SENT[-1]["body"] and "Lexware" in SENT[-1]["body"] and "Voranmeldung" in SENT[-1]["body"], SENT[-1]["body"][:300])
    check("Ergebnis kommt unverändert zurück", r["text"] == "Hallo Jarvis")
    os.environ["JARVIS_CC_STT_PROMPT"] = "Baustelle Neuberg"
    await v.transcribe(b"x" * 3000, "audio/webm", "de")
    check("Einstellbar: JARVIS_CC_STT_PROMPT ersetzt den Standard", SENT[-1]["prompt"] and "Baustelle Neuberg" in SENT[-1]["body"] and "Lexware" not in SENT[-1]["body"])
    os.environ["JARVIS_CC_STT_PROMPT"] = ""
    await v.transcribe(b"x" * 3000, "audio/mp4", "de")
    check("Leer = aus: dann geht kein Hinweis mit", not SENT[-1]["prompt"])
    check("Sprache und Modell gehen weiter mit", 'name="language"' in SENT[-1]["body"] and "faster-whisper-base" in SENT[-1]["body"])


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
