"""
Built-in tools. Each one is real: it touches the workspace, the host, the
docker socket, the task system or a configured integration. Tools whose
backing integration is missing are registered *unavailable* with a reason,
so the registry (and the model) know they exist but cannot be used yet.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from ..ai.base import trim
from ..db import new_id, now_iso
from .tool_registry import ToolContext, ToolRegistry, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import AppState


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _i(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def _b(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def register_builtin_tools(reg: ToolRegistry, state: "AppState") -> None:
    st = state
    settings = st.settings
    files = st.services["files"]
    metrics = st.services["metrics"]
    tasks = st.services["tasks"]

    # ── server ───────────────────────────────────────────────────────────
    async def server_status(ctx: ToolContext, args: dict):
        ov = metrics.overview()
        s = ov["sample"]
        return {"hostname": ov["hostname"], "os": ov["os"], "uptime_hours": round(s["uptime"] / 3600, 1),
                "cpu_percent": s["cpu"], "ram_percent": s["ram"], "ram_used_gb": round(s["ram_used"] / 1e9, 2),
                "ram_total_gb": round(s["ram_total"] / 1e9, 2), "disk_percent": s["disk"],
                "disk_free_gb": round((s["disk_total"] - s["disk_used"]) / 1e9, 2), "load": s["load"],
                "processes": s["processes"], "top_processes": metrics.processes(8),
                "services": metrics.service_status(), "docker": ov["docker"]}

    reg.register(ToolSpec("server.status", "Current server health: CPU, RAM, disk, load, uptime, top processes, "
                          "monitored services and docker state.", _obj({}), category="server",
                          risk="low", min_role="viewer", handler=server_status))

    async def docker_status(ctx: ToolContext, args: dict):
        res = await metrics.docker_containers(with_stats=bool(args.get("with_stats", True)))
        return res

    docker_available = os.path.exists(settings.docker_socket) or os.environ.get("DOCKER_HOST", "").startswith("tcp")
    reg.register(ToolSpec("docker.status", "List docker containers with state, image, ports and resource usage.",
                          _obj({"with_stats": _b("include CPU/memory per running container (slower)")}),
                          category="server", risk="low", min_role="viewer", handler=docker_status,
                          available=docker_available,
                          reason="" if docker_available else "docker socket not mounted"))

    async def docker_logs(ctx: ToolContext, args: dict):
        return await metrics.docker_logs(str(args["container"]), int(args.get("tail", 200)))

    reg.register(ToolSpec("docker.logs", "Read the last lines of a container's logs.",
                          _obj({"container": _s("container id or name"), "tail": _i("lines, default 200")},
                               ["container"]), category="server", risk="medium", handler=docker_logs,
                          available=docker_available, reason="" if docker_available else "docker socket not mounted"))

    async def docker_restart(ctx: ToolContext, args: dict):
        return await metrics.docker_action(str(args["container"]), "restart")

    docker_actions = docker_available and settings.allow_docker_actions
    reg.register(ToolSpec("docker.restart_container", "Restart a container. Requires user approval.",
                          _obj({"container": _s("container id or name"), "reason": _s("why this is needed")},
                               ["container", "reason"]), category="server", risk="high", handler=docker_restart,
                          available=docker_actions,
                          reason="" if docker_actions else ("docker socket not mounted" if not docker_available
                                                            else "JARVIS_CC_ALLOW_DOCKER_ACTIONS is not enabled")))

    async def restart_service(ctx: ToolContext, args: dict):
        return await asyncio.to_thread(metrics.restart_service, str(args["service"]))

    svc_ok = settings.allow_service_restart and bool(settings.monitored_services) and bool(shutil.which("systemctl"))
    reg.register(ToolSpec("server.restart_service", "Restart a monitored systemd service. Requires user approval.",
                          _obj({"service": _s("service name from the monitored list"),
                                "reason": _s("why this is needed")}, ["service", "reason"]),
                          category="server", risk="critical", handler=restart_service, available=svc_ok,
                          reason="" if svc_ok else "JARVIS_CC_ALLOW_SERVICE_RESTART / JARVIS_CC_MONITORED_SERVICES not set"))

    async def terminal_execute(ctx: ToolContext, args: dict):
        cmd = str(args["command"])
        cwd = files.root
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "JARVIS_CC_TOOL": "terminal"})
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(args.get("timeout", 60)))
        except asyncio.TimeoutError:
            proc.kill()
            return "command timed out", False
        text = out.decode("utf-8", "replace")
        return f"exit={proc.returncode}\n{trim(text, 8000)}", proc.returncode == 0

    reg.register(ToolSpec("terminal.execute", "Run a shell command on the server inside the workspace. "
                          "Every call requires user approval.",
                          _obj({"command": _s("shell command"), "reason": _s("what this achieves"),
                                "timeout": _i("seconds, default 60")}, ["command", "reason"]),
                          category="server", risk="critical", min_role="admin", handler=terminal_execute,
                          available=settings.allow_terminal, timeout_seconds=180,
                          reason="" if settings.allow_terminal else "JARVIS_CC_ALLOW_TERMINAL is not enabled"))

    async def logs_search(ctx: ToolContext, args: dict):
        rows = st.log.query(q=str(args.get("query", "")), level=str(args.get("level", "")),
                            source=str(args.get("source", "")), limit=int(args.get("limit", 50)))
        return [{"ts": r["ts"], "level": r["level"], "source": r["source"], "message": r["message"]} for r in rows]

    reg.register(ToolSpec("logs.search", "Search the log center.",
                          _obj({"query": _s("text to search"), "level": _s("minimum level, e.g. WARNING"),
                                "source": _s("log source"), "limit": _i("max rows")}),
                          category="observability", risk="low", min_role="viewer", handler=logs_search))

    # ── filesystem (workspace only) ──────────────────────────────────────
    async def fs_list(ctx: ToolContext, args: dict):
        return files.list(str(args.get("path", "")))

    async def fs_read(ctx: ToolContext, args: dict):
        res = files.read_text(str(args["path"]), max_bytes=int(args.get("max_bytes", 200_000)))
        return res["content"] + ("\n…[truncated]" if res["truncated"] else "")

    async def fs_write(ctx: ToolContext, args: dict):
        info = files.write_text(str(args["path"]), str(args.get("content", "")), source=f"agent:{ctx.agent_id}",
                                owner=ctx.principal.actor, task_id=ctx.task_id)
        return f"wrote {info['size']} bytes to {info['path']}"

    async def fs_delete(ctx: ToolContext, args: dict):
        return files.delete(str(args["path"]))

    async def fs_search(ctx: ToolContext, args: dict):
        return files.search(str(args["query"]), limit=int(args.get("limit", 50)))

    reg.register(ToolSpec("filesystem.list", "List a directory inside the workspace.",
                          _obj({"path": _s("relative path, '' for root")}), category="files", risk="low",
                          min_role="viewer", handler=fs_list))
    reg.register(ToolSpec("filesystem.read", "Read a text file from the workspace.",
                          _obj({"path": _s("relative path"), "max_bytes": _i("cap, default 200000")}, ["path"]),
                          category="files", risk="low", min_role="viewer", handler=fs_read))
    reg.register(ToolSpec("filesystem.write", "Create or overwrite a text file in the workspace.",
                          _obj({"path": _s("relative path"), "content": _s("full file content")},
                               ["path", "content"]), category="files", risk="medium", handler=fs_write))
    reg.register(ToolSpec("filesystem.delete", "Move a workspace file or folder to the trash. Requires approval.",
                          _obj({"path": _s("relative path"), "reason": _s("why")}, ["path", "reason"]),
                          category="files", risk="high", handler=fs_delete))
    reg.register(ToolSpec("filesystem.search", "Find workspace files by name.",
                          _obj({"query": _s("substring of the file name"), "limit": _i("max results")}, ["query"]),
                          category="files", risk="low", min_role="viewer", handler=fs_search))

    async def document_create(ctx: ToolContext, args: dict):
        title = str(args["title"]).strip()
        slug = re.sub(r"[^a-zA-Z0-9äöüÄÖÜß_-]+", "-", title).strip("-")[:60] or "document"
        ext = "md" if str(args.get("format", "markdown")).lower() in ("markdown", "md") else "txt"
        path = f"documents/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_{slug}.{ext}"
        info = files.write_text(path, str(args["content"]), source=f"agent:{ctx.agent_id}",
                                owner=ctx.principal.actor, task_id=ctx.task_id, overwrite=False)
        ctx.emit("file", {"text": f"Document created: {info['path']}", "path": info["path"]})
        return {"path": info["path"], "size": info["size"], "download": f"/api/files/download?path={info['path']}"}

    reg.register(ToolSpec("document.create", "Create a document (offer, letter, report, notes) in the workspace.",
                          _obj({"title": _s("document title"), "content": _s("full document text"),
                                "format": _s("markdown (default) or text")}, ["title", "content"]),
                          category="documents", risk="medium", handler=document_create))

    # ── tasks ────────────────────────────────────────────────────────────
    async def task_create(ctx: ToolContext, args: dict):
        t = tasks.create(title=str(args["title"]), description=str(args.get("description", "")),
                         created_by=f"agent:{ctx.agent_id}", assigned_agent=str(args.get("agent") or ctx.agent_id),
                         priority=str(args.get("priority", "normal")), parent_id=ctx.task_id,
                         conversation_id=ctx.conversation_id, run_id=ctx.run_id,
                         status="RUNNING" if not args.get("agent") or args.get("agent") == ctx.agent_id else "QUEUED")
        if ctx.task_id is None and not args.get("agent"):
            ctx.task_id = t["id"]
            run = st.runtime.get_run(ctx.run_id or "") if st.runtime else None
            if run:
                run.task_id = t["id"]
                st.db.update("agent_runs", run.id, {"task_id": t["id"]})
        ctx.emit("task", {"text": f"Task created: {t['title']}", "task_id": t["id"]})
        return {"task_id": t["id"], "status": t["status"]}

    async def task_update(ctx: ToolContext, args: dict):
        tid = str(args["task_id"])
        status = str(args.get("status", "")).upper()
        if status:
            t = tasks.set_status(tid, status, output=args.get("output"), error=args.get("error"),
                                 note=f"{ctx.agent_id} set {status}")
        else:
            t = tasks.update(tid, title=args.get("title"), description=args.get("description"),
                             priority=args.get("priority"))
        if args.get("note"):
            tasks.add_log(tid, "INFO", str(args["note"]))
        return {"task_id": tid, "status": t["status"] if t else "not found"}

    async def task_list(ctx: ToolContext, args: dict):
        rows = tasks.list(status=str(args.get("status", "")), limit=int(args.get("limit", 20)))
        return [{"id": r["id"], "title": r["title"], "status": r["status"], "agent": r["assigned_agent"],
                 "priority": r["priority"], "updated_at": r["updated_at"]} for r in rows]

    reg.register(ToolSpec("task.create", "Create a task in the task manager for multi-step work.",
                          _obj({"title": _s("short title"), "description": _s("what needs to happen"),
                                "priority": _s("low|normal|high|critical"),
                                "agent": _s("agent id to queue it for (omit to work on it yourself)")},
                               ["title"]), category="tasks", risk="low", handler=task_create))
    reg.register(ToolSpec("task.update", "Update a task's status, fields, output or add a progress note.",
                          _obj({"task_id": _s("task id"), "status": _s("QUEUED|PLANNING|RUNNING|PAUSED|COMPLETED|FAILED|CANCELLED"),
                                "output": _s("result text"), "error": _s("error text"), "note": _s("progress note"),
                                "title": _s(""), "description": _s(""), "priority": _s("")}, ["task_id"]),
                          category="tasks", risk="low", handler=task_update))
    reg.register(ToolSpec("task.list", "List tasks, optionally filtered by status (or 'active').",
                          _obj({"status": _s("status filter"), "limit": _i("max rows")}),
                          category="tasks", risk="low", min_role="viewer", handler=task_list))

    # ── agents ───────────────────────────────────────────────────────────
    async def agent_delegate(ctx: ToolContext, args: dict):
        return await st.runtime.delegate(ctx, str(args["agent_id"]), str(args["instruction"]),
                                         title=str(args.get("title", "")))

    async def agent_list(ctx: ToolContext, args: dict):
        return [{"id": a["id"], "name": a["name"], "role": a["role"], "status": a["status"],
                 "capabilities": a["capabilities"], "available_tools": a["available_tools"]}
                for a in st.agents.list(st.tools)]

    reg.register(ToolSpec("agent.delegate", "Hand a complete sub-goal to a specialist agent and get its result back. "
                          "The specialist works with its own tools; you stay the one voice to the user.",
                          _obj({"agent_id": _s("specialist id"), "instruction": _s("complete, self-contained brief"),
                                "title": _s("short task title")}, ["agent_id", "instruction"]),
                          category="agents", risk="low", handler=agent_delegate, timeout_seconds=900))
    reg.register(ToolSpec("agent.list", "List registered agents with status and capabilities.", _obj({}),
                          category="agents", risk="low", min_role="viewer", handler=agent_list))

    # ── workflows ────────────────────────────────────────────────────────
    hub = st.workflows
    wf_ok = hub is not None and hub.configured()

    async def workflow_list(ctx: ToolContext, args: dict):
        items = await hub.sync()
        return [{"id": w["id"], "name": w["name"], "active": w["active"], "trigger": w["trigger"],
                 "last_status": w["last_status"], "last_execution_at": w["last_execution_at"]} for w in items]

    async def workflow_execute(ctx: ToolContext, args: dict):
        res = await hub.trigger(str(args["workflow_id"]), args.get("payload") or {}, by=f"agent:{ctx.agent_id}")
        return res, bool(res.get("ok"))

    async def workflow_runs(ctx: ToolContext, args: dict):
        return hub.cached_runs(str(args.get("workflow_id", "")), limit=int(args.get("limit", 20)))

    reg.register(ToolSpec("workflow.list", "List automation workflows (n8n) with status and last execution.",
                          _obj({}), category="workflows", risk="low", min_role="viewer", handler=workflow_list,
                          available=wf_ok, reason="" if wf_ok else "n8n not configured (N8N_BASE_URL, N8N_API_KEY)"))
    reg.register(ToolSpec("workflow.execute", "Trigger a workflow through its webhook.",
                          _obj({"workflow_id": _s("id from workflow.list"), "payload": {"type": "object",
                                "description": "JSON body for the webhook"}}, ["workflow_id"]),
                          category="workflows", risk="medium", handler=workflow_execute, available=wf_ok,
                          reason="" if wf_ok else "n8n not configured"))
    reg.register(ToolSpec("workflow.runs", "Recent executions of a workflow.",
                          _obj({"workflow_id": _s("workflow id"), "limit": _i("max rows")}),
                          category="workflows", risk="low", min_role="viewer", handler=workflow_runs,
                          available=wf_ok, reason="" if wf_ok else "n8n not configured"))

    # ── integrations / notifications / memory / web ──────────────────────
    async def integration_status(ctx: ToolContext, args: dict):
        return [{"id": i["id"], "name": i["name"], "status": i["status"], "detail": i["detail"]}
                for i in st.integrations.list()]

    reg.register(ToolSpec("integration.status", "Connection status of every integration.", _obj({}),
                          category="integrations", risk="low", min_role="viewer", handler=integration_status))

    async def notify_user(ctx: ToolContext, args: dict):
        n = st.services["notifications"].notify(
            category=str(args.get("category", "agent")), title=str(args["title"]), body=str(args.get("body", "")),
            severity=str(args.get("severity", "info")),
            user_id=ctx.principal.id if ctx.principal.kind == "user" else "*")
        return {"notification_id": n["id"]}

    reg.register(ToolSpec("notify.user", "Send a notification to the notification center.",
                          _obj({"title": _s("headline"), "body": _s("details"),
                                "severity": _s("info|success|warning|error"), "category": _s("task|agent|server|system")},
                               ["title"]), category="communication", risk="low", handler=notify_user))

    async def memory_remember(ctx: ToolContext, args: dict):
        st.db.insert("memory", {"id": new_id("mem"), "text": str(args["text"])[:4000], "actor": ctx.principal.actor,
                                "conversation_id": ctx.conversation_id, "created_at": now_iso()})
        return "remembered"

    async def memory_search(ctx: ToolContext, args: dict):
        rows = st.db.fetchall("SELECT text, created_at FROM memory WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?",
                              (f"%{args['query']}%", int(args.get("limit", 10))))
        return rows or "nothing stored matches"

    reg.register(ToolSpec("memory.remember", "Store a fact or preference for later.", _obj({"text": _s("fact")}, ["text"]),
                          category="memory", risk="low", handler=memory_remember))
    reg.register(ToolSpec("memory.search", "Search stored facts.", _obj({"query": _s("keyword"), "limit": _i("")}, ["query"]),
                          category="memory", risk="low", min_role="viewer", handler=memory_search))

    async def web_fetch(ctx: ToolContext, args: dict):
        url = str(args["url"])
        if not url.lower().startswith(("http://", "https://")):
            return "only http(s) URLs are allowed", False
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": "JARVIS-CommandCenter/0.1"}) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        body = r.text[:400_000]
        if "html" in ctype:
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            body = html.unescape(re.sub(r"\s+", " ", body))
        return f"HTTP {r.status_code} {ctype}\n{trim(body, int(args.get('max_chars', 20000)))}", r.status_code < 400

    reg.register(ToolSpec("web.fetch", "Fetch a web page or API URL and return its text.",
                          _obj({"url": _s("http(s) URL"), "max_chars": _i("cap, default 20000")}, ["url"]),
                          category="web", risk="low", min_role="viewer", handler=web_fetch))

    # ── declared but not yet connected ───────────────────────────────────
    for name, desc, cat in (
        ("email.search", "Search the connected mailbox.", "communication"),
        ("email.send", "Send an email (approval-gated).", "communication"),
        ("calendar.read", "Read calendar events.", "productivity"),
        ("calendar.create", "Create a calendar event.", "productivity"),
        ("github.read", "Read a file or issue from GitHub.", "code"),
        ("github.commit", "Commit a change to GitHub (approval-gated).", "code"),
        ("browser.open", "Open a URL in the browser agent.", "web"),
    ):
        integration = {"email": "email", "calendar": "google", "github": "github", "browser": "browser"}[name.split(".")[0]]
        reg.register(ToolSpec(name, desc, _obj({}), category=cat,
                              risk="high" if name.endswith(("send", "commit")) else "low", handler=None,
                              available=False, reason=f"{integration} integration not connected"))
