"""
Jarvis Live Voice — a free, fully local live-conversation companion.

Loop per turn:
  browser mic (VAD-triggered clip) -> POST /api/turn
    -> speaches (local Whisper)      : audio -> text
    -> OpenRouter (Claude Sonnet 5)  : text  -> reply text   (the only step that leaves the server)
    -> speaches (local Piper)        : reply -> audio
    -> back to the browser, which plays it and keeps listening for barge-in

Nothing here calls a paid TTS/STT provider — only the LLM call goes out,
using the same OPENROUTER_API_KEY the rest of Jarvis already uses.

This service is the fast fallback line. The live page itself sends every utterance to the
Command Center chat (voice channel), so the full agent with all its tools, approvals and memory
answers; only when that is unreachable does this LLM path answer on its own, without tools.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

SPEACHES_URL = os.environ.get("SPEACHES_URL", "http://jarvis-speaches:8000")
STT_MODEL = os.environ.get("STT_MODEL", "Systran/faster-whisper-small")
TTS_MODEL = os.environ.get("TTS_MODEL", "speaches-ai/piper-de_DE-thorsten-high")
TTS_VOICE = os.environ.get("TTS_VOICE", "thorsten")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
# Kostenlos ist Pflicht: nur Modelle mit Endung ":free" werden überhaupt angefragt,
# außer ALLOW_PAID=1 ist ausdrücklich gesetzt. Fällt eines aus (Limit, Überlast),
# kommt das nächste in der Liste dran.
ALLOW_PAID = os.environ.get("ALLOW_PAID", "") == "1"   # Standard: aus, es kostet nichts
MODELS = [m.strip() for m in os.environ.get(
    "OPENROUTER_MODELS",
    "nex-agi/nex-n2.5-mini:free,nex-agi/nex-n2.5-pro:free,google/gemma-4-26b-a4b-it:free,deepseek/deepseek-v4-flash-0731:free",
).split(",") if m.strip()]
if not ALLOW_PAID:
    MODELS = [m for m in MODELS if m.endswith(":free")]

# Schnell zuerst, gründlich bei Bedarf: das schnelle Modell antwortet standardmäßig, bei kniffligen Fragen
# (Kalkulation, Vertrag, Planung, lange Fragen) steht das starke vorn. Reihenfolge der Liste = Ausweichfolge.
FAST_MODEL = os.environ.get("FAST_MODEL", "nex-agi/nex-n2.5-mini:free").strip()
DEEP_MODEL = os.environ.get("DEEP_MODEL", "nex-agi/nex-n2.5-pro:free").strip()
_DEEP_RE = re.compile(
    r"\b(angebot|kalkul|rechnung|abschlag|nachtrag|vertrag|recht|norm|din|steuer|haftung|gew(ä|ae)hrleistung|"
    r"analys|strategie|vergleich|bericht|konzept|plan(e|ung)?|kollision|optimier|ausf(ü|ue)hrlich|"
    r"schritt f(ü|ue)r schritt|entwurf|beschwerde|reklamation|mahnung|verhandl|begr(ü|ue)nd|warum)\w*", re.I)


def _models_for(text: str) -> list[str]:
    deep = len(text) > 300 or bool(_DEEP_RE.search(text))
    first = DEEP_MODEL if deep else FAST_MODEL
    rest = [m for m in ([FAST_MODEL, DEEP_MODEL] + MODELS) if m and m != first]
    out = [first]
    for m in rest:
        if m not in out and (ALLOW_PAID or m.endswith(":free")):
            out.append(m)
    return out if ALLOW_PAID else [m for m in out if m.endswith(":free")] or MODELS

SYSTEM_PROMPT = (
    "Du bist JARVIS, der persönliche Assistent. Du redest wie ein aufmerksamer, kluger Mensch am Telefon: "
    "ruhig, warm, mit trockenem Humor. Normales gesprochenes Deutsch, nicht wie geschriebener Text. "
    "Halte dich kurz: meist ein bis zwei Sätze, höchstens vier, außer der Nutzer bittet ausdrücklich um eine ausführliche Erklärung. Erst das Wichtigste, den Rest nur auf Nachfrage. Du darfst kurz reagieren (Hm, Ah, Okay, Also, Tja), bei Bedarf eine "
    "kurze Rückfrage stellen, und du sagst ehrlich, wenn du etwas nicht weißt oder kurz überlegen musst. "
    "Keine Aufzählungen, kein Markdown, keine Floskeln wie „Gerne helfe ich dir weiter“ oder „Kann ich sonst "
    "noch etwas für dich tun?“, und nie „als KI“. Sprich wie jemand, der ruhig und klar erklärt: erst das Ergebnis, dann in einem Satz den Grund. Fachwörter nur, wenn nötig. Keine Wörter wie „zusammenfassend“, „im Folgenden“ oder „gerne“ als Füllsel. Zahlen, Beträge und Uhrzeiten so sagen, wie man sie spricht („neunundzwanzig Euro neunzig“, „halb zehn“). Kleine Denkwörter wie „Also“ oder „Hm“ sind erlaubt. Fang nicht jeden Satz gleich an. Den Nutzer nennst du "
    "„Master“ (z. B. „Hallo Master“), aber nicht in jedem Satz, sondern natürlich eingestreut, vor allem beim Begrüßen und wenn es passt. Du passt bei allem auf Kollisionen und Risiken auf, beruflich wie privat (Zeit, Geld, Personen, Fahrzeuge, Material, Zusagen, Fristen, Arbeitszeit, Wege, Familie und Gesundheit): Siehst du eine, sag es ungefragt und knapp mit „Achtung, Master:“ und schlage eine bessere Lösung vor. Nur wenn es wirklich begründet ist, nichts raten. Wenn eine Aktion auf dem Server oder PC verlangt, "
    "sag locker, dass er das im normalen Jarvis-Chat anstoßen soll, dort hast du deine Werkzeuge."
)

app = FastAPI(title="Jarvis Live Voice")

# Eine dauerhafte Verbindung statt bei jeder Antwort neu (TLS-Aufbau kostet ~0,2–0,3 s).
_HTTP = httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300))


class _shared:
    async def __aenter__(self):
        return _HTTP

    async def __aexit__(self, *a):
        return False


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "llm_configured": bool(OPENROUTER_API_KEY)}


async def _transcribe(client: httpx.AsyncClient, audio: bytes, content_type: str) -> str:
    ext = "webm" if "webm" in content_type else "wav"
    files = {"file": (f"clip.{ext}", audio, content_type)}
    data = {"model": STT_MODEL, "language": "de", "response_format": "json"}
    r = await client.post(f"{SPEACHES_URL}/v1/audio/transcriptions", files=files, data=data, timeout=60)
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


async def _reply(client: httpx.AsyncClient, history: list[dict], text: str) -> str:
    if not OPENROUTER_API_KEY:
        return "Mir fehlt noch ein gültiger OpenRouter-Schlüssel, Sir. Bitte in der .env eintragen."
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history, {"role": "user", "content": text}]
    r = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        json={"model": OPENROUTER_MODEL, "max_tokens": 400, "messages": messages},
        timeout=60,
    )
    r.raise_for_status()
    d = r.json()
    if "choices" not in d:
        return f"Da ist etwas schiefgegangen, Sir: {d.get('error', {}).get('message', 'unbekannter Fehler')}"
    return d["choices"][0]["message"]["content"].strip()


_REPL = [(r"\bz\. ?B\.", "zum Beispiel"), (r"\bca\.", "circa"), (r"\bbzw\.", "beziehungsweise"), (r"\busw\.", "und so weiter"),
         (r"\bd\. ?h\.", "das heißt"), (r"\bggf\.", "gegebenenfalls"), (r"\bevtl\.", "eventuell"), (r"\bInkl\.", "inklusive"),
         (r"m²|\bqm\b", " Quadratmeter"), (r"(\d)\s?%", r"\1 Prozent"), (r"(\d)\s?(€|EUR)\b", r"\1 Euro"), (r"€", " Euro"),
         (r"&", " und "), (r"(\d{1,2}):00\b(?: Uhr)?", r"\1 Uhr"), (r"(\d{1,2}):(\d{2})\b(?: Uhr)?", r"\1 Uhr \2"),
         (r"(\d),(\d{2})\s?Euro", r"\1 Euro \2"), (r"\s{2,}", " ")]


def _speakable(text: str) -> str:
    """Was man schreibt, ist nicht, was man sagt: Abkürzungen, Euro-Zeichen und Uhrzeiten aussprechbar machen."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # Links: nur der Text
    text = re.sub(r"```.*?```", " ", text, flags=re.S)                # Codeblöcke werden nicht vorgelesen
    text = re.sub(r"[*_`#>|]+", "", text)                             # Markdown-Zeichen
    for pat, rep in _REPL:
        text = re.sub(pat, rep, text)
    return text.strip()


async def _speak(client: httpx.AsyncClient, text: str) -> bytes:
    text = _speakable(text)
    r = await client.post(
        f"{SPEACHES_URL}/v1/audio/speech",
        json={"model": TTS_MODEL, "voice": TTS_VOICE, "input": text, "response_format": "mp3"},
        timeout=60,
    )
    r.raise_for_status()
    return r.content


FILLERS = ["Hm.", "Moment.", "Ja, also …", "Lass mich kurz überlegen.", "Gute Frage.", "Mal sehen."]
_filler_cache: list[dict] = []


@app.get("/api/fillers")
async def fillers() -> dict:
    return {"items": _filler_cache}


_SENT_END = re.compile(r"(?<=[.!?…])\s+")


async def _stream_reply(client: httpx.AsyncClient, history: list[dict], text: str, memory: str = ""):
    """Yield the reply sentence by sentence while the model is still writing it."""
    if not OPENROUTER_API_KEY or not MODELS:
        yield "Mir fehlt noch ein gültiger Schlüssel, Sir."
        return
    system = SYSTEM_PROMPT + ("\n\nDas weißt du über den Nutzer und seinen Betrieb (Gedächtnis, halte dich daran):\n" + memory if memory else "")
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": text}]
    for model in _models_for(text):
        buf, got_any, first_out = "", False, False
        try:
            async with client.stream(
                "POST", "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 220, "stream": True, "messages": messages},
                timeout=httpx.Timeout(30, read=12),
            ) as r:
                if r.status_code >= 400:
                    continue  # Limit oder Überlast: nächstes kostenloses Modell
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                    except (ValueError, KeyError, IndexError):
                        continue
                    if not delta:
                        continue
                    got_any = True
                    buf += delta
                    if not first_out and len(buf) >= 40:
                        m = re.search(r"^(.{25,}?[,–—:;])\s", buf)
                        if m:
                            first_out = True
                            yield m.group(1).strip()
                            buf = buf[m.end():]
                    parts = _SENT_END.split(buf)
                    for sent in parts[:-1]:
                        if sent.strip():
                            first_out = True
                            yield sent.strip()
                    buf = parts[-1]
        except httpx.HTTPError:
            if got_any:
                break  # halb gesprochen: nicht mitten im Satz das Modell wechseln
            continue
        if buf.strip():
            yield buf.strip()
        if got_any:
            return
    yield "Alle kostenlosen Modelle sind gerade ausgelastet, Sir. Bitte gleich noch einmal."


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


async def _respond(client: httpx.AsyncClient, hist: list[dict], text: str, memory: str = ""):
    """LLM streams; every finished sentence goes to TTS at once and is yielded in order."""
    tasks: asyncio.Queue = asyncio.Queue()
    spoken: list[str] = []

    async def produce():
        try:
            async for sent in _stream_reply(client, hist, text, memory):
                spoken.append(sent)
                await tasks.put((sent, asyncio.create_task(_speak(client, sent))))
        except httpx.HTTPError:
            pass
        await tasks.put(None)

    prod = asyncio.create_task(produce())
    while True:
        item = await tasks.get()
        if item is None:
            break
        sent, task = item
        try:
            audio_bytes = await task
            yield _line({"sentence": sent, "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                         "audio_mime": "audio/mpeg"})
        except httpx.HTTPError:
            yield _line({"sentence": sent, "error": "Sprachausgabe nicht erreichbar."})
    await prod
    hist.append({"role": "user", "content": text})
    hist.append({"role": "assistant", "content": " ".join(spoken)})
    yield _line({"done": True, "history": hist})


def _hist(history: str) -> list[dict]:
    try:
        h = json.loads(history)
        return (h if isinstance(h, list) else [])[-16:]
    except ValueError:
        return []


EXTRACT_MODELS = [m.strip() for m in os.environ.get(
    "EXTRACT_MODELS", "nex-agi/nex-n2.5-pro:free,google/gemma-4-26b-a4b-it:free").split(",") if m.strip()]
if not ALLOW_PAID:
    EXTRACT_MODELS = [m for m in EXTRACT_MODELS if m.endswith(":free")]

_EXTRACT_PROMPT = (
    "Du liest einen kurzen Gesprächsausschnitt zwischen dem Nutzer (Inhaber eines Handwerksbetriebs) und seinem "
    "Assistenten JARVIS. Aufgaben:\n"
    "1) facts: Hat der NUTZER etwas Dauerhaftes gesagt, das man sich merken soll (Personen, Mitarbeiter, Kunden, "
    "Betrieb, Regeln, Entscheidungen, Vorlieben, Änderungen, Korrekturen an Jarvis)? Höchstens zwei kurze, "
    "eigenständige Sätze in der dritten Person. Nicht merken: Smalltalk, Fragen, Wetter, Vermutungen, Belangloses, "
    "niemals Passwörter, Schlüssel, Kontodaten oder Zahlenreihen. Schon Bekanntes nicht wiederholen.\n"
    "2) conflicts: Kollidiert etwas, in JEDEM Lebensbereich (beruflich und privat)? Prüfe: Zeit (Überschneidung, "
    "unrealistische Fahrzeit oder Dauer), Geld (Zahlung gegen Kontostand, Liquidität, Preise, Rabatte, Marge), "
    "Personen und Fahrzeuge (doppelt vergeben, fehlend, Urlaub, ausgeschieden), Material und Lieferzeiten, "
    "Zusagen an mehrere Kunden, Fristen und Zahlungsziele, Arbeitszeit und Überlastung (mehr als ein 8-Stunden-Tag), "
    "Familie, Gesundheit und Privates gegen Berufliches, Widersprüche zu Bekanntem, Verstöße gegen feste Regeln "
    "(z. B. interne Preise in Kundentexten, Zahlung ohne Freigabe). Jede Warnung ein Satz, beginnend mit "
    "„Achtung, Master:“, mit dem Grund. Nur warnen, wenn es wirklich begründet ist, nichts raten.\n"
    "3) advice: Höchstens ein kurzer, nützlicher Ratschlag („Ein Rat, Master: …“), wenn du eine deutlich bessere "
    "Lösung, ein Risiko oder eine Chance siehst. Sonst leer.\n"
    "Antworte NUR mit JSON: {\"facts\": [], \"conflicts\": [], \"advice\": []}."
)
_SECRETISH = re.compile(r"(sk-|api[_ -]?key|passwort|password|iban|\b\d{8,}\b|token)", re.I)


@app.post("/api/extract")
async def extract(user: str = Form(...), assistant: str = Form(""), memory: str = Form(""), recent: str = Form("")) -> dict:
    """Lernt aus dem Gespräch: gibt dauerhaft Merkenswertes zurück (oder nichts)."""
    if len(user.split()) < 3 or not OPENROUTER_API_KEY or not EXTRACT_MODELS:
        return {"facts": [], "conflicts": [], "advice": []}
    prompt = (f"{_EXTRACT_PROMPT}\n\nBereits bekannt:\n{memory[:2500]}\n\nBisheriges Gespräch:\n{recent[:1200]}\n\nNutzer: {user[:600]}\n"
              f"Jarvis: {assistant[:400]}")
    for model in EXTRACT_MODELS:
        try:
            r = await _HTTP.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                                 json={"model": model, "max_tokens": 320, "temperature": 0,
                                       "messages": [{"role": "user", "content": prompt}]}, timeout=20)
            if r.status_code >= 400:
                continue
            raw = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", raw, re.S)
            obj = json.loads(m.group(0)) if m else {}
            facts = obj.get("facts", [])
            conflicts = [c.strip() for c in obj.get("conflicts", []) if isinstance(c, str) and 8 < len(c.strip()) < 300][:2]
            advice = [c.strip() for c in obj.get("advice", []) if isinstance(c, str) and 8 < len(c.strip()) < 300][:1]
            known = memory.lower()
            out = [f.strip() for f in facts if isinstance(f, str) and 8 < len(f.strip()) < 400
                   and not _SECRETISH.search(f) and f.strip().lower() not in known][:2]
            return {"facts": out, "conflicts": conflicts, "advice": advice}
        except Exception:  # noqa: BLE001
            continue
    return {"facts": [], "conflicts": [], "advice": []}


_CLEAN_PROMPT = (
    "Du bereinigst gesprochene Befehle für den Assistenten JARVIS. Der Sprecher ist Jan Paul (Inhaber eines Handwerks- "
    "und Innenausbaubetriebs) und nuschelt manchmal; die Spracherkennung macht Fehler. Schreibe den erkannten Text so um, "
    "wie er es vermutlich sagen wollte, damit JARVIS den Befehl genau ausführen kann:\n"
    "- falsch erkannte Wörter korrigieren, besonders Namen, Orte, Firmen und Fachwörter, anhand von Gedächtnis und Gespräch\n"
    "- Füllwörter, Stottern und Wiederholungen entfernen, abgebrochene Sätze zu einem klaren Satz oder Befehl formen\n"
    "- Bezüge wie „er“, „das“, „dort“ nur auflösen, wenn das Gespräch es eindeutig hergibt\n"
    "- Ton und Absicht des Sprechers behalten (Frage bleibt Frage, Befehl bleibt Befehl)\n"
    "Streng verboten: etwas erfinden. Keine neuen Zahlen, Beträge, Uhrzeiten, Namen, Termine oder zusätzliche Aufgaben. "
    "Ist etwas Wichtiges wirklich unklar oder mehrdeutig (welcher Kunde, welche Uhrzeit, welche Mail) und würde eine falsche "
    "Vermutung Schaden anrichten, setze \"unclear\" auf true und formuliere in \"question\" EINE kurze, freundliche Rückfrage. "
    "Ändere nur, was nötig ist. Ist der Text schon klar und verständlich, gib ihn Wort für Wort unverändert zurück; "
    "Umformulieren ohne Grund ist verboten. Antworte ausschließlich als JSON: "
    "{\"text\": \"...\", \"unclear\": false, \"question\": \"\"}"
)


@app.post("/api/clean")
async def clean(text: str = Form(...), history: str = Form("[]"), memory: str = Form("")) -> dict:
    """Erkannten Text glätten, bevor er an Jarvis geht. Schnell und kostenlos; bei Fehler kommt der Originaltext zurück."""
    orig = text.strip()[:600]
    keep = {"text": orig, "changed": False, "unclear": False, "question": ""}
    if not OPENROUTER_API_KEY or len(orig.split()) < 3:
        return keep
    try:
        hist = _hist(history)[-4:]
    except Exception:  # noqa: BLE001
        hist = []
    ctx = "\n".join(("Nutzer: " if m["role"] == "user" else "Jarvis: ") + str(m["content"])[:200] for m in hist)
    prompt = (f"{_CLEAN_PROMPT}\n\nWas Jarvis über den Sprecher weiß:\n{memory[:1800]}\n\nLetzter Gesprächsverlauf:\n{ctx}\n\n"
              f"Erkannter Text: {orig}")
    for model in _clean_models():
        try:
            r = await _HTTP.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                                 json={"model": model, "max_tokens": 220, "temperature": 0,
                                       "messages": [{"role": "user", "content": prompt}]}, timeout=4)
            if r.status_code >= 400:
                continue
            raw = r.json()["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", raw, re.S)
            obj = json.loads(m.group(0)) if m else {}
            out = str(obj.get("text") or "").strip()
            if not out:
                continue
            # Sicherung gegen Erfindungen: Zahlen im Ergebnis müssen im Original vorkommen (oder ausgeschrieben sein).
            nums_new = set(re.findall(r"\d+", out)) - set(re.findall(r"\d+", orig))
            if nums_new and not re.search(r"\d", orig):
                continue
            q = str(obj.get("question") or "").strip()
            return {"text": out, "changed": out.lower().rstrip(".!? ") != orig.lower().rstrip(".!? "),
                    "unclear": bool(obj.get("unclear")) and bool(q), "question": q}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue
    return keep


def _clean_models() -> list[str]:
    ms = [FAST_MODEL, "nex-agi/nex-n2.5-pro:free", "google/gemma-4-26b-a4b-it:free"]
    return [m for i, m in enumerate(ms) if m and m not in ms[:i] and (ALLOW_PAID or m.endswith(":free"))]


@app.post("/api/say")
async def say(text: str = Form(...)) -> dict:
    """Jarvis spricht von sich aus (z. B. eine Meldung vom Herzschlag)."""
    text = text.strip()[:400]
    if not text:
        return {"error": "leer"}
    async with _shared() as c:
        audio_bytes = await _speak(c, text)
    return {"audio_base64": base64.b64encode(audio_bytes).decode("ascii"), "audio_mime": "audio/mpeg"}


@app.post("/api/turn_text")
async def turn_text(text: str = Form(...), history: str = Form("[]"), memory: str = Form("")) -> StreamingResponse:
    """The browser already recognised the speech (Chrome's own recogniser): skip Whisper entirely."""
    hist = _hist(history)
    text = text.strip()

    async def gen():
        if not text:
            yield _line({"error": "Ich habe nichts verstanden, Sir."})
            return
        yield _line({"transcript": text})
        async with _shared() as client:
            async for chunk in _respond(client, hist, text, memory[:4000]):
                yield chunk

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/turn")
async def turn(audio: UploadFile = File(...), history: str = Form("[]")) -> StreamingResponse:
    """Fallback for browsers without a built-in recogniser: local Whisper."""
    hist = _hist(history)
    raw = await audio.read()
    ctype = audio.content_type or "audio/webm"

    async def gen():
        if not raw:
            yield _line({"error": "Keine Audiodaten empfangen."})
            return
        async with _shared() as client:
            try:
                text = await _transcribe(client, raw, ctype)
            except httpx.HTTPError as e:
                yield _line({"error": f"Spracherkennung nicht erreichbar ({e.__class__.__name__})."})
                return
            if not text:
                yield _line({"error": "Ich habe nichts verstanden, Sir."})
                return
            yield _line({"transcript": text})
            async for chunk in _respond(client, hist, text):
                yield chunk

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.on_event("startup")
async def warm_up() -> None:
    """Load Whisper and the voice once, so the first real question is not the slow one."""
    async def go():
        await asyncio.sleep(3)
        try:
            async with _shared() as c:
                try:
                    await c.get("https://openrouter.ai/api/v1/models", timeout=10)   # Verbindung vorwärmen
                except Exception:
                    pass
                for t in FILLERS:
                    try:
                        _filler_cache.append({"text": t, "audio_base64": base64.b64encode(await _speak(c, t)).decode("ascii"),
                                              "audio_mime": "audio/mpeg"})
                    except Exception:
                        pass
                import struct
                pcm = struct.pack("<h", 0) * 8000
                await _transcribe(c, _wav_bytes(pcm), "audio/wav")
        except Exception:
            pass
    asyncio.create_task(go())


def _wav_bytes(pcm: bytes, rate: int = 16000) -> bytes:
    import io, wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return b.getvalue()


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
