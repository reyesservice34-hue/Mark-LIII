"""
quiz — spoken question-and-answer drill on any subject.

On the roadmap this was "quiz mode". In a Handwerksbetrieb it earns its place as
the thing nobody enjoys preparing: Unterweisung. Arbeitssicherheit, Gefahrstoffe,
Leitern und Gerüste, PSA, the DIN an Azubi is supposed to know, VOB/B basics —
ask for the subject and it drills you or an apprentice on the drive to the site.

It runs on core/free_llm.py, so questions and marking cost nothing: Gemini's
free tier, or the local model when there is no key.

Two decisions shape it:

* **Marking is done by the model, not by string comparison.** "Zwei Meter" and
  "2 m" and "ab zwei Metern Absturzhöhe" are the same answer, and an assistant
  that calls the third one wrong teaches the user to stop answering properly.
  When the model cannot be reached, it falls back to a normalised comparison and
  *says* that it did — a mark you cannot trust is worse than no mark.
* **The session lives in memory only.** A quiz interrupted by a phone call is
  not worth persisting to disk, and a half-finished drill resurfacing three days
  later after a restart is a bug, not a feature.
"""
from __future__ import annotations

import re
import threading
import unicodedata

from core.free_llm import complete, extract_json

DEFAULT_COUNT = 5
MAX_COUNT = 15

_lock = threading.Lock()
_session: dict = {}


class QuizError(Exception):
    """Something the user needs to hear, phrased for speaking aloud."""


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ä", "ae").replace("ö", "oe") \
                              .replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _generate(topic: str, count: int, level: str) -> list[dict]:
    system = ("Du bist Ausbilder in einem Handwerks- und Innenausbaubetrieb und "
              "erstellst Prüfungsfragen. Antworte ausschließlich mit JSON.")
    prompt = (
        f"Erstelle {count} Fragen zum Thema: {topic}.\n"
        f"Niveau: {level or 'Praxis, wie in einer Unterweisung'}.\n\n"
        "Regeln:\n"
        "1. Jede Frage ist in einem Satz gesprochen beantwortbar — keine Multiple Choice, "
        "keine Rechenaufgaben mit Zettel.\n"
        "2. Die Musterantwort ist kurz und enthält die Zahl oder den Begriff, auf den es "
        "ankommt.\n"
        "3. Keine erfundenen Normen, Paragraphen oder Fristen. Was du nicht sicher weißt, "
        "fragst du nicht ab.\n\n"
        'Format: [{"frage": "...", "antwort": "..."}]\n'
        "JSON:"
    )
    raw = complete(prompt, system)
    data = extract_json(raw)
    if not isinstance(data, list):
        raise QuizError("I could not put a set of questions together just now.")

    questions = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = str(item.get("frage") or item.get("question") or "").strip()
        a = str(item.get("antwort") or item.get("answer") or "").strip()
        if q and a:
            questions.append({"q": q, "a": a})
    if not questions:
        raise QuizError("The questions came back unusable — try a narrower subject.")
    return questions[:count]


def _judge(question: str, expected: str, given: str) -> tuple[str, str]:
    """Returns (verdict, comment). verdict is 'richtig' | 'teilweise' | 'falsch'."""
    prompt = (
        f"Frage: {question}\nMusterantwort: {expected}\nAntwort des Prüflings: {given}\n\n"
        "Bewerte sachlich. Zahlenangaben in anderer Schreibweise sind gleichwertig. "
        'Antworte als JSON: {"bewertung": "richtig|teilweise|falsch", "hinweis": "ein Satz"}'
    )
    try:
        data = extract_json(complete(prompt, "Du bewertest Prüfungsantworten. Nur JSON."))
        if isinstance(data, dict):
            verdict = str(data.get("bewertung", "")).strip().lower()
            if verdict in ("richtig", "teilweise", "falsch"):
                return verdict, str(data.get("hinweis", "")).strip()
    except Exception:
        pass

    # No model reachable. Say so rather than dressing a string match up as marking.
    expected_words = set(_norm(expected).split())
    given_words = set(_norm(given).split())
    overlap = len(expected_words & given_words) / max(1, len(expected_words))
    verdict = "richtig" if overlap >= 0.6 else ("teilweise" if overlap >= 0.3 else "falsch")
    return verdict, f"(compared by wording only — the model was not reachable) {expected}"


def _ask_current() -> str:
    q = _session["questions"][_session["index"]]
    return f"Frage {_session['index'] + 1} von {len(_session['questions'])}: {q['q']}"


def _start(p: dict) -> str:
    topic = str(p.get("topic", "")).strip()
    if not topic:
        return "Worüber soll ich abfragen?"
    try:
        count = int(p.get("count") or DEFAULT_COUNT)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    count = max(1, min(count, MAX_COUNT))

    questions = _generate(topic, count, str(p.get("level", "")).strip())
    with _lock:
        _session.clear()
        _session.update(topic=topic, questions=questions, index=0, correct=0.0, wrong=[])
    return f"{len(questions)} Fragen zu {topic}. " + _ask_current()


def _answer(p: dict) -> str:
    given = str(p.get("answer", "")).strip()
    if not _session:
        return "Es läuft gerade keine Abfrage. Sag mir ein Thema, dann fange ich eine an."
    if not given:
        return _ask_current()

    q = _session["questions"][_session["index"]]
    verdict, hint = _judge(q["q"], q["a"], given)

    if verdict == "richtig":
        _session["correct"] += 1
        head = "Richtig."
    elif verdict == "teilweise":
        _session["correct"] += 0.5
        head = f"Teilweise. {hint or q['a']}"
    else:
        _session["wrong"].append(q)
        head = f"Nicht ganz. Richtig wäre: {q['a']}." + (f" {hint}" if hint else "")

    _session["index"] += 1
    if _session["index"] >= len(_session["questions"]):
        return head + " " + _summary()
    return head + " " + _ask_current()


def _skip(p: dict) -> str:
    if not _session:
        return "Es läuft gerade keine Abfrage."
    q = _session["questions"][_session["index"]]
    _session["wrong"].append(q)
    _session["index"] += 1
    if _session["index"] >= len(_session["questions"]):
        return f"Die Antwort wäre gewesen: {q['a']}. " + _summary()
    return f"Die Antwort wäre gewesen: {q['a']}. " + _ask_current()


def _summary() -> str:
    total = len(_session["questions"])
    score = _session["correct"]
    topic = _session["topic"]
    weak = _session["wrong"]
    line = f"Fertig: {score:g} von {total} zum Thema {topic}."
    if weak:
        line += " Nochmal ansehen: " + "; ".join(w["q"] for w in weak[:3])
    with _lock:
        _session.clear()
    return line


def _stop(p: dict) -> str:
    if not _session:
        return "Es läuft gerade keine Abfrage."
    answered = _session["index"]
    line = (f"Abgebrochen nach {answered} von {len(_session['questions'])} Fragen, "
            f"{_session['correct']:g} richtig.")
    with _lock:
        _session.clear()
    return line


def _score(p: dict) -> str:
    if not _session:
        return "Es läuft gerade keine Abfrage."
    return (f"{_session['correct']:g} richtig nach {_session['index']} von "
            f"{len(_session['questions'])} Fragen zu {_session['topic']}.")


_ACTIONS = {
    "start": _start, "new": _start, "begin": _start,
    "answer": _answer, "antwort": _answer,
    "skip": _skip, "pass": _skip,
    "stop": _stop, "cancel": _stop, "abbrechen": _stop,
    "score": _score, "stand": _score,
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()
    if not action:
        action = "answer" if _session else "start"
    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"Die Quiz-Aktion '{action}' kenne ich nicht. Ich kann eine Abfrage "
                f"starten, Antworten bewerten, überspringen, den Stand nennen oder abbrechen.")
    try:
        result = handler(p)
    except QuizError as e:
        result = str(e)
    except Exception as e:
        result = f"Die Abfrage ist fehlgeschlagen: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result


PLUGIN = {
    "name": "quiz",
    "description": (
        "Fragt den Nutzer oder einen Azubi zu einem Thema ab — Unterweisung, "
        "Arbeitssicherheit, Fachwissen, Prüfungsvorbereitung, oder irgendein anderes "
        "Thema zum Üben. Starte mit dem Thema, gib danach jede gesprochene Antwort des "
        "Nutzers als 'answer' weiter; das Tool bewertet sie und stellt die nächste Frage. "
        "Lies die Frage genau so vor, wie das Tool sie zurückgibt, und verrate die "
        "Musterantwort nicht vorher."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | answer | skip | score | stop"
            },
            "topic": {
                "type": "STRING",
                "description": "Thema der Abfrage, z. B. 'Leitern und Tritte' oder 'VOB/B'"
            },
            "answer": {
                "type": "STRING",
                "description": "Die Antwort des Nutzers, wörtlich wie gesprochen"
            },
            "count": {
                "type": "INTEGER",
                "description": "Anzahl Fragen (Standard 5, höchstens 15)"
            },
            "level": {
                "type": "STRING",
                "description": "Optional: Niveau, z. B. 'Azubi erstes Lehrjahr' oder 'Meister'"
            }
        },
        "required": []
    },
}
