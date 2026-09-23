"""Evidence-driven automatic promotion of repeated successful MIA workflows."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from ..db import dumps, loads, now_iso

_SKIP_PREFIXES = ("learning.", "skill.", "knowledge.", "self.", "procedure.", "teach.", "system.")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:42] or "workflow")


class AutoLearningService:
    """Promote repeated, successful, observable tool paths into reusable knowledge.

    1 success: evidence only (pattern counter)
    3 matching successes: deterministic skill + procedure
    5 matching successes: specialist candidate notification, never silently creates a new agent
    """

    def __init__(self, state) -> None:
        self.state = state
        self.db = state.db

    def observe_success(self, *, run_id: str, conversation_id: str = "", task_id: str = "",
                        agent_id: str = "", goal: str = "") -> dict | None:
        rows = self.db.fetchall(
            "SELECT tool,target,result,ts FROM audit_events WHERE run_id=? AND status='ok' AND tool<>'' ORDER BY ts",
            (run_id,)) or []
        steps = []
        for r in rows:
            tool = str(r.get("tool") or "").strip()
            if not tool or tool.startswith(_SKIP_PREFIXES):
                continue
            # repeated identical read-only chatter does not define a workflow
            if steps and steps[-1]["tool"] == tool and steps[-1]["target"] == str(r.get("target") or "")[:160]:
                continue
            steps.append({"tool": tool, "target": str(r.get("target") or "")[:160],
                          "result": str(r.get("result") or "")[:500]})
        if len(steps) < 2:
            return None
        signature = " > ".join(s["tool"] for s in steps[:16])
        fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        now = now_iso()
        row = self.db.fetchone("SELECT * FROM auto_learning_patterns WHERE fingerprint=?", (fingerprint,))
        if row:
            count = int(row["success_count"] or 0) + 1
            examples = loads(row.get("examples"), [])
            if goal and goal not in examples:
                examples = [goal[:500], *examples][:5]
            self.db.execute(
                "UPDATE auto_learning_patterns SET success_count=?, tools=?, last_goal=?, examples=?, updated_at=? WHERE fingerprint=?",
                (count, dumps(steps[:16]), goal[:1200], dumps(examples), now, fingerprint))
            promoted_skill = row.get("promoted_skill") or ""
            procedure_id = row.get("procedure_id") or ""
            specialist_notified = bool(row.get("specialist_notified"))
        else:
            count, promoted_skill, procedure_id, specialist_notified = 1, "", "", False
            examples = [goal[:500]] if goal else []
            self.db.insert("auto_learning_patterns", {
                "fingerprint": fingerprint, "signature": signature[:2000], "success_count": 1,
                "tools": dumps(steps[:16]), "last_goal": goal[:1200], "examples": dumps(examples),
                "promoted_skill": "", "procedure_id": "", "specialist_notified": 0,
                "created_at": now, "updated_at": now,
            })

        out = {"fingerprint": fingerprint, "count": count, "signature": signature}
        learning = self.state.services.get("learning")
        if count == 1 and learning is not None:
            learning.record(kind="strategy", title=f"Erfolgreicher Werkzeugpfad: {signature[:120]}",
                            problem=goal[:1600], lesson=f"Werkzeugfolge: {signature}",
                            verification=f"Run {run_id} erfolgreich abgeschlossen", source="auto-learning",
                            conversation_id=conversation_id or None, confidence=0.55,
                            tags=["auto-observed", fingerprint], actor=f"agent:{agent_id or 'mia'}")

        if count >= 3 and not promoted_skill:
            promoted_skill = self._promote_skill(fingerprint, signature, steps, goal, count)
            procedure_id = self._promote_procedure(fingerprint, signature, steps, goal, count)
            self.db.execute(
                "UPDATE auto_learning_patterns SET promoted_skill=?, procedure_id=?, updated_at=? WHERE fingerprint=?",
                (promoted_skill, procedure_id, now_iso(), fingerprint))
            self.state.services["notifications"].notify(
                category="agent", severity="success", title="MIA hat eine Arbeitsweise gelernt",
                body=f"Nach {count} erfolgreichen Wiederholungen: {promoted_skill}. Als Skill und Procedure gespeichert.",
                link="/teach")
            out.update({"promoted_skill": promoted_skill, "procedure_id": procedure_id})

        if count >= 5 and not specialist_notified:
            self.db.execute("UPDATE auto_learning_patterns SET specialist_notified=1, updated_at=? WHERE fingerprint=?",
                            (now_iso(), fingerprint))
            self.state.services["notifications"].notify(
                category="agent", severity="info", title="Spezialist sinnvoll",
                body=(f"MIA hat denselben Ablauf {count}× erfolgreich ausgeführt ({signature[:180]}). "
                      "Die Procedure ist stabil genug, um daraus bei Bedarf einen Spezialagenten abzuleiten."),
                link="/teach")
            out["specialist_candidate"] = True
        return out

    def _promote_skill(self, fingerprint: str, signature: str, steps: list[dict], goal: str, count: int) -> str:
        lib = self.state.services["skills"]
        name = f"auto-{_slug(steps[0]['tool'])}-{fingerprint[:6]}"
        lines = [f"# Automatisch gelernter Ablauf", "",
                 f"Dieser Ablauf wurde {count} Mal erfolgreich beobachtet.",
                 f"Typisches Ziel: {goal or 'wiederkehrende Aufgabe'}", "", "## Schritte"]
        for i, s in enumerate(steps, 1):
            target = f" auf `{s['target']}`" if s.get("target") else ""
            lines.append(f"{i}. Nutze `{s['tool']}`{target}. Prüfe das Ergebnis, bevor du fortfährst.")
        lines += ["", "## Regel", "Nicht blind ausführen: aktuelle Eingaben, Berechtigungen und Tool-Ergebnisse bleiben maßgeblich."]
        saved = lib.save(name=name, title=f"Auto: {steps[0]['tool']}",
                         description=f"Wiederkehrender verifizierter Ablauf: {signature[:260]}",
                         content="\n".join(lines), actor="mia:auto-learning", source="auto-learning")
        return saved["name"]

    def _promote_procedure(self, fingerprint: str, signature: str, steps: list[dict], goal: str, count: int) -> str:
        teaching = self.state.services["teaching"]
        proc_steps = [{"text": f"{s['tool']} ausführen" + (f" für {s['target']}" if s.get("target") else ""),
                       "tool": s["tool"]} for s in steps]
        proc = teaching.save_procedure(
            name=f"Auto · {steps[0]['tool']} · {fingerprint[:6]}",
            description=f"Von MIA nach {count} erfolgreichen Wiederholungen gelernt: {signature[:500]}",
            goal=goal[:1200], steps=proc_steps, tools=list(dict.fromkeys(s["tool"] for s in steps)),
            created_by="mia:auto-learning", meta={"confidence": "high", "auto_learning": True,
                                                    "fingerprint": fingerprint, "successes": count})
        return proc["id"]

    def patterns(self, limit: int = 50) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM auto_learning_patterns ORDER BY updated_at DESC LIMIT ?", (limit,))
        out=[]
        for r in rows:
            x=dict(r); x["tools"]=loads(x.get("tools"), []); x["examples"]=loads(x.get("examples"), [])
            x["specialist_notified"]=bool(x.get("specialist_notified")); out.append(x)
        return out
