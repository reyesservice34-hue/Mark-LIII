"""
JARVIS am eigenen Rechner — sprechen, ohne Browser.

    python desktop_voice.py

Öffnet die Live-Leitung zum Server und hält sie offen. Man redet einfach los;
wann eine Äußerung zu Ende ist, entscheidet das Modell. Man kann ihm ins Wort
fallen, dann hört er auf zu reden.

Warum über den Server und nicht direkt zum Anbieter: der Schlüssel bleibt
dort, und die Werkzeuge, das Gedächtnis, die Freigaben und die Prüfspur sind
dieselben wie im Dashboard. Ein Satz hier und ein Satz im Browser landen in
derselben Unterhaltung.

Gebraucht wird nur das Maschinen-Token aus dem Dashboard — kein
Gemini-Schlüssel, kein zweites Konto. Eingerichtet wird es mit
INSTALL-JARVIS.bat beziehungsweise `python install_desktop.py`.

Läuft neben `desktop_agent.py`: der eine hört zu, der andere führt aus. Beide
zusammen ergeben den Rechner, der auf Zuruf tut, was man sagt.
"""
from __future__ import annotations

import asyncio
import base64
import json
import queue
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RATE = 24000          # was die Realtime-Schnittstelle spricht, in beide Richtungen
BLOCK = 1200          # 50 ms — klein genug, dass Unterbrechen sofort wirkt


def _config() -> dict:
    path = Path(__file__).resolve().parent / "config" / "api_keys.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _settings() -> tuple[str, str]:
    import os
    cfg = _config()
    url = (os.environ.get("JARVIS_GATEWAY_URL") or cfg.get("jarvis_gateway_url") or "").rstrip("/")
    token = (os.environ.get("JARVIS_GATEWAY_TOKEN") or cfg.get("jarvis_gateway_token") or "").strip()
    return url, token


def _ws_url(base: str) -> str:
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/api/voice/live"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/api/voice/live"
    return "wss://" + base + "/api/voice/live"


class Speaker:
    """Gibt die Antwort aus und kann sie mitten im Wort abbrechen."""

    def __init__(self, sd, np) -> None:
        self.sd, self.np = sd, np
        self.q: queue.Queue = queue.Queue()
        # Was von einem Paket über einen Callback hinausragt. Nur der
        # Audio-Thread fasst das an, deshalb braucht es keine Sperre — anders
        # als die interne Warteschlange von queue.Queue, in der von außen
        # herumzuräumen nicht sicher wäre.
        self._rest = np.zeros(0, dtype="int16")
        self.stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="int16",
                                      blocksize=BLOCK, callback=self._pull)
        self.stream.start()

    def _pull(self, outdata, frames, _t, _s):
        out = self.np.zeros(frames, dtype="int16")
        filled = 0
        while filled < frames:
            if len(self._rest) == 0:
                try:
                    self._rest = self.q.get_nowait()
                except queue.Empty:
                    break
            take = min(len(self._rest), frames - filled)
            out[filled:filled + take] = self._rest[:take]
            self._rest = self._rest[take:]
            filled += take
        outdata[:] = out.reshape(-1, 1)

    def play(self, pcm_bytes: bytes) -> None:
        self.q.put(self.np.frombuffer(pcm_bytes, dtype="int16"))

    def flush(self) -> None:
        """Dazwischenreden: alles Ungespielte sofort verwerfen."""
        self._rest = self.np.zeros(0, dtype="int16")
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        self.flush()
        self.stream.stop()
        self.stream.close()


async def run() -> int:
    url, token = _settings()
    if not url or not token:
        print("\n  Es fehlt die Verbindung zum Server.")
        print("  Einrichten mit:  python install_desktop.py")
        print("  (oder INSTALL-JARVIS.bat doppelklicken)\n")
        return 1

    try:
        import numpy as np
        import sounddevice as sd
        import websockets
    except ImportError as e:
        print(f"\n  Ein Paket fehlt: {e.name}")
        print("  Nachinstallieren mit:  python setup.py\n")
        return 1

    print("\n=== JARVIS — Sprache am Rechner ===\n")
    print(f"  Server:   {url}")
    try:
        print(f"  Mikrofon: {sd.query_devices(kind='input')['name']}")
        print(f"  Ausgabe:  {sd.query_devices(kind='output')['name']}")
    except Exception:  # noqa: BLE001
        print("  Audiogeräte: konnten nicht gelesen werden — es wird die Vorgabe genommen.")

    headers = {"X-Jarvis-Token": token}
    try:
        try:
            ws = await websockets.connect(_ws_url(url), additional_headers=headers,
                                          max_size=16 * 1024 * 1024)
        except TypeError:
            ws = await websockets.connect(_ws_url(url), extra_headers=headers,
                                          max_size=16 * 1024 * 1024)
    except Exception as e:  # noqa: BLE001
        print(f"\n  Die Leitung kam nicht zustande: {e.__class__.__name__}: {e}")
        print("  Prüfen: läuft der Server, stimmt das Token, ist die Adresse über HTTPS erreichbar?\n")
        return 1

    speaker = Speaker(sd, np)
    loop = asyncio.get_running_loop()
    mic_q: asyncio.Queue = asyncio.Queue(maxsize=64)

    def on_mic(indata, _frames, _t, status):
        if status:
            return
        try:
            loop.call_soon_threadsafe(mic_q.put_nowait, bytes(indata))
        except (asyncio.QueueFull, RuntimeError):
            pass      # lieber ein Block verlieren als den Audiotakt aufhalten

    mic = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                         blocksize=BLOCK, callback=on_mic)
    mic.start()

    async def send_mic() -> None:
        while True:
            chunk = await mic_q.get()
            await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                      "audio": base64.b64encode(chunk).decode("ascii")}))

    async def receive() -> None:
        said = ""
        async for raw in ws:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type", "")
            if t == "jarvis.ready":
                print(f"\n  Leitung offen — {ev.get('voice', '?')}, {ev.get('tools', 0)} Werkzeuge.")
                print("  Sprich einfach los. Beenden mit Strg+C.\n")
            elif t == "jarvis.unavailable":
                print(f"\n  {ev.get('detail', 'nicht verfügbar')}\n")
                return
            elif t == "input_audio_buffer.speech_started":
                speaker.flush()          # er hört auf, sobald du anfängst
            elif t == "conversation.item.input_audio_transcription.completed":
                text = (ev.get("transcript") or "").strip()
                if text:
                    print(f"  Du:     {text}")
            elif t == "response.output_audio_transcript.delta":
                said += ev.get("delta", "")
            elif t == "response.output_audio.delta":
                if ev.get("delta"):
                    speaker.play(base64.b64decode(ev["delta"]))
            elif t == "jarvis.tool":
                mark = "ok" if ev.get("ok") else "fehlgeschlagen"
                print(f"  [{ev.get('name')}: {mark}]")
            elif t == "response.done":
                if said.strip():
                    print(f"  JARVIS: {said.strip()}\n")
                said = ""
            elif t == "error":
                print(f"  Fehler: {(ev.get('error') or {}).get('message', '?')}")

    stop = asyncio.Event()
    with __import__("contextlib").suppress(NotImplementedError, AttributeError):
        loop.add_signal_handler(signal.SIGINT, stop.set)

    tasks = [asyncio.create_task(send_mic()), asyncio.create_task(receive())]
    try:
        done, pending = await asyncio.wait(
            [*tasks, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop(); mic.close()
        speaker.close()
        with __import__("contextlib").suppress(Exception):
            await ws.close()
        print("\n  Leitung geschlossen.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        print("\n  Beendet.\n")
        sys.exit(0)
