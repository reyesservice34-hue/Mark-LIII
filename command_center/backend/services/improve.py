"""
Besserwerden — die Schleife, die aus Fehlern etwas macht.

Er konnte sich schon Werkzeuge schreiben. Was fehlte, war der Anlass: ohne
Aufforderung passiert nichts, und wer nur auf Zuruf besser wird, wird nicht
von selbst besser.

Diese Schleife sieht in regelmäßigen Abständen nach, was tatsächlich
schiefgegangen ist, und schlägt genau eine Verbesserung vor. Sie baut nur auf
dem auf, was in der Prüfspur steht:

  * Werkzeugaufrufe, die fehlgeschlagen sind — mit ihrer echten Fehlermeldung
  * Werkzeuge, die es gibt, die aber mangels Zugangsdaten nichts tun
  * Aufrufe auf Werkzeuge, die gar nicht existieren — das ist der
    interessanteste Fall: dort wollte er etwas, wofür es nichts gab
  * Läufe, die ohne Ergebnis endeten

Was sie nicht tut: sich Probleme ausdenken. Gibt die Prüfspur nichts her,
sagt sie das und hört auf. Ein erfundener Verbesserungsvorschlag wäre
schlimmer als keiner, weil er Arbeit erzeugt, die niemandem nützt.

Und sie ändert nichts von selbst. Sie schreibt einen Entwurf und legt ihn
zur Freigabe hin — dieselbe Grenze wie überall sonst.
"""
from __future__ import annotations

import json
import re
from collections import Counter

from ..db import Database, new_id, now_iso

# Wie weit zurück geschaut wird. Kürzer wäre Rauschen, länger wären Probleme,
# die längst behoben sind.
WINDOW_HOURS = 24
MIN_EVIDENCE = 2          # einmal ist ein Zufall, zweimal ein Muster

REFLECT_SYSTEM = """You are JARVIS reviewing your own last day of work.

You are given real evidence from the audit trail: tool calls that failed,
tools that exist but are unconfigured, calls to tools that do not exist, and
runs that ended without a result. Nothing here is hypothetical.

Propose exactly ONE improvement, the one that would remove the most friction.
Prefer, in this order:

1. A new tool you could write yourself, when the evidence shows you repeatedly
   wanted something that does not exist. This is the best kind: you can build
   it, and it makes you permanently more capable.
2. A fix to one of your own source files, when the evidence shows a real
   defect — a wrong message, a missing case, an unhelpful refusal.
3. A configuration the user must supply, when the evidence shows a tool is
   only missing credentials. You cannot fix this yourself; say exactly which
   variable and what it unlocks.

Rules you do not break:

* Only use the evidence given. Do not invent a problem, and do not propose
  something "nice to have" that the evidence does not support.
* If the evidence does not justify any improvement, say so. That is a valid
  and useful answer.
* Be concrete. "Improve error handling" is not a proposal. "email.send says
  'failed' without the SMTP reason, so nobody can tell a wrong password from a
  blocked port" is.

Answer as JSON only:
{"kind": "tool" | "source" | "config" | "none",
 "title": "one line, German",
 "why": "what the evidence shows, German, two sentences at most",
 "what": "what exactly to build or change, German",
 "evidence": ["the concrete lines from the audit trail that support this"],
 "tool_name": "area.action if kind is tool, else empty",
 "file": "path if kind is source, else empty",
 "variable": "env var if kind is config, else empty"}
"""


class ImprovementService:
    def __init__(self, db: Database, bus, log) -> None:
        self.db = db
        self.bus = bus
        self.log = log

    # ── was wirklich schiefging ──────────────────────────────────────────
    def evidence(self, hours: int = WINDOW_HOURS) -> dict:
        """Die Prüfspur befragen. Keine Vermutungen, nur Zeilen."""
        rows = self.db.fetchall(
            "SELECT tool, status, error, result, agent_id, ts FROM audit_events "
            "WHERE ts >= datetime('now', ?) ORDER BY ts DESC LIMIT 800",
            (f"-{int(hours)} hours",)) or []

        failed: Counter = Counter()
        messages: dict[str, str] = {}
        missing_tool: Counter = Counter()
        unconfigured: dict[str, str] = {}

        for r in rows:
            tool = (r["tool"] or "").strip()
            status = (r["status"] or "").lower()
            err = (r["error"] or r["result"] or "").strip()
            if status in ("ok", "success", ""):
                continue
            if not tool:
                continue
            # „Tool 'x' does not exist" ist der wertvollste Hinweis überhaupt:
            # dort wollte er etwas, wofür es nichts gab.
            m = re.search(r"[Tt]ool '([^']+)' does not exist", err)
            if m:
                missing_tool[m.group(1)] += 1
                continue
            m = re.search(r"is not available: (.+)", err)
            if m:
                unconfigured[tool] = m.group(1)[:160]
                continue
            failed[tool] += 1
            messages.setdefault(tool, err[:240])

        runs = self.db.fetchall(
            "SELECT status, COUNT(*) AS n FROM agent_runs "
            "WHERE started_at >= datetime('now', ?) GROUP BY status", (f"-{int(hours)} hours",)) or []

        return {
            "window_hours": hours,
            "failed_tools": [{"tool": t, "count": n, "message": messages.get(t, "")}
                             for t, n in failed.most_common(8)],
            "missing_tools": [{"wanted": t, "count": n} for t, n in missing_tool.most_common(8)],
            "unconfigured": [{"tool": t, "reason": why} for t, why in list(unconfigured.items())[:8]],
            "runs": {r["status"]: r["n"] for r in runs},
            "audit_rows_seen": len(rows),
        }

    @staticmethod
    def worth_reflecting(ev: dict) -> bool:
        """Lohnt sich das Nachdenken überhaupt?"""
        signal = (sum(f["count"] for f in ev["failed_tools"])
                  + sum(m["count"] for m in ev["missing_tools"])
                  + len(ev["unconfigured"]))
        return signal >= MIN_EVIDENCE

    # ── daraus einen Vorschlag machen ────────────────────────────────────
    async def reflect(self, state, hours: int = WINDOW_HOURS) -> dict:
        ev = self.evidence(hours)
        if not self.worth_reflecting(ev):
            return {"kind": "none",
                    "title": "Nichts zu verbessern",
                    "why": (f"In den letzten {hours} Stunden gab es {ev['audit_rows_seen']} Einträge "
                            f"in der Prüfspur und darin kein wiederkehrendes Problem."),
                    "evidence": [], "what": "", "tool_name": "", "file": "", "variable": ""}

        provider = getattr(state.runtime, "provider", None)
        if provider is None:
            return {"kind": "none", "title": "Kein Modell eingerichtet",
                    "why": "Ohne AI-Anbieter kann ich nicht über meine eigenen Fehler nachdenken.",
                    "evidence": [], "what": "", "tool_name": "", "file": "", "variable": ""}

        known = sorted(t.name for t in state.tools.all())
        prompt = (f"Evidence from the audit trail:\n{json.dumps(ev, indent=2, ensure_ascii=False)}\n\n"
                  f"Tools you already have: {', '.join(known)}\n\n"
                  f"Answer with the JSON object only.")
        text = ""
        async for chunk in provider.stream(system=REFLECT_SYSTEM,
                                           messages=[{"role": "user",
                                                      "content": [{"type": "text", "text": prompt}]}],
                                           tools=[], max_tokens=2000):
            if chunk.get("type") == "text_delta":
                text += chunk["text"]
            elif chunk.get("type") == "error":
                return {"kind": "none", "title": "Nachdenken fehlgeschlagen",
                        "why": chunk.get("message", "unbekannt"), "evidence": [],
                        "what": "", "tool_name": "", "file": "", "variable": ""}

        proposal = self._parse(text)
        proposal.setdefault("evidence", [])
        proposal["_evidence_raw"] = ev
        return proposal

    @staticmethod
    def _parse(text: str) -> dict:
        """Das Modell antwortet gelegentlich mit Fließtext drumherum."""
        raw = (text or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {"kind": "none", "title": "Keine verwertbare Antwort",
                    "why": "Das Modell hat kein JSON zurückgegeben.", "evidence": [],
                    "what": raw[:300], "tool_name": "", "file": "", "variable": ""}
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return {"kind": "none", "title": "Antwort nicht lesbar",
                    "why": "Das JSON war beschädigt.", "evidence": [],
                    "what": raw[:300], "tool_name": "", "file": "", "variable": ""}
        if data.get("kind") not in ("tool", "source", "config", "none"):
            data["kind"] = "none"
        for key in ("title", "why", "what", "tool_name", "file", "variable"):
            data.setdefault(key, "")
        return data

    # ── ablegen, damit es nicht verpufft ─────────────────────────────────
    def record(self, proposal: dict, *, actor: str = "jarvis") -> dict:
        """Den Vorschlag als Aufgabe hinterlegen und melden."""
        if proposal.get("kind") == "none":
            return {"recorded": False, "reason": proposal.get("why", "")}
        pid = new_id("imp")
        body = "\n".join(x for x in [
            proposal.get("why", ""),
            "",
            proposal.get("what", ""),
            "",
            ("Belege aus der Prüfspur:\n" + "\n".join(f"- {e}" for e in proposal.get("evidence", [])[:6]))
            if proposal.get("evidence") else "",
        ] if x)
        self.db.insert("memory", {
            "id": new_id("mem"), "actor": actor, "conversation_id": None, "created_at": now_iso(),
            "text": f"Selbstbeobachtung: {proposal.get('title', '')} — {proposal.get('why', '')[:300]}",
        })
        self.log.info("improve", f"Verbesserungsvorschlag: {proposal.get('title', '')}")
        self.bus.publish("improvement.proposed", {
            "id": pid, "kind": proposal.get("kind"), "title": proposal.get("title"),
            "tool_name": proposal.get("tool_name", ""), "file": proposal.get("file", ""),
            "variable": proposal.get("variable", ""),
        })
        return {"recorded": True, "id": pid, "title": proposal.get("title", ""), "body": body,
                "kind": proposal.get("kind")}


__all__ = ["ImprovementService", "WINDOW_HOURS", "MIN_EVIDENCE"]
