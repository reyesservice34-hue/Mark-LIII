"""
Ein Bildschirmfoto, das den Rechner verlässt.

`computer_control` kann schon Bildschirmfotos — aber es legt sie auf dem PC ab
und gibt nur den Pfad zurück. Für JARVIS auf dem Server ist das eine
Zeichenkette: Er kann sie lesen, aber nichts sehen. Diese Aktion liefert das
Bild selbst, damit es auf dem Server ankommt und dem Modell als Bild vorgelegt
werden kann.

Zwei Dinge sind hier bewusst geregelt:

* **Verkleinert.** Ein 4K-Bildschirm ist roh mehrere Megabyte; das über die
  Leitung zu schicken und bei jeder Anfrage mitzuschleppen kostet Geld und
  bringt nichts. Die lange Kante kommt auf 1600 Pixel, als JPEG — genug, um
  Fenster, Text und Knöpfe zu erkennen.
* **Nur auf Anfrage.** Keine Aufzeichnung, kein Zeitgeber. Ein Bild entsteht,
  wenn jemand darum bittet, und sonst nie.
"""
from __future__ import annotations

import base64
import io
import json

MAX_EDGE = 1600
JPEG_QUALITY = 72


def screen_capture(parameters: dict) -> str:
    """Gibt JSON zurück: das Bild als Base64 plus die echte Bildschirmgröße.

    JSON statt eines Objekts, weil der Rückweg zum Server eine Zeichenkette
    ist. Der Server packt es wieder aus — das ist ehrlicher, als hier so zu
    tun, als führe ein Objekt hinüber.
    """
    try:
        import pyautogui
    except ImportError:
        return json.dumps({"error": "pyautogui fehlt auf diesem Rechner. "
                                    "Nachinstallieren mit: python setup.py"})

    try:
        img = pyautogui.screenshot()
    except Exception as e:  # noqa: BLE001 — z. B. kein Bildschirm auf einem Server
        return json.dumps({"error": f"Bildschirmfoto ging nicht: {e.__class__.__name__}: {e}"})

    width, height = img.size
    scale = MAX_EDGE / max(width, height)
    if scale < 1:
        img = img.resize((int(width * scale), int(height * scale)))

    buf = io.BytesIO()
    # JPEG statt PNG: als PNG sind es schnell zwei Megabyte, als JPEG ein
    # Zehntel davon — und lesbar bleibt es.
    img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    data = buf.getvalue()

    return json.dumps({
        "image_base64": base64.b64encode(data).decode("ascii"),
        "mime": "image/jpeg",
        "width": width,
        "height": height,
        "bytes": len(data),
        "note": f"Bildschirm {width}x{height}, verkleinert auf hoechstens {MAX_EDGE} px.",
    })


TOOL = {
    "name": "screen_capture",
    "description": ("Takes a screenshot of this PC and returns the image itself, not just a path — so "
                    "the server can actually look at the screen. Downscaled and JPEG-encoded so it "
                    "stays small."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "monitor": {"type": "INTEGER", "description": "reserved; 0 = the whole screen"},
        },
        "required": [],
    },
    "handler": screen_capture,
}
