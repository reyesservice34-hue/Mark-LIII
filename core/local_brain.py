"""
core/local_brain.py — 100% lokaler Denk-/Sprach-Pfad fuer MIA.

Ersetzt main.py's Gemini-Live-Loop NICHT direkt (zu riskant, live-System),
sondern laeuft daneben: Ollama fuers Denken/Tool-Calling, Speaches (lokal,
Whisper + Piper/Kerstin) fuer Sprache. Nutzt dieselbe ActionRegistry wie
main.py (discover_actions), also exakt dieselben Tool-Handler - kein
Doppel-Code.

Aktivierung ueber config/api_keys.json:  "llm_provider": "ollama"
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.action_loader import discover_actions  # noqa: E402
from core.understanding import clarify  # noqa: E402
from memory.memory_manager import (  # noqa: E402
    load_memory,
    format_memory_for_prompt,
    relevant_conversation_memory,
)
from memory.behavior_memory import behavior_context  # noqa: E402
from memory.config_manager import get_personality_mode, PERSONALITY_MODES  # noqa: E402

CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
# Dieselbe Datei, die main.py's Gemini-Pfad laedt (core/prompt.txt) - NICHT
# ein eigenes, aermeres Root-prompt.txt. So bekommt der lokale Pfad automatisch
# alle dort schon bewaehrten Regeln, u.a. die Anti-Fabrikations-Regel
# ("Never claim an action that didn't happen") aus commit 2c52eed.
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"

OLLAMA_URL = "http://127.0.0.1:11434"       # Ollama laeuft bereits lokal AUF dem Brain-Server (15GB RAM, 8 Kerne)
SPEACHES_URL = "http://127.0.0.1:8005"      # mia-speaches-kerstin, lokal auf diesem (Brain) Server
OLLAMA_MODEL = "qwen3:1.7b"                 # 2026-09-29: tried qwen3:8b for more reliable tool-calling (e.g.
                                             # self_dev) — reverted: >180s with no response on this CPU-only
                                             # hardware, unusable for interactive chat. 1.7b stays the default;
                                             # a real fix for complex tool-calling needs either GPU hardware or
                                             # Gemini once its quota recovers, not a bigger CPU-bound local model.


import re as _re_top
import dateparser as _dateparser

# Wortgrenzen-Regex nur zum AUFFINDEN eines Datumsworts im rohen Text; die
# eigentliche Berechnung macht dateparser (ausgereifte, battle-tested Bibliothek
# fuer natuerlichsprachliche Datumsangaben inkl. Deutsch) statt eigener Logik -
# das kleine Modell hat 'morgen'/'montag' beim eigenen Rechnen verwechselt,
# deshalb wird hier NICHT mehr geraten, sondern deterministisch vorgegeben.
_DATE_WORD_RE = _re_top.compile(
    r'\b(übermorgen|uebermorgen|heute|morgen|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b',
    _re_top.IGNORECASE,
)
_DATEPARSER_SETTINGS = {"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False}


def _extract_explicit_date(raw_text: str) -> tuple[str, str] | None:
    """Sucht im ROHEN Nutzertext nach einem relativen Datumswort und laesst
    dateparser das exakte Zieldatum berechnen. Gibt (gefundenes_wort,
    'YYYY-MM-DD') zurueck, oder None wenn nichts gefunden.
    """
    m = _DATE_WORD_RE.search(raw_text)
    if not m:
        return None
    word = m.group(1).lower()
    # ASCII-Transliteration normalisieren: das Modell schreibt oft 'uebermorgen'
    # statt 'übermorgen' - dateparser kennt nur die Umlaut-Form und gab sonst
    # None zurueck, wodurch der Datums-Fakt still NICHT injiziert wurde.
    word_norm = word.replace("ue", "ü").replace("ae", "ä").replace("oe", "ö")
    parsed = _dateparser.parse(word_norm, languages=["de"], settings=_DATEPARSER_SETTINGS)
    if parsed is None:
        return None
    return word, parsed.strftime("%Y-%m-%d")


_BACKGROUND_REQUEST_RE = _re_top.compile(
    r"\b(im hintergrund|kümmere dich|kümmer dich|kuemmere dich|kuemmer dich|"
    r"arbeite daran|später fertig|spaeter fertig)\b",
    _re_top.IGNORECASE,
)
_SCHEDULE_REQUEST_RE = _re_top.compile(
    r"\b(prüfe|pruefe|kontrolliere|checke|überwache|ueberwache|beobachte|"
    r"nachfassen|fass nach|follow[- ]?up|arbeite weiter|mach weiter|"
    r"kümmere dich|kümmer dich|kuemmere dich|kuemmer dich)\b",
    _re_top.IGNORECASE,
)
_ABS_DATE_RE = _re_top.compile(
    r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\.\d{1,2}\.(?:\d{4})?)\b"
)
_CLOCK_RE = _re_top.compile(
    r"(?:\bum\s+([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\s*(?:uhr)?\b|"
    r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:uhr)?\b)",
    _re_top.IGNORECASE,
)
_RELATIVE_DELAY_RE = _re_top.compile(
    r"\bin\s+(\d+|ein(?:e|en)?|zwei|drei|vier|fünf|fuenf|sechs|sieben|acht|neun|zehn|elf|zwölf|zwoelf)\s+"
    r"(minute(?:n)?|minuten?|stunde(?:n)?|stunden?|tag(?:e|en)?)\b",
    _re_top.IGNORECASE,
)
_DAYPART_RE = _re_top.compile(
    r"\b(früh|frueh|morgens|vormittag|vormittags|mittag|nachmittag|nachmittags|abend|abends|nacht|nachts)\b",
    _re_top.IGNORECASE,
)
_DAYPART_HOUR = {
    "früh": 8, "frueh": 8, "morgens": 8,
    "vormittag": 10, "vormittags": 10,
    "mittag": 12,
    "nachmittag": 15, "nachmittags": 15,
    "abend": 19, "abends": 19,
    "nacht": 21, "nachts": 21,
}


def _extract_schedule_at(raw_text: str) -> str | None:
    """Return an offset-aware ISO time for explicit future follow-up timing."""
    text = str(raw_text or "")
    now = _dt.datetime.now().astimezone()
    local_tz = now.tzinfo

    # Relative delays such as "in zwei Stunden" or "in 30 Minuten".
    relative = _RELATIVE_DELAY_RE.search(text)
    if relative:
        parsed = _dateparser.parse(
            relative.group(0),
            languages=["de"],
            settings={**_DATEPARSER_SETTINGS, "RELATIVE_BASE": now.replace(tzinfo=None)},
        )
        if parsed is not None:
            due = parsed.replace(tzinfo=local_tz)
            if due > now:
                return due.isoformat(timespec="seconds")

    date_hit = _extract_explicit_date(text)
    date_value = date_hit[1] if date_hit else ""
    date_word = date_hit[0].lower() if date_hit else ""
    if not date_value:
        match = _ABS_DATE_RE.search(text)
        if match:
            parsed = _dateparser.parse(
                match.group(1),
                languages=["de"],
                settings=_DATEPARSER_SETTINGS,
            )
            if parsed is not None:
                date_value = parsed.strftime("%Y-%m-%d")

    if not date_value:
        return None

    clock = _CLOCK_RE.search(text)
    if clock:
        hour = int(clock.group(1) or clock.group(3))
        minute = int(clock.group(2) or clock.group(4) or 0)
    else:
        daypart = _DAYPART_RE.search(text)
        if daypart:
            hour = _DAYPART_HOUR[daypart.group(1).lower()]
            minute = 0
        elif date_word and date_word not in {"heute"}:
            # Explicit future day without a clock: autonomous follow-ups run at
            # a predictable local business-morning default rather than asking
            # the user for an unnecessary time.
            hour, minute = 9, 0
        else:
            return None

    due = _dt.datetime.fromisoformat(
        f"{date_value}T{hour:02d}:{minute:02d}:00"
    ).replace(tzinfo=local_tz)
    if due <= now:
        return None
    return due.isoformat(timespec="seconds")


def _extract_background_goal(raw_text: str) -> str:
    """Extract the actual goal from an explicit background-work request."""
    text = " ".join(str(raw_text or "").split()).strip()
    if ":" in text:
        head, tail = text.split(":", 1)
        if _BACKGROUND_REQUEST_RE.search(head) and tail.strip():
            return tail.strip()
    cleaned = _BACKGROUND_REQUEST_RE.sub(" ", text, count=1)
    cleaned = _re_top.sub(
        r"(?i)^\s*(bitte|darum|dass|und|jetzt)\b[:,\s-]*",
        "",
        cleaned,
        count=1,
    ).strip(" :-,")
    return cleaned or text


def _extract_scheduled_goal(raw_text: str) -> str:
    """Remove the explicit trigger time from the later task's actual goal."""
    goal = _extract_background_goal(raw_text)
    goal = _RELATIVE_DELAY_RE.sub(" ", goal, count=1)
    goal = _DATE_WORD_RE.sub(" ", goal, count=1)
    goal = _ABS_DATE_RE.sub(" ", goal, count=1)
    goal = _CLOCK_RE.sub(" ", goal, count=1)
    goal = _DAYPART_RE.sub(" ", goal, count=1)
    goal = _re_top.sub(r"(?i)\b(am|für|fuer)\b(?=\s*[,;:-])", " ", goal)
    goal = _re_top.sub(r"(?i)\b(nochmal|noch einmal|später|spaeter)\b", " ", goal)
    goal = " ".join(goal.split()).strip(" :-,")
    return goal or _extract_background_goal(raw_text)


from memory.memory_manager import search_memory as _search_memory  # noqa: E402

# ── Inline-Tools: existieren nur hier, nicht als actions/*.py Datei -
#    analog zu main.py's eigenen inline tools (recall_memory, save_memory, etc.
#    sind dort auch inline, nicht discover_actions()-basiert). Ohne dieses Tool
#    kann MIA im lokalen Pfad NICHT auf ihr Gedaechtnis zugreifen und erfindet
#    stattdessen plausibel klingende, falsche Antworten - das ist der Bug, der
#    hier behoben wird.
_INLINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "Durchsucht das gespeicherte Langzeitgedaechtnis (Fakten, Notizen, "
                "vergangene Ereignisse, was zuletzt geaendert/gelernt wurde). "
                "IMMER aufrufen bevor du behauptest etwas nicht zu wissen, oder bevor "
                "du etwas ueber vergangene Ereignisse/Aenderungen/dich selbst sagst - "
                "nie aus dem Kopf raten oder erfinden."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Suchbegriff"}},
                "required": ["query"],
            },
        },
    },
]


# search_knowledge ist KEIN Inline-Tool mehr: es lebt jetzt als regulaere Action
# in actions/knowledge_search.py und wird via discover_actions() von JEDEM Pfad
# (main.py/Gemini und diesem lokalen Pfad) automatisch gefunden.

# Die uebrigen Inline-Tools von main.py, die auch hier sinnvoll sind (kein UI noetig):
_INLINE_TOOLS.extend([
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Speichert einen wichtigen Fakt ueber den Nutzer dauerhaft im Langzeitgedaechtnis (Vorlieben, Namen, Ziele, Kontext).",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "identity | preferences | projects | relationships | wishes | notes"},
                    "key": {"type": "string", "description": "kurzer Schluessel, z.B. lieblingsfarbe"},
                    "value": {"type": "string", "description": "der Fakt"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "Liefert CPU-, RAM-, Festplatten-Auslastung und Laufzeit des Servers, auf dem MIA laeuft.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo",
            "description": "Macht MIAs letzte eigene Aenderung rueckgaengig (verschobene/erstellte/geschriebene Dateien, geaenderte Einstellungen). action='list' zeigt, was rueckgaengig gemacht werden kann.",
            "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "'last' (Standard) oder 'list'"}}},
        },
    },
])


def _as_text(v) -> str:
    # Das kleine Modell liefert Argumente gelegentlich als dict/Liste statt String
    # (z.B. {"query": {"Thema": "Ehrlichkeit"}}) -> robust zu Text machen statt zu crashen.
    if isinstance(v, dict):
        v = " ".join(str(x) for x in v.values())
    elif isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    return str(v or "").strip()


def _run_inline_tool(name: str, args: dict) -> str | None:
    """Gibt das Ergebnis zurueck wenn `name` ein Inline-Tool ist, sonst None
    (dann soll der Aufrufer bei der normalen ActionRegistry weitersuchen)."""
    if name == "recall_memory":
        return _search_memory(_as_text(args.get("query", "")), limit=8)
    if name == "save_memory":
        from memory.memory_manager import update_memory
        cat = _as_text(args.get("category")) or "notes"
        key = _as_text(args.get("key")); val = _as_text(args.get("value"))
        if not key or not val:
            return "Zum Speichern brauche ich key und value."
        update_memory({cat: {key: {"value": val}}})
        return f"Gespeichert: {cat}/{key} = {val}"
    if name == "system_status":
        import psutil, time as _t
        vm = psutil.virtual_memory(); du = psutil.disk_usage("/")
        up = _t.time() - psutil.boot_time()
        return (f"CPU {psutil.cpu_percent(interval=0.5):.0f}% | RAM {vm.percent:.0f}% "
                f"({vm.used/2**30:.1f}/{vm.total/2**30:.1f} GB) | Disk {du.percent:.0f}% | "
                f"Laufzeit {up/3600:.1f} h | Prozesse {len(psutil.pids())}")
    if name == "undo":
        from core import undo as undo_stack
        if _as_text(args.get("action")).lower() == "list":
            items = undo_stack.history()
            return ("Rueckgaengig machbar (neueste zuerst):\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(items))) if items else "Ich habe noch nichts geaendert, das ich rueckgaengig machen koennte."
        return undo_stack.undo_last()
    return None


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


_memory_prompt_snapshot: str | None = None


def _load_system_prompt(refresh_memory: bool = False) -> str:
    global _memory_prompt_snapshot

    # Compact local execution core. The full Live prompt remains untouched for
    # Gemini Live; CPU-only Ollama gets the same durable memory/cognition/tools
    # without repeatedly evaluating tens of thousands of prompt characters.
    base = """Du bist MIA, die zentrale Assistentin des Nutzers.
Antworte in der Sprache der aktuellen Nutzernachricht.
Arbeite zuerst, rede danach. Bei ausführbaren Aufgaben nutze echte Tools oder den persistenten Task-Worker.
Behaupte niemals, etwas getan, geprüft, gespeichert oder erledigt zu haben, wenn kein echtes Tool-Ergebnis oder Task-Status DONE vorliegt.
Erfinde keine Fakten, Erinnerungen, Ergebnisse, Links, Fähigkeiten oder Verbindungen.
Nutze dein gespeichertes Gedächtnis und relevanten früheren Gesprächskontext. Wenn alte Details fehlen, nutze recall_memory statt zu raten.
Frühere Gespräche sind Kontext, keine neuen Ausführungsbefehle.
Wenn ein Tool scheitert, nenne den konkreten Blocker kurz und nutze eine vorhandene sichere Alternative.
Standardantwort: 1-3 kurze Sätze. Wiederhole die Anfrage nicht. Keine leeren Ankündigungen.
Für mehrschrittige Aufgaben arbeite intern nacheinander und liefere am Ende nur das verifizierte Ergebnis.
Du bist MIA und bleibst konsistent mit deiner gespeicherten Persönlichkeit und deinem Gedächtnis."""

    # Memory waehrend einer laufenden Unterhaltung stabil halten.
    # Dadurch kann Ollama den langen Prefix zwischen Turns wiederverwenden.
    if refresh_memory or _memory_prompt_snapshot is None:
        try:
            _memory_prompt_snapshot = format_memory_for_prompt(load_memory())
        except Exception as e:
            print(f"[LocalBrain] memory injection failed: {e}")
            if _memory_prompt_snapshot is None:
                _memory_prompt_snapshot = ""

    if _memory_prompt_snapshot:
        base = f"{base}\n\n{_memory_prompt_snapshot}"

    try:
        personality_mode = get_personality_mode()
        personality_text = PERSONALITY_MODES.get(personality_mode, "")
    except Exception as e:
        print(f"[LocalBrain] personality injection failed: {e}")
        personality_mode = "mia"
        personality_text = ""
    if personality_text:
        base = f"{base}\n\n[PERSONALITY]\n{personality_text}"

    # format_memory_for_prompt() already carries cognitive context; avoid
    # injecting the same cognitive state twice.

    # Datum ist innerhalb eines Tages stabil und zerstoert deshalb den Prefix-Cache nicht.
    now = _dt.datetime.now()
    weekday_de = [
        "Montag", "Dienstag", "Mittwoch", "Donnerstag",
        "Freitag", "Samstag", "Sonntag"
    ][now.weekday()]

    return (
        f"{base}\n\nAKTUELLES DATUM: {weekday_de}, {now.strftime('%d.%m.%Y')} "
        f"(Tool-Format: {now.strftime('%Y-%m-%d')})."
    )

def _record_cognitive_turn(user_text: str, assistant_text: str) -> None:
    try:
        from brain.cognition import observe_turn
        observe_turn(user_text, assistant_text, "de-DE")
    except Exception as e:
        print(f"[LocalBrain] cognitive turn recording failed: {e}")


def _lower_types(schema):
    """Gemini nutzt GROSSE Typnamen (OBJECT/STRING); Ollama erwartet klein."""
    if isinstance(schema, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _lower_types(v))
                for k, v in schema.items()}
    if isinstance(schema, list):
        return [_lower_types(v) for v in schema]
    return schema


_registry = None


def get_registry():
    global _registry
    if _registry is None:
        _registry = discover_actions(
            actions_dir=BASE_DIR / "actions",
            reserved_names=set(),
            logger=lambda msg: print(f"[LocalBrain][Actions] {msg}"),
        )
    return _registry


def build_ollama_tools() -> list[dict]:
    declarations = get_registry().get_tool_declarations()
    action_tools = [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": _lower_types(d["parameters"]),
            },
        }
        for d in declarations
    ]
    return action_tools + _INLINE_TOOLS


# Regelbasierter Router statt LLM-Call: verzoegerungsfrei (keine Inferenz noetig)
# UND zuverlaessiger fuer klare Faelle als ein 3B-Modell beim Klassifizieren.
# Pro Tool eine Stichwortliste, die typische deutsche Formulierungen abdeckt.
_TOOL_KEYWORDS: dict[str, list[str]] = {
    "weather_report": ["wetter", "regen", "sonne", "temperatur", "grad", "schnee", "wettervorhersage"],
    "web_search": ["suche", "google", "such mal", "im internet", "recherchier", "finde heraus", "nachrichten", "news"],
    "browser_control": ["browser", "webseite", "öffne die seite", "tab", "internet seite", "url"],
    "open_app": ["öffne", "starte", "app öffnen", "programm öffnen", "launch"],
    "file_controller": ["datei", "ordner", "verzeichnis", "speicher unter", "lösch die datei", "umbenennen"],
    "file_processor": ["datei verarbeiten", "konvertier", "analysier die datei", "pdf", "dokument"],
    "flight_finder": ["flug", "flüge", "flughafen", "fliegen nach"],
    "game_updater": ["spiel update", "game update", "steam"],
    "reminder": ["erinner", "erinnerung", "reminder", "termin", "wecker"],
    "code_helper": ["code", "programmier", "funktion schreib", "bug", "python", "javascript", "script"],
    "dev_agent": ["entwickl", "deploy", "server", "repository", "git"],
    "ask_command_center": ["command center", "kommandozentrale", "system status"],
    "agency_agent": [
        "agententeam", "team von agenten", "mehrere agenten", "delegier", "delegiere",
        "spezialisten einsetzen", "agentur", "multi-agent", "multi agent",
    ],
    "agent_manager": [
        "agent erstellen", "agenten erstellen", "neuen agent", "neue agentin",
        "spezialagent", "spezialisten anlegen", "agent aktualisieren", "agent ändern",
        "agent verwalten", "agenten verwalten", "agentenliste",
    ],
    "set_personality": [
        "persönlichkeit", "persoenlichkeit", "verhalte dich", "sei lockerer",
        "sei wärmer", "sei waermer", "sei professioneller", "wie jarvis",
    ],
    "self_dev": [
        "verbesser dich", "trainier dich", "ändere dich selbst", "aendere dich selbst",
        "dashboard ändern", "dashboard aendern", "kommandozentrale ändern",
        "werkzeug erstellen", "neues werkzeug", "plugin schreiben",
    ],
    "clone_and_learn": ["github", "gitlab", "klon", "clone", "repo", "repository"],
    "install_cloned_repo": ["installier", "installieren", "setup", "dependencies", "abhängigkeiten", "npm", "pip"],
    "plugin_manager": ["plugin", "erweiterung installieren", "plugin installieren", "addon", "add-on"],
    "read_link": ["http://", "https://", "www.", "link", "seite", "webseite", "url"],
    "recall_memory": [
        "erinnerst du dich", "weißt du noch", "was hast du gelernt", "was wurde geändert",
        "was hast du heute", "erinnere dich", "weißt du was", "kennst du",
    ],
    "save_memory": ["merk dir", "merke dir", "speicher", "notier", "vergiss nicht dass", "ich heiße", "mein name ist", "ich mag", "ich bin"],
    "system_status": ["cpu", "ram", "speicher voll", "auslastung", "wie geht es dir", "systemstatus", "server status", "laufzeit"],
    "undo": ["rückgängig", "mach das rückgängig", "undo", "zurücknehmen", "nein nicht das"],
    # Nur explizite Hintergrund-/Monitoring-Absicht. Direkte Befehle wie
    # "erledige das" oder "kümmer dich" sollen sofort ausgeführt werden.
    "background_task": [
        "im hintergrund", "hintergrundaufgabe", "arbeite im hintergrund",
        "später fertig", "spaeter fertig", "offene aufgaben", "ergebnis der aufgabe",
        "warteschlange", "nachfassen", "fass nach", "follow-up",
        "prüfe später", "pruefe spaeter", "später prüfen", "spaeter pruefen",
        "überwache", "ueberwache", "beobachte",
    ],
    "search_knowledge": [
        "was weißt du über", "was kannst du", "welche skills", "erkläre mir", "erklär mir",
        "wie funktioniert", "wie geht", "was ist", "strategie", "tipps", "vorgehen",
        "business", "finanz", "marketing", "vertrieb", "führung", "ethik", "regeln",
    ],
}


def _matching_tool_names(clear_text: str, tool_names: list[str]) -> list[str]:
    """Verzoegerungsfreier Stichwort-Check statt LLM-Call. Gibt NUR die
    tatsaechlich passenden Tool-Namen zurueck (meist 0-2 statt aller 13) -
    das haelt den Ollama-Kontext klein, was auf dieser CPU-only Hardware der
    Hauptfaktor fuer die Antwortzeit ist. Leere Liste = reine Konversation,
    keine Tools noetig.
    """
    text_lower = clear_text.lower()
    matches = []
    for tool_name in tool_names:
        for kw in _TOOL_KEYWORDS.get(tool_name, []):
            if kw in text_lower:
                matches.append(tool_name)
                break
    return matches


def _needs_tools(clear_text: str, tool_names: list[str]) -> bool:
    return bool(_matching_tool_names(clear_text, tool_names))


def _deterministic_worker_fallback(clear_text: str, tools: list[dict], registry):
    """Execute only safe/read-only tools deterministically when the small local
    model refuses to emit a tool_call. Returns (name, args, result) or None."""
    names = {t.get("function", {}).get("name") for t in tools}
    low = clear_text.lower()
    task_text = clear_text.split("AUFGABE:", 1)[-1].strip()

    if "system_status" in names and any(k in low for k in (
        "systemstatus", "system status", "cpu", "ram", "auslastung", "laufzeit"
    )):
        args = {}
        result = _run_inline_tool("system_status", args)
        return "system_status", args, result

    if "recall_memory" in names and any(k in low for k in (
        "erinner", "weisst du", "weißt du", "vorhin", "gestern", "damals",
        "letztes mal", "was habe ich", "was hab ich"
    )):
        args = {"query": task_text}
        result = _run_inline_tool("recall_memory", args)
        return "recall_memory", args, result

    if "search_knowledge" in names and any(k in low for k in (
        "was weisst du", "was weißt du", "wie funktioniert", "erklaer", "erklär",
        "strategie", "wissen", "kenntnis", "skills", "vorgehen"
    )):
        args = {"query": task_text}
        result = registry.run("search_knowledge", args, ctx={})
        return "search_knowledge", args, result

    if "weather_report" in names and any(k in low for k in (
        "wetter", "temperatur", "regen", "sonne", "schnee"
    )):
        import re as _re_worker
        m = _re_worker.search(
            r"(?i)wetter.*?in\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .-]{1,50}?)(?:\s+(?:mit|und|heute|morgen|aktuell)|[,.]|$)",
            task_text,
        )
        if m:
            args = {"city": m.group(1).strip(), "time": "today"}
            result = registry.run("weather_report", args, ctx={})
            return "weather_report", args, result

    if "web_search" in names and any(k in low for k in (
        "suche", "recherch", "internet", "aktuell", "finde heraus", "nachrichten", "news"
    )):
        args = {"query": task_text, "mode": "search"}
        result = registry.run("web_search", args, ctx={})
        return "web_search", args, result

    return None


def chat(user_text: str, history: list[dict] | None = None, skip_clarify: bool = False,
         routing_text: str | None = None, exclude_tools: set[str] | None = None) -> tuple[str, list[dict]]:
    """Ein Gespraechsturn, komplett lokal ueber Ollama + lokale Tool-Ausfuehrung.

    Jede Eingabe (Chat-Tipp ODER STT-Transkript) laeuft zuerst durch die
    Verstehens-Schicht (core.understanding.clarify): Fuellwoerter, abgebrochene
    Saetze und unklare Formulierungen werden zu einem klaren Befehls-/Fragesatz
    umgeformt, BEVOR MIA ihn verarbeitet. So versteht sie auch fragmentierte
    oder dialektgefaerbte Eingaben zuverlaessig.
    """
    all_tools = build_ollama_tools()
    tool_names = [t["function"]["name"] for t in all_tools]

    worker_mode = bool(globals().get("_FORCE_TOOL_EXECUTION", False))
    if worker_mode:
        now = _dt.datetime.now()
        system_prompt = (
            "Du bist MIAs autonomer Hintergrund-Ausfuehrungsmotor. "
            "Deine Aufgabe ist echte Ausfuehrung, nicht Ankuendigung. "
            "Nutze fuer operative Aufgaben die angebotenen Tools. "
            "Behaupte niemals, etwas getan oder geprueft zu haben, wenn kein passendes "
            "Tool-Ergebnis vorliegt. Wenn ein Tool scheitert, nutze ein geeignetes "
            "Alternativ-Tool, sofern vorhanden. Respektiere Fehler, Bestaetigungsgrenzen "
            "und Sicherheitsregeln der Tools. Antworte am Ende kurz auf Deutsch mit dem "
            "konkreten, nachpruefbaren Ergebnis. Keine Versprechen wie 'ich werde'. "
            f"Aktuelles Datum: {now.strftime('%Y-%m-%d')}."
        )
    else:
        system_prompt = _load_system_prompt(refresh_memory=not bool(history))

    clear_text = user_text if skip_clarify else clarify(user_text)

    # Explicit background-work language must create REAL work immediately.
    # Do not burn a local-model round just to decide whether "kümmer dich darum"
    # means a background task: that latency caused the exact false-activity
    # behaviour this subsystem is meant to prevent.
    schedule_at = _extract_schedule_at(clear_text)
    if not worker_mode and schedule_at and _SCHEDULE_REQUEST_RE.search(clear_text):
        registry = get_registry()
        if registry.has("background_task"):
            goal = _extract_scheduled_goal(clear_text)
            args = {"action": "schedule", "goal": goal, "run_at": schedule_at}
            result = registry.run("background_task", args, ctx={})
            messages = [{"role": "system", "content": system_prompt}] + (history or [])
            messages.append({"role": "user", "content": clear_text})
            messages.append({"role": "assistant", "content": "", "tool_calls": [{
                "type": "function",
                "function": {"name": "background_task", "arguments": args},
            }]})
            messages.append({"role": "tool", "content": str(result)})
            answer = str(result)
            _record_cognitive_turn(user_text, answer)
            return answer, messages

    if not worker_mode and _BACKGROUND_REQUEST_RE.search(clear_text):
        registry = get_registry()
        if registry.has("background_task"):
            goal = _extract_background_goal(clear_text)
            args = {"action": "create", "goal": goal}
            result = registry.run(
                "background_task",
                args,
                ctx={},
            )
            messages = [{"role": "system", "content": system_prompt}] + (history or [])
            messages.append({"role": "user", "content": clear_text})
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "type": "function",
                    "function": {
                        "name": "background_task",
                        "arguments": args,
                    },
                }],
            })
            messages.append({"role": "tool", "content": str(result)})
            answer = str(result)
            _record_cognitive_turn(user_text, answer)
            return answer, messages

    # 2026-09-30: history used to REPLACE the system prompt entirely when given, silently
    # dropping the persona + memory injection (_load_system_prompt()) for any caller that
    # passed prior turns — exactly what "remember like a human" needs. System prompt now
    # always leads; passed history is prior conversation turns appended after it.
    messages = [{"role": "system", "content": system_prompt}] + (history or [])
    # Verhaltensgedaechtnis greift bei jeder Eingabe ZUERST - vor Recall und Tools. Hintergrund-
    # Worker (Agenten) befolgen die Regeln ebenfalls, lernen aber nicht aus Aufgabentexten.
    behavior_block = behavior_context(clear_text, learn=not worker_mode)
    if behavior_block:
        messages.append({"role": "system", "content": behavior_block})
    if not worker_mode:
        try:
            recalled_context = relevant_conversation_memory(clear_text, limit=6, max_chars=2800)
        except Exception as e:
            print(f"[LocalBrain] automatic conversation recall failed: {e}")
            recalled_context = ""
        if recalled_context:
            messages.append({"role": "system", "content": recalled_context})
    messages.append({"role": "user", "content": clear_text})

    # Gleiche Optimierung wie in chat_stream_and_speak: Datum deterministisch
    # vorgeben und NUR die per Stichwort passenden Tools mitschicken. Der
    # Hintergrund-Worker lief mit allen 19 Tool-Schemas im Kontext in den
    # 120s-Timeout (CPU-only Prompt-Eval).
    date_hit = _extract_explicit_date(user_text)
    if date_hit:
        word, resolved = date_hit
        messages.append({"role": "system", "content": f"FAKT (nicht selbst nachrechnen, direkt uebernehmen): '{word}' bedeutet hier exakt das Datum {resolved}. Falls du ein Tool mit einem 'date'-Feld aufrufst, nutze GENAU '{resolved}'."})
    route_text = routing_text if routing_text is not None else clear_text
    matched_names = set(_matching_tool_names(route_text, tool_names))
    if exclude_tools:
        matched_names.difference_update(exclude_tools)
    _lower = route_text.lower().strip()

    # Worker-Routing: explizite Systemstatus-Anfragen muessen deterministisch
    # auf system_status gehen. Ein generisches dev_agent/web_search daneben
    # macht das kleine lokale Modell unnoetig unentschlossen.
    if worker_mode and "system_status" in tool_names and any(
        k in _lower for k in ("systemstatus", "cpu", "ram", "auslastung", "laufzeit")
    ):
        matched_names = {"system_status"}

    # Websuche nur als Fallback anbieten, wenn der Router sonst gar kein
    # passendes Werkzeug gefunden hat. Nicht pauschal zu jedem Worker-Job.
    if worker_mode and not matched_names and "web_search" in tool_names:
        matched_names.add("web_search")

    # Fast-Chat: Memory- und Confirmation-Tools nur laden, wenn der Turn sie braucht.

    if any(k in _lower for k in (
        "erinner", "weißt du", "weisst du", "vorhin", "gestern", "damals",
        "letztes mal", "hatten wir", "haben wir schon",
        "was habe ich", "was hab ich", "wer bin ich",
        "wie heiße ich", "wie heisse ich", "was weißt du über mich",
        "was weisst du ueber mich"
    )):
        matched_names.add("recall_memory")

    if (
        _lower in {
            "ja", "ja bitte", "ok", "okay", "mach das", "tu das", "genau",
            "nein", "ne", "abbrechen", "bestätigen", "bestaetigen"
        }
        or _lower.startswith(("ja ", "ja,", "nein ", "nein,", "ok ", "okay "))
    ):
        matched_names.add("check_pending_confirmation")
        matched_names.add("respond_to_confirmation")

    tools = [t for t in all_tools if t["function"]["name"] in matched_names]

    registry = get_registry()

    # In the background worker, clear read-only jobs should never wait for the
    # language model just to format a tool call. Execute the deterministic tool
    # immediately and return its raw evidence. The worker's verification layer
    # remains authoritative and will reject failed/error results.
    if worker_mode:
        deterministic = _deterministic_worker_fallback(clear_text, tools, registry)
        if deterministic is not None:
            d_name, d_args, d_result = deterministic
            print(
                f"[LocalBrain][Worker] Deterministischer Preflight: {d_name}",
                flush=True,
            )
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": d_name, "arguments": d_args},
                }],
            })
            messages.append({"role": "tool", "content": str(d_result)})
            return str(d_result).strip(), messages

    for _round in range(12):  # bounded multi-step execution
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False, "think": False,
                  "keep_alive": "30m", "options": {"temperature": 0.0, "num_ctx": 8192, "num_thread": 8, "num_predict": 384},  # deterministisch: bei Default-Temperatur driftete das 3B-Modell in Tool-Runden gelegentlich ab
                  **({"tools": tools} if tools else {})},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            force_tools = bool(globals().get("_FORCE_TOOL_EXECUTION", False))
            answer_text = (msg.get("content") or "").strip()
            answer_low = answer_text.lower()

            tool_messages = [
                m for m in messages
                if isinstance(m, dict) and m.get("role") == "tool"
            ]
            last_tool_text = (
                str(tool_messages[-1].get("content") or "").lower().strip()
                if tool_messages else ""
            )
            failure_markers = (
                "fehler", "error", "fehlgeschlagen", "failed",
                "couldn't get", "could not get", "could not be retrieved",
                "returned false", "resource_exhausted", "quota",
                "nicht erreichbar", "unreachable", "timeout",
                "unbekanntes tool", "unknown tool", "search failed",
            )
            last_tool_failed = bool(tool_messages) and (
                not last_tool_text
                or any(marker in last_tool_text for marker in failure_markers)
            )
            promise_markers = (
                "ich werde", "werde ich", "ich versuche es", "ich mache das jetzt",
                "i will", "i'll", "let me", "i am going to", "i'm going to",
                "will now use",
            )
            promise_only = any(marker in answer_low for marker in promise_markers)

            # Deterministischer Worker-Pfad: Wenn exakt ein Tool angeboten wird
            # und dieses keine Pflichtparameter braucht, muss das kleine Modell
            # nicht erst einen Tool-Call formulieren. Fuehre es direkt aus.
            if force_tools and not tool_messages and len(tools) == 1:
                tool_spec = tools[0]
                fn_spec = tool_spec.get("function", {})
                required = ((fn_spec.get("parameters") or {}).get("required") or [])
                if not required:
                    name = fn_spec.get("name")
                    inline_result = _run_inline_tool(name, {})
                    if inline_result is not None:
                        result = inline_result
                    elif registry.has(name):
                        try:
                            result = registry.run(name, {}, ctx={})
                        except Exception as e:
                            result = f"Fehler beim Ausfuehren von {name}: {e}"
                    else:
                        result = f"Fehler: unbekanntes Tool '{name}'"
                    print(
                        "[LocalBrain][Worker] Deterministischer Tool-Aufruf:",
                        name,
                        flush=True,
                    )
                    messages.append({"role": "tool", "content": str(result)})
                    continue

            forced_retries = sum(
                1 for m in messages
                if isinstance(m, dict)
                and m.get("role") == "system"
                and "AUTONOMER AUSFUEHRUNGSMODUS" in str(m.get("content") or "")
            )

            needs_execution = not tool_messages or last_tool_failed

            if force_tools and not tool_messages:
                deterministic = _deterministic_worker_fallback(clear_text, tools, registry)
                if deterministic is not None:
                    d_name, d_args, d_result = deterministic
                    print(
                        f"[LocalBrain][Worker] Deterministischer Tool-Fallback: {d_name}",
                        flush=True,
                    )
                    messages.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "type": "function",
                            "function": {"name": d_name, "arguments": d_args},
                        }],
                    })
                    messages.append({"role": "tool", "content": str(d_result)})
                    messages.append({
                        "role": "system",
                        "content": (
                            "AUTONOMER AUSFUEHRUNGSMODUS: Das Tool wurde wirklich ausgefuehrt. "
                            "Gib jetzt nur das konkrete Tool-Ergebnis als Abschlussantwort aus. "
                            "Keine Ankuendigung und keine erfundenen Details."
                        ),
                    })
                    continue

            if force_tools and tools and needs_execution and forced_retries < 4:
                print(
                    "[LocalBrain][Worker] Weiterer Tool-Aufruf erforderlich. Kandidaten:",
                    [t["function"]["name"] for t in tools],
                    flush=True,
                )
                messages.append({
                    "role": "system",
                    "content": (
                        "AUTONOMER AUSFUEHRUNGSMODUS: Die Aufgabe ist NOCH NICHT erledigt. "
                        "Der vorherige Tool-Aufruf ist fehlgeschlagen oder es wurde noch kein "
                        "Tool ausgefuehrt. Antworte NICHT mit einer Ankuendigung. Waehle jetzt "
                        "ein passendes verfuegbares Tool und fuehre es wirklich aus."
                    ),
                })
                continue

            if (
                force_tools and tool_messages and not last_tool_failed
                and promise_only and forced_retries < 4
            ):
                messages.append({
                    "role": "system",
                    "content": (
                        "AUTONOMER AUSFUEHRUNGSMODUS: Ein Tool-Ergebnis liegt bereits vor. "
                        "Keine weitere Ankuendigung. Gib jetzt ausschliesslich das konkrete "
                        "Ergebnis des erfolgreichen Tool-Aufrufs als Abschlussantwort aus."
                    ),
                })
                continue

            if not worker_mode:
                _record_cognitive_turn(user_text, answer_text)
            return answer_text, messages

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments") or {}
            inline_result = _run_inline_tool(name, args)
            if inline_result is not None:
                result = inline_result
            elif registry.has(name):
                try:
                    result = registry.run(name, args, ctx={})
                except Exception as e:
                    result = f"Fehler beim Ausfuehren von {name}: {e}"
            else:
                result = f"Fehler: unbekanntes Tool '{name}'"
            messages.append({"role": "tool", "content": str(result)})
        # Nach jeder Tool-Runde: Ergebnis-Disziplin erzwingen. Beobachtet: das
        # kleine Modell beendete eine Aufgabe mit "Ich werde es jetzt versuchen"
        # und markierte sie damit als erledigt - eine Ankuendigung ist kein Ergebnis.
        messages.append({"role": "system", "content": (
            "STRIKT: Nutze nur die Tool-Ergebnisse oben. Hat ein Tool nichts Brauchbares "
            "geliefert, rufe JETZT das passendere andere Tool auf (z.B. weather_report fuer "
            "Wetter, search_knowledge fuer Wissen) statt es anzukuendigen. Beende NIE mit "
            "'ich werde ... versuchen' - entweder konkretes Ergebnis oder klare Aussage, "
            "was genau nicht ging und warum.")})

    return "Ich bin mir nicht sicher, wie ich das ohne weitere Rueckfrage loesen kann.", messages


# ── Lokales STT/TTS ueber speaches (Whisper + Piper/Kerstin) ──────────────

def transcribe(audio_path: str) -> str:
    with open(audio_path, "rb") as f:
        resp = requests.post(
            f"{SPEACHES_URL}/v1/audio/transcriptions",
            files={"file": f},
            data={"model": "Systran/faster-whisper-small"},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


def speak(text: str, out_path: str, voice: str = "kerstin") -> str:
    resp = requests.post(
        f"{SPEACHES_URL}/v1/audio/speech",
        json={"model": f"speaches-ai/piper-de_DE-{voice}-low", "input": text, "voice": voice, "response_format": "wav"},
        timeout=30,
    )
    resp.raise_for_status()
    Path(out_path).write_bytes(resp.content)
    return out_path


def is_local_mode_enabled() -> bool:
    return _load_config().get("llm_provider", "gemini").strip().lower() == "ollama"


import re as _re
import tempfile as _tempfile

_SENT_END = _re.compile(r'(?<=[.!?])\s+')


def chat_stream_and_speak(user_text: str, history: list[dict] | None = None, voice: str = "kerstin"):
    """
    Wie chat(), ABER: sobald die finale Antwort (kein Tool-Call mehr noetig)
    beginnt, wird sie TOKEN-WEISE von Ollama gestreamt UND SATZWEISE sofort
    gesprochen - MIA muss nicht erst fertig denken, bevor sie zu sprechen
    anfaengt. Senkt die gefuehlte Wartezeit spuerbar, auch ohne GPU.

    Yields dicts: {"type": "audio", "path": str, "text": str}  pro fertigem Satz
                  {"type": "done", "full_answer": str, "history": list}  am Ende
    Tool-Calling-Runden davor laufen weiterhin non-streaming (Entscheidungslogik
    soll nicht Satz-fuer-Satz "gedacht" werden).
    """
    all_tools = build_ollama_tools()
    tool_names = [t["function"]["name"] for t in all_tools]
    system_prompt = _load_system_prompt(refresh_memory=not bool(history))
    clear_text = clarify(user_text)

    # 2026-09-30: history used to REPLACE the system prompt entirely when given, silently
    # dropping the persona + memory injection (_load_system_prompt()) for any caller that
    # passed prior turns — exactly what "remember like a human" needs. System prompt now
    # always leads; passed history is prior conversation turns appended after it.
    messages = [{"role": "system", "content": system_prompt}] + (history or [])
    # Verhaltensgedaechtnis greift bei jeder Nutzereingabe ZUERST - vor Recall und Tools.
    behavior_block = behavior_context(clear_text)
    if behavior_block:
        messages.append({"role": "system", "content": behavior_block})
    try:
        recalled_context = relevant_conversation_memory(clear_text, limit=6, max_chars=2800)
    except Exception as e:
        print(f"[LocalBrain] automatic conversation recall failed: {e}")
        recalled_context = ""
    if recalled_context:
        messages.append({"role": "system", "content": recalled_context})
    messages.append({"role": "user", "content": clear_text})

    # Same truth-first fast path as chat(): an explicit background-work request
    # creates a persistent task immediately. Voice/streaming must never re-open
    # the old failure mode where MIA merely says she is working.
    schedule_at = _extract_schedule_at(clear_text)
    if (schedule_at and _SCHEDULE_REQUEST_RE.search(clear_text)) or _BACKGROUND_REQUEST_RE.search(clear_text):
        fast_registry = get_registry()
        if fast_registry.has("background_task"):
            if schedule_at and _SCHEDULE_REQUEST_RE.search(clear_text):
                goal = _extract_scheduled_goal(clear_text)
                bg_args = {"action": "schedule", "goal": goal, "run_at": schedule_at}
            else:
                goal = _extract_background_goal(clear_text)
                bg_args = {"action": "create", "goal": goal}
            result = fast_registry.run(
                "background_task",
                bg_args,
                ctx={},
            )
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "type": "function",
                    "function": {
                        "name": "background_task",
                        "arguments": bg_args,
                    },
                }],
            })
            messages.append({"role": "tool", "content": str(result)})
            answer = str(result)
            _record_cognitive_turn(user_text, answer)
            out_path = _tempfile.mktemp(suffix=".wav")
            try:
                speak(answer, out_path, voice=voice)
                yield {"type": "audio", "path": out_path, "text": answer}
            except Exception as e:
                yield {"type": "error", "text": f"TTS fehlgeschlagen: {e}"}
            yield {"type": "done", "full_answer": answer, "history": messages}
            return

    # Datum deterministisch in Python berechnen statt vom Modell raten lassen
    # (es hat 'morgen'/'montag' verwechselt). Wird als unmissverstaendlicher
    # Fakt direkt vor dem Tool-Call eingefuegt - das Modell muss nur noch
    # kopieren, nicht mehr rechnen.
    date_hit = _extract_explicit_date(user_text)
    if date_hit:
        word, resolved = date_hit
        messages.append({
            "role": "system",
            "content": f"FAKT (nicht selbst nachrechnen, direkt uebernehmen): '{word}' bedeutet hier exakt das Datum {resolved}. Falls du ein Tool mit einem 'date'-Feld aufrufst, nutze GENAU '{resolved}'.",
        })

    registry = get_registry()

    # ROUTER: schneller Stichwort-Check OHNE Ollama-Call. Statt ALLER 13
    # Tool-Definitionen schicken wir nur die 1-2 passenden mit - das haelt den
    # Kontext klein, was auf dieser CPU-only Hardware der Hauptfaktor fuer die
    # Antwortzeit ist. Leere Liste = reine Konversation, kein Tool-Call-Versuch.
    matched_names = set(_matching_tool_names(clear_text, tool_names))
    matched_names.add("recall_memory")  # 2026-09-29: same reasoning as chat() above
    matched_names.add("check_pending_confirmation")
    matched_names.add("respond_to_confirmation")
    tools = [t for t in all_tools if t["function"]["name"] in matched_names]
    skip_tool_rounds = not tools

    # Runden 1..N: Tool-Calling, non-streaming (wie chat()) - nur falls Tools gematcht haben
    for _round in range(12 if not skip_tool_rounds else 0):
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "tools": tools, "stream": False, "think": False,
                  "keep_alive": "30m", "options": {"temperature": 0.0, "num_ctx": 8192, "num_thread": 8, "num_predict": 384}},  # deterministisch: bei Default-Temperatur driftete das 3B-Modell in Tool-Runden gelegentlich in andere Sprachen ab und rief das Tool nicht auf
            timeout=120,  # 60s war zu knapp: nach einem Embedding-Call (bge-m3) muss Ollama das Chat-Modell ggf. neu laden
        )
        resp.raise_for_status()
        msg = resp.json().get("message", {})
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            # Kein weiterer Tool-Call mehr -> ab hier STREAMEN statt fertige Antwort zu nehmen.
            # Der letzte Assistant-Turn wird verworfen und stattdessen gestreamt neu erzeugt,
            # damit wir Satz-fuer-Satz sprechen koennen statt die fertige Antwort abzuwarten.
            break

        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments") or {}
            inline_result = _run_inline_tool(name, args)
            if inline_result is not None:
                result = inline_result
            elif registry.has(name):
                try:
                    result = registry.run(name, args, ctx={})
                except Exception as e:
                    result = f"Fehler beim Ausfuehren von {name}: {e}"
            else:
                result = f"Fehler: unbekanntes Tool '{name}'"
            messages.append({"role": "tool", "content": str(result)})
    else:
        if not skip_tool_rounds:
            yield {"type": "done", "full_answer": "Zu viele Tool-Aufrufe hintereinander.", "history": messages}
            return
        # skip_tool_rounds=True: range(0) laeuft "leer" durch, das ist der
        # gewuenschte schnelle CHAT-Pfad, kein Fehler - einfach weiter zum Streaming.

    # Wenn Tool-Ergebnisse im Kontext sind: harter Faithfulness-Zwang. Das
    # kleine Modell paraphrasiert sonst und erfindet Details dazu, die nicht
    # im Tool-Ergebnis stehen (beobachtet bei recall_memory).
    if any(m.get("role") == "tool" for m in messages):
        messages.append({
            "role": "system",
            "content": (
                "STRIKT: Antworte AUSSCHLIESSLICH mit Informationen, die woertlich in den "
                "Tool-Ergebnissen oben stehen. Erfinde NICHTS dazu, ergaenze KEINE Details, "
                "die dort nicht stehen. Wenn etwas nicht im Tool-Ergebnis steht, sag es nicht. "
                "Gib die Inhalte in klaren, kurzen Saetzen wieder."
            ),
        })

    # Finale Antwort STREAMEN, satzweise sofort sprechen
    buffer = ""
    full_answer = ""
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": True, "think": False,
              "keep_alive": "30m",  # Chat-Modell warm halten, sonst Neuladen nach Embedding-Calls
              "options": {"temperature": 0.0, "num_ctx": 8192, "num_thread": 8, "num_predict": 384}},
        timeout=300,  # Read-Timeout bis zum ersten Token: Prompt-Eval mit Wissens-Kontext dauert auf CPU laenger als 120s
        stream=True,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        token = chunk.get("message", {}).get("content", "")
        if not token:
            continue
        buffer += token
        full_answer += token

        parts = _SENT_END.split(buffer)
        if len(parts) > 1:
            *complete, buffer = parts
            for sentence in complete:
                sentence = sentence.strip()
                if not sentence:
                    continue
                out_path = _tempfile.mktemp(suffix=".wav")
                try:
                    speak(sentence, out_path, voice=voice)
                    yield {"type": "audio", "path": out_path, "text": sentence}
                except Exception as e:
                    yield {"type": "error", "text": f"TTS fehlgeschlagen fuer Satz: {e}"}

    # Rest (letzter unvollstaendiger Satz nach Stream-Ende) noch sprechen
    tail = buffer.strip()
    if tail:
        out_path = _tempfile.mktemp(suffix=".wav")
        try:
            speak(tail, out_path, voice=voice)
            yield {"type": "audio", "path": out_path, "text": tail}
        except Exception as e:
            yield {"type": "error", "text": f"TTS fehlgeschlagen fuer Restsatz: {e}"}

    messages.append({"role": "assistant", "content": full_answer})
    _record_cognitive_turn(user_text, full_answer)
    yield {"type": "done", "full_answer": full_answer, "history": messages}


if __name__ == "__main__":
    tools = build_ollama_tools()
    print(f"Verfuegbare Tools ({len(tools)}):", [t["function"]["name"] for t in tools][:10])
    antwort, _ = chat("Wer bist du? Antworte in einem Satz.")
    print("MIA (lokal):", antwort)
