"""
Master Agent runtime.

One `MasterRuntime` per process. It owns the provider, runs agent loops as
background asyncio tasks, streams their progress over the event bus, and is
the only code path that executes tools — through `ToolExecutor`, which
applies role checks, the approval gate and the audit trail.

Two modes, chosen from the environment (see config.resolved_master_mode):
  local   — the loop below talks to an AI provider and the tool registry;
  remote  — the message is handed to the upstream control plane
            (core/control_plane.py, the contract the desktop already speaks)
            and its answer is relayed verbatim.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import platform
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..ai import build_provider
from ..ai.base import LLMProvider, text_of, trim
from ..auth import ROLE_RANK, Principal
from ..config import REPO_ROOT
from ..db import dumps, loads, new_id, now_iso
from .tool_registry import ToolContext, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import AppState

STATUS_LABELS = {
    "planning": "ANALYZING REQUEST", "executing": "TASK IN PROGRESS", "waiting": "AWAITING APPROVAL",
    "completed": "TASK COMPLETED", "failed": "TASK FAILED", "cancelled": "STOPPED", "delegated": "AGENT ACTIVE",
}
_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".yml", ".yaml",
             ".toml", ".ini", ".log", ".xml", ".sh", ".env.example", ".sql"}


# Die Live-Konsole liest jede Antwort laut vor. Fähigkeiten und Werkzeuge bleiben dieselben wie im Chat;
# nur die Form der Antwort ändert sich.
VOICE_HINT = (
    "\n\nSPRACHMODUS (Live-Konsole): Deine Antwort wird laut vorgelesen. Sprich wie ein aufmerksamer Mensch am "
    "Telefon: normales gesprochenes Deutsch, meist ein bis drei Sätze, erst das Ergebnis, Details nur auf Nachfrage. "
    "Kein Markdown, keine Listen, keine Tabellen, keine Links oder Codeblöcke, Zahlen und Uhrzeiten so, wie man "
    "sie spricht. Wenn du ein Werkzeug brauchst, benutze es wie im Chat, sag vorher in einem kurzen Satz, was du "
    "tust, und danach knapp, was dabei herauskam. Lange Texte (E-Mail-Entwürfe, Berichte) legst du ab und "
    "nennst nur das Wesentliche; zeig sie im Dashboard. Was eine Freigabe braucht, fragst du kurz laut: der Master "
    "kann mit „ja“ oder „nein“ antworten."
)


# Schnell zuerst, gründlich wenn es nötig ist: Standard ist das schnelle Modell. Auf das stärkere (Sonnet 5)
# wechselt Jarvis von selbst, entweder gleich, weil die Aufgabe offensichtlich schwer ist, oder mittendrin über
# das Werkzeug think.deeper, wenn er merkt, dass es knifflig wird.
_DEEP_RE = re.compile(
    r"\b(angebot|kalkul|rechnung|abschlag|nachtrag|vertrag|recht|norm|din|steuer|haftung|gew(ä|ae)hrleistung|"
    r"analys|strategie|vergleich|bericht|konzept|plan(e|ung)?|kollision|optimier|ausf(ü|ue)hrlich|"
    r"schritt f(ü|ue)r schritt|entwurf|beschwerde|reklamation|mahnung|verhandl|begr(ü|ue)nd|warum)\w*", re.I)


def needs_deep(goal: str) -> bool:
    g = (goal or "").strip()
    return len(g) > 400 or bool(_DEEP_RE.search(g))


MODEL_HINT = (
    "\n\nMODELLWAHL: Du läufst gerade auf dem schnellen Modell, damit du zügig antwortest. Wird eine Aufgabe "
    "knifflig (Kalkulation oder Angebot, Verträge, Rechtliches oder Normen, heikle Kundenmails, längere Planung, "
    "Terminkollisionen mit mehreren Beteiligten, mehrstufige Probleme), rufst du zuerst think.deeper auf. Danach "
    "antwortet das stärkere Modell. Beide Modelle sind kostenlos. Einfache Fragen, Nachschlagen, kurze Antworten und "
    "Routine erledigst du selbst, ohne zu wechseln."
)


@dataclass
class RunHandle:
    id: str
    agent_id: str
    principal: Principal
    conversation_id: str | None
    message_id: str | None
    task_id: str | None
    parent_run_id: str | None = None
    depth: int = 0
    status: str = "planning"
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    steps: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    started_at: str = field(default_factory=now_iso)
    text: str = ""
    voice: bool = False          # Antwort wird vorgelesen (Live-Konsole)
    deep: bool = False           # stärkeres Modell (Sonnet 5) statt des schnellen

    def public(self) -> dict:
        return {"id": self.id, "agent_id": self.agent_id, "conversation_id": self.conversation_id,
                "message_id": self.message_id, "task_id": self.task_id, "parent_run_id": self.parent_run_id,
                "status": self.status, "label": STATUS_LABELS.get(self.status, self.status.upper()),
                "started_at": self.started_at, "steps": self.steps[-50:], "usage": self.usage,
                "initiated_by": self.principal.actor}


def _persona_from_repo() -> str:
    """Reuse the desktop persona (core/prompt.txt) so both faces share one identity."""
    try:
        text = (REPO_ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    keep = []
    for heading in ("IDENTITY:", "LANGUAGE:"):
        start = text.find(heading)
        if start == -1:
            continue
        rest = text[start:]
        m = re.search(r"\n[A-Z][A-Z \-—/]+:\n", rest[len(heading):])
        keep.append(rest[: len(heading) + m.start()] if m else rest)
    return "\n\n".join(k.strip() for k in keep)


def core_memory(state, limit: int = 0) -> str:
    """Das Hauptgedächtnis als Text — für jeden Systemtext, jedes Mal.

    Der Nutzer wollte ausdrücklich, dass er das VOR jeder Antwort und jeder
    Handlung kennt. Ein Werkzeug, das er aufrufen könnte, erfüllt das nicht:
    Er würde es manchmal aufrufen und manchmal nicht. Also steht es im Text,
    bevor die erste Frage kommt, und kann gar nicht übersehen werden.
    """
    if not limit:
        # Die Obergrenze steht an einer Stelle: im Modul, das sie durchsetzt.
        # Sie hier noch einmal hinzuschreiben hieße, sie beim nächsten Ändern
        # zu vergessen — und dann fehlten Sätze, die der Nutzer eingetragen hat.
        from ..modules.memory import MAX_PINNED
        limit = MAX_PINNED
    try:
        rows = state.db.fetchall(
            "SELECT text FROM memory WHERE pinned=1 ORDER BY created_at LIMIT ?", (limit,))
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    lines = "\n".join(f"- {r['text']}" for r in rows)
    return ("HAUPTGEDÄCHTNIS — das hier gilt, ohne Ausnahme, in jeder Antwort und vor jeder "
            "Handlung. Widerspricht eine Anfrage dem, sag es, statt es zu übergehen:\n" + lines)


def recall_memory(state, text: str, limit: int = 6) -> str:
    """Erinnerungen, die zur aktuellen Frage passen — zusätzlich zum Hauptgedächtnis, bei jeder Anfrage neu.

    Das Hauptgedächtnis (angeheftete Sätze) steht ohnehin immer im Text. Der große Rest wird hier nach den
    Wörtern der Frage durchsucht, damit er beim Antworten nicht vergessen wird.
    """
    words = {w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{5,}", text or "")}
    if not words:
        return ""
    try:
        rows = state.db.fetchall("SELECT text FROM memory WHERE pinned=0 ORDER BY created_at DESC LIMIT 400")
    except Exception:  # noqa: BLE001
        return ""
    scored = []
    for r in rows:
        low = r["text"].lower()
        hit = sum(1 for w in words if w in low)
        if hit:
            scored.append((hit, r["text"]))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return ""
    return ("ERINNERUNGEN, die zu dieser Anfrage passen — nutze sie zuerst, bevor du rätst oder nachfragst:\n"
            + "\n".join(f"- {t[:300]}" for _, t in scored[:limit]))


class ToolExecutor:
    def __init__(self, state: "AppState"):
        self.state = state

    async def execute(self, ctx: ToolContext, name: str, args: dict) -> tuple[str, bool]:
        st = self.state
        spec: ToolSpec | None = st.tools.get(name) if st.tools else None
        if spec is None:
            return f"Tool '{name}' does not exist.", False
        if not spec.available or spec.handler is None:
            return f"Tool '{name}' is not available: {spec.reason or 'not configured'}", False
        if ROLE_RANK.get(ctx.principal.role, 0) < ROLE_RANK.get(spec.min_role, 99):
            st.log.audit(actor_type=ctx.principal.kind, actor_id=ctx.principal.actor, agent_id=ctx.agent_id,
                         tool=name, action="tool.call", target=_target(args), status="denied",
                         error=f"requires role {spec.min_role}", task_id=ctx.task_id, run_id=ctx.run_id)
            return f"Permission denied: '{name}' requires the {spec.min_role} role.", False
        if not isinstance(args, dict):
            return "Tool input must be a JSON object.", False
        missing = [k for k in (spec.input_schema.get("required") or []) if k not in args]
        if missing:
            return f"Missing required input: {', '.join(missing)}", False

        approvals = st.services["approvals"]
        if spec.needs_approval(st.tools.approval_threshold):
            approval = approvals.request(
                action=name, reason=str(args.get("reason") or spec.description)[:500],
                target=_target(args), risk=spec.risk, requested_by=ctx.principal.actor,
                agent_id=ctx.agent_id, task_id=ctx.task_id, run_id=ctx.run_id, payload=args)
            ctx.emit("approval", {"text": f"Approval requested for {name} → {_target(args)}",
                                  "approval_id": approval["id"], "tool": name, "target": _target(args),
                                  "risk": spec.risk, "code": approval["code"]})
            handle = st.runtime.get_run(ctx.run_id or "") if st.runtime else None
            if handle:
                st.runtime._set_status(handle, "waiting", f"Waiting for approval: {name}")
            else:
                st.agents.set_status(ctx.agent_id, "WAITING", task_id=ctx.task_id, run_id=ctx.run_id,
                                     activity=f"Waiting for approval: {name}")
            decision = await approvals.wait(approval["id"])
            if handle:
                st.runtime._set_status(handle, "executing", f"Running {name}")
            else:
                st.agents.set_status(ctx.agent_id, "EXECUTING", task_id=ctx.task_id, run_id=ctx.run_id,
                                     activity=f"Running {name}")
            if decision.get("status") != "approved":
                st.log.audit(actor_type="agent", actor_id=ctx.principal.actor, agent_id=ctx.agent_id, tool=name,
                             action="tool.call", target=_target(args), status=decision.get("status", "rejected"),
                             task_id=ctx.task_id, run_id=ctx.run_id, meta={"approval_id": approval["id"]})
                note = decision.get("decision_note") or ""
                return (f"The user {decision.get('status', 'rejected')} this action"
                        f"{': ' + note if note else ''}. Do not retry it.", False)

        try:
            result = await asyncio.wait_for(spec.handler(ctx, args), timeout=spec.timeout_seconds)
            ok = True
            if isinstance(result, tuple) and len(result) == 2:
                result, ok = result
            text = result if isinstance(result, str) else dumps(result)
        except asyncio.TimeoutError:
            text, ok = f"Tool '{name}' timed out after {int(spec.timeout_seconds)}s.", False
        except Exception as e:  # noqa: BLE001
            text, ok = f"Tool '{name}' failed: {e}", False
        st.tools.record(name, ok)
        st.agents.bump(ctx.agent_id, "tool_calls")
        # A running recording keeps the real trace: what ran, with what, and what came back.
        teaching = st.services.get("teaching")
        if teaching is not None and teaching.any_active() and not name.startswith("teach."):
            teaching.record_tool_call(user_id=ctx.principal.id, tool=name, params=_safe_args(args),
                                      result=text, ok=ok, agent_id=ctx.agent_id)
        st.log.audit(actor_type="agent", actor_id=ctx.principal.actor, agent_id=ctx.agent_id, tool=name,
                     action="tool.call", target=_target(args), status="ok" if ok else "error",
                     result=text if ok else "", error="" if ok else text, task_id=ctx.task_id, run_id=ctx.run_id)
        st.log.log("INFO" if ok else "WARNING", f"agent.{ctx.agent_id}",
                   f"{name} → {'ok' if ok else 'error'}: {trim(text, 200)}",
                   task_id=ctx.task_id, agent_id=ctx.agent_id, run_id=ctx.run_id, data={"tool": name})
        if ctx.task_id:
            st.services["tasks"].add_log(ctx.task_id, "INFO" if ok else "WARNING", f"{name}: {trim(text, 300)}")
        return text, ok


def _target(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("path", "target", "url", "container", "service", "command", "workflow_id", "agent_id",
                "title", "name", "to", "query"):
        if key in args and isinstance(args[key], (str, int, float)):
            return str(args[key])[:200]
    return trim(dumps(args), 200)


class MasterRuntime:
    def __init__(self, state: "AppState"):
        self.state = state
        self.settings = state.settings
        self.mode = self.settings.resolved_master_mode()
        self.provider: LLMProvider | None = None
        self.provider_error = ""
        if self.mode == "local":
            self.provider = build_provider(self.settings.configured_ai_provider())
            if self.provider is None:
                self.mode = "none"
                self.provider_error = "AI provider could not be initialised"
        self.fast_provider: LLMProvider | None = None
        self._make_free()
        self._build_fast_provider()
        self.executor = ToolExecutor(state)
        self._runs: dict[str, RunHandle] = {}
        self._provider_health: dict = {"status": "unknown", "detail": "not checked yet"}
        self._persona = _persona_from_repo()

    # ── status ───────────────────────────────────────────────────────────
    def status(self) -> dict:
        active = [h for h in self._runs.values() if h.status in ("planning", "executing", "waiting")]
        if self.mode == "local":
            online = self._provider_health.get("status") in ("healthy", "degraded", "unknown")
        elif self.mode == "remote":
            online = bool(os.environ.get("JARVIS_GATEWAY_TOKEN")) and bool(self.settings.gateway_url)
        else:
            online = False
        if not online:
            label = "MASTER AGENT OFFLINE"
        elif any(h.status == "waiting" for h in active):
            label = "AWAITING APPROVAL"
        elif active:
            label = "TASK IN PROGRESS"
        else:
            label = "JARVIS READY"
        return {
            "mode": self.mode, "online": online, "label": label,
            "provider": self.provider.info.public() if self.provider else (
                {"id": "remote", "model": "upstream control plane", "label": f"Remote · {self.settings.gateway_url}"}
                if self.mode == "remote" else None),
            "provider_health": self._provider_health, "active_runs": [h.public() for h in active],
            "error": self.provider_error or (
                "" if self.mode != "none" else
                "No AI provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY or "
                "LOCAL_LLM_URL for local mode, or JARVIS_GATEWAY_URL + JARVIS_GATEWAY_TOKEN for remote mode."),
        }

    def _make_free(self) -> None:
        """Ohne ausdrückliches JARVIS_ALLOW_PAID=1 nur kostenlose Modelle: gründlich und schnell je eine Kette."""
        from ..ai.free import FREE_DEEP, FREE_FAST, FreeChain, allow_paid
        from ..ai.openai_compat import OpenAICompatProvider
        base = self.provider
        if allow_paid() or not isinstance(base, OpenAICompatProvider) or base.info.model.endswith(":free"):
            return
        self.provider = FreeChain(base, FREE_DEEP, "Kostenlos")
        self.fast_provider = FreeChain(base, FREE_FAST, "Kostenlos (schnell)")

    def _build_fast_provider(self) -> None:
        """Ein zweites, schnelles Modell hinter demselben Zugang (z. B. OpenRouter). Ohne Zugang bleibt es beim einen."""
        from ..ai.openai_compat import OpenAICompatProvider
        base = self.provider
        if self.fast_provider is not None or base is None or not isinstance(base, OpenAICompatProvider):
            return
        model = os.environ.get("JARVIS_FAST_MODEL", "").strip()
        if not model and "openrouter" in base.base_url:
            model = "anthropic/claude-haiku-4.5"
        if not model or model == base.info.model:
            return
        self.fast_provider = OpenAICompatProvider(base.info.id, base.base_url, base.api_key, model)

    def provider_for(self, handle: "RunHandle") -> LLMProvider:
        if handle.deep or self.fast_provider is None:
            return self.provider  # type: ignore[return-value]
        return self.fast_provider

    async def check_provider(self) -> dict:
        if self.provider is None:
            self._provider_health = {"status": "offline" if self.mode == "none" else "unknown",
                                     "detail": "no local provider"}
            return self._provider_health
        try:
            self._provider_health = await self.provider.health()
        except Exception as e:  # noqa: BLE001
            self._provider_health = {"status": "degraded", "detail": str(e)[:200]}
        self.state.bus.publish("master.status", self.status())
        return self._provider_health

    def get_run(self, run_id: str) -> RunHandle | None:
        return self._runs.get(run_id)

    def active_runs(self) -> list[dict]:
        return [h.public() for h in self._runs.values() if h.status in ("planning", "executing", "waiting")]

    # ── entry points ─────────────────────────────────────────────────────
    async def start_chat_run(self, conversation: dict, user_message: dict, principal: Principal,
                             agent_id: str | None = None, channel: str = "") -> dict:
        st = self.state
        agent_id = agent_id or st.agents.master_id()
        run_id = new_id("run")
        assistant = st.services["chat"].add_message(
            conversation["id"], "assistant", "", status="streaming", run_id=run_id,
            meta={"agent_id": agent_id})
        handle = RunHandle(id=run_id, agent_id=agent_id, principal=principal,
                           conversation_id=conversation["id"], message_id=assistant["id"],
                           task_id=None, voice=(channel == "voice"))
        self._register(handle, initiated_by=principal.actor)
        handle.task = asyncio.create_task(self._drive(handle, conversation=conversation,
                                                      user_message=user_message))
        return {"run": handle.public(), "message": assistant}

    async def start_task_run(self, task: dict, principal: Principal, agent_id: str) -> dict:
        st = self.state
        run_id = new_id("run")
        handle = RunHandle(id=run_id, agent_id=agent_id, principal=principal,
                           conversation_id=task.get("conversation_id"), message_id=None, task_id=task["id"])
        self._register(handle, initiated_by=principal.actor)
        st.services["tasks"].set_status(task["id"], "PLANNING", agent=agent_id, run_id=run_id,
                                        note=f"Assigned to {agent_id}")
        handle.task = asyncio.create_task(self._drive(handle, task=task))
        return {"run": handle.public()}

    async def cancel_run(self, run_id: str, by: str = "") -> bool:
        handle = self._runs.get(run_id)
        if not handle or handle.status not in ("planning", "executing", "waiting"):
            return False
        handle.cancel.set()
        if handle.task and not handle.task.done():
            handle.task.cancel()
        self.state.log.audit(actor_type="user", actor_id=by or "system", action="run.cancel",
                             target=run_id, status="ok", agent_id=handle.agent_id, run_id=run_id,
                             task_id=handle.task_id)
        return True

    def _register(self, handle: RunHandle, initiated_by: str) -> None:
        self._runs[handle.id] = handle
        self.state.db.insert("agent_runs", {
            "id": handle.id, "agent_id": handle.agent_id, "task_id": handle.task_id,
            "conversation_id": handle.conversation_id, "message_id": handle.message_id,
            "parent_run_id": handle.parent_run_id, "status": "planning", "started_at": handle.started_at,
            "finished_at": None, "error": "", "steps": "[]", "usage": "{}", "initiated_by": initiated_by,
        })
        self.state.agents.bump(handle.agent_id, "runs")
        self.state.bus.publish("run.started", handle.public())

    # ── driver ───────────────────────────────────────────────────────────
    async def _drive(self, handle: RunHandle, *, conversation: dict | None = None,
                     user_message: dict | None = None, task: dict | None = None) -> None:
        st = self.state
        chat = st.services["chat"]
        tasks = st.services["tasks"]
        error = ""
        try:
            self._set_status(handle, "planning", "Analyzing request")
            if self.mode == "none":
                raise RuntimeError(self.status()["error"])
            if self.mode == "remote":
                text = await self._run_remote(handle, conversation, user_message, task)
            else:
                text = await self._run_local(handle, conversation, user_message, task)
            handle.text = text
            self._set_status(handle, "completed", "Task completed")
            if handle.message_id:
                chat.update_message(handle.message_id, content=text, status="complete",
                                    blocks=handle.steps, meta={"agent_id": handle.agent_id,
                                                               "usage": handle.usage,
                                                               "turns": getattr(handle, "_turns", [])})
            if handle.task_id:
                tasks.set_status(handle.task_id, "COMPLETED", output=text, note="Task completed")
            st.agents.bump(handle.agent_id, "completed")
        except asyncio.CancelledError:
            error = "cancelled"
            self._set_status(handle, "cancelled", "Stopped by user")
            if handle.message_id:
                chat.update_message(handle.message_id, content=handle.text, status="stopped",
                                    blocks=handle.steps)
            if handle.task_id:
                tasks.set_status(handle.task_id, "CANCELLED", note="Run stopped by user")
        except Exception as e:  # noqa: BLE001
            error = str(e) or e.__class__.__name__
            self._set_status(handle, "failed", "Task failed", error=error)
            st.agents.bump(handle.agent_id, "errors")
            st.log.error(f"agent.{handle.agent_id}", f"Run failed: {error}", run_id=handle.id,
                         task_id=handle.task_id, agent_id=handle.agent_id)
            if handle.message_id:
                chat.update_message(handle.message_id, content=handle.text, status="error",
                                    blocks=handle.steps, meta={"error": error, "agent_id": handle.agent_id})
            if handle.task_id:
                tasks.set_status(handle.task_id, "FAILED", error=error, note=f"Task failed: {error}")
            st.services["notifications"].notify(
                category="agent", severity="error", title="JARVIS could not complete the request",
                body=error[:500], link=f"/chat/{handle.conversation_id}" if handle.conversation_id else "/tasks",
                user_id=handle.principal.id if handle.principal.kind == "user" else "*")
        finally:
            st.db.update("agent_runs", handle.id, {
                "status": handle.status, "finished_at": now_iso(), "error": error,
                "steps": dumps(handle.steps[-200:]), "usage": dumps(handle.usage)})
            st.agents.set_status(handle.agent_id, "ERROR" if handle.status == "failed" else "IDLE",
                                 error=error if handle.status == "failed" else "")
            st.bus.publish("run.finished", {**handle.public(), "error": error})
            # keep finished runs briefly for late subscribers, then drop
            asyncio.get_running_loop().call_later(120, self._runs.pop, handle.id, None)

    def _set_status(self, handle: RunHandle, status: str, activity: str = "", error: str = "") -> None:
        handle.status = status
        agent_status = {"planning": "THINKING", "executing": "EXECUTING", "waiting": "WAITING",
                        "delegated": "EXECUTING"}.get(status)
        if agent_status:
            self.state.agents.set_status(handle.agent_id, agent_status, task_id=handle.task_id,
                                         run_id=handle.id, activity=activity)
        self.state.bus.publish("run.status", {**handle.public(), "activity": activity, "error": error})

    def _step(self, handle: RunHandle, kind: str, text: str, **extra) -> None:
        step = {"ts": now_iso(), "kind": kind, "text": text[:600], **extra}
        handle.steps.append(step)
        self.state.bus.publish("run.activity", {"run_id": handle.id, "parent_run_id": handle.parent_run_id,
                                                "agent_id": handle.agent_id, "conversation_id": handle.conversation_id,
                                                "task_id": handle.task_id, **step})

    # ── local agent loop ─────────────────────────────────────────────────
    async def _run_local(self, handle: RunHandle, conversation: dict | None, user_message: dict | None,
                         task: dict | None) -> str:
        st = self.state
        chat = st.services["chat"]
        if conversation and user_message:
            messages = self._history(conversation["id"], user_message["id"])
            goal = user_message.get("content", "")
        else:
            goal = f"{task['title']}\n\n{task.get('description', '')}".strip()
            messages = [{"role": "user", "content": [{"type": "text", "text": goal}]}]
        text = await self._agent_loop(handle, messages, goal=goal)
        if conversation and user_message and text:
            chat.maybe_title(conversation["id"], user_message.get("content", ""))
        return text

    async def _agent_loop(self, handle: RunHandle, messages: list[dict], goal: str = "") -> str:
        st = self.state
        chat = st.services["chat"]
        agent = st.agents.get(handle.agent_id)
        if agent is None:
            raise RuntimeError(f"Agent '{handle.agent_id}' is not registered")
        if not agent.enabled:
            raise RuntimeError(f"Agent '{agent.name}' is disabled")
        assert self.provider is not None
        if self.fast_provider is not None and not handle.deep and needs_deep(goal):
            handle.deep = True
        tools = st.tools.for_agent(agent.tools, handle.principal.role)
        if handle.depth >= self.settings.max_delegation_depth:
            tools = [t for t in tools if t.name != "agent.delegate"]
        system = self._system_prompt(agent, tools)
        if handle.voice:
            system += VOICE_HINT
        if agent.kind == "master":
            recalled = recall_memory(st, goal)
            if recalled:
                system += "\n\n" + recalled
        if self.fast_provider is not None and agent.kind == "master":
            system += MODEL_HINT
        tool_defs = [t.to_def() for t in tools]
        # Bilder, die Werkzeuge in diesem Zug besorgt haben. Nach den
        # Werkzeugergebnissen gehen sie als eigene Nachricht an das Modell.
        pending_images: list[str] = []
        ctx = ToolContext(state=st, principal=handle.principal, agent_id=handle.agent_id, run_id=handle.id,
                          task_id=handle.task_id, conversation_id=handle.conversation_id, depth=handle.depth,
                          emit=lambda kind, data: self._step(handle, kind, data.get("text", kind), **{
                              k: v for k, v in data.items() if k != "text"}),
                          attach=pending_images.append)
        turns: list[dict] = []
        text_out: list[str] = []
        last_flush = 0.0
        steps = 0
        max_steps = self.settings.max_agent_steps
        self._step(handle, "plan", f"Planning: {trim(goal, 120)}" if goal else "Planning")

        while True:
            if handle.cancel.is_set():
                raise asyncio.CancelledError()
            steps += 1
            if steps > max_steps:
                messages.append({"role": "user", "content": [{"type": "text", "text":
                    "[system] Step budget exhausted. Give your best final answer now without calling tools."}]})
                tool_defs = []
            self._set_status(handle, "planning" if steps == 1 else "executing",
                             "Analyzing request" if steps == 1 else "Generating result")
            content: list[dict] = []
            tool_calls: list[dict] = []
            stop_reason = "end_turn"
            segment: list[str] = []
            provider = self.provider_for(handle)
            async for ev in provider.stream(system=system, messages=messages, tools=tool_defs):
                if handle.cancel.is_set():
                    raise asyncio.CancelledError()
                et = ev["type"]
                if et == "text_delta":
                    segment.append(ev["text"])
                    if handle.message_id:
                        st.bus.publish("chat.delta", {"run_id": handle.id, "message_id": handle.message_id,
                                                      "conversation_id": handle.conversation_id, "text": ev["text"]})
                        now = asyncio.get_running_loop().time()
                        if now - last_flush > 1.5:
                            last_flush = now
                            chat.update_message(handle.message_id, content="\n\n".join(
                                [*text_out, "".join(segment)]), status="streaming")
                elif et == "tool_use":
                    tool_calls.append(ev)
                elif et == "error":
                    raise RuntimeError(ev["message"])
                elif et == "message_end":
                    content = ev["content"]
                    stop_reason = ev["stop_reason"]
                    for k in ("input_tokens", "output_tokens"):
                        handle.usage[k] = handle.usage.get(k, 0) + int((ev.get("usage") or {}).get(k, 0) or 0)
            seg_text = "".join(segment).strip()
            if seg_text:
                text_out.append(seg_text)
            if content:
                messages.append({"role": "assistant", "content": content})
                turns.append({"role": "assistant", "content": content})
            if not tool_calls:
                if stop_reason == "max_tokens":
                    text_out.append("…[response cut off by the token limit]")
                break

            results: list[dict] = []
            for call in tool_calls:
                if handle.cancel.is_set():
                    raise asyncio.CancelledError()
                name, args = call["name"], call.get("input") or {}
                self._set_status(handle, "executing", f"Running {name}")
                self._step(handle, "tool_call", f"{_activity_label(name)} — {_target(args)}", tool=name)
                if handle.message_id:
                    st.bus.publish("chat.tool_call", {"run_id": handle.id, "message_id": handle.message_id,
                                                      "conversation_id": handle.conversation_id,
                                                      "tool": name, "input": _safe_args(args)})
                result, ok = await self.executor.execute(ctx, name, args)
                self._step(handle, "tool_result", f"{name}: {trim(result, 200)}", tool=name, ok=ok)
                if handle.message_id:
                    st.bus.publish("chat.tool_result", {"run_id": handle.id, "message_id": handle.message_id,
                                                        "conversation_id": handle.conversation_id,
                                                        "tool": name, "ok": ok, "output": trim(result, 1500)})
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": trim(result, 12000),
                                "is_error": not ok})
            messages.append({"role": "user", "content": results})
            turns.append({"role": "user", "content": [
                {**r, "content": trim(r["content"], 1500)} for r in results]})

            # Sehen, nicht nur lesen: Ein Bildschirmfoto als Pfad im Text wäre
            # für das Modell eine Zeichenkette. Als Bildblock ist es das, was
            # der Nutzer vor sich hat.
            if pending_images:
                blocks = self._image_blocks(pending_images)
                pending_images.clear()
                if blocks:
                    messages.append({"role": "user", "content": blocks})
                    turns.append({"role": "user", "content": [
                        {"type": "text", "text": "[Bild vom Werkzeug angehängt]"}]})

        handle._turns = turns  # type: ignore[attr-defined]
        final = "\n\n".join(text_out).strip()
        handle.text = final
        return final

    def _image_blocks(self, paths: list[str]) -> list[dict]:
        """Bilder aus dem Arbeitsbereich in Blöcke, die jeder Anbieter versteht.

        Grenzen mit Absicht: höchstens drei Bilder pro Zug und 5 MB je Bild.
        Ein Zug, der zehn Bildschirmfotos mitschleppt, sprengt das Fenster und
        macht die Antwort schlechter statt besser.
        """
        out: list[dict] = []
        root = Path(self.state.services["files"].root)
        for rel in paths[:3]:
            try:
                path = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
                path.relative_to(root)                      # nichts von außerhalb
                data = path.read_bytes()
            except (OSError, ValueError):
                continue
            if len(data) > 5 * 1024 * 1024:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            if not mime.startswith("image/"):
                continue
            out.append({"type": "image", "media_type": mime,
                        "data": base64.b64encode(data).decode()})
        if out:
            out.append({"type": "text", "text": "Das ist das Bild, das dein Werkzeug geholt hat. "
                                                "Beschreibe nur, was wirklich darauf zu sehen ist."})
        return out

    # ── delegation (called by the agent.delegate tool) ───────────────────
    async def delegate(self, ctx: ToolContext, agent_id: str, instruction: str, title: str = "") -> str:
        st = self.state
        spec = st.agents.get(agent_id)
        if spec is None:
            return f"No agent '{agent_id}'. Available: {', '.join(a.id for a in self._specialists())}"
        if not spec.enabled:
            return f"Agent '{spec.name}' is disabled."
        if spec.kind == "master" or agent_id == ctx.agent_id:
            return "An agent cannot delegate to itself or to the master agent."
        if ctx.depth + 1 > self.settings.max_delegation_depth:
            return "Delegation depth limit reached; do the work yourself."
        tasks = st.services["tasks"]
        parent = ctx.task_id
        if parent is None:
            root = tasks.create(title=title or trim(instruction, 80), description=instruction,
                                created_by=ctx.principal.actor, assigned_agent=ctx.agent_id,
                                conversation_id=ctx.conversation_id, run_id=ctx.run_id, status="RUNNING")
            parent = root["id"]
            ctx.task_id = parent
            handle_parent = self._runs.get(ctx.run_id or "")
            if handle_parent:
                handle_parent.task_id = parent
                st.db.update("agent_runs", handle_parent.id, {"task_id": parent})
        sub = tasks.create(title=title or trim(instruction, 80), description=instruction,
                           created_by=f"agent:{ctx.agent_id}", assigned_agent=agent_id, parent_id=parent,
                           conversation_id=ctx.conversation_id, status="PLANNING")
        child = RunHandle(id=new_id("run"), agent_id=agent_id, principal=ctx.principal,
                          conversation_id=ctx.conversation_id, message_id=None, task_id=sub["id"],
                          parent_run_id=ctx.run_id, depth=ctx.depth + 1)
        self._register(child, initiated_by=f"agent:{ctx.agent_id}")
        parent_handle = self._runs.get(ctx.run_id or "")
        if parent_handle:
            self._step(parent_handle, "delegate", f"{spec.name} takes over: {trim(instruction, 120)}",
                       delegate_agent_id=agent_id, child_run_id=child.id)
            st.bus.publish("run.status", {**parent_handle.public(), "status": "delegated",
                                          "label": f"{spec.name.upper()} ACTIVE", "activity": spec.name})
        st.log.info(f"agent.{ctx.agent_id}", f"Delegated to {spec.name}: {trim(instruction, 200)}",
                    task_id=sub["id"], agent_id=agent_id, run_id=child.id)
        messages = [{"role": "user", "content": [{"type": "text", "text": instruction}]}]
        error = ""
        try:
            tasks.set_status(sub["id"], "RUNNING", run_id=child.id)
            text = await self._agent_loop(child, messages, goal=instruction)
            child.status = "completed"
            tasks.set_status(sub["id"], "COMPLETED", output=text, note=f"{spec.name} completed the sub-task")
            st.agents.bump(agent_id, "completed")
            return text or "(the specialist returned no text)"
        except asyncio.CancelledError:
            child.status = "cancelled"
            tasks.set_status(sub["id"], "CANCELLED")
            raise
        except Exception as e:  # noqa: BLE001
            error = str(e)
            child.status = "failed"
            st.agents.bump(agent_id, "errors")
            tasks.set_status(sub["id"], "FAILED", error=error)
            return f"{spec.name} failed: {error}"
        finally:
            st.db.update("agent_runs", child.id, {"status": child.status, "finished_at": now_iso(),
                                                  "error": error, "steps": dumps(child.steps[-200:]),
                                                  "usage": dumps(child.usage)})
            st.agents.set_status(agent_id, "ERROR" if child.status == "failed" else "IDLE", error=error)
            st.bus.publish("run.finished", {**child.public(), "error": error})
            if parent_handle:
                self._set_status(parent_handle, "executing", "Continuing")
            self._runs.pop(child.id, None)

    def _specialists(self):
        return [a for a in (self.state.agents.get(i) for i in self.state.agents._specs)
                if a and a.kind != "master" and a.enabled]

    # ── prompt & history ─────────────────────────────────────────────────
    def live_instructions(self) -> str:
        """The persona for the open line.

        Spoken answers are not written answers read aloud: no lists, no
        markdown, no headings, and short enough that the other person can
        interrupt. The honesty rules are the same ones as everywhere else —
        they are the point, not a style choice.
        """
        persona = _persona_from_repo()
        # Dieselben stehenden Anweisungen wie im Chat: Eine Regel, die nur
        # getippt gilt und gesprochen nicht, wäre keine Regel.
        standing = str(self.state.db.get_setting("master_instructions", "") or "").strip()
        core = core_memory(self.state)
        return "\n\n".join(x for x in [persona, core, (
            "You are on an open voice line. Speak German unless spoken to in another language.\n"
            "Answer in spoken sentences: short, no lists, no markdown, no headings, no code read "
            "out letter by letter. Two or three sentences unless more is genuinely needed.\n"
            "You have real tools. Use them rather than guessing, and say what you did.\n"
            "Never claim something was done that no tool confirmed. If a tool is missing or "
            "unconfigured, say which one and what it needs.\n"
            "If something needs approval, say so plainly and tell them it is waiting in the "
            "dashboard — do not pretend it ran.\n"
            "The other person can interrupt you at any time. When they do, stop and listen."
        ), ("STEHENDE ANWEISUNGEN DES NUTZERS (gelten immer):\n" + standing) if standing else ""] if x)

    def _system_prompt(self, agent, tools: list[ToolSpec]) -> str:
        st = self.state
        parts = []
        if agent.kind == "master" and self._persona:
            parts.append(self._persona)
        core = core_memory(st)
        if core:
            parts.append(core)
        parts.append(
            "OPERATING CONTEXT: You run inside the JARVIS Command Center on the user's server. The user "
            "watches a live dashboard: every tool call, task and approval you trigger is visible there. "
            "Tool results are ground truth — never claim an action happened unless a tool confirmed it. "
            "If a tool is unavailable or an action is rejected, say so plainly. Use Markdown for structure "
            "when it helps; keep short answers short.")
        parts.append(f"AGENT: {agent.name} — {agent.role}\n{agent.instructions}".strip())
        if agent.kind == "master":
            specs = self._specialists()
            if specs and any(t.name == "agent.delegate" for t in tools):
                roster = "\n".join(f"- {a.id}: {a.role} (capabilities: {', '.join(a.capabilities) or '—'})"
                                   for a in specs)
                parts.append("SPECIALISTS you can hand a whole sub-goal to with agent.delegate "
                             "(the user never sees them; the answer is yours):\n" + roster)
            parts.append("TASKS: for work with several steps or that the user will want to follow, create a "
                         "task with task.create first, then do the work. High-risk tools pause for the "
                         "user's approval automatically — explain the reason in the 'reason' argument.")
            parts.append(
                "THE USER'S PC: when they ask you to open, close or drive something on their computer, use "
                "desktop.open_app or desktop.run — the paired desktop carries it out. Check desktop.devices "
                "first if you are unsure which machine or which action exists. If no desktop is online, say "
                "that plainly instead of claiming you opened something.\n"
                "LEARNING BY DEMONSTRATION: when they say they want to show you a workflow, start "
                "teach.start, let them work, add teach.note for the reasons behind steps, then teach.stop "
                "and teach.learn — that writes the procedure down and can create a specialist for it. "
                "Before starting something that sounds familiar, check procedure.list: if you already "
                "learned it, run it with procedure.run instead of improvising it again.")
        # Fähigkeiten stehen hier nur mit Name und Zweck. Der volle Text kommt
        # über skill.open, wenn er ihn braucht — sonst bezahlt jedes Gespräch
        # für Anleitungen, die niemand aufschlägt.
        lib = st.services.get("skills")
        if lib is not None:
            catalogue = lib.catalogue()
            if catalogue:
                parts.append(catalogue)
        # Dasselbe für das, was er über diese Anlage WEISS: Ein Handbuch passt
        # nicht in die 30 Sätze des Hauptgedächtnisses, gehört aber zu dem, was
        # er kennen muss. Also auch hier nur Titel und Zweck — den vollen Text
        # holt er sich mit knowledge.open, statt zu raten.
        wissen = st.services.get("knowledge")
        if wissen is not None:
            verzeichnis = wissen.catalogue()
            if verzeichnis:
                parts.append(verzeichnis)
        # Was der Nutzer im Dashboard unter Gedächtnis einträgt, gilt in jedem
        # Gespräch — und zwar über den eingebauten Voreinstellungen. Es steht
        # weit hinten im Text, weil das Letzte am stärksten wirkt.
        standing = st.db.get_setting("master_instructions", "") or ""
        if standing:
            parts.append("STEHENDE ANWEISUNGEN DES NUTZERS (gelten immer, sie gehen deinen eigenen "
                         "Gewohnheiten vor):\n" + str(standing).strip())
        # Composio zuerst — aber nur, wenn es wirklich verbunden ist. Eine
        # Regel, die auf einen nicht eingerichteten Dienst zeigt, schickt ihn
        # in eine Sackgasse und kostet zwei Werkzeugaufrufe, bevor er merkt,
        # dass da nichts ist.
        composio = st.services.get("composio")
        if composio is not None and composio.configured():
            parts.append(
                "COMPOSIO FIRST: for anything that happens in an outside service — mail, calendar, "
                "chat, tickets, documents, CRM — check Composio before improvising: composio.apps "
                "shows what is actually connected, composio.tools finds the right tool, composio.run "
                "does it. Only when nothing there fits, fall back to your own tools. Composio being "
                "unreachable or having no match is never a reason to stop: say what you tried and "
                "carry on with what you have.")
        parts.append(
            "WHAT YOU ARE MADE OF: call system.inventory when you need to know what you can actually do "
            "right now — which tools work, which integrations are connected, which MCP servers and skills "
            "exist. Do that instead of guessing from memory; the answer changes as the user connects "
            "things.")
        unavailable = [t for t in st.tools.all() if not (t.available and t.handler)]
        if unavailable:
            parts.append("NOT AVAILABLE right now (integration not connected): " +
                         ", ".join(sorted({t.name.split('.')[0] for t in unavailable})) +
                         ". Say so if the user asks for these instead of pretending.")
        parts.append(f"ENVIRONMENT: host={socket.gethostname()} os={platform.system()} "
                     f"workspace={self.settings.workspace_dir} "
                     f"now={datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        return "\n\n".join(parts)

    def _history(self, conversation_id: str, upto_message_id: str, limit: int = 40) -> list[dict]:
        chat = self.state.services["chat"]
        rows = chat.messages(conversation_id, limit=limit + 20)
        out: list[dict] = []
        for m in rows:
            if m["role"] == "user":
                blocks = [{"type": "text", "text": m["content"]}] if m["content"] else []
                blocks.extend(self._attachment_blocks(m.get("meta", {}).get("attachments") or []))
                if blocks:
                    out.append({"role": "user", "content": blocks})
            elif m["role"] == "assistant":
                turns = (m.get("meta") or {}).get("turns")
                if turns and m["status"] == "complete":
                    out.extend(turns)
                elif m["content"]:
                    out.append({"role": "assistant", "content": [{"type": "text", "text": m["content"]}]})
            if m["id"] == upto_message_id:
                break
        # providers require the first message to be from the user
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out[-limit:] if len(out) > limit else out

    def _attachment_blocks(self, attachments: list[dict]) -> list[dict]:
        files = self.state.services.get("files")
        blocks: list[dict] = []
        for att in attachments[:6]:
            rel = att.get("path", "")
            try:
                path = files.resolve(rel) if files else None
            except Exception:
                path = None
            name = att.get("name") or rel
            if path is None or not path.exists():
                blocks.append({"type": "text", "text": f"[Attachment '{name}' is missing]"})
                continue
            mime = att.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream"
            size = path.stat().st_size
            if mime.startswith("image/") and size <= 5 * 1024 * 1024:
                blocks.append({"type": "image", "media_type": mime,
                               "data": base64.b64encode(path.read_bytes()).decode()})
            elif mime == "application/pdf":
                text = _pdf_text(path)
                blocks.append({"type": "text", "text": f"[Attachment '{name}' (PDF, {size} bytes) at {rel}]\n"
                               + (trim(text, 60000) if text else "(text could not be extracted — pypdf not installed)")})
            elif mime.startswith("text/") or path.suffix.lower() in _TEXT_EXT:
                try:
                    body = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    body = ""
                blocks.append({"type": "text", "text": f"[Attachment '{name}' at {rel}]\n{trim(body, 60000)}"})
            else:
                blocks.append({"type": "text", "text": f"[Attachment '{name}' ({mime}, {size} bytes) saved at {rel}]"})
        return blocks

    # ── remote mode ──────────────────────────────────────────────────────
    async def _run_remote(self, handle: RunHandle, conversation: dict | None, user_message: dict | None,
                          task: dict | None) -> str:
        from core.control_plane import (ControlPlane, ControlPlaneAuthError, ControlPlaneError,
                                        ControlPlaneOffline)
        st = self.state
        chat = st.services["chat"]
        command = user_message["content"] if user_message else f"{task['title']}\n{task.get('description', '')}"
        cp = ControlPlane(base_url=self.settings.gateway_url, actor=self.settings.gateway_actor)
        remote_conv = (conversation or {}).get("meta", {}).get("remote_conversation_id") or None
        self._step(handle, "info", f"Forwarding to control plane {cp.base_url}")

        def on_status(res):
            st.bus.publish_threadsafe("run.activity", {
                "run_id": handle.id, "agent_id": handle.agent_id, "conversation_id": handle.conversation_id,
                "ts": now_iso(), "kind": "info",
                "text": f"Control plane: {res.status}" + (f" → {res.routed_to}" if res.routed_to else "")})

        try:
            res = await asyncio.to_thread(cp.run, command, remote_conv, on_status)
        except ControlPlaneAuthError as e:
            raise RuntimeError(f"The control plane rejected the gateway token ({e})")
        except ControlPlaneOffline as e:
            raise RuntimeError(f"The control plane is not reachable: {e}")
        except ControlPlaneError as e:
            raise RuntimeError(str(e))
        if conversation and res.conversation_id and res.conversation_id != remote_conv:
            meta = {**conversation.get("meta", {}), "remote_conversation_id": res.conversation_id}
            st.db.update("conversations", conversation["id"], {"meta": dumps(meta)})
        if res.awaiting_approval():
            text = res.result.strip() or "This needs your explicit approval on the control plane."
            if res.approval_code:
                text += f"\n\nApproval code: **{res.approval_code}**"
            self._step(handle, "approval", "Control plane is waiting for approval", code=res.approval_code)
            return text
        if res.ok():
            return res.result.strip() or "Done."
        raise RuntimeError(res.error.strip() or "The control plane reported a failure")


def _activity_label(tool: str) -> str:
    head = tool.split(".")[0]
    return {"server": "Checking server", "docker": "Inspecting containers", "filesystem": "Reading workspace",
            "terminal": "Executing command", "workflow": "Running workflow", "task": "Updating tasks",
            "agent": "Contacting specialist", "web": "Fetching page", "document": "Writing document",
            "memory": "Consulting memory", "logs": "Searching logs", "integration": "Checking integrations",
            "notify": "Sending notification", "github": "Contacting GitHub"}.get(head, f"Using {tool}")


def _safe_args(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 400:
            out[k] = v[:400] + "…"
        else:
            out[k] = v
    return out


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # optional
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:50])
    except Exception:
        return ""
