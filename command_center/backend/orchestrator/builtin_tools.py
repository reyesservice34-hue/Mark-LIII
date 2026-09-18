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

    # ── calendar ─────────────────────────────────────────────────────────
    calendar = st.services["calendar"]
    cal_ok = calendar.available()
    cal_reason = calendar.unavailable_reason()

    async def calendar_read(ctx: ToolContext, args: dict):
        events, backend = await calendar.list(days=int(args.get("days", 7)), query=str(args.get("query", "")))
        if not events:
            return {"backend": backend, "events": [], "note": "no appointments in that period"}
        return {"backend": backend, "events": [
            {"title": e["title"], "start": e["start"], "end": e["end"], "location": e["location"],
             "notes": e["notes"]} for e in events]}

    async def calendar_create(ctx: ToolContext, args: dict):
        event, backend, note = await calendar.create(
            title=str(args["title"]), when=str(args["when"]), at=str(args.get("at", "")),
            duration=args.get("duration", 60), location=str(args.get("location", "")),
            notes=str(args.get("notes", "")))
        ctx.emit("calendar", {"text": f"Appointment booked ({backend}): {event['title']} {event['start']}"})
        return {"booked": True, "backend": backend, "start": event["start"], "end": event["end"],
                "title": event["title"], "note": note}

    async def calendar_move(ctx: ToolContext, args: dict):
        event, backend = await calendar.move(str(args["query"]), str(args["when"]), str(args.get("at", "")))
        return {"moved": True, "backend": backend, "title": event["title"], "start": event["start"]}

    async def calendar_cancel(ctx: ToolContext, args: dict):
        event, backend = await calendar.cancel(str(args["query"]))
        return {"cancelled": True, "backend": backend, "title": event["title"], "start": event["start"]}

    reg.register(ToolSpec("calendar.read", "List upcoming appointments. Uses Google Calendar when connected, "
                          "otherwise the local calendar on this server.",
                          _obj({"days": _i("how far ahead to look, default 7"),
                                "query": _s("only appointments whose title contains this")}),
                          category="productivity", risk="low", min_role="viewer", handler=calendar_read,
                          available=cal_ok, reason=cal_reason))
    reg.register(ToolSpec("calendar.create", "Book an appointment. Refuses dates it cannot read rather than guessing.",
                          _obj({"title": _s("what the appointment is"),
                                "when": _s("date, e.g. 2026-09-24, 'tomorrow', 'Montag' — may include the time"),
                                "at": _s("time, e.g. 14:00 (optional if 'when' already has it)"),
                                "duration": _s("minutes or e.g. '1h', default 60"),
                                "location": _s("where"), "notes": _s("extra notes")}, ["title", "when"]),
                          category="productivity", risk="medium", handler=calendar_create,
                          available=cal_ok, reason=cal_reason))
    reg.register(ToolSpec("calendar.move", "Move an existing appointment to a new date/time.",
                          _obj({"query": _s("part of the appointment title"), "when": _s("new date"),
                                "at": _s("new time")}, ["query", "when"]),
                          category="productivity", risk="medium", handler=calendar_move,
                          available=cal_ok, reason=cal_reason))
    reg.register(ToolSpec("calendar.cancel", "Cancel an appointment. Requires approval.",
                          _obj({"query": _s("part of the appointment title"), "reason": _s("why")},
                               ["query", "reason"]),
                          category="productivity", risk="high", handler=calendar_cancel,
                          available=cal_ok, reason=cal_reason))

    # ── email ────────────────────────────────────────────────────────────
    mail = st.services["email"]
    mail_ok = mail.configured()
    mail_reason = mail.unavailable_reason()

    async def email_search(ctx: ToolContext, args: dict):
        q = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 10))
        if str(args.get("unread", "")).lower() in ("1", "true", "yes"):
            items = await mail.unread(limit)
        elif q:
            items = await mail.search(q, limit)
        else:
            items = await mail.recent(limit)
        return items or "no matching mail"

    async def email_read(ctx: ToolContext, args: dict):
        return await mail.read(str(args["query"]))

    async def email_draft(ctx: ToolContext, args: dict):
        msg = mail.build(str(args["to"]), str(args.get("subject", "")), str(args.get("body", "")),
                         str(args.get("cc", "")))
        return {"drafted": True, "to": msg["To"], "subject": msg["Subject"], "body": str(args.get("body", "")),
                "note": "Nothing was sent. Read the draft to the user and use email.send only once they agree."}

    async def email_send(ctx: ToolContext, args: dict):
        result = await mail.send(str(args["to"]), str(args.get("subject", "")), str(args.get("body", "")),
                                 str(args.get("cc", "")))
        ctx.emit("email", {"text": f"Mail sent to {result['to']}"})
        return result

    reg.register(ToolSpec("email.search", "Search the mailbox, or list the newest / unread mail.",
                          _obj({"query": _s("text in subject, sender or body"), "limit": _i("max results"),
                                "unread": _s("'true' for unread only")}),
                          category="communication", risk="low", handler=email_search,
                          available=mail_ok, reason=mail_reason, timeout_seconds=60))
    reg.register(ToolSpec("email.read", "Read one mail in full, including its body.",
                          _obj({"query": _s("text identifying the mail")}, ["query"]),
                          category="communication", risk="low", handler=email_read,
                          available=mail_ok, reason=mail_reason, timeout_seconds=60))
    reg.register(ToolSpec("email.draft", "Compose a mail without sending it. Always draft before sending.",
                          _obj({"to": _s("recipient address"), "subject": _s("subject"), "body": _s("full text"),
                                "cc": _s("optional cc")}, ["to", "body"]),
                          category="communication", risk="low", handler=email_draft,
                          available=mail_ok, reason=mail_reason))
    reg.register(ToolSpec("email.send", "Send a mail. Irreversible, so it always requires the user's approval.",
                          _obj({"to": _s("recipient address"), "subject": _s("subject"), "body": _s("full text"),
                                "cc": _s("optional cc"), "reason": _s("why this mail is being sent")},
                               ["to", "body", "reason"]),
                          category="communication", risk="high", handler=email_send,
                          available=mail_ok, reason=mail_reason, timeout_seconds=90))

    # ── github ───────────────────────────────────────────────────────────
    gh = st.services["github"]
    gh_ok = gh.configured()
    gh_reason = gh.unavailable_reason()

    async def github_read(ctx: ToolContext, args: dict):
        return await gh.read_file(str(args.get("repo", "")), str(args["path"]), str(args.get("ref", "")))

    async def github_issues(ctx: ToolContext, args: dict):
        if args.get("number"):
            return await gh.read_issue(str(args.get("repo", "")), int(args["number"]))
        return await gh.list_issues(str(args.get("repo", "")), str(args.get("state", "open")),
                                    int(args.get("limit", 10)))

    async def github_commits(ctx: ToolContext, args: dict):
        return await gh.list_commits(str(args.get("repo", "")), int(args.get("limit", 10)),
                                     str(args.get("branch", "")))

    async def github_repo(ctx: ToolContext, args: dict):
        return await gh.repo_overview(str(args.get("repo", "")))

    reg.register(ToolSpec("github.read", "Read a file or list a directory in a GitHub repository.",
                          _obj({"repo": _s("owner/repo (optional if GITHUB_DEFAULT_REPO is set)"),
                                "path": _s("path inside the repository"), "ref": _s("branch, tag or commit")},
                               ["path"]), category="code", risk="low", min_role="viewer", handler=github_read,
                          available=gh_ok, reason=gh_reason))
    reg.register(ToolSpec("github.issues", "List issues and pull requests, or read one with its comments.",
                          _obj({"repo": _s("owner/repo"), "number": _i("issue/PR number to read in full"),
                                "state": _s("open|closed|all"), "limit": _i("max rows")}),
                          category="code", risk="low", min_role="viewer", handler=github_issues,
                          available=gh_ok, reason=gh_reason))
    reg.register(ToolSpec("github.commits", "Recent commits on a repository.",
                          _obj({"repo": _s("owner/repo"), "branch": _s("branch"), "limit": _i("max rows")}),
                          category="code", risk="low", min_role="viewer", handler=github_commits,
                          available=gh_ok, reason=gh_reason))
    reg.register(ToolSpec("github.repo", "Overview of a repository: description, default branch, open issues.",
                          _obj({"repo": _s("owner/repo")}), category="code", risk="low", min_role="viewer",
                          handler=github_repo, available=gh_ok, reason=gh_reason))

    # ── desktop (the paired PC) ──────────────────────────────────────────
    desktop = st.services["desktop"]

    async def desktop_devices(ctx: ToolContext, args: dict):
        devices = desktop.list()
        if not devices:
            return ("No desktop is paired yet. Start JARVIS on the PC with the gateway token set and it "
                    "registers itself.")
        return [{"name": d["name"], "id": d["id"], "platform": d["platform"], "online": d["online"],
                 "last_seen": d["last_seen_at"], "can": [a.get("name") for a in d["actions"]]}
                for d in devices]

    async def desktop_open_app(ctx: ToolContext, args: dict):
        device = desktop.resolve(str(args.get("device", "")))
        cmd = await desktop.dispatch(device_id=device["id"], action="open_app",
                                     params={"app_name": str(args["app"])},
                                     requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
                                     task_id=ctx.task_id, run_id=ctx.run_id)
        ctx.emit("desktop", {"text": f"{device['name']}: opened {args['app']}"})
        return (cmd["result"] or "done", cmd["status"] == "done")

    async def desktop_run(ctx: ToolContext, args: dict):
        device = desktop.resolve(str(args.get("device", "")))
        action = str(args["action"])
        params = args.get("params") or {}
        if not isinstance(params, dict):
            return "params must be a JSON object", False
        cmd = await desktop.dispatch(device_id=device["id"], action=action, params=params,
                                     requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
                                     task_id=ctx.task_id, run_id=ctx.run_id,
                                     timeout=float(args.get("timeout", 90)))
        ctx.emit("desktop", {"text": f"{device['name']}: {action}"})
        return (cmd["result"] or cmd["error"] or "done", cmd["status"] == "done")

    # Driving someone's own machine is gated by default; set
    # JARVIS_CC_DESKTOP_REQUIRE_APPROVAL=false once you trust the setup.
    gate_desktop = (os.environ.get("JARVIS_CC_DESKTOP_REQUIRE_APPROVAL", "true").strip().lower()
                    not in ("0", "false", "no", "off"))

    reg.register(ToolSpec("desktop.devices", "List the paired desktops, whether they are online, and which "
                          "actions each one offers.", _obj({}), category="desktop", risk="low",
                          min_role="viewer", handler=desktop_devices))
    reg.register(ToolSpec("desktop.open_app", "Open an application or website on the paired PC.",
                          _obj({"app": _s("application name, e.g. 'Chrome', 'Excel', 'WhatsApp'"),
                                "device": _s("which desktop, if more than one is online")}, ["app"]),
                          category="desktop", risk="medium", handler=desktop_open_app, timeout_seconds=120))
    reg.register(ToolSpec("desktop.run", "Run one of the PC's own actions on it — typing, clicking, window "
                          "control, volume, browser control, screenshots. Call desktop.devices first to see "
                          "what that machine offers and which parameters it takes.",
                          _obj({"action": _s("action name from desktop.devices"),
                                "params": {"type": "object", "description": "arguments for that action"},
                                "device": _s("which desktop"), "reason": _s("why this is needed"),
                                "timeout": _i("seconds to wait, default 90")}, ["action", "reason"]),
                          category="desktop", risk="high" if gate_desktop else "medium",
                          requires_approval=gate_desktop, handler=desktop_run, timeout_seconds=180))

    async def desktop_whatsapp(ctx: ToolContext, args: dict):
        """Reach the user on their phone through the WhatsApp the desktop already has linked."""
        device = desktop.resolve(str(args.get("device", "")))
        if not any(a.get("name") == "whatsapp" for a in device["actions"]):
            return (f"The desktop '{device['name']}' has no WhatsApp channel. It is linked once by QR "
                    f"code in the desktop's plugin settings.", False)
        params = {"message": str(args["message"]), "action": str(args.get("mode", "voice"))}
        if args.get("contact"):
            params["contact"] = str(args["contact"])
        cmd = await desktop.dispatch(device_id=device["id"], action="whatsapp", params=params,
                                     requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
                                     task_id=ctx.task_id, run_id=ctx.run_id, timeout=180)
        ctx.emit("whatsapp", {"text": f"WhatsApp sent via {device['name']}"})
        return (cmd["result"] or cmd["error"] or "sent", cmd["status"] == "done")

    reg.register(ToolSpec("notify.whatsapp", "Reach the user on their phone by WhatsApp, as a spoken voice "
                          "note in your own voice or as text. Goes through the desktop's linked WhatsApp, so "
                          "it needs that PC to be running. Use it when something matters and they are away "
                          "from the machine.",
                          _obj({"message": _s("the finished message, written to be heard: short sentences, "
                                              "first person, no lists, no markdown"),
                                "mode": _s("voice (default) or text"),
                                "contact": _s("recipient as named in WhatsApp; omit for the configured one"),
                                "device": _s("which desktop")}, ["message"]),
                          category="communication", risk="medium", handler=desktop_whatsapp,
                          timeout_seconds=200))

    # ── teach mode ───────────────────────────────────────────────────────
    teaching = st.services["teaching"]

    async def teach_start(ctx: ToolContext, args: dict):
        rec = teaching.start(title=str(args["title"]), goal=str(args.get("goal", "")),
                             user_id=ctx.principal.id, actor=ctx.principal.actor,
                             conversation_id=ctx.conversation_id)
        ctx.emit("teach", {"text": f"Recording started: {rec['title']}"})
        return {"recording_id": rec["id"], "note": "Everything said and every tool used is now recorded "
                                                    "until teach.stop."}

    async def teach_note(ctx: ToolContext, args: dict):
        rec_id = teaching.active_for(ctx.principal.id)
        if not rec_id:
            return "Nothing is being recorded right now.", False
        teaching.append(rec_id, kind="note", text=str(args["text"]), actor=ctx.agent_id)
        return "noted"

    async def teach_stop(ctx: ToolContext, args: dict):
        rec = teaching.stop(user_id=ctx.principal.id)
        return {"recording_id": rec["id"], "events": rec["event_count"],
                "note": "Use teach.learn to turn it into a procedure."}

    async def teach_learn(ctx: ToolContext, args: dict):
        rec_id = str(args.get("recording_id") or "") or teaching.active_for(ctx.principal.id) or ""
        if not rec_id:
            recent = teaching.list(limit=1)
            rec_id = recent[0]["id"] if recent else ""
        if not rec_id:
            return "There is no recording to learn from.", False
        result = await teaching.distill(st, rec_id, create_agent=bool(args.get("create_agent", True)),
                                        actor=ctx.principal.actor)
        proc = result["procedure"]
        ctx.emit("teach", {"text": f"Learned procedure: {proc['name']}"})
        return {"procedure_id": proc["id"], "name": proc["name"], "steps": len(proc["steps"]),
                "agent": (result.get("agent") or {}).get("name", ""),
                "dropped_tools": result.get("dropped_tools", [])}

    async def procedure_list(ctx: ToolContext, args: dict):
        procs = teaching.procedures()
        if not procs:
            return "Nothing has been learned yet."
        return [{"id": p["id"], "name": p["name"], "description": p["description"],
                 "steps": len(p["steps"]), "agent": p["agent_id"], "trigger": p["trigger"],
                 "runs": p["runs"], "last_run_at": p["last_run_at"]} for p in procs]

    async def procedure_run(ctx: ToolContext, args: dict):
        proc = teaching.procedure(str(args["procedure_id"]))
        if not proc:
            matches = [p for p in teaching.procedures()
                       if str(args["procedure_id"]).lower() in p["name"].lower()]
            if len(matches) != 1:
                return "No single procedure matches that — use procedure.list first.", False
            proc = matches[0]
        agent_id = proc["agent_id"] or ctx.agent_id
        briefing = teaching.briefing(proc)
        if args.get("inputs"):
            briefing += "\n\nValues for this run:\n" + "\n".join(
                f"- {k}: {v}" for k, v in (args["inputs"] or {}).items())
        teaching.mark_run(proc["id"], "started")
        if agent_id and agent_id != ctx.agent_id and st.agents.get(agent_id):
            return await st.runtime.delegate(ctx, agent_id, briefing, title=f"Procedure: {proc['name']}")
        return {"procedure": proc["name"], "briefing": briefing,
                "note": "No specialist is attached — carry out these steps yourself."}

    reg.register(ToolSpec("teach.start", "Start recording a demonstration: everything the user says and "
                          "every tool that runs is captured so it can be turned into a repeatable procedure.",
                          _obj({"title": _s("what is being demonstrated"),
                                "goal": _s("what a successful run achieves")}, ["title"]),
                          category="teaching", risk="low", handler=teach_start))
    reg.register(ToolSpec("teach.note", "Add a note to the running recording — why a step happens, or a rule "
                          "to remember.", _obj({"text": _s("the note")}, ["text"]),
                          category="teaching", risk="low", handler=teach_note))
    reg.register(ToolSpec("teach.stop", "Stop the running recording.", _obj({}),
                          category="teaching", risk="low", handler=teach_stop))
    reg.register(ToolSpec("teach.learn", "Turn a recording into a named procedure, and optionally into a "
                          "specialist agent that can run it from then on.",
                          _obj({"recording_id": _s("defaults to the most recent recording"),
                                "create_agent": _b("also create a specialist, default true")}),
                          category="teaching", risk="medium", handler=teach_learn, timeout_seconds=300))
    reg.register(ToolSpec("procedure.list", "List everything JARVIS has learned so far.", _obj({}),
                          category="teaching", risk="low", min_role="viewer", handler=procedure_list))
    reg.register(ToolSpec("procedure.run", "Run a learned procedure, handing it to its specialist if it has one.",
                          _obj({"procedure_id": _s("id or name"),
                                "inputs": {"type": "object", "description": "values for its placeholders"}},
                               ["procedure_id"]),
                          category="teaching", risk="medium", handler=procedure_run, timeout_seconds=900))

    # ── web search ───────────────────────────────────────────────────────
    async def web_search_tool(ctx: ToolContext, args: dict):
        from ..services.external import web_search
        results = await web_search(str(args["query"]), int(args.get("limit", 8)), str(args.get("region", "")))
        return results or "no results"

    reg.register(ToolSpec("web.search", "Search the web and return titles, URLs and snippets. "
                          "Use web.fetch afterwards to read a promising page in full.",
                          _obj({"query": _s("what to search for"), "limit": _i("max results, default 8"),
                                "region": _s("optional region code, e.g. de-de")}, ["query"]),
                          category="web", risk="low", min_role="viewer", handler=web_search_tool,
                          timeout_seconds=45))
