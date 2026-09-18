"""
JARVIS auf dem eigenen Rechner einrichten.

    python install_desktop.py        (oder INSTALL-JARVIS.bat doppelklicken)

Fragt der Reihe nach, was gebraucht wird, schreibt config/api_keys.json und
prüft am Ende, ob der Server wirklich antwortet. Nichts davon muss man von
Hand tippen, und nichts wird stillschweigend überschrieben: was schon
dasteht, bleibt, wenn man Enter drückt.

Gebraucht wird nur das Maschinen-Token aus dem Dashboard. Die Desktop-App
redet über deinen Server — sie hat kein eigenes Gehirn und braucht deshalb
auch keinen eigenen Schlüssel.
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
    """Wirklich nachfragen, statt zu behaupten, es sei eingerichtet.

    Zwei Fragen, nicht eine: lebt der Server, und taugt das Token? Nur das
    erste zu prüfen und „fertig" zu melden hat den Nutzer schon einmal mit
    einem unbrauchbaren Token vor die Tür gesetzt.
    """
    if not url:
        return "keine Serveradresse angegeben"
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=10) as res:
            body = json.loads(res.read().decode("utf-8"))
        status = body.get("status", "?")
        mode = (body.get("components", {}).get("agent_gateway", {}) or {}).get("mode", "?")
    except Exception as e:  # noqa: BLE001
        return f"nicht erreichbar: {e.__class__.__name__}"

    head = f"Server erreichbar. Status {status}, Master Agent {mode}"
    if not token:
        return head + " — aber ohne Token. Ohne das geht die Desktop-App nicht."

    req = urllib.request.Request(base + "/api/voice/live/capabilities",
                                 headers={"X-Jarvis-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            caps = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return head + "\n  Aber: der Server kennt dieses Token nicht (401). Ein neues erzeugen."
        if e.code == 403:
            return head + "\n  Aber: das Token gilt, die Rolle reicht nicht (403). Gebraucht wird 'operator'."
        return head + f"\n  Aber: Token-Prüfung gab HTTP {e.code}."
    except Exception as e:  # noqa: BLE001
        return head + f"\n  Token-Prüfung fehlgeschlagen: {e.__class__.__name__}"

    line = head + f"\n  Token akzeptiert, {caps.get('tools', 0)} Werkzeuge stehen bereit."
    if not caps.get("available"):
        line += f"\n  Live-Leitung noch nicht bereit: {caps.get('detail', '')}"
    return line


def create_token(url: str, name: str) -> str:
    """Ein Maschinen-Token direkt hier erzeugen.

    Der Umweg über das Dashboard und ein zweites Terminal ist genau die
    Stelle, an der es in der Praxis scheitert: falsches Fenster, falscher
    Wert aus der Tabelle, halb kopiert. Hier wird einmal das Admin-Passwort
    gefragt, der Rest läuft von selbst — und das Passwort wird nirgends
    gespeichert.
    """
    import getpass
    import http.cookiejar
    import urllib.error
    import urllib.request

    say()
    say("  Kein Token vorhanden. Ich kann jetzt eines erzeugen —")
    say("  dafür brauche ich einmal deine Dashboard-Anmeldung.")
    say("  Sie wird nur für diesen Aufruf benutzt und nirgends gespeichert.")
    say("  (Leer lassen und Enter, wenn du es lieber selbst im Dashboard machst.)")
    try:
        user = input("     Benutzername [admin]: ").strip() or "admin"
        # getpass zeigt nichts an, auch keine Sternchen. Das irritiert beim
        # Tippen, deshalb steht es dabei — sonst hält man es für hängend.
        say("     (Das Passwort bleibt beim Tippen unsichtbar, das ist normal.)")
        pw = getpass.getpass("     Passwort: ")
    except (EOFError, KeyboardInterrupt):
        return ""
    if not pw:
        return ""

    base = url.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(path: str, body: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        with opener.open(req, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))

    try:
        login = post("/api/auth/login", {"username": user, "password": pw})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            say("     Anmeldung abgelehnt: Benutzername oder Passwort stimmt nicht.")
        elif e.code == 429:
            say("     Zu viele Versuche. Eine Minute warten, dann noch einmal.")
        else:
            say(f"     Anmeldung fehlgeschlagen (HTTP {e.code}).")
        return ""
    except Exception as e:  # noqa: BLE001
        say(f"     Der Server antwortet nicht: {e.__class__.__name__}: {e}")
        return ""

    role = (login.get("user") or {}).get("role", "")
    if role != "admin":
        say(f"     Dieses Konto hat die Rolle '{role}'. Token anlegen darf nur ein Administrator.")
        return ""

    actor = (name or "desktop").lower().replace(" ", "-")[:40] or "desktop"
    try:
        created = post("/api/auth/tokens", {"name": name or "Desktop", "actor": actor,
                                            "role": "operator"},
                       {"X-CSRF-Token": login.get("csrf_token", "")})
    except urllib.error.HTTPError as e:
        say(f"     Token konnte nicht erzeugt werden (HTTP {e.code}).")
        return ""
    except Exception as e:  # noqa: BLE001
        say(f"     Token konnte nicht erzeugt werden: {e.__class__.__name__}: {e}")
        return ""

    secret = created.get("secret", "")
    if secret:
        say(f"     Token erzeugt: {secret[:10]}… ({len(secret)} Zeichen) — eingetragen.")
    else:
        say("     Der Server hat kein Token zurückgegeben. Dann bitte im Dashboard anlegen.")
    return secret


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
    token = ask("Maschinen-Token", "beginnt mit jcc_ — Enter drücken, dann erzeuge ich eines",
                cfg.get("jarvis_gateway_token", ""), secret=True)
    # Nichts da oder offensichtlich falsch: gleich hier eines besorgen, statt
    # den Nutzer zwischen Fenstern hin- und herzuschicken.
    if url and (not token or not token.startswith("jcc_")):
        if token and not token.startswith("jcc_"):
            say()
            say("  Der bisherige Wert ist kein Token (ein Token beginnt mit jcc_).")
        made = create_token(url, cfg.get("desktop_device_name", "") or platform.node() or "Desktop")
        if made:
            token = made
    # Die Tabelle im Dashboard zeigt auch Kennungen und Hashes. Wer den falschen
    # Wert erwischt, sieht das sonst erst beim ersten Verbindungsversuch, und
    # dann sagt der Server nur „unbekannt".
    if token and not token.startswith("jcc_"):
        import re
        say()
        say("  Das ist kein Maschinen-Token. Ein Token sieht so aus: jcc_XXXXXXXX… (52 Zeichen).")
        if re.fullmatch(r"[0-9a-f]{64}", token):
            say("  Deines sind 64 Hex-Zeichen — das ist der gespeicherte Hash, nicht das Token.")
            say("  Der Hash lässt sich nicht zurückrechnen, es braucht ein neues Token.")
        say("  Im Dashboard: Einstellungen → Machine tokens → Create token.")
        say("  Der Wert erscheint EINMAL, direkt nach dem Erstellen — den kopieren.")
        say()
        again = ask("Maschinen-Token", "beginnt mit jcc_", "", secret=True)
        if again:
            token = again
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
    bold("3. Gemini-Schlüssel (optional, wird meistens nicht gebraucht)")
    say("  Die NEUE Desktop-App (JARVIS.bat) redet über deinen Server und braucht")
    say("  keinen eigenen Schlüssel. Dieser hier ist nur für die alte main.py mit")
    say("  ihrer eigenen Gemini-Sitzung.")
    say("  Falls doch: aistudio.google.com/apikey — er beginnt mit AIza")
    say("  Sonst einfach Enter drücken.\n")
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

    bold("Fertig")
    say("  JARVIS starten:      JARVIS.bat          (Fenster, Sprache, Fernsteuerung)")
    say("                       python jarvis_desktop.py")
    say()
    say("  Ohne Fenster:        python desktop_voice.py    (nur sprechen, Konsole)")
    say("                       python desktop_agent.py    (nur Fernsteuerung)")
    if cfg.get("gemini_api_key"):
        say()
        say("  Die alte App mit eigener Gemini-Sitzung: START-JARVIS.bat")
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
