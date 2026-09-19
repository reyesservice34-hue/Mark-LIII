"""
JARVIS mit Windows starten lassen — oder es wieder lassen.

    python autostart.py           zeigt den Zustand
    python autostart.py an        trägt ihn ein
    python autostart.py aus       trägt ihn aus

Warum das nötig ist: Die Kopplung mit dem Server besteht nur, solange auf
diesem Rechner etwas läuft, das sich meldet. Ein Fenster, das man nach dem
Neustart vergisst zu öffnen, ist keine Kopplung — dann steht im Dashboard
„kein Gerät online", und niemand weiß warum.

Eingetragen wird eine Aufgabe in der Windows-Aufgabenplanung, die beim
Anmelden startet. Sie ist dem Autostart-Ordner überlegen: Sie überlebt es,
wenn der Ordner aufgeräumt wird, sie lässt sich zentral ansehen, und sie
startet die Brücke nach einem Absturz erneut. Geht das nicht — manche
Systeme sperren schtasks für normale Benutzer —, fällt es auf den
Autostart-Ordner zurück, und das steht dann auch da.

Kein Dienst und keine Administratorrechte: Ein Dienst liefe ohne angemeldeten
Benutzer und könnte weder tippen noch klicken noch den Bildschirm sehen —
genau das, wofür die Kopplung da ist.

Gestartet wird die Brücke ohne Fenster (`desktop_agent.py` über `pythonw`).
Wer das große Fenster will, startet JARVIS.bat zusätzlich.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "JARVIS-Bruecke.cmd"
TASK = "JARVIS Desktop Bridge"


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


def _schtasks(*args: str) -> tuple[int, str]:
    """schtasks aufrufen. Gibt (Rückgabewert, Ausgabe) — nie eine Ausnahme."""
    if os.name != "nt":
        return 1, "nur unter Windows"
    try:
        out = subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=25)
    except Exception as e:  # noqa: BLE001
        return 1, f"{e.__class__.__name__}: {e}"
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def task_enabled() -> bool:
    code, _ = _schtasks("/query", "/tn", TASK)
    return code == 0


def enabled() -> bool:
    """Eingerichtet ist eingerichtet — egal auf welchem der beiden Wege."""
    t = target()
    return bool(task_enabled() or (t and t.exists()))


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


def _exe() -> Path:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return pyw if pyw.exists() else Path(sys.executable)


def turn_on() -> str:
    if os.name != "nt":
        return ("Autostart gibt es so nur unter Windows. Unter Linux oder macOS "
                "nimm einen systemd-Dienst bzw. ein LaunchAgent.")

    # Erst die Aufgabenplanung: sie startet auch nach einem Absturz neu und
    # überlebt einen aufgeräumten Autostart-Ordner.
    cmd = f'"{_exe()}" "{ROOT / "desktop_agent.py"}"'
    code, out = _schtasks("/create", "/tn", TASK, "/tr", cmd, "/sc", "onlogon", "/f", "/rl", "limited")
    if code == 0:
        return (f"Eingetragen als Aufgabe {TASK!r} (startet beim Anmelden).\n"
                "JARVIS meldet sich ab dem nächsten Anmelden von selbst am Server.")

    d = startup_dir()
    if d is None:
        return f"Die Aufgabenplanung wollte nicht: {out.strip()[:200]}"
    d.mkdir(parents=True, exist_ok=True)
    t = d / NAME
    t.write_text(_launcher_text(), encoding="utf-8")
    return (f"Die Aufgabenplanung wollte nicht ({out.strip()[:120]}).\n"
            f"Stattdessen im Autostart-Ordner eingetragen: {t}")


def turn_off() -> str:
    done = []
    if task_enabled():
        code, out = _schtasks("/delete", "/tn", TASK, "/f")
        done.append(f"Aufgabe {TASK!r} entfernt." if code == 0
                    else f"Aufgabe ließ sich nicht entfernen: {out.strip()[:120]}")
    t = target()
    if t is not None and t.exists():
        t.unlink()
        done.append(f"Ausgetragen: {t}")
    if not done:
        return "War nicht eingetragen — es ändert sich nichts."
    return "\n".join(done)


def main() -> int:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if arg in ("an", "ein", "on", "enable"):
        print(turn_on())
    elif arg in ("aus", "off", "disable"):
        print(turn_off())
    else:
        t = target()
        if os.name != "nt":
            print("Autostart: unter diesem Betriebssystem nicht eingerichtet.")
        elif task_enabled():
            print(f"Autostart: EIN  (Aufgabenplanung: {TASK!r})")
            print("Ausschalten mit:  python autostart.py aus")
        elif t is not None and t.exists():
            print(f"Autostart: EIN  ({t})")
            print("Ausschalten mit:  python autostart.py aus")
        else:
            print("Autostart: AUS")
            print("Einschalten mit:  python autostart.py an")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
