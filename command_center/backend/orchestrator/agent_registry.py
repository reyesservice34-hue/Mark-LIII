"""
Agent registry — roster + live state for every agent JARVIS can dispatch.

The roster is data: the built-in team below, optionally replaced or extended
by `config/command_center/agents.json` (same shape as `config/agency.json`,
so the desktop's agency roster can be reused as-is). Live state (status,
current task, last activity, counters) lives in memory and is mirrored to
the `agents` table so history survives restarts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..db import Database, dumps, loads, now_iso
from ..events import EventBus

STATUSES = ("IDLE", "THINKING", "EXECUTING", "WAITING", "ERROR", "OFFLINE")
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\-]{0,63}$")


@dataclass
class AgentSpec:
    id: str
    name: str
    description: str = ""
    role: str = ""
    kind: str = "specialist"           # master | specialist | external
    instructions: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)      # tool names / prefixes, "*" for all
    model: str = ""                    # "" → provider default
    provider: str = ""                 # "" → configured default
    enabled: bool = True
    source: str = "built-in"
    config: dict = field(default_factory=dict)
    icon: str = "bot"


@dataclass
class AgentState:
    status: str = "IDLE"
    current_task_id: str | None = None
    current_run_id: str | None = None
    current_activity: str = ""
    last_activity_at: str | None = None
    last_error: str = ""
    started_at: str | None = None
    stats: dict = field(default_factory=lambda: {"runs": 0, "errors": 0, "tool_calls": 0,
                                                 "completed": 0})


DEFAULT_AGENTS: list[AgentSpec] = [
    AgentSpec(
        id="master", name="JARVIS", kind="master", icon="sparkles",
        role="Master Agent — the one voice the user talks to.",
        description="Understands the request, acts directly with tools, or delegates to a specialist "
                    "and returns one answer.",
        capabilities=["conversation", "planning", "delegation", "task management", "server operations"],
        tools=["*"],
        instructions=(
            "You are the Master Agent. Decide what the request needs: answer directly, use a tool, "
            "create a task for work that takes several steps, or delegate a whole sub-goal to a "
            "specialist with agent.delegate. Keep the user informed in one voice."),
    ),
    AgentSpec(
        id="coding", name="Coding Agent", icon="code",
        role="Writes, reviews and changes code inside the workspace.",
        description="Reads and writes files in the workspace, runs commands when permitted, "
                    "and reports exactly what changed.",
        capabilities=["code review", "implementation", "debugging", "repository inspection"],
        tools=["filesystem.*", "terminal.execute", "github.*", "task.*", "web.fetch"],
        instructions=(
            "Work only inside the workspace. Read before you write. Report the files you changed "
            "and anything you could not do. Never claim a command ran if the tool refused it."),
    ),
    AgentSpec(
        id="research", name="Research Agent", icon="search",
        role="Finds and condenses facts from the web and from workspace documents.",
        description="Fetches pages, reads documents, and returns sourced conclusions.",
        capabilities=["web research", "document analysis", "summaries"],
        tools=["web.fetch", "filesystem.list", "filesystem.read", "memory.*"],
        instructions=(
            "Answer only from what you actually fetched or read. Cite the source URL or file. "
            "Say plainly when something could not be found."),
    ),
    AgentSpec(
        id="server", name="Server Agent", icon="server",
        role="Monitors and, with approval, operates the server.",
        description="Checks CPU, memory, disk, containers and services; restarts are approval-gated.",
        capabilities=["health checks", "docker", "service control", "log analysis"],
        tools=["server.*", "docker.*", "logs.search", "terminal.execute", "notify.user"],
        instructions=(
            "Diagnose before acting. Every restart or command goes through the approval gate — "
            "explain the reason in the request. Report metrics as numbers, not adjectives."),
    ),
    AgentSpec(
        id="document", name="Document Agent", icon="file-text",
        role="Creates offers, letters, reports and other documents.",
        description="Drafts structured documents into the workspace as Markdown or text.",
        capabilities=["document drafting", "offers and quotes", "structured reports"],
        tools=["document.create", "filesystem.*", "memory.*"],
        instructions=(
            "Write complete, ready-to-send documents. Use the recipient's language. Save the "
            "document with document.create and return the path."),
    ),
    AgentSpec(
        id="automation", name="Automation Agent", icon="workflow",
        role="Runs and inspects workflows and integrations.",
        description="Lists, triggers and inspects n8n workflows and integration health.",
        capabilities=["workflow execution", "integration status", "scheduling"],
        tools=["workflow.*", "integration.status", "notify.user", "task.*"],
        instructions="Confirm a workflow exists before triggering it. Report execution ids and outcomes.",
    ),
    AgentSpec(
        id="reyes-service", name="Reyes Service Agent", icon="briefcase",
        role="Business assistant for Reyes Service (Handwerk, Innenausbau, Sanierung, Montage).",
        description="Quotes, costing, site and crew planning, customer communication.",
        capabilities=["quotes", "costing", "site planning", "customer communication"],
        tools=["document.create", "filesystem.*", "memory.*", "task.*", "calendar.*", "email.*"],
        instructions=(
            "You support a premium craft business. Be precise with numbers, list assumptions, and "
            "write customer-facing text in the customer's language."),
    ),
    AgentSpec(
        id="email", name="Email Agent", icon="mail",
        role="Reads and drafts email once a mailbox integration is connected.",
        description="Requires the email integration; sending is approval-gated.",
        capabilities=["inbox search", "drafting", "sending (gated)"],
        tools=["email.*"],
        instructions="Draft first, send only through the gated tool.",
    ),
    AgentSpec(
        id="calendar", name="Calendar Agent", icon="calendar",
        role="Books and reads appointments once a calendar integration is connected.",
        description="Requires the calendar integration.",
        capabilities=["read calendar", "create events"],
        tools=["calendar.*"],
        instructions="Never invent an appointment. Confirm the exact date and time.",
    ),
]


class AgentRegistry:
    def __init__(self, db: Database, bus: EventBus, logger: Callable[[str], None] = print):
        self.db = db
        self.bus = bus
        self._log = logger
        self._specs: dict[str, AgentSpec] = {}
        self._state: dict[str, AgentState] = {}

    # ── roster ───────────────────────────────────────────────────────────
    def load(self, roster_path: Path | None = None) -> None:
        for spec in DEFAULT_AGENTS:
            self.register(spec)
        if roster_path and roster_path.exists():
            self._load_roster_file(roster_path)

    def _load_roster_file(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            self._log(f"Agent roster {path.name} unreadable ({e}) — using built-in roster")
            return
        if raw.get("replace") is True:
            self._specs.clear()
            self._state.clear()
        count = 0
        for entry in raw.get("agents", []):
            spec = self._coerce(entry, source=path.name)
            if spec:
                self.register(spec, replace=True)
                count += 1
        if raw.get("entry") and raw["entry"] in self._specs and "master" not in self._specs:
            self._specs[raw["entry"]].kind = "master"
        self._log(f"Agent roster loaded from {path.name} ({count} agents)")

    @staticmethod
    def _coerce(entry: dict, source: str) -> AgentSpec | None:
        agent_id = str(entry.get("id") or entry.get("name") or "").strip().lower().replace(" ", "-")
        if not _NAME_RE.match(agent_id):
            return None
        tools = [str(t).strip() for t in (entry.get("tools") or []) if str(t).strip()]
        return AgentSpec(
            id=agent_id, name=str(entry.get("display_name") or entry.get("name") or agent_id),
            description=str(entry.get("description") or ""), role=str(entry.get("role") or ""),
            kind=str(entry.get("kind") or "specialist"), instructions=str(entry.get("instructions") or ""),
            capabilities=[str(c) for c in entry.get("capabilities") or []], tools=tools,
            model=str(entry.get("model") or ""), provider=str(entry.get("provider") or entry.get("backend") or ""),
            enabled=bool(entry.get("enabled", True)), source=source,
            config={k: v for k, v in entry.items() if k not in (
                "id", "name", "display_name", "description", "role", "kind", "instructions",
                "capabilities", "tools", "model", "provider", "enabled")},
            icon=str(entry.get("icon") or "bot"),
        )

    def register(self, spec: AgentSpec, replace: bool = False) -> AgentSpec:
        if spec.id in self._specs and not replace:
            raise ValueError(f"agent '{spec.id}' already registered")
        stored = self.db.fetchone("SELECT * FROM agents WHERE id=?", (spec.id,))
        if stored:
            spec.enabled = bool(stored["enabled"])
            state = self._state.setdefault(spec.id, AgentState())
            state.stats = loads(stored.get("stats"), state.stats) or state.stats
            state.last_activity_at = stored.get("last_activity_at")
        else:
            self._state.setdefault(spec.id, AgentState())
        self._specs[spec.id] = spec
        self._persist(spec)
        return spec

    def _persist(self, spec: AgentSpec) -> None:
        state = self._state.get(spec.id) or AgentState()
        self.db.upsert("agents", {
            "id": spec.id, "name": spec.name, "description": spec.description, "role": spec.role,
            "kind": spec.kind, "capabilities": dumps(spec.capabilities), "tools": dumps(spec.tools),
            "model": spec.model, "provider": spec.provider, "enabled": 1 if spec.enabled else 0,
            "source": spec.source, "config": dumps(spec.config), "created_at": now_iso(),
            "updated_at": now_iso(), "last_activity_at": state.last_activity_at,
            "stats": dumps(state.stats),
        })

    # ── read ─────────────────────────────────────────────────────────────
    def get(self, agent_id: str) -> AgentSpec | None:
        return self._specs.get(agent_id)

    def master_id(self) -> str:
        for spec in self._specs.values():
            if spec.kind == "master":
                return spec.id
        return next(iter(self._specs), "master")

    def state(self, agent_id: str) -> AgentState:
        return self._state.setdefault(agent_id, AgentState())

    def public(self, agent_id: str, tool_registry=None, provider_info: dict | None = None) -> dict | None:
        spec = self._specs.get(agent_id)
        if not spec:
            return None
        state = self.state(agent_id)
        tools_resolved: list[dict] = []
        if tool_registry is not None:
            for t in tool_registry.all():
                from .tool_registry import _matches
                if _matches(t.name, spec.tools):
                    tools_resolved.append({"name": t.name, "available": t.available and t.handler is not None})
        available_tools = sum(1 for t in tools_resolved if t["available"])
        status = state.status
        if not spec.enabled:
            status = "OFFLINE"
        elif tools_resolved and available_tools == 0 and spec.kind != "master":
            status = "OFFLINE" if state.status == "IDLE" else state.status
        return {
            "id": spec.id, "name": spec.name, "description": spec.description, "role": spec.role,
            "kind": spec.kind, "capabilities": spec.capabilities, "tools": spec.tools,
            "tools_resolved": tools_resolved, "available_tools": available_tools,
            "model": spec.model or (provider_info or {}).get("model", ""),
            "provider": spec.provider or (provider_info or {}).get("id", ""),
            "enabled": spec.enabled, "source": spec.source, "icon": spec.icon,
            "status": status, "current_task_id": state.current_task_id,
            "current_run_id": state.current_run_id, "current_activity": state.current_activity,
            "last_activity_at": state.last_activity_at, "last_error": state.last_error,
            "started_at": state.started_at, "stats": state.stats,
            "health": "offline" if not spec.enabled else ("degraded" if (
                tools_resolved and available_tools == 0 and spec.kind != "master") else "healthy"),
        }

    def list(self, tool_registry=None, provider_info: dict | None = None) -> list[dict]:
        return [self.public(a, tool_registry, provider_info) for a in self._specs]

    # ── live state ───────────────────────────────────────────────────────
    def set_status(self, agent_id: str, status: str, *, task_id: str | None = None,
                   run_id: str | None = None, activity: str = "", error: str = "") -> None:
        if status not in STATUSES:
            status = "IDLE"
        state = self.state(agent_id)
        state.status = status
        state.last_activity_at = now_iso()
        state.current_activity = activity
        if status in ("IDLE", "OFFLINE", "ERROR"):
            state.current_task_id = None
            state.current_run_id = None
            state.started_at = None
        else:
            if task_id is not None:
                state.current_task_id = task_id
            if run_id is not None:
                state.current_run_id = run_id
            if state.started_at is None:
                state.started_at = now_iso()
        if error:
            state.last_error = error
        spec = self._specs.get(agent_id)
        if spec:
            self.db.update("agents", agent_id, {"last_activity_at": state.last_activity_at,
                                                "stats": dumps(state.stats)})
        self.bus.publish("agent.status", {
            "id": agent_id, "status": status if (spec and spec.enabled) else "OFFLINE",
            "current_task_id": state.current_task_id, "current_run_id": state.current_run_id,
            "current_activity": activity, "last_activity_at": state.last_activity_at,
            "last_error": state.last_error, "started_at": state.started_at, "stats": state.stats,
        })

    def bump(self, agent_id: str, key: str, n: int = 1) -> None:
        st = self.state(agent_id)
        st.stats[key] = st.stats.get(key, 0) + n

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentSpec | None:
        spec = self._specs.get(agent_id)
        if not spec:
            return None
        spec.enabled = enabled
        self._persist(spec)
        self.set_status(agent_id, "IDLE" if enabled else "OFFLINE")
        return spec

    def counts(self) -> dict:
        active = sum(1 for a, s in self._state.items()
                     if s.status in ("THINKING", "EXECUTING", "WAITING") and self._specs.get(a) and self._specs[a].enabled)
        return {"total": len(self._specs), "enabled": sum(1 for s in self._specs.values() if s.enabled),
                "active": active}

    # ── run history ──────────────────────────────────────────────────────
    def runs(self, agent_id: str = "", limit: int = 50, task_id: str = "") -> list[dict]:
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.fetchall(f"SELECT * FROM agent_runs {where} ORDER BY started_at DESC LIMIT ?",
                                [*params, limit])
        for r in rows:
            r["steps"] = loads(r["steps"], [])
            r["usage"] = loads(r["usage"], {})
        return rows
