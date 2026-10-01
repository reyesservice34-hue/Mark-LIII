"""
Server-side agent management for MIA.

One canonical roster is shared by the Mark-LIII agency and the Command Center:
config/command_center/agents.json. Creating or updating a specialist is
reversible, persisted on MIA-BRAIN-01, and hot-loaded by both runtimes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ROSTER_PATH = BASE_DIR / "config" / "command_center" / "agents.json"
BACKUP_DIR = BASE_DIR / "data" / "agent-roster-backups"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# Mark-LIII action names and Command Center tool namespaces are intentionally
# different. Keep both in the same roster entry instead of pretending one name
# works in both runtimes.
_CC_TOOL_MAP = {
    "web_search": ["web.search", "web.fetch"],
    "read_link": ["web.fetch"],
    "file_processor": ["filesystem.read", "filesystem.list"],
    "file_controller": ["filesystem.*"],
    "code_helper": ["filesystem.read", "filesystem.list"],
    "dev_agent": ["filesystem.*", "terminal.execute", "github.*"],
    "self_dev": ["self.*", "filesystem.*", "terminal.execute"],
    "system_status": ["server.*", "logs.search"],
    "ssh_exec": ["server.*", "terminal.execute", "logs.search"],
    "background_task": ["task.*", "notify.user"],
    "reminder": ["task.*", "calendar.*", "notify.user"],
    "search_knowledge": ["memory.*", "filesystem.read"],
    "knowledge_lookup": ["memory.*", "filesystem.read"],
    "send_message": ["notify.user"],
    "ask_command_center": ["task.*", "memory.*", "notify.user"],
}


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9_]+", "_", (value or "").strip().lower()).strip("_")
    if out and out[0].isdigit():
        out = "agent_" + out
    return out[:64]


def _load() -> dict:
    if not ROSTER_PATH.exists():
        return {"replace": False, "entry": "coordinator", "agents": [], "flows": []}
    try:
        data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Agentenliste ist unlesbar: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Agentenliste hat kein gültiges Objektformat.")
    data.setdefault("replace", False)
    data.setdefault("entry", "coordinator")
    data.setdefault("agents", [])
    data.setdefault("flows", [])
    return data


def _save(data: dict) -> Path:
    ROSTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if ROSTER_PATH.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(ROSTER_PATH, BACKUP_DIR / f"agents-{stamp}.json")

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".agents-", suffix=".json", dir=ROSTER_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o640)
        os.replace(tmp_name, ROSTER_PATH)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
    return ROSTER_PATH


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _available_mia_tools() -> set[str]:
    from core.action_loader import discover_actions
    from core.plugin_loader import discover_plugins

    actions = discover_actions(BASE_DIR / "actions", reserved_names={"agent_manager"},
                               logger=lambda _message: None)
    plugins = discover_plugins(BASE_DIR / "plugins",
                               core_tool_names=actions.names() | {"agent_manager"},
                               logger=lambda _message: None)
    names = set(actions.names())
    names.update(
        declaration["name"]
        for declaration in plugins.get_tool_declarations()
        if declaration.get("name")
    )
    # These are provided inline by core/local_brain.py rather than actions/.
    names.update({"recall_memory", "save_memory", "system_status", "undo"})
    return names


def _normalise_tools(requested) -> tuple[list[str], list[str]]:
    available = _available_mia_tools()
    accepted, rejected = [], []
    for name in _strings(requested):
        if name in {"agent_manager", "agency_agent"}:
            rejected.append(name)
        elif name in available:
            accepted.append(name)
        else:
            rejected.append(name)
    return list(dict.fromkeys(accepted)), list(dict.fromkeys(rejected))


def _cc_tools(mia_tools: list[str]) -> list[str]:
    out: list[str] = []
    for tool in mia_tools:
        out.extend(_CC_TOOL_MAP.get(tool, []))
    return list(dict.fromkeys(out))


def _public(entry: dict) -> dict:
    return {
        "id": entry.get("id") or entry.get("name"),
        "name": entry.get("display_name") or entry.get("name"),
        "role": entry.get("role", ""),
        "capabilities": entry.get("capabilities", []),
        "mia_tools": entry.get("mia_tools", []),
        "command_center_tools": entry.get("tools", []),
    }


def manage_agents(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    operation = str(params.get("operation") or "list").strip().lower()
    roster = _load()
    agents = roster["agents"]

    if operation == "list":
        if not agents:
            return "Keine zusätzlich angelegten Spezialagenten. Das feste Kernteam ist aktiv."
        return json.dumps([_public(agent) for agent in agents], ensure_ascii=False, indent=2)

    raw_id = str(params.get("id") or params.get("name") or "")
    agent_id = _slug(raw_id)
    if not _NAME_RE.match(agent_id):
        return "Agent nicht angelegt: Bitte einen eindeutigen Namen mit mindestens zwei Zeichen angeben."

    existing = next((item for item in agents
                     if str(item.get("id") or item.get("name")) == agent_id), None)
    display_name = str(params.get("name") or "").strip()[:80] or agent_id.replace("_", " ").title()
    role = str(params.get("role") or "").strip()[:500]
    instructions = str(params.get("instructions") or "").strip()[:20_000]
    capabilities = _strings(params.get("capabilities"))
    mia_tools, rejected = _normalise_tools(params.get("tools"))

    if operation == "create":
        if existing is not None:
            return f"Agent '{agent_id}' existiert bereits. Nutze operation='update'."
        if not role or not instructions:
            return "Agent nicht angelegt: Rolle und konkrete Arbeitsanweisungen fehlen."
        entry = {
            "id": agent_id,
            "name": agent_id,
            "display_name": display_name,
            "kind": "specialist",
            "role": role,
            "description": str(params.get("description") or role).strip()[:1000],
            "instructions": instructions,
            "capabilities": capabilities,
            "mia_tools": mia_tools,
            "tools": _cc_tools(mia_tools),
            "model": "",
            "provider": "",
            "enabled": True,
            "icon": str(params.get("icon") or "sparkles")[:40],
        }
        agents.append(entry)
        edge = ["coordinator", agent_id]
        if edge not in roster["flows"]:
            roster["flows"].append(edge)
        _save(roster)
        extra = f" Nicht verfügbare oder unzulässige Werkzeuge ausgelassen: {', '.join(rejected)}." if rejected else ""
        return (
            f"[AGENT_CREATED] Spezialagent '{display_name}' wurde serverseitig angelegt, "
            f"mit {len(mia_tools)} MIA-Werkzeug(en) und {len(entry['tools'])} "
            f"Command-Center-Berechtigung(en).{extra}"
        )

    if operation == "update":
        if existing is None:
            return f"Agent '{agent_id}' wurde nicht gefunden."
        if params.get("name"):
            existing["display_name"] = display_name
        if params.get("role") is not None:
            existing["role"] = role
        if params.get("description") is not None:
            existing["description"] = str(params.get("description") or "").strip()[:1000]
        if params.get("instructions") is not None:
            existing["instructions"] = instructions
        if params.get("capabilities") is not None:
            existing["capabilities"] = capabilities
        if params.get("tools") is not None:
            existing["mia_tools"] = mia_tools
            existing["tools"] = _cc_tools(mia_tools)
        _save(roster)
        extra = f" Ausgelassen: {', '.join(rejected)}." if rejected else ""
        return f"[AGENT_UPDATED] Spezialagent '{agent_id}' wurde serverseitig aktualisiert.{extra}"

    return "Unbekannte Operation. Verwende list, create oder update."


TOOL = {
    "name": "agent_manager",
    "description": (
        "Creates, lists, or updates persistent specialist agents on MIA-BRAIN-01. "
        "Use when the owner asks to create/configure an agent, specialist, agent team, "
        "or new delegated role. One server-side roster feeds both MIA's own agency and "
        "the Command Center. Creating/updating is reversible and needs no extra approval; "
        "this tool deliberately cannot delete agents or grant itself/agency recursion."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "description": "list | create | update"},
            "id": {"type": "STRING", "description": "Stable snake_case agent id"},
            "name": {"type": "STRING", "description": "Human-readable agent name"},
            "role": {"type": "STRING", "description": "The specialist's responsibility"},
            "description": {"type": "STRING", "description": "Short dashboard description"},
            "instructions": {"type": "STRING", "description": "Concrete operating instructions"},
            "capabilities": {"type": "ARRAY", "items": {"type": "STRING"}},
            "tools": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Existing MIA action/plugin names the agent may use",
            },
            "icon": {"type": "STRING"},
        },
        "required": ["operation"],
    },
    "handler": manage_agents,
}
