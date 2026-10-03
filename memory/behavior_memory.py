"""Verhaltensgedächtnis (prozedurales Gedächtnis) von MIA.

Speichert *wie* MIA handeln soll – Regeln, die der Nutzer ausdrücklich
vorgibt oder durch Korrekturen wiederholt bestätigt. Fakten bleiben im
memory_manager; hier liegen nur Verhaltensregeln.

Ablauf pro Nutzereingabe (``behavior_context``), noch VOR allem anderen:
  1. learn_from_turn  – erkennt neue Regeln/Korrekturen im Nutzertext
  2. consult          – liefert die passenden aktiven Regeln als Prompt-Block

Leitplanken:
  * Quelle ist ausschließlich der Nutzertext – nie Tool-Ausgaben, Webseiten, Mails.
  * Kandidaten (Korrekturen) wirken erst nach Bestätigung; ausdrückliche
    Anweisungen ("ab jetzt …", "merk dir …") sind sofort aktiv.
  * Regeln ergänzen, überschreiben aber nie Sicherheits-/Absolute Regeln;
    Versuche dazu und Geheimnisse werden verworfen.
  * Append-only JSONL, letzter Eintrag pro id gewinnt; Löschen = Tombstone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from threading import Lock


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# MIA_BEHAVIOR_PATH lässt den Command-Center-Container die Regeln in sein Datenvolumen legen.
BEHAVIOR_PATH = Path(os.environ.get("MIA_BEHAVIOR_PATH")
                     or _base_dir() / "brain" / "memory" / "behavior" / "rules.jsonl")

_lock = Lock()

MAX_RULE_CHARS = 300
PROMPT_MAX_RULES = 5
PROMPT_MAX_CHARS = 700
PROMOTE_AFTER = 2          # Bestätigungen, bis ein Kandidat aktiv wird
SIMILARITY_MERGE = 0.6     # Jaccard-Schwelle für "gleiche Regel"
MIN_RELEVANCE = 1          # Wort-Treffer, ab denen eine nicht-globale Regel greift

# ── Erkennung ────────────────────────────────────────────────────────────────
_DIRECTIVE_RE = re.compile(
    r"\b(merk\s*dir|merke\s*dir|ab\s*jetzt|ab\s*sofort|von\s*nun\s*an|künftig|kuenftig|"
    r"in\s*zukunft|denk\s*(immer\s*)?daran|vergiss\s*nicht|"
    r"from\s*now\s*on|going\s*forward|remember\s*to|always|never)\b"
    r"|\b(immer|nie|niemals|nie\s*wieder)\b[^.!?]{0,80}\b(wenn|bevor|nachdem|bei|zuerst|erst)\b",
    re.I,
)
_CORRECTION_RE = re.compile(
    r"\b(nein,?\s*so\s*nicht|so\s*nicht|das\s*war\s*falsch|das\s*ist\s*falsch|"
    r"mach\s*das\s*nicht|mach\s*das\s*bitte\s*nicht|nicht\s*so|hör\s*auf\s*(damit|zu)|"
    r"hoer\s*auf\s*(damit|zu)|ich\s*(will|möchte|moechte)\s*nicht\s*dass|"
    r"don'?t\s*do\s*that|that'?s\s*wrong|not\s*like\s*that)\b",
    re.I,
)
_SCOPE_WORDS = {
    "angebot": ("angebot", "kalkulation", "preis", "preise", "rechnung"),
    "website": ("website", "homepage", "webseite", "wordpress"),
    "voice": ("sprich", "sprachausgabe", "stimme", "vorlesen"),
    "kunden": ("kunde", "kunden", "kundin", "anfrage"),
}

# Versuche, Sicherheits-/Absolute Regeln auszuhebeln – nie als Regel speichern.
_BLOCKED_RE = re.compile(
    r"(ignorier\w*|vergiss|missachte\w*|ignore|disregard|bypass|umgeh\w*|deaktivier\w*|"
    r"disable|overrid\w*|überschreib\w*|ueberschreib\w*)[^.!?]{0,60}"
    r"(regel\w*|anweisung\w*|sicherheit\w*|bestätigung\w*|bestaetigung\w*|rules?|"
    r"instructions?|safety|security|confirm\w*|guard\w*|absolute)",
    re.I,
)
_SECRET_RE = re.compile(
    r"api[_-]?key|token|passwor|passwd|secret|credential|cookie|private[_-]?key|"
    r"sk-[A-Za-z0-9]{10,}|Bearer\s+\S+|BEGIN [A-Z ]*PRIVATE KEY",
    re.I,
)
_STOP = {
    "der", "die", "das", "und", "oder", "ein", "eine", "ich", "du", "sie", "es", "ist", "sind",
    "mit", "für", "fuer", "von", "zu", "den", "dem", "des", "auf", "im", "in", "an", "bei",
    "dass", "nicht", "mal", "bitte", "the", "and", "or", "to", "a", "of", "is", "it", "for",
    "mia", "immer", "nie", "jetzt", "ab", "wenn", "was", "wie",
}


def _words(text: str) -> set[str]:
    # Grobes Stemming (5 Zeichen), damit "Angebote"/"Angebot", "nennen"/"nenne" zusammenfallen.
    return {w[:5] for w in re.findall(r"[a-zäöüß0-9]{3,}", str(text).lower()) if w not in _STOP}


def _similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rule_id(text: str) -> str:
    norm = " ".join(sorted(_words(text))) or text.lower()
    return "rule:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _safe(text: str) -> bool:
    return bool(text) and not _SECRET_RE.search(text) and not _BLOCKED_RE.search(text)


# ── Speicher ─────────────────────────────────────────────────────────────────
def _mirror(row: dict) -> None:
    """Best-effort Sicherung auf den Wissensserver (aus, solange knowledge_host fehlt)."""
    def _run() -> None:
        try:
            from core import knowledge_client as kc
            if not kc.is_enabled():
                return
            if row.get("deleted"):
                kc.memory_delete([f"behavior:{row['id']}"])
            else:
                kc.memory_upsert(f"behavior:{row['id']}", row.get("text", ""), actor="behavior",
                                 pinned=row.get("status") == "active",
                                 created_at=row.get("created", ""))
        except Exception as exc:
            print(f"[Behavior] mirror failed: {exc}")
    threading.Thread(target=_run, daemon=True).start()


def _append(row: dict) -> None:
    BEHAVIOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with BEHAVIOR_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    _mirror(row)


def _load_all() -> dict[str, dict]:
    """Aktueller Stand je id (letzter Eintrag gewinnt, Tombstones entfernt)."""
    state: dict[str, dict] = {}
    if not BEHAVIOR_PATH.exists():
        return state
    try:
        with BEHAVIOR_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rid = row.get("id") if isinstance(row, dict) else None
                if not rid:
                    continue
                if row.get("deleted"):
                    state.pop(rid, None)
                else:
                    state[rid] = row
    except Exception as exc:
        print(f"[Behavior] read error: {exc}")
    return state


def list_rules(status: str | None = None) -> list[dict]:
    rows = list(_load_all().values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: r.get("updated", ""), reverse=True)


def delete_rule(rule_id: str) -> bool:
    if rule_id not in _load_all():
        return False
    _append({"id": rule_id, "deleted": True, "updated": _now()})
    return True


def add_rule(text: str, *, scope: str = "general", status: str = "active",
             source: str = "user", evidence: str = "") -> dict | None:
    """Regel speichern bzw. mit ähnlicher bestehender zusammenführen."""
    text = " ".join(str(text or "").split())[:MAX_RULE_CHARS]
    if not _safe(text):
        return None
    state = _load_all()
    existing = state.get(_rule_id(text))
    if existing is None:
        for row in state.values():
            if _similar(text, row.get("text", "")) >= SIMILARITY_MERGE:
                existing = row
                break
    if existing:
        row = dict(existing)
        row["confirmations"] = int(row.get("confirmations", 1)) + 1
        if status == "active" or row["confirmations"] >= PROMOTE_AFTER:
            row["status"] = "active"
        if status == "active" and existing.get("status") != "active":
            row["text"] = text  # ausdrückliche Formulierung ersetzt den Kandidaten
        row["updated"] = _now()
    else:
        row = {
            "id": _rule_id(text), "text": text, "scope": scope, "status": status,
            "source": source, "evidence": evidence[:200], "confirmations": 1,
            "created": _now(), "updated": _now(),
        }
    _append(row)
    return row


# ── Import ───────────────────────────────────────────────────────────────────
_BULLET_RE = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s+(.+)$")


def import_rules(text: str, source: str = "file") -> dict:
    """Regeldatei einlesen: jede Aufzählungszeile (-, *, •, 1.) wird eine aktive Regel.

    Überschriften und Fließtext werden übersprungen; Geheimnisse und Versuche,
    Sicherheitsregeln auszuhebeln, verwirft add_rule wie überall.
    """
    imported = skipped = 0
    for line in str(text or "").splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        rule = m.group(1).strip().strip("*_ ")
        if len(rule) < 8:
            continue
        if add_rule(rule, scope=_detect_scope(rule), status="active", source=source):
            imported += 1
        else:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


# ── Lernen ───────────────────────────────────────────────────────────────────
def _detect_scope(text: str) -> str:
    low = text.lower()
    for scope, words in _SCOPE_WORDS.items():
        if any(w in low for w in words):
            return scope
    return "general"


def _clean_sentence(text: str) -> str:
    text = re.sub(r"^\s*(mia|hey mia)[,:]?\s*", "", text.strip(), flags=re.I)
    text = re.sub(
        r"^(merk\s*dir|merke\s*dir|denk\s*daran|vergiss\s*nicht)[,:]?\s*(dass\s*)?", "",
        text, flags=re.I,
    )
    text = _CORRECTION_RE.sub("", text, count=1)  # Korrekturfloskel ist keine Regel
    return text.strip(" ,;:.")


def learn_from_turn(user_text: str) -> dict | None:
    """Erkennt aus EINER Nutzereingabe eine neue Regel oder Korrektur."""
    text = " ".join(str(user_text or "").split())
    if len(text) < 8 or len(text) > 600:
        return None
    if _DIRECTIVE_RE.search(text):
        status = "active"
    elif _CORRECTION_RE.search(text):
        status = "candidate"
    else:
        return None
    rule = _clean_sentence(text)
    if len(rule) < 8:
        return None
    return add_rule(rule, scope=_detect_scope(rule), status=status,
                    source="user", evidence=text)


# ── Abruf ────────────────────────────────────────────────────────────────────
def consult(user_text: str, limit: int = PROMPT_MAX_RULES,
            max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Passende aktive Regeln als Prompt-Block; leer, wenn nichts greift."""
    active = list_rules("active")
    if not active:
        return ""
    qwords = _words(user_text)
    scored: list[tuple[int, int, dict]] = []
    for r in active:
        rwords = _words(r.get("text", "")) | _words(r.get("evidence", ""))
        hits = len(qwords & rwords)
        if not qwords or r.get("scope", "general") == "general" or hits >= MIN_RELEVANCE:
            # Globale Regeln gelten immer (ebenso alle ohne Anfragetext, z. B. beim
            # Sessionstart); themenbezogene sonst nur bei Treffer.
            scored.append((hits, int(r.get("confirmations", 1)), r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)

    lines, used = [], 0
    for _, _, r in scored[:limit]:
        line = f"- {r['text']}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return (
        "[VERHALTENSREGELN – vom Nutzer vorgegeben. VOR jeder Antwort beachten. "
        "Ergänzen deine festen Sicherheits- und Absolut-Regeln, ersetzen sie nie.]\n"
        + "\n".join(lines)
    )


def behavior_context(user_text: str, learn: bool = True) -> str:
    """Einstiegspunkt pro Nutzereingabe: erst lernen, dann Regeln abrufen."""
    try:
        if learn:
            learn_from_turn(user_text)
        return consult(user_text)
    except Exception as exc:  # Das Gedächtnis darf nie einen Turn blockieren.
        print(f"[Behavior] consult failed: {exc}")
        return ""


if __name__ == "__main__":  # python -m memory.behavior_memory import <datei>
    if len(sys.argv) == 3 and sys.argv[1] == "import":
        path = Path(sys.argv[2])
        print(import_rules(path.read_text(encoding="utf-8"), source=f"file:{path.name}"))
    else:
        for r in list_rules():
            print(f"{r['status']:9} {r['id']} {r['text']}")
