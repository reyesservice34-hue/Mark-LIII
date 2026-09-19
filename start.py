"""
start.py — hol die neueste Version und starte JARVIS.

Damit niemand mehr Git-Befehle von Hand tippen muss. Das Skript zieht den
aktuellen Stand vom Branch, sagt dir in Klartext WAS geladen ist (welcher
Commit, ob die neue Anrede drin ist, welche Plugins gefunden werden), und
startet dann main.py. Wenn etwas nicht der neue Stand ist, siehst du es hier —
bevor JARVIS läuft, nicht erst an einer komischen WhatsApp-Nachricht.

Aufruf:  python start.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRANCH = "claude/agency-agent-installation-ransfo"


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _line(label: str, value: str) -> None:
    print(f"  {label:24} {value}")


def main() -> int:
    print("\n=== JARVIS — Start ===\n")

    # Läuft das aus einem Git-Klon?
    code, _ = _run(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        print("  Dieser Ordner ist kein Git-Klon. Der JARVIS, der dir antwortet,")
        print("  liegt dann woanders. Starte ihn aus dem Ordner, in dem 'git' geht,")
        print("  oder sag mir, aus welchem Ordner du ihn sonst startest.\n")
        return 1

    # Neuesten Stand holen (nur Netzwerkfehler sind hier fatal, Konflikte melden wir).
    print("  Hole den neuesten Stand …")
    _run(["git", "fetch", "origin", BRANCH])
    code, out = _run(["git", "checkout", BRANCH])
    if code != 0:
        _line("Achtung:", "konnte nicht auf den Branch wechseln —")
        print(f"    {out.splitlines()[0] if out else ''}")
    code, out = _run(["git", "pull", "--ff-only", "origin", BRANCH])
    if code != 0:
        _line("Hinweis:", "git pull kam nicht durch (lokale Änderungen?).")
        print(f"    {out.splitlines()[0] if out else ''}")

    # Was ist jetzt wirklich geladen?
    _, commit = _run(["git", "log", "--oneline", "-1"])
    _line("Geladener Stand:", commit or "unbekannt")

    cfg = HERE / "memory" / "config_manager.py"
    address_ok = cfg.exists() and "mein Herr" in cfg.read_text(encoding="utf-8", errors="replace")
    _line("Anrede 'mein Herr':", "JA — neuer Stand" if address_ok
          else "NEIN — alter Stand, der Pull ist nicht angekommen")

    # Welche Plugins findet JARVIS? So siehst du, dass Kalender/E-Mail/… da sind.
    try:
        sys.path.insert(0, str(HERE))
        from core.plugin_loader import discover_plugins
        reg = discover_plugins(HERE / "plugins", core_tool_names=set(), logger=lambda m: None)
        names = sorted(d["name"] for d in reg.get_tool_declarations())
        _line("Gefundene Plugins:", ", ".join(names) if names else "keine")
    except Exception as e:
        _line("Plugins:", f"konnten nicht geladen werden — {e}")

    if not address_ok:
        print("\n  Der neue Stand liegt NICHT vor. Ich starte trotzdem, aber es ist")
        print("  der alte JARVIS. Wenn 'git pull' oben einen Hinweis zeigte, ist das")
        print("  der Grund — schick mir die Zeile.\n")
    else:
        print("\n  Neuer Stand ist geladen. Starte JARVIS …\n")

    # JARVIS starten — dieselbe Python, mit der start.py läuft.
    return subprocess.call([sys.executable, str(HERE / "main.py")], cwd=str(HERE))


if __name__ == "__main__":
    sys.exit(main())
