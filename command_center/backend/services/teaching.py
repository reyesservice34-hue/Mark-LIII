"""
Teach mode — show JARVIS once, let it keep the lesson.

A recording is a trace of a real working session: what you said, which tools
actually ran with which arguments and what came back, and every desktop action
that was executed. It is not a screen capture and it does not guess at intent
from pixels; it records what the system genuinely observed.

Distilling turns that trace into a **procedure**: a name, a goal, ordered
steps, the tools it needs, and an optional trigger. The distillation is done by
the configured model reading the trace, and it is constrained to the tools that
actually exist — a procedure that references a tool the server does not have is
rejected rather than stored as a promise.

A procedure can also produce a **specialist agent**: a persisted roster entry
with the instructions the model wrote, limited to the tools the procedure needs.
It appears in the Agents page like any other agent and survives restarts.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..ai.base import trim
from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus
from ..logbook import LogBook

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import AppState

MAX_EVENTS = 400
_NAME_RE = re.compile(r"[^a-z0-9]+")

DISTILL_SYSTEM = """You turn a recorded working session into a reusable procedure.

You are given a trace: what the user said, which tools ran with which arguments, what
they returned, and any desktop actions. Write the procedure that would repeat this work.

Rules:
- Use ONLY tools from the AVAILABLE TOOLS list. Never invent one.
- Steps are imperative and concrete, in the order they must happen. Name the tool each
  step uses when it uses one.
- Where the recording contained a specific value that would change next time (a customer
  name, a date, a file), write it as a {placeholder} and list it in "inputs".
- If the trace is too thin to repeat reliably, say so in "confidence" and keep the steps
  to what you actually saw.

Answer with ONE JSON object and nothing else:
{
  "name": "short imperative name",
  "description": "one sentence a human reads in a list",
  "goal": "what a successful run achieves",
  "inputs": [{"name": "placeholder", "description": "what to fill in"}],
  "steps": [{"text": "do this", "tool": "tool.name or empty"}],
  "tools": ["tool.name", ...],
  "confidence": "high|medium|low",
  "notes": "anything the user should know, or empty",
  "agent": {
    "name": "Specialist name",
    "role": "one line",
    "instructions": "how this specialist should work, written for it to follow",
    "capabilities": ["...", "..."]
  }
}"""


class TeachingError(Exception):
    """Something the user needs to hear, phrased plainly."""


def _slug(text: str) -> str:
    return _NAME_RE.sub("-", (text or "").strip().lower()).strip("-")[:48] or "procedure"


class TeachingService:
    def __init__(self, db: Database, bus: EventBus, log: LogBook):
        self.db = db
        self.bus = bus
        self.log = log
        self._active: dict[str, str] = {}      # user_id → recording_id
        self._reload_active()

    def _reload_active(self) -> None:
        for row in self.db.fetchall("SELECT id, user_id FROM recordings WHERE status='recording'"):
            self._active[row["user_id"]] = row["id"]

    # ── recording ────────────────────────────────────────────────────────
    def active_for(self, user_id: str) -> str | None:
        return self._active.get(user_id)

    def any_active(self) -> bool:
        return bool(self._active)

    def start(self, *, title: str, goal: str = "", user_id: str = "", actor: str = "",
              conversation_id: str | None = None) -> dict:
        if self._active.get(user_id):
            raise TeachingError("A recording is already running. Stop it before starting another.")
        row = {"id": new_id("rec"), "title": title.strip()[:120] or "Untitled recording", "goal": goal[:2000],
               "status": "recording", "user_id": user_id, "actor": actor,
               "conversation_id": conversation_id, "started_at": now_iso(), "ended_at": None,
               "procedure_id": None, "meta": "{}"}
        self.db.insert("recordings", row)
        self._active[user_id] = row["id"]
        self.append(row["id"], kind="start", text=f"Recording started: {row['title']}"
                    + (f" — goal: {goal}" if goal else ""), actor=actor)
        recording = self.get(row["id"])
        self.bus.publish("recording.started", recording)
        self.log.info("teach", f"Recording started: {row['title']}", data={"recording_id": row["id"]})
        return recording

    def stop(self, recording_id: str = "", user_id: str = "") -> dict:
        recording_id = recording_id or self._active.get(user_id, "")
        if not recording_id:
            raise TeachingError("Nothing is being recorded right now.")
        row = self.db.fetchone("SELECT * FROM recordings WHERE id=?", (recording_id,))
        if not row:
            raise TeachingError("That recording does not exist.")
        if row["status"] == "recording":
            self.append(recording_id, kind="stop", text="Recording stopped")
            self.db.update("recordings", recording_id, {"status": "recorded", "ended_at": now_iso()})
        self._active = {u: r for u, r in self._active.items() if r != recording_id}
        recording = self.get(recording_id)
        self.bus.publish("recording.stopped", recording)
        return recording

    def append(self, recording_id: str, *, kind: str, text: str = "", tool: str = "",
               params: dict | None = None, result: str = "", ok: bool = True, actor: str = "") -> dict | None:
        row = self.db.fetchone("SELECT status FROM recordings WHERE id=?", (recording_id,))
        if not row or row["status"] != "recording" and kind not in ("stop",):
            return None
        count = int(self.db.scalar("SELECT COUNT(*) FROM recording_events WHERE recording_id=?",
                                   (recording_id,)) or 0)
        if count >= MAX_EVENTS:
            return None
        event = {"id": new_id("ev"), "recording_id": recording_id, "ts": now_iso(), "kind": kind,
                 "text": trim(text, 2000), "tool": tool, "params": dumps(params or {}),
                 "result": trim(result, 2000), "ok": 1 if ok else 0, "actor": actor}
        self.db.insert("recording_events", event)
        event["params"] = params or {}
        self.bus.publish("recording.event", event)
        return event

    # -- hooks used by the rest of the system --
    def record_tool_call(self, *, user_id: str, tool: str, params: dict, result: str, ok: bool,
                         agent_id: str = "") -> None:
        rec = self._active.get(user_id)
        if rec:
            self.append(rec, kind="tool", tool=tool, params=params, result=result, ok=ok, actor=agent_id)

    def record_message(self, *, user_id: str, role: str, text: str) -> None:
        rec = self._active.get(user_id)
        if rec and text.strip():
            self.append(rec, kind="said" if role == "user" else "answered", text=text, actor=role)

    def record_desktop(self, command: dict, actor: str = "") -> None:
        for rec in set(self._active.values()):
            self.append(rec, kind="desktop", tool=f"desktop:{command['action']}",
                        params=command.get("params") or {}, result=command.get("result", ""),
                        ok=command.get("status") == "done", actor=actor)

    # ── read ─────────────────────────────────────────────────────────────
    def get(self, recording_id: str, with_events: bool = True) -> dict | None:
        row = self.db.fetchone("SELECT * FROM recordings WHERE id=?", (recording_id,))
        if not row:
            return None
        out = dict(row)
        out["meta"] = loads(row.get("meta"), {})
        if with_events:
            out["events"] = self.events(recording_id)
        out["event_count"] = int(self.db.scalar(
            "SELECT COUNT(*) FROM recording_events WHERE recording_id=?", (recording_id,)) or 0)
        return out

    def events(self, recording_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM recording_events WHERE recording_id=? ORDER BY seq", (recording_id,))
        for r in rows:
            r["params"] = loads(r["params"], {})
            r["ok"] = bool(r["ok"])
        return rows

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM recordings ORDER BY started_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = loads(r["meta"], {})
            r["event_count"] = int(self.db.scalar(
                "SELECT COUNT(*) FROM recording_events WHERE recording_id=?", (r["id"],)) or 0)
        return rows

    # ── distillation ─────────────────────────────────────────────────────
    def transcript(self, recording_id: str) -> str:
        lines = []
        for e in self.events(recording_id):
            if e["kind"] in ("said", "answered", "note", "start", "stop"):
                lines.append(f"[{e['kind']}] {e['text']}")
            elif e["kind"] in ("tool", "desktop"):
                lines.append(f"[{e['kind']}] {e['tool']}({json.dumps(e['params'], ensure_ascii=False)[:300]})"
                             f" -> {'ok' if e['ok'] else 'ERROR'}: {trim(e['result'], 300)}")
        return "\n".join(lines)

    async def distill(self, state: "AppState", recording_id: str, *, create_agent: bool = True,
                      actor: str = "") -> dict:
        recording = self.get(recording_id)
        if not recording:
            raise TeachingError("That recording does not exist.")
        events = recording["events"]
        if len(events) < 3:
            raise TeachingError("There is almost nothing in that recording — do the work once with "
                                "JARVIS while recording, then try again.")
        runtime = state.runtime
        if runtime.mode != "local" or runtime.provider is None:
            raise TeachingError("Distilling a recording needs a local AI provider. Configure one, or "
                                "write the procedure by hand.")
        # The model is offered what works right now, but the procedure is validated
        # against every registered tool: a step that needs the mailbox is worth
        # keeping even while the mailbox is unconfigured — the run will say so.
        available = [t.name for t in state.tools.available()]
        known = {t.name for t in state.tools.all()}
        prompt = (f"RECORDING: {recording['title']}\n"
                  f"GOAL AS STATED: {recording['goal'] or '(none stated)'}\n\n"
                  f"AVAILABLE TOOLS:\n{', '.join(available)}\n\n"
                  f"TRACE:\n{self.transcript(recording_id)}")
        text_parts: list[str] = []
        async for ev in runtime.provider.stream(system=DISTILL_SYSTEM,
                                                messages=[{"role": "user",
                                                           "content": [{"type": "text", "text": prompt}]}],
                                                tools=[], max_tokens=8000):
            if ev["type"] == "text_delta":
                text_parts.append(ev["text"])
            elif ev["type"] == "error":
                raise TeachingError(f"The model could not read the recording: {ev['message']}")
        data = _extract_json("".join(text_parts))
        if not isinstance(data, dict) or not data.get("steps"):
            raise TeachingError("The model did not return a usable procedure.")

        wanted = [t for t in (data.get("tools") or []) if isinstance(t, str)]
        unknown = [t for t in wanted if t not in known]
        tools = [t for t in wanted if t in known]
        needs_setup = [t for t in tools if t not in available]
        steps = [{"text": str(s.get("text", "")).strip(), "tool": str(s.get("tool", "")).strip()}
                 for s in data.get("steps", []) if str(s.get("text", "")).strip()]
        for s in steps:
            if s["tool"] and s["tool"] not in known:
                s["tool"] = ""
            elif s["tool"] and s["tool"] not in available and s["tool"] not in needs_setup:
                needs_setup.append(s["tool"])
        procedure = self.save_procedure(
            name=str(data.get("name") or recording["title"]), description=str(data.get("description", "")),
            goal=str(data.get("goal") or recording["goal"]), steps=steps, tools=tools,
            recording_id=recording_id, created_by=actor,
            meta={"inputs": data.get("inputs") or [], "confidence": data.get("confidence", ""),
                  "notes": data.get("notes", ""), "dropped_tools": unknown,
                  "needs_setup": needs_setup})
        self.db.update("recordings", recording_id, {"procedure_id": procedure["id"], "status": "learned"})

        agent = None
        if create_agent and isinstance(data.get("agent"), dict):
            agent = self.create_agent(state, procedure, data["agent"], actor=actor)
            self.db.update("procedures", procedure["id"], {"agent_id": agent["id"], "updated_at": now_iso()})
            procedure["agent_id"] = agent["id"]

        # The lesson also goes into memory, so the master agent can recall it in chat.
        self.db.insert("memory", {
            "id": new_id("mem"),
            "text": f"Learned procedure '{procedure['name']}': {procedure['description']} "
                    f"Steps: " + " | ".join(s["text"] for s in steps[:12]),
            "actor": actor, "conversation_id": recording.get("conversation_id"), "created_at": now_iso()})

        self.log.info("teach", f"Recording distilled into procedure '{procedure['name']}'"
                      + (f" and agent '{agent['name']}'" if agent else ""),
                      data={"procedure_id": procedure["id"], "recording_id": recording_id})
        self.bus.publish("procedure.created", procedure)
        return {"procedure": procedure, "agent": agent, "dropped_tools": unknown,
                "needs_setup": needs_setup}

    # ── procedures ───────────────────────────────────────────────────────
    def save_procedure(self, *, name: str, description: str = "", goal: str = "", steps: list[dict],
                       tools: list[str], trigger: dict | None = None, recording_id: str | None = None,
                       created_by: str = "", agent_id: str = "", meta: dict | None = None) -> dict:
        row = {"id": new_id("proc"), "name": name.strip()[:120] or "Procedure", "description": description[:1000],
               "goal": goal[:2000], "steps": dumps(steps), "tools": dumps(tools),
               "trigger": dumps(trigger or {"type": "manual"}), "agent_id": agent_id,
               "recording_id": recording_id, "enabled": 1, "created_at": now_iso(), "updated_at": now_iso(),
               "created_by": created_by, "runs": 0, "last_run_at": None, "last_status": "",
               "meta": dumps(meta or {})}
        self.db.insert("procedures", row)
        return self.procedure(row["id"])

    def procedure(self, procedure_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM procedures WHERE id=?", (procedure_id,))
        if not row:
            return None
        out = dict(row)
        out["steps"] = loads(row["steps"], [])
        out["tools"] = loads(row["tools"], [])
        out["trigger"] = loads(row["trigger"], {})
        out["meta"] = loads(row["meta"], {})
        out["enabled"] = bool(row["enabled"])
        return out

    def procedures(self, limit: int = 100) -> list[dict]:
        return [self.procedure(r["id"]) for r in self.db.fetchall(
            "SELECT id FROM procedures ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def update_procedure(self, procedure_id: str, **fields) -> dict | None:
        allowed = {"name", "description", "goal", "steps", "tools", "trigger", "enabled", "agent_id"}
        values: dict[str, Any] = {}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            values[k] = dumps(v) if k in ("steps", "tools", "trigger") else (
                (1 if v else 0) if k == "enabled" else v)
        if not values:
            return self.procedure(procedure_id)
        values["updated_at"] = now_iso()
        self.db.update("procedures", procedure_id, values)
        procedure = self.procedure(procedure_id)
        self.bus.publish("procedure.updated", procedure)
        return procedure

    def delete_procedure(self, procedure_id: str) -> bool:
        cur = self.db.execute("DELETE FROM procedures WHERE id=?", (procedure_id,))
        if cur.rowcount:
            self.bus.publish("procedure.deleted", {"id": procedure_id})
        return cur.rowcount > 0

    def mark_run(self, procedure_id: str, status: str) -> None:
        row = self.db.fetchone("SELECT runs FROM procedures WHERE id=?", (procedure_id,))
        self.db.update("procedures", procedure_id, {"runs": int((row or {}).get("runs", 0)) + 1,
                                                    "last_run_at": now_iso(), "last_status": status})

    def briefing(self, procedure: dict) -> str:
        """The instruction a run hands to the agent."""
        lines = [f"Run the learned procedure “{procedure['name']}”.",
                 f"Goal: {procedure['goal'] or procedure['description']}", "", "Steps:"]
        for i, step in enumerate(procedure["steps"], 1):
            lines.append(f"{i}. {step['text']}" + (f"  [tool: {step['tool']}]" if step.get("tool") else ""))
        inputs = (procedure.get("meta") or {}).get("inputs") or []
        if inputs:
            lines += ["", "Values to fill in (ask if you do not have them):"]
            lines += [f"- {{{i.get('name')}}}: {i.get('description', '')}" for i in inputs if i.get("name")]
        notes = (procedure.get("meta") or {}).get("notes")
        if notes:
            lines += ["", f"Note from when this was learned: {notes}"]
        needs_setup = (procedure.get("meta") or {}).get("needs_setup") or []
        if needs_setup:
            lines += ["", "These steps need a connection that is not set up yet: "
                      + ", ".join(needs_setup) + ". Do the rest and say plainly what you could not do."]
        lines += ["", "Follow the steps in order. If a step cannot be done, say which one and why "
                      "instead of skipping it silently."]
        return "\n".join(lines)

    # ── learned agents ───────────────────────────────────────────────────
    def create_agent(self, state: "AppState", procedure: dict, spec: dict, actor: str = "") -> dict:
        from ..orchestrator.agent_registry import AgentSpec
        base = _slug(spec.get("name") or procedure["name"])
        agent_id = base
        n = 2
        while state.agents.get(agent_id):
            agent_id = f"{base}-{n}"
            n += 1
        tools = list(dict.fromkeys([*procedure["tools"], "task.*", "memory.*", "notify.user"]))
        row = {"id": agent_id, "name": str(spec.get("name") or procedure["name"])[:80],
               "role": str(spec.get("role", ""))[:200],
               "description": f"Learned from the recording “{procedure['name']}”.",
               "instructions": str(spec.get("instructions", "")) + "\n\n" + self.briefing(procedure),
               "capabilities": dumps([str(c) for c in (spec.get("capabilities") or [])]),
               "tools": dumps(tools), "icon": "sparkles", "enabled": 1,
               "procedure_id": procedure["id"], "created_at": now_iso(), "created_by": actor}
        self.db.upsert("learned_agents", row)
        state.agents.register(AgentSpec(
            id=agent_id, name=row["name"], role=row["role"], description=row["description"],
            instructions=row["instructions"], capabilities=loads(row["capabilities"], []),
            tools=tools, source="learned", icon="sparkles",
            config={"procedure_id": procedure["id"]}), replace=True)
        self.bus.publish("agent.learned", {"id": agent_id, "name": row["name"],
                                           "procedure_id": procedure["id"]})
        return {"id": agent_id, "name": row["name"], "role": row["role"], "tools": tools}

    @staticmethod
    def load_learned_agents(db: Database, registry) -> int:
        from ..orchestrator.agent_registry import AgentSpec
        rows = db.fetchall("SELECT * FROM learned_agents")
        for r in rows:
            registry.register(AgentSpec(
                id=r["id"], name=r["name"], role=r["role"], description=r["description"],
                instructions=r["instructions"], capabilities=loads(r["capabilities"], []),
                tools=loads(r["tools"], []), enabled=bool(r["enabled"]), source="learned",
                icon=r["icon"] or "sparkles", config={"procedure_id": r["procedure_id"]}), replace=True)
        return len(rows)


def _extract_json(text: str):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    for candidate in (cleaned, cleaned[cleaned.find("{"):cleaned.rfind("}") + 1] if "{" in cleaned else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None
