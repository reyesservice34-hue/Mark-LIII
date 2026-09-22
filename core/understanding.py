"""
core/understanding.py — Verstehens-Schicht zwischen Nutzer-Eingabe (Chat ODER
Sprache) und MIA's Denk-/Tool-Pfad (local_brain.py).

Zweck: Egal WIE der Nutzer es sagt/schreibt - Fueller ("aeh", "aehm", "quasi"),
abgebrochene Saetze, Dialekt, STT-Transkriptionsfehler, unklare Grammatik -
MIA soll die eigentliche ABSICHT trotzdem sicher erfassen.

Zwei Stufen:
  1. Regelbasierte Bereinigung (schnell, kostenlos, kein LLM-Call):
     Fuellwoerter raus, Mehrfach-Leerzeichen weg, abgebrochene Wiederholungen
     zusammenfassen ("ich moechte, ich moechte, ich will" -> "ich will").
  2. LLM-Klarstellung (ein kurzer, dedizierter Ollama-Call MIT einem eigenen
     System-Prompt, der NUR interpretiert, nichts ausfuehrt): formt die
     bereinigte Roheingabe in einen klaren, vollstaendigen Befehls-/Fragesatz
     um. Dieser klare Satz - NICHT die Roheingabe - geht an local_brain.chat().

Faellt bei jedem Fehler (Ollama nicht erreichbar etc.) auf die bereinigte
Roheingabe zurueck - versteht dann eben etwas weniger gut, bricht aber nie.
"""
from __future__ import annotations

import re
from pathlib import Path

import requests

OLLAMA_URL = "http://127.0.0.1:11434"       # lokal auf dem Brain-Server
OLLAMA_MODEL = "qwen2.5:3b"                 # klein & schnell genug fuer die Verstehens-Vorstufe

# Deutsche Fuell-/Verzoegerungswoerter, wie sie in Sprache (und Tippfehlern) auftauchen.
_FILLERS = re.compile(
    r"\b(äh+m?|ähm|hm+|also äh|so äh|quasi|irgendwie halt|ja also|na ja)\b",
    re.IGNORECASE,
)
_MULTI_SPACE = re.compile(r"\s{2,}")
_REPEATED_START = re.compile(r"^(.{3,30}?),\s*\1", re.IGNORECASE)  # "ich möchte, ich möchte"

_CLARIFY_SYSTEM_PROMPT = (
    "Du bist eine reine TEXT-BEREINIGUNG, keine Assistentin. Du beantwortest NICHTS, "
    "du fuehrst NICHTS aus, du interpretierst NICHTS hinein. "
    "Deine EINZIGE Aufgabe: Entferne NUR Fuellwoerter (aeh, aehm, aeh also), Wiederholungen "
    "und Versprecher aus der folgenden Nutzer-Eingabe und mache daraus einen sauberen, "
    "grammatikalisch korrekten Satz - GENAU in derselben Form wie das Original: "
    "eine FRAGE bleibt eine FRAGE, ein BEFEHL bleibt ein BEFEHL, eine AUSSAGE bleibt "
    "eine AUSSAGE. Enthaelt die Eingabe MEHRERE Teile (z.B. zwei Fragen), behalte ALLE "
    "Teile im Ergebnis. Du sprichst den Nutzer NICHT an und antwortest ihm NICHT - "
    "du gibst nur seine eigenen Worte sauber formuliert zurueck.\n\n"
    "KRITISCH WICHTIG: Zeit- und Datumsangaben (morgen, heute, uebermorgen, Wochentage, "
    "Uhrzeiten, 'in X Stunden/Minuten', Datumsangaben) sind KEINE Fuellwoerter und werden "
    "NIEMALS entfernt oder veraendert - sie sind fuer die Aufgabe entscheidend.\n\n"
    "Beispiele:\n"
    "Eingabe: 'wer bist du eigentlich'\n"
    "Richtig: 'Wer bist du?'  (bleibt eine Frage AN den Assistenten)\n"
    "Falsch: 'Du bist ein Assistent.'  (das ist KEINE Bereinigung, das ist eine erfundene Antwort)\n\n"
    "Eingabe: 'wer bist du und wie spaet ist es'\n"
    "Richtig: 'Wer bist du und wie spaet ist es?'  (beide Fragen bleiben erhalten)\n"
    "Falsch: 'Wie spaet ist es?'  (der erste Teil wurde faelschlich weggelassen)\n\n"
    "Eingabe: 'erinnere mich äh morgen um 9 uhr an den zahnarzt'\n"
    "Richtig: 'Erinnere mich morgen um 9 Uhr an den Zahnarzt.'  ('morgen' MUSS erhalten bleiben)\n"
    "Falsch: 'Erinnere mich um 9 Uhr an den Zahnarzt.'  (KRITISCHER FEHLER: 'morgen' faelschlich als Fuellwort entfernt)\n\n"
    "Antworte NUR mit dem bereinigten Satz, ohne Anfuehrungszeichen, ohne Erklaerung."
)


def _rule_based_cleanup(text: str) -> str:
    cleaned = _FILLERS.sub("", text)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip(" ,.")
    m = _REPEATED_START.match(cleaned)
    if m:
        cleaned = cleaned[m.end(1):].lstrip(", ").strip()
        cleaned = m.group(1) + " " + cleaned if not cleaned.lower().startswith(m.group(1).lower()) else cleaned
    return cleaned.strip()


def clarify(raw_text: str, timeout: float = 8.0) -> str:
    """
    Nimmt rohe Nutzer-Eingabe (Chat-Tippfehler ODER STT-Transkript) und liefert
    einen klaren, vollstaendigen Satz zurueck, der an local_brain.chat() geht.
    Bricht nie hart ab - im Zweifel wird die regelbasiert bereinigte Roheingabe
    zurueckgegeben.
    """
    cleaned = _rule_based_cleanup(raw_text)
    if not cleaned:
        return raw_text.strip()

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": _CLARIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": cleaned},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        result = (resp.json().get("message", {}).get("content") or "").strip()
        # Sicherheitsnetz: leere/kaputte LLM-Antwort -> auf regelbasiert bereinigten Text zurueckfallen
        return result if result else cleaned
    except Exception:
        return cleaned


if __name__ == "__main__":
    tests = [
        "ähm, kannst du äh, also ich möchte, ich möchte dass du mir, äh, den server neu startest",
        "mach mal äh das ding mit den logs an",
        "wie viel uhr ist es eigentlich gerade",
    ]
    for t in tests:
        print(f"ROH:    {t}")
        print(f"KLAR:   {clarify(t)}")
        print()
