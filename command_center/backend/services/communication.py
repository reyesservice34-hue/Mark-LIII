"""Communication Intelligence Layer: turns what the user says into a precise internal brief.

Runs before the model sees a chat/voice message. It never executes anything and never
touches approvals; it only reads real state (conversation, tasks, notifications, repos,
learning ledger) and hands the model an internal note. Where the state does not say what
"das" refers to, the answer is "unresolved" — a reference is never invented.

Pipeline: raw text -> normalise -> resolve context -> intent -> sub-tasks -> risk/confidence
-> internal brief (system prompt only, never shown in the chat).
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta, timezone

from ..db import new_id, now_iso

ACTIVE = ("PLANNING", "RUNNING", "PAUSED", "WAITING_FOR_APPROVAL", "QUEUED")

# Project vocabulary. Only words from here (plus words already used in the conversation)
# are used to repair typos and speech-to-text errors, so ordinary words are left alone.
LEXICON = (
    "chat", "stimme", "sprachsteuerung", "dashboard", "backend", "frontend", "server", "aufgabe", "task",
    "agent", "werkzeug", "tool", "gedächtnis", "memory", "kalender", "termin", "rechnung", "angebot", "kunde",
    "login", "freigabe", "freigaben", "streaming", "session", "protokoll", "logs", "fehler", "deployment",
    "deploy", "repository", "commit", "push", "container", "docker", "datenbank", "test", "tests",
    "regression", "training", "selbstheilung", "watchdog", "meldung", "standort", "buchhaltung",
    "plancraft", "lexware", "gmail", "telefon", "whatsapp", "komponente", "reparier", "repariere",
    "prüfe", "prüf", "fertig", "weiter", "einbauen", "sauber",
)
_COMPOUNDS = {"back end": "backend", "front end": "frontend", "dash board": "dashboard",
              "data bank": "datenbank", "daten bank": "datenbank", "doc ker": "docker"}
_FILLER = re.compile(r"\b(äh+m?|ähm|hm+|also|halt|eben|mal eben|sozusagen|quasi|okay so|ja also)\b[,]?\s*", re.I)
_COLLOQUIAL = {"nee": "nein", "nö": "nein", "jo": "ja", "jau": "ja", "joa": "ja", "guck": "schau", "gucke": "schau"}

# component label -> (detecting words, what a good job on it covers). Nothing beyond these
# steps is added to a task; they are the parts of that component that exist in this project.
COMPONENTS = {
    "Chat": (r"\bchat\w*|\bunterhaltung\w*|\bnachricht\w*", ["Frontend und Backend des Chats", "Session-Verhalten", "Streaming", "Logs"]),
    "Stimme": (r"\bstimme\w*|\bvoice\w*|\bsprach\w*|\bstt\b|\btts\b|\bmikrofon\w*", ["Sprachpfad STT → Text → Agent → TTS", "Session", "Logs"]),
    "Dashboard": (r"\bdashboard\w*|\boberfl(ä|ae)che\w*|\bseite\b", ["Frontend-Ansicht", "zugehörige API-Aufrufe", "Konsolen-/Logfehler"]),
    "Server": (r"\bserver\w*|\bcontainer\w*|\bdocker\w*", ["Container-Status", "Health-Check", "Logs"]),
    "Kalender": (r"\bkalender\w*|\btermin\w*", ["Kalenderdienst", "Integrationsstatus", "Logs"]),
    "Freigaben": (r"\bfreigabe\w*|\bapproval\w*", ["offene Freigaben", "Freigabelogik"]),
    "Aufgaben": (r"\baufgabe\w*|\btask\w*", ["Task-Status", "Laufprotokoll"]),
    "Lernen": (r"\blern\w*|\btraining\w*|\bgedächtnis\w*|\bmemory\b", ["Lernspeicher", "Skills/Prozeduren"]),
}
_FIX = re.compile(r"\b(fertig|repar\w*|fix\w*|behebe\w*|beheb\w*|einbau\w*|sauber|richtig)\b", re.I)
_VERIFY_ONLY = re.compile(r"^(pr(ü|ue)f\w*|schau|check\w*|teste?\w*|zeig\w*|such\w*)\b", re.I)
_ORDER_VERBS = re.compile(
    r"^(mach\w*|bau\w*|pr(ü|ue)f\w*|schau\w*|guck\w*|repar\w*|fix\w*|behebe\w*|beheb\w*|lös\w*|leg\w*|erstell\w*|"
    r"schreib\w*|push\w*|deploy\w*|start\w*|check\w*|teste?\w*|such\w*|zeig\w*|öffne\w*|schick\w*|send\w*|"
    r"lösch\w*|entfern\w*|aktualisier\w*|änder\w*|nimm|füg\w*|bring\w*|und jetzt|jetzt)\b", re.I)
_DEICTIC = re.compile(r"\b(das|dem|den|die|dort|damit|dabei|davon|dieses?|diesen|jenes?|da oben|da unten|das andere|"
                      r"den anderen|alles)\b", re.I)
_TIMEREF = re.compile(r"\bwie (gestern|vorgestern|letzte woche|vorhin|letztes mal|beim letzten mal)\b", re.I)
_SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:und\s+)?(?:danach|dann|anschlie(?:ß|ss)end|hinterher|und dann)\b\s*,?\s*", re.I)
_FIRST = re.compile(r"^\s*(?:mach\s+)?(?:erst(?:mal)?|zuerst|als erstes)\b[,]?\s*", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß0-9_-]+", text or "")


def _known_repos(state) -> list[str]:
    files = state.services.get("files")
    if files is None:
        return []
    try:
        from .repos import RepoService
        return [r["name"] for r in RepoService(files).list()]
    except Exception:  # noqa: BLE001 — the workspace listing must not break understanding
        return []


class CommunicationLayer:
    PATTERN_THRESHOLD = 3          # confirmed uses before a phrase counts as a stable habit
    SHORT_COMMAND_WORDS = 8

    def __init__(self, state) -> None:
        self.state = state
        self.db = state.db

    # ── 1. normalise ─────────────────────────────────────────────────────
    def normalize(self, text: str, *, voice: bool = False, vocab: set[str] | None = None) -> tuple[str, list[dict]]:
        raw = (text or "").strip()
        fixes: list[dict] = []
        t = _FILLER.sub("", raw) if voice else raw
        for a, b in _COMPOUNDS.items():
            if re.search(re.escape(a), t, re.I):
                t = re.sub(re.escape(a), b, t, flags=re.I)
                fixes.append({"from": a, "to": b, "why": "getrenntes Wort zusammengesetzt"})
        out: list[str] = []
        prev = ""
        known = {w.lower() for w in LEXICON} | {w.lower() for w in (vocab or set())}
        for tok in re.findall(r"\S+", t):
            core = tok.strip(".,;:!?\"'()").lower()
            tail = tok[len(tok.rstrip(".,;:!?\"'()")):]
            if core and core == prev:
                fixes.append({"from": core, "to": "", "why": "Wiederholung"})
                continue
            prev = core
            if core in _COLLOQUIAL:
                new = _COLLOQUIAL[core]
                fixes.append({"from": core, "to": new, "why": "Umgangssprache"})
                out.append(new + tail)
                continue
            if len(core) >= 5 and core not in known and core.isalpha():
                cut = 0.78 if voice else 0.84
                cands = difflib.get_close_matches(core, sorted(known), n=2, cutoff=cut)
                if cands:
                    first = difflib.SequenceMatcher(None, core, cands[0]).ratio()
                    second = difflib.SequenceMatcher(None, core, cands[1]).ratio() if len(cands) > 1 else 0.0
                    if first - second >= 0.05:
                        fixes.append({"from": core, "to": cands[0], "why": "Tippfehler/Hörfehler nach Projektvokabular"})
                        out.append(cands[0] + tail)
                        continue
            out.append(tok)
        return " ".join(out).strip(), fixes

    # ── 2. context ───────────────────────────────────────────────────────
    def _history(self, conversation_id: str, current: str, limit: int = 12) -> list[dict]:
        chat = self.state.services.get("chat")
        if not chat or not conversation_id:
            return []
        rows = chat.messages(conversation_id, limit=limit + 2)
        if rows and rows[-1]["role"] == "user" and (rows[-1].get("content") or "").strip() == (current or "").strip():
            rows = rows[:-1]
        return rows[-limit:]

    def _vocab(self, history: list[dict]) -> set[str]:
        out: set[str] = set()
        for m in history:
            out.update(w.lower() for w in _words(m.get("content") or "") if len(w) >= 5)
        return out

    def _active_tasks(self, conversation_id: str) -> tuple[list[dict], str]:
        rows = self.db.fetchall(
            f"SELECT id,title,status,conversation_id,updated_at FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE))}) "
            "AND parent_id IS NULL ORDER BY updated_at DESC LIMIT 20", ACTIVE) or []
        mine = [r for r in rows if r.get("conversation_id") == conversation_id]
        if mine:
            return mine, "Task dieser Unterhaltung"
        return rows, "Task im System"

    def _components_in(self, text: str) -> list[str]:
        return [name for name, (pat, _steps) in COMPONENTS.items() if re.search(pat, text or "", re.I)]

    def _recent_components(self, history: list[dict], n: int = 6) -> list[str]:
        seen: list[str] = []
        for m in reversed(history[-n:]):
            for c in self._components_in(m.get("content") or ""):
                if c not in seen:
                    seen.append(c)
        return seen

    def _last_order(self, history: list[dict]) -> dict | None:
        for m in reversed(history):
            body = (m.get("content") or "").strip()
            if m["role"] == "user" and body and _ORDER_VERBS.search(body) and len(_words(body)) >= 3:
                return m
        return None

    def _resolve_active(self, conversation_id: str, history: list[dict]) -> dict:
        tasks, source = self._active_tasks(conversation_id)
        if len(tasks) == 1:
            t = tasks[0]
            return {"status": "resolved", "kind": "task", "source": source, "id": t["id"],
                    "label": f"{t['title']} ({t['status']})"}
        if len(tasks) > 1:
            return {"status": "ambiguous", "kind": "task", "source": source,
                    "candidates": [f"{t['title']} ({t['status']})" for t in tasks[:5]]}
        last = self._last_order(history)
        if last:
            return {"status": "resolved", "kind": "conversation_order", "source": "letzter Arbeitsauftrag im Gespräch",
                    "label": (last["content"] or "").strip()[:200]}
        return {"status": "unresolved", "kind": "task", "source": "",
                "reason": "Kein aktiver Task und kein früherer Arbeitsauftrag in dieser Unterhaltung."}

    def _resolve_failing(self, text: str, history: list[dict]) -> dict:
        explicit = self._components_in(text)
        if len(explicit) == 1:
            return {"status": "resolved", "kind": "component", "source": "im Satz genannt", "label": explicit[0]}
        recent = self._recent_components(history)
        if len(recent) == 1:
            return {"status": "resolved", "kind": "component", "source": "letzte Nachrichten im Gespräch", "label": recent[0]}
        since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds").replace("+00:00", "Z")
        failed = self.db.fetchall("SELECT title FROM tasks WHERE status='FAILED' AND updated_at>=? ORDER BY updated_at DESC LIMIT 5", (since,)) or []
        alerts = self.db.fetchall("SELECT title FROM notifications WHERE read=0 AND severity IN ('error','warning') "
                                  "AND created_at>=? ORDER BY created_at DESC LIMIT 5", (since,)) or []
        cands = [f"fehlgeschlagener Task: {r['title']}" for r in failed] + [f"Meldung: {r['title']}" for r in alerts]
        if len(cands) == 1:
            return {"status": "resolved", "kind": "failure", "source": "System (letzte 48 h)", "label": cands[0]}
        if cands:
            return {"status": "ambiguous", "kind": "failure", "source": "System (letzte 48 h)", "candidates": cands[:6]}
        return {"status": "unresolved", "kind": "failure", "source": "",
                "reason": "Weder im Gespräch noch im System ist ein Fehler bzw. eine Rotmeldung erkennbar."}

    def _resolve_time(self, text: str) -> dict:
        m = _TIMEREF.search(text or "")
        which = (m.group(1) if m else "").lower()
        now = datetime.now(timezone.utc)
        days = {"gestern": (1, 1), "vorgestern": (2, 2), "letzte woche": (7, 14)}.get(which)
        if not days:
            return {"status": "unresolved", "kind": "history", "source": "",
                    "reason": f"'wie {which}' verweist auf nichts, das aufgezeichnet ist."}
        start = (now - timedelta(days=days[1])).replace(hour=0, minute=0, second=0, microsecond=0)
        end = (now - timedelta(days=days[0] - 1)).replace(hour=0, minute=0, second=0, microsecond=0) if days[0] == days[1] else now
        rows = self.db.fetchall("SELECT title FROM tasks WHERE status='COMPLETED' AND completed_at>=? AND completed_at<? "
                                "ORDER BY completed_at DESC LIMIT 6", (start.isoformat(), end.isoformat())) or []
        if not rows:
            return {"status": "unresolved", "kind": "history", "source": "",
                    "reason": f"Für '{which}' gibt es keine abgeschlossene Arbeit in den Aufzeichnungen."}
        titles = [r["title"] for r in rows]
        if len(titles) == 1:
            return {"status": "resolved", "kind": "history", "source": f"abgeschlossener Task ({which})", "label": titles[0]}
        return {"status": "ambiguous", "kind": "history", "source": f"abgeschlossene Tasks ({which})", "candidates": titles}

    def _resolve_repo(self, history: list[dict]) -> dict:
        names = _known_repos(self.state)
        mentioned = [n for n in names if any(n.lower() in (m.get("content") or "").lower() for m in history)]
        pick = mentioned if mentioned else names
        if len(pick) == 1:
            return {"status": "resolved", "kind": "repo", "source": "Workspace-Repositories", "label": pick[0]}
        if len(pick) > 1:
            return {"status": "ambiguous", "kind": "repo", "source": "Workspace-Repositories", "candidates": pick[:6]}
        return {"status": "unresolved", "kind": "repo", "source": "",
                "reason": "Im Workspace ist kein Repository bekannt und im Gespräch wurde keins genannt."}

    # ── 3. intent ────────────────────────────────────────────────────────
    def classify_intent(self, text: str) -> str:
        t = (text or "").strip().lower()
        n = len(_words(t))
        if re.match(r"^(stopp?|halt( an)?|abbrechen|brich ab|hör auf|lass (es|das) sein)\b", t):
            return "stop"
        if re.match(r"^(nein|lieber nicht|auf keinen fall|ablehnen|nicht freigeben)\b", t):
            return "correction" if (n >= 3 and re.search(r"nicht so|anders|lass|stattdessen|sondern|andere", t)) else "reject"
        if re.match(r"^(ja|okay|ok|klar|passt|freigeben|genehmig\w*|einverstanden)\b", t) and n <= 4:
            return "approve"
        if re.search(r"\b(nicht so|stattdessen|sondern|das andere|den anderen|die andere|nimm (den|die|das) andere\w*|"
                     r"lass (den|die|das)? ?\w* ?(teil )?(wie (er|sie|es) ist|so wie))\b", t) or re.match(r"^(anders|nicht das)\b", t):
            return "correction"
        if re.search(r"\bweiter\b|\bfortsetzen\b|setz\w* .* fort", t) and n <= 6:
            return "continue"
        if _ORDER_VERBS.search(t):
            return "order"
        if t.endswith("?") or re.match(r"^(was|wie|warum|wieso|weshalb|wann|wo|wer|welche\w*|kannst du|gibt es|ist|sind|hast du)\b", t):
            return "question"
        return "information"

    # ── 4. sub-tasks ─────────────────────────────────────────────────────
    def split_tasks(self, text: str) -> list[str]:
        t = _FIRST.sub("", text or "")
        parts = [p.strip(" ,.;") for p in _SPLIT.split(t) if p and p.strip(" ,.;")]
        # "... die Stimme und prüf danach alles": the connector sits inside the second clause, so the verb
        # belongs to the following part, not to the one before it.
        fixed: list[str] = []
        carry = ""
        for p in parts:
            p = (carry + " " + p).strip()
            carry = ""
            m = re.match(r"^(.*\S)\s+und\s+(\w+)$", p, re.I)
            if m and _ORDER_VERBS.match(m.group(2)):
                p, carry = m.group(1), m.group(2)
            fixed.append(re.sub(r"\berst(?:mal)?\b\s*", "", p, count=1, flags=re.I).strip() if not fixed else p)
        if carry:
            fixed.append(carry)
        return fixed if len(fixed) > 1 else []

    # ── pipeline ─────────────────────────────────────────────────────────
    def understand(self, conversation_id: str, text: str, *, voice: bool = False) -> dict:
        history = self._history(conversation_id, text)
        normalized, fixes = self.normalize(text, voice=voice, vocab=self._vocab(history))
        intent = self.classify_intent(normalized)
        words = _words(normalized)
        parts = self.split_tasks(normalized)
        explicit = self._components_in(normalized)
        referential = bool(_DEICTIC.search(normalized)) and len(words) <= self.SHORT_COMMAND_WORDS + 4 and not explicit
        refs: list[dict] = []
        pending = int(self.state.services["approvals"].pending_count()) if self.state.services.get("approvals") else 0

        # a follow-up that says "then / afterwards check everything" refers to the area just worked on
        verify_all = bool(re.search(r"\b(pr(ü|ue)f\w*|check\w*|teste?\w*)\b.*\balles\b|\balles\b.*\bpr(ü|ue)f\w*", normalized, re.I))
        if _TIMEREF.search(normalized):
            refs.append(self._resolve_time(normalized))
        if re.search(r"\bpush\w*|\bdeploy\w*", normalized, re.I) and not explicit:
            refs.append(self._resolve_repo(history))
        elif re.search(r"\bwarum\b.*\b(rot|fehler|kaputt|geht nicht|fehlschl\w*)\b|\b(rot|kaputt)\b", normalized, re.I) and not explicit:
            refs.append(self._resolve_failing(normalized, history))
        elif intent in ("continue", "correction") or (intent == "order" and referential and not explicit):
            refs.append(self._resolve_active(conversation_id, history))
        if verify_all:
            comps = self._recent_components(history) or explicit
            refs.append({"status": "resolved", "kind": "scope", "source": "zuletzt bearbeiteter Bereich" if comps else "System",
                         "label": ", ".join(comps) if comps else "gesamtes System (Gesamttest)"})

        bad = [r for r in refs if r["status"] != "resolved"]
        change = intent in ("order", "correction", "continue") and not (_VERIFY_ONLY.match(normalized) and not _FIX.search(normalized))
        if bad and change:
            policy = "ask"
        elif bad:
            policy = "analyse"
        else:
            policy = "proceed"
        if intent in ("approve", "reject"):
            policy = "approval_via_ui"
        confidence = "low" if bad else ("medium" if (fixes or referential) else "high")
        needed = bool(refs or fixes or parts or referential or intent in ("continue", "correction", "stop", "approve", "reject")
                      or len(words) <= self.SHORT_COMMAND_WORDS)
        u = {"raw": text, "normalized": normalized, "corrections": fixes, "intent": intent, "voice": voice,
             "subtasks": parts, "components": explicit or self._recent_components(history), "references": refs,
             "policy": policy, "confidence": confidence, "pending_approvals": pending,
             "brief_needed": needed}
        u["brief"] = self.brief(u) if needed else ""
        return u

    # ── 5. internal brief ────────────────────────────────────────────────
    def brief(self, u: dict) -> str:
        lines = ["VERSTÄNDNIS-NOTIZ (intern, gehört nicht in die Antwort; der Nutzer redet normal, ohne technische Prompts):",
                 f"- Originalaussage: \"{u['raw']}\""]
        if u["corrections"]:
            lines.append("- Sprachliche Korrekturen (Vermutung, Original hat Vorrang bei Widerspruch): " + "; ".join(
                f"'{c['from']}'→'{c['to'] or '(gestrichen)'}'" for c in u["corrections"][:8]))
        lines.append(f"- Absicht: {u['intent']} · Sicherheit des Verständnisses: {u['confidence']}")
        for r in u["references"]:
            if r["status"] == "resolved":
                lines.append(f"- Bezug aufgelöst ({r['kind']}, Quelle: {r['source']}): {r['label']}")
            elif r["status"] == "ambiguous":
                lines.append(f"- Bezug MEHRDEUTIG ({r['kind']}, Quelle: {r['source']}): " + " | ".join(r["candidates"]))
            else:
                lines.append(f"- Bezug NICHT BESTIMMBAR ({r['kind']}): {r.get('reason', '')}")
        if u["subtasks"]:
            lines.append("- Mehrere Aufträge, Reihenfolge einhalten, nach jedem Schritt verifizieren, erst dann den nächsten:")
            lines.extend(f"    {i}. {p}" for i, p in enumerate(u["subtasks"], 1))
        comps = u["components"]
        if u["intent"] == "order" and _FIX.search(u["normalized"]) and comps:
            steps: list[str] = []
            for c in comps[:2]:
                steps.extend(COMPONENTS[c][1])
            lines.append("- Vorgehen (nur, was sich aus Auftrag und Projekt ergibt): betroffene Stelle bestimmen, bekannte Fehler "
                         f"berücksichtigen, prüfen: {', '.join(dict.fromkeys(steps))}; Ursache beheben, Regressionstest, Ergebnis "
                         "verifizieren. Keine Anforderungen dazu erfinden.")
        if u["intent"] == "correction":
            lines.append("- Korrektur: den bisherigen Auftrag anpassen und alles Unbeanstandete unverändert lassen; nicht von vorn beginnen.")
        if u["intent"] == "continue":
            lines.append("- Fortsetzung: genau den oben aufgelösten Task weiterführen, keinen anderen wählen.")
        if u["intent"] == "stop":
            lines.append("- Stopp: laufende Arbeit beenden, nichts Neues beginnen, kurz bestätigen was gestoppt wurde.")
        if u["intent"] in ("approve", "reject"):
            lines.append(f"- Das ist eine Zustimmung/Ablehnung. Offene Freigaben im System: {u['pending_approvals']}. Freigaben werden nur über "
                         "den Freigabe-Weg erteilt; ein Chat-\"ja\" ersetzt sie nicht.")
        pol = {"ask": "Stelle GENAU EINE gezielte Rückfrage mit den Kandidaten. Keine ändernde Aktion, bevor der Bezug geklärt ist. Nichts raten.",
               "analyse": "Nur ungefährliche Analyse (lesen/prüfen). Bezug in der Antwort offen benennen, nichts verändern.",
               "proceed": "Bezug ist eindeutig: direkt umsetzen, keine unnötige Rückfrage.",
               "approval_via_ui": "Keine Aktion allein aufgrund dieser Antwort ausführen."}[u["policy"]]
        lines.append(f"- Vorgehen bei Unsicherheit: {pol}")
        lines.append("- Freigaberegeln, Rollen und Risikostufen gelten unverändert; kritische Aktionen laufen über die vorhandene Freigabe.")
        return "\n".join(lines)

    # ── 6. learn the user's habits (only stable, confirmed ones) ─────────
    def observe(self, u: dict | None) -> dict | None:
        """Call after a run completed successfully. Stores a short phrase once it recurs."""
        if not u or u.get("policy") in ("ask", "analyse", "approval_via_ui"):
            return None
        phrase = " ".join(_words(u["normalized"].lower()))
        n = len(phrase.split())
        if not phrase or n > self.SHORT_COMMAND_WORDS or u["intent"] in ("information", "question"):
            return None
        target = (u["references"][0]["kind"] if u["references"] else "-")
        row = self.db.fetchone("SELECT * FROM comm_patterns WHERE phrase=? AND intent=? AND target_kind=?",
                               (phrase, u["intent"], target))
        now = now_iso()
        if row:
            uses = int(row["uses"]) + 1
            self.db.execute("UPDATE comm_patterns SET uses=?,last_at=? WHERE id=?", (uses, now, row["id"]))
        else:
            uses = 1
            self.db.execute("INSERT INTO comm_patterns(id,phrase,intent,target_kind,uses,first_at,last_at,promoted) VALUES(?,?,?,?,?,?,?,0)",
                            (new_id("cp"), phrase, u["intent"], target, uses, now, now))
        promoted = False
        if uses >= self.PATTERN_THRESHOLD and not (row and row["promoted"]):
            learning = self.state.services.get("learning")
            if learning is not None:
                learning.record(kind="preference", title=f"Kurzbefehl: \"{phrase}\"",
                                lesson=f"Der Nutzer sagt \"{phrase}\" für die Absicht '{u['intent']}' (Bezug: {target}).",
                                verification=f"{uses} bestätigte Verwendungen (Lauf erfolgreich abgeschlossen).",
                                source="communication", confidence=0.8, tags=["kommunikation", "kurzbefehl"], actor="mia:communication")
                self.db.execute("UPDATE comm_patterns SET promoted=1 WHERE phrase=? AND intent=? AND target_kind=?",
                                (phrase, u["intent"], target))
                promoted = True
        return {"phrase": phrase, "uses": uses, "promoted": promoted}
