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

CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
# Dieselbe Datei, die main.py's Gemini-Pfad laedt (core/prompt.txt) - NICHT
# ein eigenes, aermeres Root-prompt.txt. So bekommt der lokale Pfad automatisch
# alle dort schon bewaehrten Regeln, u.a. die Anti-Fabrikations-Regel
# ("Never claim an action that didn't happen") aus commit 2c52eed.
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"

OLLAMA_URL = "http://127.0.0.1:11434"       # Ollama laeuft bereits lokal AUF dem Brain-Server (15GB RAM, 8 Kerne)
SPEACHES_URL = "http://127.0.0.1:8005"      # mia-speaches-kerstin, lokal auf diesem (Brain) Server
OLLAMA_MODEL = "qwen3:1.7b"                 # dasselbe Modell wie understanding.py - vermeidet teures Modell-Wechseln in Ollama


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


def _load_system_prompt() -> str:
    try:
        base = PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        base = "Du bist MIA, eine hilfsbereite, ehrliche KI-Assistentin. Sprich Deutsch."
    # Aktuelles Datum/Uhrzeit fest einbetten - ohne das rechnet das kleine
    # Modell bei "morgen", "in einer Stunde" etc. oft falsch (z.B. bei
    # Erinnerungen), weil es kein echtes Zeitgefuehl hat.
    now = _dt.datetime.now()
    weekday_de = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][now.weekday()]
    return (
        f"{base}\n\nAKTUELLES DATUM UND UHRZEIT: {weekday_de}, {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} Uhr "
        f"(Format fuer Tools: Datum als {now.strftime('%Y-%m-%d')}, Uhrzeit als HH:MM). "
        "Rechne relative Zeitangaben ('morgen', 'in einer Stunde', 'naechsten Montag') IMMER ausgehend von diesem Datum, nicht raten."
    )


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
    "self_dev": ["verbesser dich", "trainier dich"],
    "recall_memory": [
        "erinnerst du dich", "weißt du noch", "was hast du gelernt", "was wurde geändert",
        "was hast du heute", "erinnere dich", "weißt du was", "kennst du",
    ],
    "save_memory": ["merk dir", "merke dir", "speicher", "notier", "vergiss nicht dass", "ich heiße", "mein name ist", "ich mag", "ich bin"],
    "system_status": ["cpu", "ram", "speicher voll", "auslastung", "wie geht es dir", "systemstatus", "server status", "laufzeit"],
    "undo": ["rückgängig", "mach das rückgängig", "undo", "zurücknehmen", "nein nicht das"],
    # Nur explizite Hintergrund-Absicht. Generische Woerter wie "Aufgabe", "erledige"
    # oder "kuemmere dich" duerfen direkte Befehle NICHT erneut in die Queue schicken.
    "background_task": ["im hintergrund", "hintergrundaufgabe", "später fertig", "spaeter fertig", "arbeite im hintergrund", "offene aufgaben", "ergebnis der aufgabe", "warteschlange"],
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
    system_prompt = _load_system_prompt()

    clear_text = user_text if skip_clarify else clarify(user_text)

    messages = history[:] if history else [{"role": "system", "content": system_prompt}]
    messages.append({"role": "user", "content": clear_text})

    # Gleiche Optimierung wie in chat_stream_and_speak: Datum deterministisch
    # vorgeben und NUR die per Stichwort passenden Tools mitschicken. Der
    # Hintergrund-Worker lief mit allen 19 Tool-Schemas im Kontext in den
    # 120s-Timeout (CPU-only Prompt-Eval).
    date_hit = _extract_explicit_date(user_text)
    if date_hit:
        word, resolved = date_hit
        messages.append({"role": "system", "content": f"FAKT (nicht selbst nachrechnen, direkt uebernehmen): '{word}' bedeutet hier exakt das Datum {resolved}. Falls du ein Tool mit einem 'date'-Feld aufrufst, nutze GENAU '{resolved}'."})
    # Hintergrund-Worker routen nur nach dem echten Auftragsziel. So koennen
    # sie background_task ausschliessen und keine neue Queue-Aufgabe erzeugen.
    route_text = routing_text if routing_text is not None else clear_text
    matched_names = set(_matching_tool_names(route_text, tool_names))
    if exclude_tools:
        matched_names.difference_update(exclude_tools)
    tools = [t for t in all_tools if t["function"]["name"] in matched_names]

    registry = get_registry()

    for _round in range(4):  # max 4 Tool-Call-Runden pro Turn, verhindert Endlosschleifen
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False, "think": False,
                  "keep_alive": "30m", "options": {"temperature": 0.0},  # deterministisch: bei Default-Temperatur driftete das 3B-Modell in Tool-Runden gelegentlich ab
                  **({"tools": tools} if tools else {})},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return (msg.get("content") or "").strip(), messages

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
    system_prompt = _load_system_prompt()
    clear_text = clarify(user_text)

    messages = history[:] if history else [{"role": "system", "content": system_prompt}]
    messages.append({"role": "user", "content": clear_text})

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
    tools = [t for t in all_tools if t["function"]["name"] in matched_names]
    skip_tool_rounds = not tools

    # Runden 1..N: Tool-Calling, non-streaming (wie chat()) - nur falls Tools gematcht haben
    for _round in range(4 if not skip_tool_rounds else 0):
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "tools": tools, "stream": False, "think": False,
                  "keep_alive": "30m", "options": {"temperature": 0.0}},  # deterministisch: bei Default-Temperatur driftete das 3B-Modell in Tool-Runden gelegentlich in andere Sprachen ab und rief das Tool nicht auf
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
              "options": {"temperature": 0.0}},
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
    yield {"type": "done", "full_answer": full_answer, "history": messages}


if __name__ == "__main__":
    tools = build_ollama_tools()
    print(f"Verfuegbare Tools ({len(tools)}):", [t["function"]["name"] for t in tools][:10])
    antwort, _ = chat("Wer bist du? Antworte in einem Satz.")
    print("MIA (lokal):", antwort)
