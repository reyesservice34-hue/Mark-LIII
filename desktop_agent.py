"""
desktop_agent.py — let the JARVIS Command Center drive this PC.

Run this next to the normal app (or instead of it, if you only want the remote
control part):

    python desktop_agent.py

It registers this machine with the server, then waits for commands and runs
them with the same actions the desktop uses itself. Nothing is opened on this
computer's network: the connection always goes outward.

Configure it like the rest of the control plane, in config/api_keys.json
(gitignored) or as environment variables:

    JARVIS_GATEWAY_URL     https://your-server/…      ("jarvis_gateway_url")
    JARVIS_GATEWAY_TOKEN   the machine token          ("jarvis_gateway_token")

Optional in config/api_keys.json:

    "desktop_device_name":     "Buero-PC"          (default: the hostname)
    "desktop_blocked_actions": ["dev_agent", ...]  (default: a small safe list)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.desktop_runner import DesktopRunner  # noqa: E402


def main() -> int:
    print("\n=== JARVIS — Desktop-Fernsteuerung ===\n")
    runner = DesktopRunner()
    if not runner.configured():
        print("  Es fehlt die Verbindung zum Server.")
        print("  Trag in config/api_keys.json ein:")
        print('    "jarvis_gateway_url":   "https://dein-server/…"')
        print('    "jarvis_gateway_token": "das Maschinen-Token aus den Einstellungen"')
        print("  Oder setz die Umgebungsvariablen JARVIS_GATEWAY_URL und JARVIS_GATEWAY_TOKEN.\n")
        return 1

    actions = runner.declarations()
    print(f"  Server:   {runner.base_url}")
    print(f"  Rechner:  {runner.name}")
    print(f"  Aktionen: {len(actions)} ({', '.join(a['name'] for a in actions[:8])}"
          f"{' …' if len(actions) > 8 else ''})")
    print("\n  Warte auf Befehle. Beenden mit Strg+C.\n")
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        runner.stop()
        print("\n  Beendet.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
