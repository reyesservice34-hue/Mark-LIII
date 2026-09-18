"""
Prüft in einem Durchgang, ob dieser Rechner mit dem Server reden kann.

    python check_connection.py      (oder PRUEFEN.bat doppelklicken)

Jeder Schritt einzeln, damit man sieht, wo es hakt — und nicht „geht nicht"
dasteht, wo drei verschiedene Ursachen in Frage kommen. Das Token wird dabei
nie ausgegeben, nur seine Form.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def ok(label: str, detail: str = "") -> None:
    print(f"  \033[32m✓\033[0m {label}" + (f"  —  {detail}" if detail else ""))


def bad(label: str, detail: str = "") -> None:
    print(f"  \033[31m✗\033[0m {label}" + (f"  —  {detail}" if detail else ""))


def main() -> int:
    import urllib.error
    import urllib.request

    from core.live_client import load_settings

    print("\n=== Verbindung prüfen ===\n")
    url, token = load_settings()

    # 1. Konfiguration
    if not url:
        bad("Serveradresse", "fehlt — python install_desktop.py")
        return 1
    ok("Serveradresse", url)
    if not token:
        bad("Maschinen-Token", "fehlt — python install_desktop.py")
        return 1
    # Ein echtes Token ist "jcc_" + 48 Zeichen. Wer stattdessen 64 Hex-Zeichen
    # hat, hat den Hash aus der Datenbank erwischt — der ist absichtlich nicht
    # umkehrbar, also hilft kein Nachschlagen, nur ein neues Token. Das gleich
    # hier zu sagen spart eine Runde gegen den Server.
    import re as _re
    if not token.startswith("jcc_"):
        looks_hash = bool(_re.fullmatch(r"[0-9a-f]{64}", token))
        bad("Maschinen-Token", f"{len(token)} Zeichen, beginnt mit {token[:6]}… — das ist kein Token")
        print("      Ein Token sieht so aus:  jcc_XXXXXXXX…  (52 Zeichen)")
        if looks_hash:
            print("      Deines sind 64 Hex-Zeichen — das ist der gespeicherte HASH, nicht das")
            print("      Token. Der Hash lässt sich nicht zurückrechnen; es braucht ein neues.")
        print("      Im Dashboard: Einstellungen → Machine tokens → Create token.")
        print("      Der Wert wird EINMAL angezeigt, direkt nach dem Erstellen.")
        print("      Dann:  python install_desktop.py")
        return 1
    ok("Maschinen-Token", f"{len(token)} Zeichen, beginnt mit {token[:8]}…")

    # 2. Ist der Server überhaupt da?
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=15) as res:
            health = json.loads(res.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        bad("Server erreichbar", f"{e.__class__.__name__}: {e}")
        print("\n  Läuft der Container? Stimmt die Adresse? Ist die Domain über HTTPS erreichbar?\n")
        return 1
    gw = (health.get("components", {}).get("agent_gateway") or {})
    ok("Server erreichbar", f"Status {health.get('status')}, Master Agent {gw.get('status')} "
                            f"({gw.get('mode')})")
    if gw.get("status") != "healthy":
        bad("Master Agent", gw.get("detail", "")[:90])
        print("      Ohne AI-Schlüssel antwortet er nicht. In command_center/.env eintragen.")

    # 3. Taugt das Token? Das ist die Frage, an der es meistens hängt.
    req = urllib.request.Request(url.rstrip("/") + "/api/voice/live/capabilities",
                                 headers={"X-Jarvis-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            caps = json.loads(res.read().decode("utf-8"))
        ok("Token akzeptiert", f"{caps.get('tools', 0)} Werkzeuge stehen bereit")
        if caps.get("available"):
            ok("Live-Leitung", caps.get("detail", ""))
        else:
            bad("Live-Leitung", caps.get("detail", ""))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            bad("Token akzeptiert", "401 — der Server kennt dieses Token nicht")
            print("      Häufigste Ursachen: nur ein Teil eingefügt, die Token-ID statt des")
            print("      Geheimnisses, oder inzwischen widerrufen.")
            print("      Neu erzeugen im Dashboard unter Einstellungen → Machine tokens,")
            print("      dann:  python install_desktop.py")
        elif e.code == 403:
            bad("Token akzeptiert", "403 — gültig, aber die Rolle reicht nicht")
            print("      Das Token braucht die Rolle 'operator', nicht 'viewer'.")
        else:
            bad("Token akzeptiert", f"HTTP {e.code}")
        return 1
    except Exception as e:  # noqa: BLE001
        bad("Token akzeptiert", f"{e.__class__.__name__}: {e}")
        return 1

    # 4. Audio — ohne das nützt die beste Leitung nichts.
    try:
        import sounddevice as sd
        ok("Mikrofon", str(sd.query_devices(kind="input")["name"])[:60])
        ok("Lautsprecher", str(sd.query_devices(kind="output")["name"])[:60])
    except ImportError:
        bad("Audio", "sounddevice fehlt — python setup.py")
    except Exception as e:  # noqa: BLE001
        bad("Audio", f"kein Gerät gefunden: {e}")

    try:
        import PyQt6  # noqa: F401
        ok("Fenster (PyQt6)", "vorhanden")
    except ImportError:
        bad("Fenster (PyQt6)", "fehlt — python setup.py; ohne das geht nur desktop_voice.py")

    print("\n  Alles Grüne steht. Starten mit:  JARVIS.bat\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
