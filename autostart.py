"""
JARVIS mit Windows starten lassen — oder es wieder lassen.

    python autostart.py           zeigt den Zustand
    python autostart.py an        trägt ihn ein
    python autostart.py aus       trägt ihn aus

Warum das nötig ist: Die Kopplung mit dem Server besteht nur, solange auf
diesem Rechner etwas läuft, das sich meldet. Ein Fenster, das man nach dem
Neustart vergisst zu öffnen, ist keine Kopplung — dann steht im Dashboard
„kein Gerät online", und niemand weiß warum.

Eingetragen wird eine Verknüpfung im Autostart-Ordner des angemeldeten
Benutzers. Kein Dienst, keine Aufgabenplanung, keine Administratorrechte:
Ein Dienst liefe ohne angemeldeten Benutzer und könnte weder tippen noch
klicken noch den Bildschirm sehen — genau das, wofür die Kopplung da ist.

Gestartet wird die Brücke ohne Fenster (`desktop_agent.py` über `pythonw`).
Wer das große Fenster will, startet JARVIS.bat zusätzlich.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "JARVIS-Bruecke.cmd"


def startup_dir() -> Path | None:
    """Der Autostart-Ordner dieses Benutzers, falls es ihn gibt."""
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def target() -> Path | None:
    d = startup_dir()
    return (d / NAME) if d else None


def enabled() -> bool:
    t = target()
    return bool(t and t.exists())


def _launcher_text() -> str:
    """Eine Zeile, die die Brücke ohne Fenster startet.

    `start ""` mit pythonw: kein schwarzes Fenster beim Anmelden, und die cmd
    beendet sich sofort wieder, statt im Autostart hängen zu bleiben.
    """
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return (
        "@echo off\r\n"
        "REM Von autostart.py angelegt. Loeschen schaltet den Autostart ab.\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'start "" "{exe}" "{ROOT / "desktop_agent.py"}"\r\n'
    )


def turn_on() -> str:
    d = startup_dir()
    if d is None:
        return ("Autostart gibt es so nur unter Windows. Unter Linux oder macOS "
                "nimm einen systemd-Dienst bzw. ein LaunchAgent.")
    d.mkdir(parents=True, exist_ok=True)
    t = d / NAME
    t.write_text(_launcher_text(), encoding="utf-8")
    return f"Eingetragen: {t}\nJARVIS meldet sich ab dem nächsten Anmelden von selbst am Server."


def turn_off() -> str:
    t = target()
    if t is None:
        return "Unter diesem Betriebssystem ist nichts eingetragen."
    if not t.exists():
        return "War nicht eingetragen — es ändert sich nichts."
    t.unlink()
    return f"Ausgetragen: {t}"


def main() -> int:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if arg in ("an", "ein", "on", "enable"):
        print(turn_on())
    elif arg in ("aus", "off", "disable"):
        print(turn_off())
    else:
        t = target()
        if t is None:
            print("Autostart: unter diesem Betriebssystem nicht eingerichtet.")
        elif t.exists():
            print(f"Autostart: EIN  ({t})")
            print("Ausschalten mit:  python autostart.py aus")
        else:
            print("Autostart: AUS")
            print("Einschalten mit:  python autostart.py an")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
