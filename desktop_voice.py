"""
JARVIS am eigenen Rechner — sprechen auf der Konsole, ohne Browser.

    python desktop_voice.py

Die dünne Variante von `jarvis_desktop.py`: dieselbe offene Leitung, nur ohne
Fenster. Für einen Rechner ohne Oberfläche, oder wenn man sehen will, was
über die Leitung geht.

Die Audio- und Protokolllogik steht in core/live_client.py und wird von
beiden benutzt — zweimal dasselbe zu schreiben wäre zweimal dieselben Fehler.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.live_client import LiveClient, load_settings


def main() -> int:
    url, token = load_settings()
    if not url or not token:
        print("\n  Es fehlt die Verbindung zum Server.")
        print("  Einrichten mit:  python install_desktop.py")
        print("  (oder INSTALL-JARVIS.bat doppelklicken)\n")
        return 1

    print("\n=== JARVIS — Sprache am Rechner ===\n")
    print(f"  Server: {url}")

    state = {"said": ""}

    def on_ready(ev: dict) -> None:
        print(f"\n  Leitung offen — {ev.get('voice', '?')}, {ev.get('tools', 0)} Werkzeuge.")
        print("  Sprich einfach los. Beenden mit Strg+C.\n")

    def on_said(text: str, done: bool) -> None:
        if done and text.strip():
            print(f"  JARVIS: {text.strip()}\n")

    client = LiveClient(
        url, token,
        on_ready=on_ready,
        on_heard=lambda t: print(f"  Du:     {t}"),
        on_said=on_said,
        on_tool=lambda n, ok: print(f"  [{n}: {'ok' if ok else 'fehlgeschlagen'}]"),
        on_error=lambda d: print(f"  {d}"))

    try:
        reason = asyncio.run(client.run())
        if reason:
            print(f"\n  {reason}\n")
    except KeyboardInterrupt:
        print("\n  Beendet.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
