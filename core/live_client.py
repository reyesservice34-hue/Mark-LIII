"""
Die offene Leitung, von einem Rechner aus — als Baustein, nicht als Skript.

Hier steckt alles, was für ein Gespräch mit dem Server nötig ist: Mikrofon
auf, PCM hoch, PCM runter, Wiedergabe, Dazwischenreden. Wer das benutzt,
bekommt Rückrufe und muss von Audio nichts wissen.

Zwei benutzen das: `desktop_voice.py` auf der Konsole und `jarvis_desktop.py`
mit Fenster. Zweimal dasselbe zu schreiben wäre zweimal dieselben Fehler.

Der Schlüssel des Anbieters liegt auf dem Server, hier nicht. Ausgewiesen wird
sich mit dem Maschinen-Token.
"""
from __future__ import annotations

import asyncio
import base64
import json
import queue
from pathlib import Path
from typing import Callable

RATE = 24000          # was die Realtime-Schnittstelle spricht, in beide Richtungen
BLOCK = 1200          # 50 ms — klein genug, dass Unterbrechen sofort wirkt

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_settings() -> tuple[str, str]:
    """Serveradresse und Maschinen-Token, aus der Umgebung oder der Konfiguration."""
    import os
    cfg: dict = {}
    path = REPO_ROOT / "config" / "api_keys.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    url = (os.environ.get("JARVIS_GATEWAY_URL") or cfg.get("jarvis_gateway_url") or "").rstrip("/")
    token = (os.environ.get("JARVIS_GATEWAY_TOKEN") or cfg.get("jarvis_gateway_token") or "").strip()
    return url, token


def ws_url(base: str) -> str:
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


class LiveClient:
    """Eine offene Leitung zum Server. Alles Sichtbare kommt über Rückrufe.

    Die Rückrufe laufen im Asyncio-Thread dieses Clients, nicht im Thread des
    Aufrufers. Eine Oberfläche muss sie also in ihren eigenen Thread bringen —
    bei Qt über ein Signal. Das steht hier, weil ein Fenster sonst sporadisch
    abstürzt und niemand weiß warum.
    """

    def __init__(self, url: str, token: str, *,
                 on_state: Callable[[str], None] | None = None,
                 on_ready: Callable[[dict], None] | None = None,
                 on_heard: Callable[[str], None] | None = None,
                 on_said: Callable[[str, bool], None] | None = None,
                 on_tool: Callable[[str, bool], None] | None = None,
                 on_error: Callable[[str], None] | None = None) -> None:
        self.url, self.token = url, token
        self.on_state, self.on_ready = on_state, on_ready
        self.on_heard, self.on_said = on_heard, on_said
        self.on_tool, self.on_error = on_tool, on_error
        self._stop = asyncio.Event()
        self._ws = None
        self._speaker: Speaker | None = None

    def _fire(self, cb, *args) -> None:
        if cb is None:
            return
        try:
            cb(*args)
        except Exception:  # noqa: BLE001
            pass      # ein Fehler in der Oberfläche darf die Leitung nicht kappen

    def _why_refused(self, err: Exception) -> str:
        """Aus „HTTP 403" einen Satz machen, mit dem man etwas anfangen kann.

        Ein WebSocket, der vor dem Händedruck geschlossen wird, kommt beim
        Aufrufer immer als 403 an — egal ob das Token unbekannt ist, die Rolle
        nicht reicht oder der Server zu alt ist, um Maschinen-Token auf die
        Leitung zu lassen. Die drei sind über die normale HTTP-Schnittstelle
        auseinanderzuhalten, also wird dort nachgefragt statt geraten.
        """
        text = f"{err.__class__.__name__}: {err}"
        if "403" not in text and "401" not in text:
            return f"Die Leitung kam nicht zustande: {text}"

        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url.rstrip("/") + "/api/voice/live/capabilities",
                                     headers={"X-Jarvis-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                caps = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return ("Der Server kennt dieses Token nicht. Ein neues erzeugen: "
                        "python install_desktop.py")
            if e.code == 403:
                return ("Das Token gilt, aber seine Rolle reicht nicht. Gebraucht wird "
                        "'operator', nicht 'viewer'.")
            return f"Die Leitung wurde abgelehnt (HTTP {e.code})."
        except Exception:  # noqa: BLE001
            return f"Die Leitung kam nicht zustande: {text}"

        if not caps.get("machine_tokens"):
            return ("Der Server läuft noch in einer älteren Fassung: sie lässt nur den "
                    "Browser auf die Live-Leitung, nicht diesen Rechner. Auf dem Server "
                    "neu bauen (git pull, dann command_center/install.sh), danach geht es.")
        return ("Das Token gilt für die Schnittstelle, aber nicht für die Live-Leitung. "
                "Gebraucht wird die Rolle 'operator'.")

    async def run(self) -> str:
        """Leitung öffnen und halten. Gibt zurück, warum sie endete."""
        if not self.url or not self.token:
            return ("Es fehlt die Verbindung zum Server. Einrichten mit: "
                    "python install_desktop.py")
        try:
            import numpy as np
            import sounddevice as sd
            import websockets
        except ImportError as e:
            return f"Ein Paket fehlt: {e.name}. Nachinstallieren mit: python setup.py"

        self._fire(self.on_state, "connecting")
        headers = {"X-Jarvis-Token": self.token}
        try:
            try:
                ws = await websockets.connect(ws_url(self.url), additional_headers=headers,
                                              max_size=16 * 1024 * 1024)
            except TypeError:
                ws = await websockets.connect(ws_url(self.url), extra_headers=headers,
                                              max_size=16 * 1024 * 1024)
        except Exception as e:  # noqa: BLE001
            self._fire(self.on_state, "closed")
            return self._why_refused(e)
        self._ws = ws

        speaker = Speaker(sd, np)
        self._speaker = speaker
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

        async def pump_mic() -> None:
            while True:
                chunk = await mic_q.get()
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(chunk).decode("ascii")}))

        reason = "Leitung geschlossen."

        async def pump_down() -> None:
            nonlocal reason
            said = ""
            async for raw in ws:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                t = ev.get("type", "")
                if t == "jarvis.ready":
                    self._fire(self.on_ready, ev)
                    self._fire(self.on_state, "listening")
                elif t == "jarvis.unavailable":
                    reason = ev.get("detail", "Die Live-Leitung ist nicht verfügbar.")
                    self._fire(self.on_error, reason)
                    return
                elif t == "input_audio_buffer.speech_started":
                    speaker.flush()
                    self._fire(self.on_state, "listening")
                elif t == "conversation.item.input_audio_transcription.completed":
                    text = (ev.get("transcript") or "").strip()
                    if text:
                        said = ""
                        self._fire(self.on_heard, text)
                elif t == "response.created":
                    said = ""
                    self._fire(self.on_state, "thinking")
                elif t == "response.output_audio_transcript.delta":
                    said += ev.get("delta", "")
                    self._fire(self.on_said, said, False)
                elif t == "response.output_audio.delta":
                    if ev.get("delta"):
                        self._fire(self.on_state, "speaking")
                        speaker.play(base64.b64decode(ev["delta"]))
                elif t == "jarvis.tool":
                    self._fire(self.on_tool, ev.get("name", ""), bool(ev.get("ok")))
                elif t == "response.done":
                    self._fire(self.on_said, said, True)
                    self._fire(self.on_state, "listening")
                elif t == "error":
                    self._fire(self.on_error, (ev.get("error") or {}).get("message", "Fehler auf der Leitung."))

        tasks = [asyncio.create_task(pump_mic()), asyncio.create_task(pump_down()),
                 asyncio.create_task(self._stop.wait())]
        try:
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
        finally:
            mic.stop(); mic.close()
            speaker.close()
            self._speaker = None
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
            self._fire(self.on_state, "closed")
        return reason

    def say(self, text: str) -> None:
        """Etwas Getipptes einwerfen, ohne es zu sprechen."""
        ws = self._ws
        if ws is None:
            return
        asyncio.get_event_loop().create_task(ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]}})))

    def stop(self) -> None:
        self._stop.set()


__all__ = ["LiveClient", "Speaker", "load_settings", "ws_url", "RATE", "BLOCK"]
