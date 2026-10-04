"""
actions/deep_think.py — "Tief nachdenken": MIA gibt schwere Denkaufgaben an Claude
(Sonnet) ueber das Claude-Abo des Nutzers ab, per Claude Code im Textmodus.

Sicherheit: Claude laeuft OHNE Werkzeuge (--tools ""), ohne MCP, ohne Nutzer-
Einstellungen/Hooks, in einem leeren Temp-Ordner. Es kommt nur Text rein und
Text raus; eingeschleuste Anweisungen koennen so nichts auf dem Server ausloesen.

Einrichtung (einmalig, durch den Nutzer): `claude setup-token` ausfuehren und den
Token in TOKEN_PATH ablegen (chmod 600). Ohne Token meldet das Tool sich ehrlich ab.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

TOKEN_PATH = Path(os.environ.get("MIA_CLAUDE_TOKEN_FILE", "/root/.config/mia/claude_oauth_token"))
USAGE_PATH = Path("/root/.config/mia/deep_think_usage.json")
MODEL = os.environ.get("MIA_DEEP_MODEL", "sonnet")
MAX_PER_HOUR = int(os.environ.get("MIA_DEEP_MAX_PER_HOUR", "20"))
TIMEOUT_S = 240

_SYSTEM = (
    "Du bist der Denk-Kern von MIA, der Assistentin von Reyes Service (Handwerk/Innenausbau). "
    "Antworte auf Deutsch, klar, gruendlich und praxisnah. Du hast keine Werkzeuge; arbeite nur "
    "mit dem gegebenen Text. Erfinde keine Fakten, sondern benenne, was fehlt."
)


def _claude_bin() -> str | None:
    return shutil.which("claude") or next(
        (p for p in ("/root/.local/bin/claude",) if Path(p).exists()), None)


def _within_limit() -> bool:
    now = time.time()
    try:
        stamps = [t for t in json.loads(USAGE_PATH.read_text()) if now - t < 3600]
    except Exception:
        stamps = []
    if len(stamps) >= MAX_PER_HOUR:
        return False
    stamps.append(now)
    try:
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(stamps))
    except Exception:
        pass
    return True


def deep_think(parameters: dict, player=None, session_memory=None) -> str:
    question = str(parameters.get("question") or "").strip()
    context = str(parameters.get("context") or "").strip()
    if not question:
        return "Keine Frage angegeben."
    if not TOKEN_PATH.exists():
        return ("Tief nachdenken ist noch nicht eingerichtet (kein Claude-Abo-Token). "
                "Beantworte die Frage selbst so gut du kannst.")
    exe = _claude_bin()
    if not exe:
        return "Claude Code ist auf dem Server nicht installiert."
    if not _within_limit():
        return f"Stundenlimit fuer Tief nachdenken erreicht ({MAX_PER_HOUR}/h). Antworte selbst."

    prompt = question if not context else f"Kontext:\n{context[:20000]}\n\nAufgabe:\n{question}"
    # Saubere Umgebung: keine Gateway-Umleitung aus ~/.claude/settings.json, kein API-Key.
    env = {"HOME": "/root", "PATH": "/root/.local/bin:/usr/local/bin:/usr/bin:/bin",
           "CLAUDE_CODE_OAUTH_TOKEN": TOKEN_PATH.read_text().strip(), "LANG": "C.UTF-8"}
    cmd = [exe, "-p", "--setting-sources", "", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--model", MODEL, "--system-prompt", _SYSTEM,
           "--output-format", "json"]
    start = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="mia-deep-") as cwd:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=TIMEOUT_S, cwd=cwd, env=env)
    except subprocess.TimeoutExpired:
        return "Tief nachdenken hat zu lange gebraucht (Zeitlimit). Antworte selbst."
    try:
        data = json.loads(r.stdout)
        text, failed = str(data.get("result") or ""), bool(data.get("is_error"))
    except Exception:
        text, failed = (r.stdout or r.stderr)[:300], True
    if player:
        try:
            player.write_log(f"MIA: [deep_think] {time.time() - start:.1f}s ok={not failed}")
        except Exception:
            pass
    if failed or not text:
        return f"Tief nachdenken fehlgeschlagen: {text[:300]}"
    return text


TOOL = {
    "name": "deep_think",
    "description": (
        "Gibt eine schwierige Denkaufgabe an ein deutlich staerkeres Modell (Claude) ab und "
        "liefert dessen Antwort. Nutzen bei: Angeboten/Kalkulationen, Planung, Analysen, "
        "Texten mit Gewicht, kniffligen Fragen oder wenn du unsicher bist. NICHT fuer Smalltalk "
        "oder einfache Fakten. Dauert einige Sekunden bis Minuten. Gib im Feld context alle "
        "relevanten Fakten mit, das Modell sieht sonst nichts."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING", "description": "Die Aufgabe oder Frage"},
            "context": {"type": "STRING", "description": "Relevante Fakten, Gespraechsauszug, Zahlen"},
        },
        "required": ["question"],
    },
    "handler": deep_think,
}
