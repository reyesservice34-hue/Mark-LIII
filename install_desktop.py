"""
JARVIS auf dem eigenen Rechner einrichten.

    python install_desktop.py        (oder INSTALL-JARVIS.bat doppelklicken)

Fragt der Reihe nach, was gebraucht wird, schreibt config/api_keys.json und
prüft am Ende, ob der Server wirklich antwortet. Nichts davon muss man von
Hand tippen, und nichts wird stillschweigend überschrieben: was schon
dasteht, bleibt, wenn man Enter drückt.

Zwei Betriebsarten, weil nicht jeder beides braucht:

  Fernsteuerung   Der Server bedient diesen PC. Braucht nur das
                  Maschinen-Token aus dem Dashboard.
  Volle App       Dazu die Sprachsteuerung am Rechner. Braucht zusätzlich
                  einen Gemini-Schlüssel, weil die Live-Sprachsitzung der
                  Desktop-App darauf läuft.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config" / "api_keys.json"


def say(text: str = "") -> None:
    print(text)


def bold(text: str) -> None:
    print(f"\n=== {text} ===\n")


def ask(label: str, hint: str = "", current: str = "", secret: bool = False) -> str:
    """Eine Frage. Enter behält den bisherigen Wert."""
    if current:
        shown = f"{current[:6]}…{current[-4:]}" if secret and len(current) > 12 else current
        say(f"  {label}  [bisher: {shown}]")
    else:
        say(f"  {label}  [noch leer]")
    if hint:
        say(f"     {hint}")
    try:
        value = input("     > ").strip()
    except (EOFError, KeyboardInterrupt):
        say()
        return current
    return value or current


def load_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            say("  Die vorhandene config/api_keys.json ist beschädigt — sie wird neu angelegt.")
    return {}


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.name != "nt":
        CONFIG.chmod(0o600)


def check_server(url: str, token: str) -> str:
    """Wirklich nachfragen, statt zu behaupten, es sei eingerichtet."""
    if not url:
        return "keine Serveradresse angegeben"
    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url.rstrip("/") + "/api/health")
        with urllib.request.urlopen(req, timeout=10) as res:
            body = json.loads(res.read().decode("utf-8"))
        status = body.get("status", "?")
        mode = (body.get("components", {}).get("agent_gateway", {}) or {}).get("mode", "?")
        if not token:
            return f"Server erreichbar (Status {status}, Master Agent {mode}) — aber ohne Token"
        return f"Server erreichbar. Status {status}, Master Agent {mode}"
    except Exception as e:  # noqa: BLE001
        return f"nicht erreichbar: {e.__class__.__name__}"


def main() -> int:
    bold("JARVIS auf diesem Rechner einrichten")
    say(f"  Betriebssystem: {platform.system()} {platform.release()}")
    say(f"  Python:         {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        say("\n  Python ist zu alt. Gebraucht wird 3.10 oder neuer — python.org/downloads")
        say("  Beim Installieren „Add python.exe to PATH\" ankreuzen.")
        return 1

    cfg = load_config()

    # ── 1. Abhängigkeiten ────────────────────────────────────────────────
    bold("1. Pakete installieren")
    say("  Das dauert beim ersten Mal ein paar Minuten.")
    try:
        input("  Enter zum Starten, oder Strg+C zum Abbrechen … ")
    except (EOFError, KeyboardInterrupt):
        say()
        return 1
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
                       check=True)
    except subprocess.CalledProcessError:
        say("\n  Die Installation der Pakete schlug fehl. Die Ausgabe oben sagt, woran es lag.")
        say("  Häufig: kein C++-Compiler für ein Paket, oder eine zu neue Python-Version.")
        return 1

    # ── 2. Verbindung zum Server ─────────────────────────────────────────
    bold("2. Verbindung zum Server")
    say("  Das Maschinen-Token legst du im Dashboard an:")
    say("  Einstellungen → Maschinen-Token → Token erstellen (Rolle: operator)")
    say("  Es wird nur einmal angezeigt.\n")

    url = ask("Adresse des Servers", "z. B. https://jarvis.jarvis-reyes.de",
              cfg.get("jarvis_gateway_url", ""))
    token = ask("Maschinen-Token", "aus dem Dashboard, wird nur einmal angezeigt",
                cfg.get("jarvis_gateway_token", ""), secret=True)
    name = ask("Name dieses Rechners", "frei wählbar, z. B. Buero-PC",
               cfg.get("desktop_device_name", platform.node() or "desktop"))

    if url:
        cfg["jarvis_gateway_url"] = url.rstrip("/")
    if token:
        cfg["jarvis_gateway_token"] = token
    if name:
        cfg["desktop_device_name"] = name
        cfg.setdefault("jarvis_actor", name.lower().replace(" ", "-"))
    cfg["control_plane_enabled"] = True

    # ── 3. Sprache am Rechner ────────────────────────────────────────────
    bold("3. Sprachsteuerung am Rechner (kann man überspringen)")
    say("  Die Live-Sprachsitzung der Desktop-App läuft über Gemini.")
    say("  Schlüssel holen: aistudio.google.com/apikey — er beginnt mit AIza")
    say("  Leer lassen, wenn dieser PC nur ferngesteuert werden soll.\n")
    gem = ask("Gemini-Schlüssel", "beginnt mit AIza", cfg.get("gemini_api_key", ""), secret=True)
    if gem and not gem.startswith("AIza"):
        say("\n  Das sieht nicht nach einem Gemini-Schlüssel aus (die beginnen mit AIza).")
        say("  Er wird trotzdem eingetragen — wenn es nicht läuft, ist das der Grund.")
    if gem:
        cfg["gemini_api_key"] = gem
    cfg.setdefault("os_system", platform.system())

    save_config(cfg)
    say(f"\n  Gespeichert in {CONFIG}")

    # ── 4. Nachsehen, ob es wirklich steht ───────────────────────────────
    bold("4. Prüfen")
    say(f"  {check_server(cfg.get('jarvis_gateway_url', ''), cfg.get('jarvis_gateway_token', ''))}")

    has_voice = bool(cfg.get("gemini_api_key"))
    bold("Fertig")
    if has_voice:
        say("  Volle App starten:      START-JARVIS.bat  (oder: python start.py)")
        say("  Nur Fernsteuerung:      python desktop_agent.py")
    else:
        say("  Ohne Gemini-Schlüssel läuft die Fernsteuerung, nicht die Sprache am Rechner:")
        say("      python desktop_agent.py")
        say("  Danach steht dieser PC im Dashboard unter Desktop und der Server kann ihn bedienen.")
    say()
    say("  Das Token steht in config/api_keys.json — die Datei ist in .gitignore und")
    say("  gehört nirgendwo anders hin.")
    say()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(1)
