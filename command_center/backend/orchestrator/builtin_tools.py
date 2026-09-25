"""
Built-in tools. Each one is real: it touches the workspace, the host, the
docker socket, the task system or a configured integration. Tools whose
backing integration is missing are registered *unavailable* with a reason,
so the registry (and the model) know they exist but cannot be used yet.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..ai.base import trim
from ..db import new_id, now_iso
from ..services.composio import DASHBOARD as COMPOSIO_DASHBOARD
from ..services.files import WorkspaceError
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

    reg.register(ToolSpec("terminal.execute", "Run a shell command inside the Command Center container's workspace. "
                          "Every call requires user approval. Do not use this to read /root/Mark-LIII paths; "
                          "use filesystem.read, which maps those host paths to the read-only Mark-LIII workspace.",
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
    reg.register(ToolSpec("filesystem.read", "Read a text file from the workspace. The host project "
                          "/root/Mark-LIII is mounted read-only and may be addressed either by its full host "
                          "path or as Mark-LIII/... . Always use this tool for those project files.",
                          _obj({"path": _s("workspace-relative path or /root/Mark-LIII/... host path"),
                                "max_bytes": _i("cap, default 200000")}, ["path"]),
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
        wanted = st.runtime._runnable_agent(str(args.get("agent") or "")) if args.get("agent") else ""
        args = {**args, "agent": wanted}
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

    # ── Modellwahl ───────────────────────────────────────────────────────
    async def think_deeper(ctx: ToolContext, args: dict):
        run = st.runtime.get_run(ctx.run_id or "") if st.runtime else None
        if run is None or st.runtime.fast_provider is None:
            return {"ok": True, "note": "Du läufst bereits auf dem stärksten Modell."}
        if st.runtime._is_local(st.runtime.provider_for(run)):
            # Two local models share one CPU and one memory budget: switching means loading the larger one cold
            # and dropping the resident one. Stay on the current model.
            return {"ok": True, "note": "Auf diesem Server gibt es kein stärkeres Modell, das schnell genug wäre. "
                                        "Du bleibst auf dem aktuellen Modell. Mach mit der Aufgabe weiter."}
        run.deep = True
        ctx.emit("model", {"text": "Wechsle auf das stärkere Modell: " + str(args.get("reason", ""))[:120]})
        return {"ok": True, "note": "Ab jetzt antwortet das stärkere Modell. Mach mit der Aufgabe weiter."}

    reg.register(ToolSpec("think.deeper", "Wechselt für den Rest dieser Aufgabe auf das stärkere, langsamere Modell "
                          "(Claude Sonnet 5). Nur bei kniffligen Aufgaben, nicht bei Routine.",
                          _obj({"reason": _s("kurz: warum die Aufgabe knifflig ist")}),
                          category="meta", risk="low", min_role="viewer", handler=think_deeper))

    async def tools_load(ctx: ToolContext, args: dict):
        from .runtime import _VOICE_GROUPS, VOICE_GROUP_NAMES
        run = st.runtime.get_run(ctx.run_id or "") if st.runtime else None
        want = {str(g).strip().lower() for g in (args.get("groups") or [])}
        ok = sorted(g for g in want if g in _VOICE_GROUPS)
        if run is not None:
            run.loaded |= set(ok)
        return {"loaded": ok, "unknown": sorted(want - set(ok)), "available_groups": VOICE_GROUP_NAMES,
                "note": "Die Werkzeuge dieser Gruppen stehen ab dem nächsten Schritt bereit."}

    reg.register(ToolSpec("tools.load", "Lädt weitere Werkzeuggruppen nach (Sprachmodus zeigt zuerst nur die häufigsten). "
                          "Gruppen: calendar, email, web, server, files, desktop, code, automation, learning, whatsapp, agents.",
                          _obj({"groups": {"type": "array", "items": {"type": "string"}, "description": "Gruppennamen"}},
                               ["groups"]),
                          category="meta", risk="low", min_role="viewer", handler=tools_load))

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
            severity=str(args.get("severity", "info")), meta={"push": True},        # ein Agent meldet nur, was der Nutzer wissen soll: aufs Handy
            user_id=ctx.principal.id if ctx.principal.kind == "user" else "*")
        return {"notification_id": n["id"]}

    reg.register(ToolSpec("notify.user", "Send a notification to the notification center.",
                          _obj({"title": _s("headline"), "body": _s("details"),
                                "severity": _s("info|success|warning|error"), "category": _s("task|agent|server|system")},
                               ["title"]), category="communication", risk="low", handler=notify_user))

    async def notify_call(ctx: ToolContext, args: dict):
        from ..services import phone
        text = str(args["message"])
        st.services["notifications"].notify(category="agent", title="Anruf: " + text[:60], body=text, severity="critical",
                                            meta={"push": True, "call": False},
                                            user_id=ctx.principal.id if ctx.principal.kind == "user" else "*")
        r = await phone.call(text, force=bool(args.get("sofort")))
        return (r["hinweis"], bool(r["angerufen"]))

    reg.register(ToolSpec("notify.call", "Call the user on their own phone and read a message aloud. ONLY for genuinely urgent "
                          "cases that cannot wait: a site emergency, a deadline about to be missed, a critical failure. "
                          "Everything else goes through notify.user. Calls only the user's saved number; there is a cool-down "
                          "so it never rings repeatedly. Tells you if calling is not set up.",
                          _obj({"message": _s("what to say, in German, two short sentences, first person, no markdown"),
                                "sofort": {"type": "boolean", "description": "ignore the cool-down (only if the last call was about something else)"}},
                               ["message"]), category="communication", risk="medium", handler=notify_call, timeout_seconds=40))

    async def memory_remember(ctx: ToolContext, args: dict):
        row_id = new_id("mem")
        st.db.insert("memory", {"id": row_id, "text": str(args["text"])[:4000], "actor": ctx.principal.actor,
                                "conversation_id": ctx.conversation_id, "created_at": now_iso()})
        try:
            # Sofort einen Bedeutungs-Vektor anlegen. Klappt das nicht, holt die Suche es später nach.
            from ..ai import embeddings as _emb
            await _emb.index_one(st.db, row_id, str(args["text"])[:4000])
        except Exception:  # noqa: BLE001
            pass
        if args.get("core"):
            # Das Hauptgedächtnis ist gedeckelt: Was hier hineinkommt, wird bei
            # jeder Anfrage mitgeschickt. Ist kein Platz, wird das gesagt statt
            # still einen anderen Satz zu verdrängen.
            from ..modules.memory import MAX_PINNED
            have = st.db.scalar("SELECT COUNT(*) FROM memory WHERE pinned=1") or 0
            if have >= MAX_PINNED:
                return (f"Gemerkt — aber nicht im Hauptgedächtnis: dort sind alle {MAX_PINNED} Plätze belegt. "
                        "Der Nutzer kann im Dashboard unter Gedächtnis einen herausnehmen.")
            st.db.execute("UPDATE memory SET pinned=1 WHERE id=?", (row_id,))
            return "remembered (im Hauptgedächtnis)"
        return "remembered"

    async def memory_search(ctx: ToolContext, args: dict):
        limit = int(args.get("limit", 10))
        try:
            from ..ai import embeddings as _emb
            semantic = await _emb.search_all(st.db, str(args["query"]), limit=limit, min_score=0.25, include_pinned=True)
        except Exception:  # noqa: BLE001
            semantic = None
        rows = [{"source": h["source"], "text": h["text"], "created_at": h["created_at"]} for h in (semantic or [])]
        seen = {r["text"] for r in rows}
        for r in st.db.fetchall("SELECT text, created_at FROM memory WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?",
                                (f"%{args['query']}%", limit)):
            if r["text"] not in seen and len(rows) < limit:
                rows.append({"source": "Gedächtnis", **r})
        return rows or "nothing stored matches"

    reg.register(ToolSpec("memory.remember",
                          "Store a fact or preference for later. Set core=true only for things that must "
                          "hold in every single conversation — those are put in front of you every time, "
                          "and there is room for a handful, not a hundred.",
                          _obj({"text": _s("fact"), "core": {"type": "boolean",
                                "description": "put it in the main memory, present in every conversation"}},
                               ["text"]),
                          category="memory", risk="low", handler=memory_remember))
    async def memory_forget(ctx: ToolContext, args: dict):
        q = str(args["query"])
        rows = st.db.fetchall("SELECT id, text FROM memory WHERE text LIKE ? LIMIT 5", (f"%{q}%",))
        if not rows:
            return f"Nichts Gemerktes passt auf '{q}'.", False
        if len(rows) > 1:
            return ("Mehrere Einträge passen — welcher? "
                    + " | ".join(r["text"][:80] for r in rows)), False
        st.db.execute("DELETE FROM memory WHERE id=?", (rows[0]["id"],))
        return f"Vergessen: {rows[0]['text'][:120]}"

    reg.register(ToolSpec("memory.forget", "Delete one remembered fact. Only deletes when exactly one "
                          "entry matches, so nothing disappears by accident.",
                          _obj({"query": _s("text of the fact to forget")}, ["query"]),
                          category="memory", risk="medium", handler=memory_forget))
    reg.register(ToolSpec("memory.search", "Search the one memory — remembered facts, the main memory (Hauptgedächtnis), "
                          "standing instructions and company knowledge — by meaning and by keyword. Each hit says "
                          "where it comes from. Ask in your own words: 'who cannot drive' finds 'has no driving licence'.",
                          _obj({"query": _s("what you are looking for, in plain words"), "limit": _i("")}, ["query"]),
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
            notes=str(args.get("notes", "")), category=str(args.get("category", "")).strip().lower())
        ctx.emit("calendar", {"text": f"Appointment booked ({backend}): {event['title']} {event['start']}"})
        return {"booked": True, "backend": backend, "start": event["start"], "end": event["end"],
                "title": event["title"], "category": event.get("category", ""), "location": event.get("location", ""),
                "notes": event.get("notes", ""), "note": note}

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
                                "location": _s("where"), "notes": _s("extra notes"),
                                "category": _s("optional: tour | ich | privat. Leave EMPTY: Jarvis recognises what it is "
                                               "(Tour für das Team, Termin des Masters, privat), the place, the team and "
                                               "the vehicle from the text and memory")}, ["title", "when"]),
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
        acc = str(args.get("account", "")).strip()
        if str(args.get("unread", "")).lower() in ("1", "true", "yes"):
            items = await mail.unread(limit, account=acc)
        elif q:
            items = await mail.search(q, limit, account=acc)
        else:
            items = await mail.recent(limit, account=acc)
        return items or "no matching mail"

    async def email_read(ctx: ToolContext, args: dict):
        return await mail.read(str(args["query"]), account=str(args.get("account", "")).strip())

    async def email_draft(ctx: ToolContext, args: dict):
        acc = str(args.get("account", "")).strip()
        msg = mail.build(str(args["to"]), str(args.get("subject", "")), str(args.get("body", "")),
                         str(args.get("cc", "")), account=acc)
        return {"drafted": True, "from": msg["From"], "to": msg["To"], "subject": msg["Subject"],
                "body": str(args.get("body", "")),
                "note": "Nothing was sent. Read the draft to the user and use email.send only once they agree."}

    async def email_send(ctx: ToolContext, args: dict):
        result = await mail.send(str(args["to"]), str(args.get("subject", "")), str(args.get("body", "")),
                                 str(args.get("cc", "")), account=str(args.get("account", "")).strip())
        ctx.emit("email", {"text": f"Mail sent from {result['from']} to {result['to']}"})
        return result

    STYLE = ("SCHREIBSTIL, immer einhalten: Schreibe wie ein echter Handwerksbetrieb-Inhaber, nicht wie eine KI. "
             "Persönlich, direkt, ruhig und verbindlich. Kurze, normale Sätze, meist 3 bis 7 Sätze insgesamt. "
             "Fang mit der Sache an, nicht mit Floskeln (kein 'ich hoffe, Sie sind wohlauf', kein 'vielen Dank für Ihre "
             "Nachricht' als Standardsatz, kein 'gerne' oder 'selbstverständlich' in jedem Satz). "
             "Anrede und Sie/Du so wie der Empfänger schreibt; Kunden im Zweifel mit 'Guten Tag Frau/Herr Nachname,' und Sie. "
             "Kein Markdown, keine Aufzählungen, kein Fettdruck, keine Emojis, keine Gedankenstriche als Stilmittel, keine "
             "Ausrufezeichen-Ketten. Vermeide typische KI-Wörter und -Wendungen (nahtlos, maßgeschneidert, ganzheitlich, "
             "Expertise, im Folgenden, zusammenfassend, ich freue mich darauf). Abwechslungsreich formulieren, nicht jede "
             "Mail gleich aufbauen. Am Ende ein klarer nächster Schritt (Termin, Rückmeldung, Unterlage). Nichts erfinden: "
             "keine Preise, Termine, Zusagen oder Normen, die nicht belegt sind; lieber nachfragen. Interne Sätze, Margen "
             "und Stundensätze nie nennen. Grußformel: 'Viele Grüße' bzw. 'Freundliche Grüße', darunter 'Paul'.")
    ACC = ("Postfach: buero, privat oder rechnungen. Beim Antworten IMMER das Postfach, in dem die Mail angekommen "
           "ist (Feld 'account' der Suchergebnisse). Bei Suchen ohne Angabe werden alle durchsucht.")

    reg.register(ToolSpec("email.search", "Search the mailbox, or list the newest / unread mail.",
                          _obj({"query": _s("text in subject, sender or body"), "limit": _i("max results"),
                                "unread": _s("'true' for unread only"), "account": _s(ACC)}),
                          category="communication", risk="low", handler=email_search,
                          available=mail_ok, reason=mail_reason, timeout_seconds=60))
    reg.register(ToolSpec("email.read", "Read one mail in full, including its body.",
                          _obj({"query": _s("text identifying the mail"), "account": _s(ACC)}, ["query"]),
                          category="communication", risk="low", handler=email_read,
                          available=mail_ok, reason=mail_reason, timeout_seconds=60))
    reg.register(ToolSpec("email.draft", "Compose a mail without sending it. Always draft before sending. " + STYLE,
                          _obj({"to": _s("recipient address"), "subject": _s("subject"), "body": _s("full text"),
                                "cc": _s("optional cc"), "account": _s(ACC)}, ["to", "body"]),
                          category="communication", risk="low", handler=email_draft,
                          available=mail_ok, reason=mail_reason))
    reg.register(ToolSpec("email.send", "Send a mail. Irreversible, so it always requires the user's approval. " + STYLE,
                          _obj({"to": _s("recipient address"), "subject": _s("subject"), "body": _s("full text"),
                                "cc": _s("optional cc"), "reason": _s("why this mail is being sent"), "account": _s(ACC)},
                               ["to", "body", "reason"]),
                          category="communication", risk="high", handler=email_send,
                          available=mail_ok, reason=mail_reason, timeout_seconds=90))

    # ── Lexware / Buchhaltung ──
    from .lexware_tools import register_lexware_tools
    register_lexware_tools(reg, st, files)

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

    # ── composio (one account, a few hundred services) ───────────────────
    # Deliberately three tools rather than one per Composio tool: the catalogue
    # runs to thousands, and handing a model thousands of declarations buys
    # confusion, not capability. It discovers, then acts.
    composio = st.services["composio"]
    cmp_ok = composio.configured()
    cmp_reason = composio.unavailable_reason()

    async def composio_apps(ctx: ToolContext, args: dict):
        conns = await composio.connections()
        if not conns:
            return (f"Composio has no app connected for user '{composio.user_id}' yet. Connect one "
                    f"at {COMPOSIO_DASHBOARD} — the OAuth happens there, not here.", False)
        return [{"app": c["toolkit"], "usable": c["status"] == "ACTIVE" and not c["disabled"],
                 "status": c["status"], "connected_at": c["created_at"]} for c in conns]

    async def composio_tools(ctx: ToolContext, args: dict):
        found = await composio.tools(toolkit=str(args.get("app", "")), search=str(args.get("query", "")),
                                     limit=int(args.get("limit", 20)))
        if not found:
            return "Composio has no tool matching that. Try a broader query, or composio.apps first.", False
        live = await composio.live_toolkits()
        return [{"tool": t["slug"], "app": t["toolkit"], "what_it_does": t["description"],
                 "connected": (not t["needs_connection"]) or t["toolkit"] in live,
                 "arguments": t["input_parameters"]} for t in found]

    async def composio_run(ctx: ToolContext, args: dict):
        params = args.get("arguments") or {}
        if not isinstance(params, dict):
            return "arguments must be a JSON object", False
        result = await composio.execute(str(args["tool"]), arguments=params,
                                        text=str(args.get("instruction", "")))
        ctx.emit("composio", {"text": f"{result['toolkit']}: {result['tool']}"})
        return result

    reg.register(ToolSpec("composio.apps", "Which third-party services are connected through Composio and "
                          "usable right now — Gmail, Slack, Notion, Linear and so on.", _obj({}),
                          category="integrations", risk="low", min_role="viewer", handler=composio_apps,
                          available=cmp_ok, reason=cmp_reason))
    reg.register(ToolSpec("composio.tools", "Find a Composio tool to do something in a connected service. "
                          "Returns each tool's slug and the arguments it takes, so composio.run can use it.",
                          _obj({"query": _s("what you want to do, e.g. 'send an email', 'create an issue'"),
                                "app": _s("limit to one service, e.g. gmail, slack, notion"),
                                "limit": _i("max rows, default 20")}),
                          category="integrations", risk="low", min_role="viewer", handler=composio_tools,
                          available=cmp_ok, reason=cmp_reason, timeout_seconds=60))
    reg.register(ToolSpec("composio.run", "Execute one Composio tool in a connected service. This acts in the "
                          "user's real accounts — it sends, posts, creates and deletes for real. Find the "
                          "slug and its arguments with composio.tools first.",
                          _obj({"tool": _s("tool slug from composio.tools, e.g. GMAIL_SEND_EMAIL"),
                                "arguments": {"type": "object", "description": "arguments for that tool"},
                                "instruction": _s("plain-language alternative to arguments; not both"),
                                "reason": _s("why this is needed")}, ["tool", "reason"]),
                          category="integrations", risk="high", handler=composio_run,
                          available=cmp_ok, reason=cmp_reason, timeout_seconds=120))

    # ── sich selbst erweitern ────────────────────────────────────────────
    # Ausdrückliche Anweisung des Nutzers: er darf sich selbst programmieren,
    # muss aber JEDES MAL um Genehmigung fragen. Also ist hier alles, was
    # etwas verändert, auf `high` mit requires_approval — schreiben,
    # vorschlagen, freigeben, abschalten, zurückdrehen. Nur Lesen und Prüfen
    # laufen frei durch: sie ändern nichts, und ein Gatter davor wäre bloß
    # Lärm, der die echten Freigaben entwertet.
    selfext = st.services["selfext"]
    evolution = st.services["core_evolution"]

    async def self_tools(ctx: ToolContext, args: dict):
        items = selfext.list()
        if not items:
            return ("Du hast dir noch kein Werkzeug geschrieben. self.write legt eines an, "
                    "self.check prüft es, self.activate gibt es frei.")
        return items

    async def self_write(ctx: ToolContext, args: dict):
        return selfext.write(str(args["name"]), str(args["source"]),
                             author=ctx.principal.actor,
                             existing_tools={x.name for x in st.tools.all()})

    async def self_read(ctx: ToolContext, args: dict):
        return selfext.read(str(args["name"]))

    async def self_check(ctx: ToolContext, args: dict):
        return await selfext.check(str(args["name"]))

    async def self_activate(ctx: ToolContext, args: dict):
        res = selfext.activate(str(args["name"]), st.tools, actor=ctx.principal.actor)
        st.tools.snapshot(st.db)
        ctx.emit("selfext", {"text": f"Neues Werkzeug freigegeben: {args['name']}"})
        return res

    async def self_disable(ctx: ToolContext, args: dict):
        return selfext.disable(str(args["name"]), st.tools, actor=ctx.principal.actor)

    # ── seinen eigenen Quelltext ─────────────────────────────────────────
    async def self_tree(ctx: ToolContext, args: dict):
        return selfext.source_tree(str(args.get("tree", "")))

    async def self_source(ctx: ToolContext, args: dict):
        return selfext.source(str(args["file"]))

    async def self_propose(ctx: ToolContext, args: dict):
        gain = str(args.get("capability_gain", "")).strip()
        evidence = str(args.get("evidence", "")).strip()
        verify = str(args.get("verification_plan", "")).strip()
        if not gain or not evidence or not verify:
            return "Core-Vorschlag abgelehnt: Fähigkeitsgewinn, Nachweis und Verifikationsplan sind Pflicht.", False
        reason = (f"{args['reason']}\n\nCAPABILITY_GAIN: {gain}\nEVIDENCE: {evidence}\n"
                  f"VERIFICATION_PLAN: {verify}")
        return selfext.propose(str(args["file"]), str(args["source"]), reason,
                               author=ctx.principal.actor)

    async def self_proposals(ctx: ToolContext, args: dict):
        items = selfext.proposals()
        return items or "Es liegt kein Änderungsvorschlag vor."

    async def self_verify(ctx: ToolContext, args: dict):
        proposal_id = str(args["proposal_id"])
        first = await selfext.verify_proposal(
            proposal_id,
            capability_gain=str(args.get("capability_gain", "")),
            verification_plan=str(args.get("verification_plan", "")))
        if not first.get("eligible_for_approval"):
            return first
        # self.apply gates on evolution.ensure_verified(), which needs the candidate
        # hash, activation mode and stage that only this second stage records — and
        # it adds the baseline comparison (public API, size benchmark).
        second = await evolution.verify(proposal_id)
        known = {c.get("name") for c in first.get("checks", [])}
        return {**first, "status": second["status"],
                "checks": [*first.get("checks", []), *[c for c in second["checks"] if c.get("name") not in known]],
                "activation_mode": second["activation_mode"],
                "eligible_for_approval": second["eligible_for_apply"]}

    async def self_apply(ctx: ToolContext, args: dict):
        verified = evolution.ensure_verified(str(args["proposal_id"]))
        res = selfext.apply(str(args["proposal_id"]), actor=ctx.principal.actor)
        evolution.arm_after_apply(res, verified)
        learning.record(kind="self", title=f"Core-Änderung vorbereitet: {args['proposal_id']}",
                        lesson=str(args.get("reason", "")),
                        verification="Sandbox-Verifikation bestanden: " + ", ".join(
                            c.get("name", "") for c in verified.get("checks", []) if c.get("ok")),
                        source="core-evolution", conversation_id=ctx.conversation_id, confidence=1.0,
                        tags=["core-evolution", "verified", "approved"], actor=ctx.principal.actor)
        return {**res, "verified": True, "activation_mode": verified["activation_mode"],
                "next": "Aktivierung braucht self.rebuild (Frontend) oder self.restart (Backend)."}

    async def self_revert(ctx: ToolContext, args: dict):
        return selfext.revert(str(args["proposal_id"]), actor=ctx.principal.actor)

    reg.register(ToolSpec("self.tools", "Which tools you have written for yourself, and their state.",
                          _obj({}), category="self", risk="low", min_role="viewer", handler=self_tools))
    reg.register(ToolSpec("self.write",
                          "Write a new tool for yourself, or replace one you wrote. The file must define "
                          "TOOL = {name, description, input_schema, risk} and an async run(args). An "
                          "optional selftest() is run during self.check. Writing changes nothing yet.",
                          _obj({"name": _s("area.action, lower case, e.g. wetter.heute"),
                                "source": _s("the complete Python file"),
                                "reason": _s("what this tool is for")}, ["name", "source", "reason"]),
                          category="self", risk="high", requires_approval=True, handler=self_write))
    reg.register(ToolSpec("self.read", "Read back the source of a tool you wrote.",
                          _obj({"name": _s("tool name")}, ["name"]),
                          category="self", risk="low", min_role="viewer", handler=self_read))
    reg.register(ToolSpec("self.check",
                          "Import a tool you wrote in a separate process, validate its shape and run its "
                          "selftest. It must pass before it can be activated.",
                          _obj({"name": _s("tool name")}, ["name"]),
                          category="self", risk="low", handler=self_check, timeout_seconds=40))
    reg.register(ToolSpec("self.activate",
                          "Load a checked tool into the live registry, permanently. It then runs inside "
                          "this server with the same rights the server has.",
                          _obj({"name": _s("tool name"),
                                "reason": _s("what it is for and why it is safe")}, ["name", "reason"]),
                          category="self", risk="critical", requires_approval=True, min_role="admin", handler=self_activate))
    reg.register(ToolSpec("self.disable", "Switch off a tool you wrote.",
                          _obj({"name": _s("tool name"), "reason": _s("why")}, ["name", "reason"]),
                          category="self", risk="high", requires_approval=True, handler=self_disable))

    reg.register(ToolSpec("self.tree", "List the files of your own source code.",
                          _obj({"tree": _s("limit to one tree, e.g. command_center/backend")}),
                          category="self", risk="low", min_role="viewer", handler=self_tree))
    reg.register(ToolSpec("self.source", "Read one file of your own source code.",
                          _obj({"file": _s("path as self.tree prints it")}, ["file"]),
                          category="self", risk="low", min_role="viewer", handler=self_source))
    reg.register(ToolSpec("self.propose",
                          "Propose a changed version of one of your own source files. This writes a "
                          "proposal with a diff and changes nothing that is running.",
                          _obj({"file": _s("path as self.tree prints it"),
                                "source": _s("the complete new file"),
                                "reason": _s("what should change"),
                                "capability_gain": _s("specific new or measurably improved capability"),
                                "evidence": _s("evidence showing the limitation exists and this change addresses it"),
                                "verification_plan": _s("sandbox/tests/comparison that must pass before activation")},
                               ["file", "source", "reason", "capability_gain", "evidence", "verification_plan"]),
                          category="self", risk="medium", handler=self_propose))
    reg.register(ToolSpec("self.proposals", "The change proposals you have made to your own code.",
                          _obj({}), category="self", risk="low", min_role="viewer", handler=self_proposals))
    reg.register(ToolSpec("self.verify",
                          "Verify a proposed core/source change in an isolated server-side check before approval. "
                          "The report is machine-generated and is required before self.apply can run.",
                          _obj({"proposal_id": _s("id from self.propose"),
                                "capability_gain": _s("specific capability or measurable improvement expected"),
                                "verification_plan": _s("what should be proven by the checks")},
                               ["proposal_id","capability_gain","verification_plan"]),
                          category="self", risk="low", min_role="admin", handler=self_verify, timeout_seconds=600))
    reg.register(ToolSpec("self.apply",
                          "Write a proposal into your own source tree. The old file is backed up first. "
                          "It takes effect only after the server restarts.",
                          _obj({"proposal_id": _s("id from self.propose"),
                                "reason": _s("why this verified change should be activated")},
                               ["proposal_id", "reason"]),
                          category="self", risk="critical", requires_approval=True, min_role="admin", handler=self_apply))
    reg.register(ToolSpec("self.revert", "Undo an applied proposal from its backup.",
                          _obj({"proposal_id": _s("id from self.propose"),
                                "reason": _s("why")}, ["proposal_id", "reason"]),
                          category="self", risk="high", requires_approval=True, min_role="admin",
                          handler=self_revert))

    async def self_rebuild(ctx: ToolContext, args: dict):
        res = await selfext.rebuild_frontend(actor=ctx.principal.actor)
        ctx.emit("selfext", {"text": "Dashboard neu gebaut"})
        return res

    async def self_restart(ctx: ToolContext, args: dict):
        pending = evolution.begin_activation()
        res = selfext.restart_server(actor=ctx.principal.actor)
        return {**res, "core_evolution": pending}

    async def self_can_rebuild(ctx: ToolContext, args: dict):
        ok, why = selfext.can_rebuild()
        return {"can_rebuild_dashboard": ok, "reason": why,
                "note": ("Änderungen am Backend brauchen self.restart, Änderungen am Dashboard "
                         "self.rebuild. self.tree sagt bei jeder Datei, welches von beiden.")}

    reg.register(ToolSpec("self.can_rebuild",
                          "Whether this server can rebuild its own dashboard, and if not, why not.",
                          _obj({}), category="self", risk="low", min_role="viewer",
                          handler=self_can_rebuild))
    reg.register(ToolSpec("self.rebuild",
                          "Rebuild the dashboard from the current sources so a change you made to it "
                          "actually takes effect. Type-checks first and refuses to build if that fails; "
                          "the running dashboard is only swapped once the new one is complete.",
                          _obj({"reason": _s("what was changed and why it should go live")}, ["reason"]),
                          category="self", risk="critical", requires_approval=True, min_role="admin",
                          handler=self_rebuild, timeout_seconds=900))
    reg.register(ToolSpec("self.restart",
                          "Restart the server so a change you made to your own backend code takes "
                          "effect. The container brings it back within about ten seconds.",
                          _obj({"reason": _s("what was changed and why it should go live")}, ["reason"]),
                          category="self", risk="critical", requires_approval=True, min_role="admin",
                          handler=self_restart))

    # ── besser werden ────────────────────────────────────────────────────
    # Er konnte sich schon Werkzeuge schreiben; was fehlte, war der Anlass.
    # Diese beiden schauen in die Prüfspur statt in die Fantasie.
    improve = st.services["improve"]

    async def self_review(ctx: ToolContext, args: dict):
        hours = int(args.get("hours", 24))
        ev = improve.evidence(hours)
        if not improve.worth_reflecting(ev):
            return (f"In den letzten {hours} Stunden ({ev['audit_rows_seen']} Einträge in der "
                    f"Prüfspur) gibt es kein wiederkehrendes Problem. Nichts zu verbessern.")
        return ev

    async def self_improve(ctx: ToolContext, args: dict):
        proposal = await improve.reflect(st, int(args.get("hours", 24)))
        recorded = improve.record(proposal, actor=ctx.principal.actor)
        if not recorded.get("recorded"):
            return recorded.get("reason") or "Nichts, was die Prüfspur hergibt."
        ctx.emit("improve", {"text": f"Verbesserungsvorschlag: {recorded['title']}"})
        st.services["notifications"].notify(
            category="agent", severity="info", title=f"Verbesserung: {recorded['title']}",
            body=recorded["body"][:600], link="/logs")
        return {"kind": recorded["kind"], "title": recorded["title"], "detail": recorded["body"],
                "next": ("Wenn es ein Werkzeug ist: self.write, self.check, dann self.activate. "
                         "Wenn es eine Quelldatei ist: self.source lesen, self.propose, dann "
                         "self.apply. Beides braucht deine Freigabe.")}

    reg.register(ToolSpec("self.review",
                          "Look at your own audit trail: which tool calls failed, which tools were "
                          "wanted but do not exist, which are only missing credentials. Evidence, not "
                          "opinion — it returns nothing when nothing recurred.",
                          _obj({"hours": _i("how far back, default 24")}),
                          category="self", risk="low", min_role="viewer", handler=self_review))
    reg.register(ToolSpec("self.improve",
                          "Review your own last day and propose exactly one concrete improvement, built "
                          "only on what the audit trail actually shows. Proposing changes nothing — "
                          "building it is self.write / self.propose, and that still needs approval.",
                          _obj({"hours": _i("how far back, default 24")}),
                          category="self", risk="low", handler=self_improve, timeout_seconds=120))

    # ── MIA experience memory ────────────────────────────────────────────
    learning = st.services["learning"]

    async def learning_search(ctx: ToolContext, args: dict):
        raw = str(args.get("kinds", ""))
        kinds = [x.strip() for x in raw.split(",") if x.strip()]
        return learning.search(str(args.get("query", "")), kinds=kinds or None,
                               limit=int(args.get("limit", 6)))

    async def learning_solution(ctx: ToolContext, args: dict):
        failed = args.get("failed_attempts") or []
        tags = args.get("tags") or []
        return learning.record(
            kind="solution", title=str(args["title"]), problem=str(args.get("problem", "")),
            lesson=str(args["solution"]),
            failed_attempts=failed if isinstance(failed, list) else [str(failed)],
            verification=str(args.get("verification", "")), source="mia",
            conversation_id=ctx.conversation_id, confidence=float(args.get("confidence", 1.0)),
            tags=tags if isinstance(tags, list) else [str(tags)], actor=ctx.principal.actor)

    async def learning_error(ctx: ToolContext, args: dict):
        tags = args.get("tags") or []
        attempt = str(args.get("attempt", "")).strip()
        return learning.record(
            kind="error", title=str(args["title"]), problem=str(args.get("problem", "")),
            lesson=str(args.get("why_it_failed", "")),
            failed_attempts=[attempt] if attempt else [],
            verification=str(args.get("verification", "")), source="mia",
            conversation_id=ctx.conversation_id, confidence=float(args.get("confidence", 0.9)),
            tags=tags if isinstance(tags, list) else [str(tags)], actor=ctx.principal.actor)

    async def learning_preference(ctx: ToolContext, args: dict):
        explicit = bool(args.get("explicit", False))
        return learning.record(
            kind="preference", title=str(args["title"]), problem=str(args.get("scope", "")),
            lesson=str(args["preference"]), verification=str(args.get("evidence", "")),
            source="user-explicit" if explicit else "user-repeated",
            conversation_id=ctx.conversation_id, confidence=1.0 if explicit else 0.8,
            tags=["user-preference"], actor=ctx.principal.actor)

    async def learning_gap(ctx: ToolContext, args: dict):
        return learning.record(
            kind="gap", title=str(args["topic"]), problem=str(args.get("missing", "")),
            lesson=str(args.get("why", "")), verification="not learned yet",
            source="mia-detected", conversation_id=ctx.conversation_id,
            confidence=float(args.get("confidence", 0.8)), tags=["knowledge-gap"],
            actor=ctx.principal.actor)

    reg.register(ToolSpec(
        "learning.search",
        "Search MIA's experience memory for solved problems, failed attempts, strategies, preferences and lessons. "
        "Use this before retrying a familiar problem.",
        _obj({"query": _s("problem or topic to recall"), "kinds": _s("optional comma-separated kinds"),
              "limit": _i("max results, default 6")}, ["query"]),
        category="learning", risk="low", min_role="viewer", handler=learning_search))
    reg.register(ToolSpec(
        "learning.record_solution",
        "Store a reusable solution after a non-trivial problem was actually solved. Include failed attempts and "
        "verification so the same problem is not solved from scratch next time.",
        _obj({"title": _s("short reusable name"), "problem": _s("symptoms/problem"),
              "solution": _s("successful steps and why they worked"),
              "failed_attempts": {"type": "array", "items": {"type": "string"}},
              "verification": _s("how success was confirmed"),
              "confidence": {"type": "number"},
              "tags": {"type": "array", "items": {"type": "string"}}},
             ["title", "solution"]),
        category="learning", risk="low", handler=learning_solution))
    reg.register(ToolSpec(
        "learning.record_error",
        "Remember an important failed approach so MIA does not repeat it blindly in a similar case.",
        _obj({"title": _s("short failure name"), "problem": _s("problem being solved"),
              "attempt": _s("what was tried"), "why_it_failed": _s("known reason or observed failure"),
              "verification": _s("evidence that it failed"), "confidence": {"type": "number"},
              "tags": {"type": "array", "items": {"type": "string"}}},
             ["title", "attempt"]),
        category="learning", risk="low", handler=learning_error))
    reg.register(ToolSpec(
        "learning.record_preference",
        "Store a stable user preference only when the user explicitly stated it or repeated evidence makes it clear. "
        "Never turn a one-off request into a permanent preference.",
        _obj({"title": _s("preference name"), "preference": _s("what the user prefers"),
              "scope": _s("where it applies"), "evidence": _s("what established it"),
              "explicit": _b("true when the user stated it directly")},
             ["title", "preference", "evidence", "explicit"]),
        category="learning", risk="low", handler=learning_preference))
    reg.register(ToolSpec(
        "learning.note_gap",
        "Record a genuine knowledge gap MIA detected after checking existing memory and knowledge. "
        "This does not contact an external model.",
        _obj({"topic": _s("area that needs learning"), "missing": _s("what is not known yet"),
              "why": _s("why learning this would improve MIA"), "confidence": {"type": "number"}},
             ["topic", "missing", "why"]),
        category="learning", risk="low", handler=learning_gap))

    async def learning_teacher(ctx: ToolContext, args: dict):
        base = (os.environ.get("N8N_WEBHOOK_BASE_URL") or os.environ.get("N8N_BASE_URL") or "").rstrip("/")
        if not base:
            return "Claude teacher unavailable: n8n webhook base URL is not configured.", False
        token = os.environ.get("MIA_TEACHER_BRIDGE_TOKEN", "")
        if not token:
            return "Claude teacher unavailable: MIA_TEACHER_BRIDGE_TOKEN is not configured.", False
        header = os.environ.get("MIA_TEACHER_BRIDGE_HEADER", "X-Mia-Teacher-Bridge")
        url = base + "/webhook/mia-claude-teacher-7f2d0c1a83e34b63910f4bd8"
        payload = {
            "topic": str(args["topic"]),
            "question": str(args["question"]),
            "context": str(args.get("context", "")),
        }
        async with httpx.AsyncClient(timeout=float(args.get("timeout", 330))) as client:
            response = await client.post(url, json=payload, headers={header: token})
        if response.status_code in (401, 403):
            return "Claude teacher rejected the request: bridge token invalid (MIA_TEACHER_BRIDGE_TOKEN mismatch).", False
        if response.status_code >= 400:
            return f"Claude teacher failed ({response.status_code}): {response.text[:1200]}", False
        try:
            wire = response.json()
        except ValueError:
            return f"Claude teacher returned invalid JSON: {response.text[:1200]}", False
        if isinstance(wire, list):
            wire = wire[0] if wire else {}
        stdout = str(wire.get("stdout", "")) if isinstance(wire, dict) else str(wire)
        if isinstance(wire, dict) and int(wire.get("code") or 0) != 0:
            return f"Claude teacher SSH failed: {wire.get('stderr') or stdout}", False
        try:
            claude = json.loads(stdout)
        except ValueError:
            claude = {"result": stdout}
        lesson = str(claude.get("result") or claude.get("text") or stdout).strip()
        if not lesson:
            return "Claude teacher returned no lesson.", False
        record = learning.record(
            kind="teacher", title=f"Claude lesson: {args['topic']}",
            problem=str(args["question"]), lesson=lesson,
            verification="Read-only Claude Code teacher session. Verify before applying changes.",
            source="claude-code", conversation_id=ctx.conversation_id,
            confidence=0.75, tags=["claude-teacher", str(args["topic"])[:80]],
            actor=ctx.principal.actor)
        return {
            "lesson": lesson,
            "stored_as": record["id"],
            "model": claude.get("model"),
            "cost_usd": claude.get("total_cost_usd") or claude.get("cost_usd"),
            "note": "Lesson stored. MIA must verify it against the real system before applying it.",
        }

    reg.register(ToolSpec(
        "learning.teacher",
        "Ask the server's Claude Code to teach MIA about a genuine knowledge gap. Claude can inspect MIA source "
        "but runs in safe/restricted plan mode with only Read/Grep/Glob, so it cannot edit files or run commands. "
        "Use only after memory, existing knowledge, specialists and normal research were insufficient. This always "
        "pauses for explicit user approval before Claude is contacted, then stores the lesson for later reuse.",
        _obj({"topic": _s("learning area"), "question": _s("specific lesson requested"),
              "context": _s("relevant observations/errors/context"),
              "why": _s("why Claude is needed and what improvement is expected"),
              "timeout": _i("seconds, default 330")},
             ["topic", "question", "why"]),
        category="learning", risk="high", requires_approval=True,
        handler=learning_teacher, timeout_seconds=360))

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

    # ── tippen und klicken, egal in welchem Fenster ──────────────────────
    # Möglich war das schon über desktop.run mit action="computer_control"
    # und darin nochmal einem "action"-Feld. Zwei verschachtelte Aktionsnamen
    # sind genau die Stelle, an der ein Modell danebengreift und es niemand
    # merkt. Also drei eigene Werkzeuge mit klarem Schema — derselbe Weg,
    # dasselbe Genehmigungstor, nur ohne Ratespiel.
    async def _computer(ctx: ToolContext, args: dict, inner: dict, label: str):
        device = desktop.resolve(str(args.get("device", "")))
        if not any(a.get("name") == "computer_control" for a in device["actions"]):
            return (f"'{device['name']}' bietet keine Tastatur- und Maussteuerung an. "
                    f"Läuft dort die aktuelle Desktop-App?", False)
        cmd = await desktop.dispatch(device_id=device["id"], action="computer_control", params=inner,
                                     requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
                                     task_id=ctx.task_id, run_id=ctx.run_id,
                                     timeout=float(args.get("timeout", 60)))
        ctx.emit("desktop", {"text": f"{device['name']}: {label}"})
        return (cmd["result"] or cmd["error"] or "done", cmd["status"] == "done")

    async def desktop_type(ctx: ToolContext, args: dict):
        text = str(args["text"])
        return await _computer(ctx, args, {"action": "type", "text": text},
                               f"getippt ({len(text)} Zeichen)")

    async def desktop_press(ctx: ToolContext, args: dict):
        keys = str(args["keys"]).strip()
        inner = {"action": "hotkey", "keys": keys} if "+" in keys else {"action": "press", "key": keys}
        return await _computer(ctx, args, inner, f"Taste {keys}")

    async def desktop_click(ctx: ToolContext, args: dict):
        if args.get("description"):
            inner = {"action": "screen_click", "description": str(args["description"])}
            label = f"geklickt: {args['description']}"
        elif args.get("x") is not None and args.get("y") is not None:
            inner = {"action": "click", "x": int(args["x"]), "y": int(args["y"])}
            label = f"geklickt bei {args['x']},{args['y']}"
        else:
            return "Entweder 'description' oder 'x' und 'y' angeben.", False
        return await _computer(ctx, args, inner, label)

    reg.register(ToolSpec("desktop.type",
                          "Type text on the user's PC — into whatever window has the focus: a form, an "
                          "editor, a chat, a program that has no interface of its own. Bring the right "
                          "window to the front first (desktop.open_app), then type.",
                          _obj({"text": _s("the text to type"), "device": _s("which desktop"),
                                "reason": _s("why this is needed"), "timeout": _i("seconds, default 60")},
                               ["text", "reason"]),
                          category="desktop", risk="high" if gate_desktop else "medium",
                          requires_approval=gate_desktop, handler=desktop_type, timeout_seconds=120))
    reg.register(ToolSpec("desktop.press",
                          "Press a key or a combination on the user's PC, e.g. Enter, Escape, F5, ctrl+s, "
                          "alt+tab.",
                          _obj({"keys": _s("key or combination, e.g. Enter or ctrl+s"),
                                "device": _s("which desktop"), "reason": _s("why")},
                               ["keys", "reason"]),
                          category="desktop", risk="high" if gate_desktop else "medium",
                          requires_approval=gate_desktop, handler=desktop_press, timeout_seconds=60))
    reg.register(ToolSpec("desktop.click",
                          "Click on the user's screen — either at a coordinate, or by describing what to "
                          "click; the PC then looks for it on screen.",
                          _obj({"description": _s("what to click, e.g. 'the Save button'"),
                                "x": _i("x coordinate"), "y": _i("y coordinate"),
                                "device": _s("which desktop"), "reason": _s("why")}, ["reason"]),
                          category="desktop", risk="high" if gate_desktop else "medium",
                          requires_approval=gate_desktop, handler=desktop_click, timeout_seconds=90))

    # ── den Bildschirm des Nutzers sehen ─────────────────────────────────
    # Der PC schickt das Bild selbst, nicht einen Pfad. Hier wird es in den
    # Arbeitsbereich gelegt und dem Modell als Bild angehängt — sonst bliebe
    # es eine Zeichenkette, die es lesen, aber nicht ansehen kann.
    async def desktop_screen(ctx: ToolContext, args: dict):
        device = desktop.resolve(str(args.get("device", "")))
        if not any(a.get("name") == "screen_capture" for a in device["actions"]):
            return (f"'{device['name']}' kann noch keine Bildschirmfotos schicken. Auf dem PC "
                    f"einmal git pull und JARVIS.bat neu starten.", False)
        cmd = await desktop.dispatch(device_id=device["id"], action="screen_capture",
                                     params={"monitor": int(args.get("monitor", 0) or 0)},
                                     requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
                                     task_id=ctx.task_id, run_id=ctx.run_id, timeout=60)
        if cmd["status"] != "done":
            return cmd["error"] or "Der PC hat kein Bild geschickt.", False
        try:
            payload = json.loads(cmd["result"] or "{}")
        except ValueError:
            return f"Der PC antwortete nicht in JSON: {str(cmd['result'])[:200]}", False
        if payload.get("error"):
            return str(payload["error"]), False
        raw = payload.get("image_base64") or ""
        if not raw:
            return "Der PC schickte kein Bild.", False

        import base64 as _b64
        name = f"bildschirm-{device['name'].lower().replace(' ', '-')}.jpg"
        target = Path(files.root) / "downloads" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(_b64.b64decode(raw))
        except Exception as e:  # noqa: BLE001
            return f"Das Bild ließ sich nicht speichern: {e.__class__.__name__}", False

        ctx.attach(f"downloads/{name}")
        ctx.emit("desktop", {"text": f"Bildschirm von {device['name']} geholt"})
        return {"device": device["name"], "screen": f"{payload.get('width')}x{payload.get('height')}",
                "file": f"downloads/{name}", "bytes": payload.get("bytes"),
                "hinweis": "Das Bild hängt an diesem Zug — beschreibe nur, was wirklich darauf zu sehen ist."}

    reg.register(ToolSpec("desktop.screen",
                          "Look at the user's screen: the PC takes a screenshot and the picture is "
                          "attached for you to actually see. Use it before clicking or typing when you "
                          "are unsure what is on screen, and to check whether something worked.",
                          _obj({"device": _s("which desktop"), "monitor": _i("0 = the whole screen"),
                                "reason": _s("why you need to look")}, ["reason"]),
                          category="desktop", risk="high" if gate_desktop else "medium",
                          requires_approval=gate_desktop, handler=desktop_screen, timeout_seconds=120))

    # ── sprechen: eine Stimme, auf dem Rechner des Nutzers ───────────────
    # Der Text wird HIER zu Ton (ElevenLabs, falls eingerichtet) und geht
    # fertig an den PC. Der Rechner bekommt keinen Schlüssel: Eine Stimme, ein
    # Ort, an dem sie entsteht — sonst klingt JARVIS auf jedem Gerät anders.
    async def desktop_speak(ctx: ToolContext, args: dict):
        from ..services.voice_service import VoiceError
        text = str(args["text"]).strip()
        if not text:
            return "Es gibt nichts zu sagen.", False
        voice = st.services["voice"]
        try:
            audio, mime = await voice.speak(text)
        except VoiceError as e:
            return str(e), False

        device = desktop.resolve(str(args.get("device", "")))
        if not any(a.get("name") == "speak_audio" for a in device["actions"]):
            return (f"'{device['name']}' kann noch keinen Ton abspielen. Auf dem PC einmal "
                    f"git pull und JARVIS neu starten.", False)
        import base64 as _b64
        cmd = await desktop.dispatch(
            device_id=device["id"], action="speak_audio",
            params={"audio_base64": _b64.b64encode(audio).decode("ascii"), "mime": mime},
            requested_by=ctx.principal.actor, agent_id=ctx.agent_id,
            task_id=ctx.task_id, run_id=ctx.run_id, timeout=90)
        if cmd["status"] != "done":
            return cmd["error"] or "Der PC hat den Ton nicht abgespielt.", False
        try:
            payload = json.loads(cmd["result"] or "{}")
        except ValueError:
            payload = {}
        if payload.get("error"):
            return str(payload["error"]), False
        # Der Text gehört ins Protokoll, nicht das Audio — 200 KB Base64 in
        # jedem Schritt würde die Zeitleiste unlesbar machen.
        ctx.emit("speak", {"text": f"{device['name']}: „{text[:60]}…" if len(text) > 60
                                   else f"{device['name']}: „{text}“"})
        return {"spoken_on": device["name"], "voice": voice.tts_provider(),
                "bytes": payload.get("bytes", len(audio)), "mime": mime}

    reg.register(ToolSpec("desktop.speak",
                          "Say something out loud on the user's PC. The server turns the text into a "
                          "voice (ElevenLabs when configured) and sends the finished audio — use it "
                          "when the user asked you to say or read something aloud there.",
                          _obj({"text": _s("what to say, in the language the user speaks"),
                                "device": _s("which desktop")}, ["text"]),
                          category="desktop", risk="low", handler=desktop_speak, timeout_seconds=150))

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

    async def whatsapp_to_owner(ctx: ToolContext, args: dict):
        # Nur an den Nutzer selbst: ein "contact" im Aufruf wird verworfen (Sofortregel 5). Fremde Empfaenger
        # laufen ueber notify.whatsapp_kontakt und brauchen deshalb eine Freigabe.
        return await desktop_whatsapp(ctx, {k: v for k, v in args.items() if k != "contact"})

    reg.register(ToolSpec("notify.whatsapp", "Reach the user on their phone by WhatsApp, as a spoken voice "
                          "note in your own voice or as text. Goes through the desktop's linked WhatsApp, so "
                          "it needs that PC to be running. Use it when something matters and they are away "
                          "from the machine. Only ever writes to the user themselves; for anyone else use "
                          "notify.whatsapp_kontakt.",
                          _obj({"message": _s("the finished message, written to be heard: short sentences, "
                                              "first person, no lists, no markdown"),
                                "mode": _s("voice (default) or text"),
                                "device": _s("which desktop")}, ["message"]),
                          category="communication", risk="medium", handler=whatsapp_to_owner,
                          timeout_seconds=200))
    reg.register(ToolSpec("notify.whatsapp_kontakt", "Send a WhatsApp message to ANOTHER person than the user "
                          "(a customer, a colleague). Goes through the desktop's linked WhatsApp. Always needs "
                          "the user's approval before anything is sent: show the finished text and the recipient "
                          "and wait for the yes.",
                          _obj({"message": _s("the finished message"),
                                "contact": _s("recipient as named in WhatsApp"),
                                "mode": _s("text (default) or voice"),
                                "device": _s("which desktop")}, ["message", "contact"]),
                          category="communication", risk="high", requires_approval=True,
                          handler=desktop_whatsapp, timeout_seconds=200))

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

    # ── Fähigkeiten: Anleitungen, die er bei Bedarf aufschlägt ───────────
    # Zwei Stufen, damit ungenutztes Wissen nicht jedes Gespräch belastet: Im
    # Systemtext stehen nur Name und Zweck, der volle Text kommt über skill.open.
    # Das ist reiner Text und damit bei jedem Anbieter gleich brauchbar —
    # Anthropic, OpenAI, Gemini, lokales Modell.
    skills = st.services["skills"]

    async def skill_list(ctx: ToolContext, args: dict):
        items = skills.all(enabled_only=True)
        if not items:
            return "Es ist noch keine Fähigkeit hinterlegt."
        return [{"name": s["name"], "what_for": s["description"], "used": s["uses"]} for s in items]

    async def skill_open(ctx: ToolContext, args: dict):
        from ..services.skills import SkillError
        try:
            opened = skills.open(str(args["name"]))
        except SkillError as e:
            return str(e), False
        ctx.emit("skill", {"text": f"Fähigkeit aufgeschlagen: {opened['name']}"})
        return opened

    async def skill_save(ctx: ToolContext, args: dict):
        from ..services.skills import SkillError
        try:
            saved = skills.save(
                name=str(args.get("name", "")),
                title=str(args.get("title", "")),
                description=str(args.get("description", "")),
                content=str(args.get("content", "")),
                actor=ctx.principal.actor, source=str(args.get("source", "mia-learning")) or "mia-learning")
        except SkillError as e:
            return str(e), False
        ctx.emit("skill", {"text": f"Neue Fähigkeit gelernt: {saved['name']}"})
        learning = st.services.get("learning")
        if learning is not None:
            learning.record(kind="teacher", title=f"Skill gelernt: {saved['title']}",
                            problem=str(args.get("why", "")), lesson=saved["description"],
                            verification=str(args.get("verification", "")), source="mia-skill",
                            conversation_id=ctx.conversation_id, confidence=float(args.get("confidence", 0.8)),
                            tags=["skill", saved["name"]], actor=ctx.principal.actor)
        return saved

    reg.register(ToolSpec("skill.list", "Which skills are available — each one a written instruction you "
                          "can open when a task calls for it.", _obj({}),
                          category="skills", risk="low", min_role="viewer", handler=skill_list))
    reg.register(ToolSpec("skill.open", "Open a skill and read its full instructions. Do this BEFORE "
                          "starting a task of that kind, not after.",
                          _obj({"name": _s("skill name from skill.list")}, ["name"]),
                          category="skills", risk="low", min_role="viewer", handler=skill_open))
    reg.register(ToolSpec("skill.save",
                          "Save or update a reusable skill MIA has learned from verified work, a teacher, documentation, or a successful procedure. Use only when the instructions are concrete and reusable. Do not store guesses as skills.",
                          _obj({"name": _s("lowercase reusable skill name"),
                                "title": _s("human-readable title"),
                                "description": _s("when this skill should be used"),
                                "content": _s("full reusable instructions"),
                                "why": _s("what demonstrated the need for this skill"),
                                "verification": _s("how the skill was verified"),
                                "confidence": {"type":"number","description":"0..1 confidence after verification"},
                                "source": _s("teacher/source, e.g. claude-code, user-demo, docs")},
                               ["name","description","content"]),
                          category="skills", risk="medium", handler=skill_save))

    # ── Wissensspeicher: was er über diese Anlage weiß ───────────────────
    # Der Unterschied zu einer Fähigkeit ist inhaltlich, nicht technisch:
    # Eine Fähigkeit sagt, WIE etwas zu tun ist. Wissen sagt, WAS der Fall ist —
    # unter welchem Pfad der Server liegt, welche n8n-Instanz gemeint ist,
    # welcher Widerspruch noch ungeklärt ist. Beides wäre im Hauptgedächtnis zu
    # lang; beides muss er trotzdem kennen.
    wissensspeicher = st.services["knowledge"]

    async def knowledge_list(ctx: ToolContext, args: dict):
        items = wissensspeicher.all(enabled_only=True)
        if not items:
            return "Im Wissensspeicher liegt noch nichts."
        return [{"name": k["slug"], "title": k["title"], "what_about": k["summary"],
                 "lines": k["lines"], "opened": k["uses"]} for k in items]

    async def knowledge_open(ctx: ToolContext, args: dict):
        from ..services.knowledge import KnowledgeError
        try:
            doc = wissensspeicher.open(str(args["name"]))
        except KnowledgeError as e:
            return str(e), False
        ctx.emit("knowledge", {"text": f"nachgeschlagen: {doc['title']}"})
        return doc

    async def knowledge_search(ctx: ToolContext, args: dict):
        treffer = wissensspeicher.search(str(args["query"]), int(args.get("limit", 5)))
        if not treffer:
            return (f"Zu '{args['query']}' steht nichts im Wissensspeicher. "
                    f"Das heißt nicht, dass es nicht stimmt — nur, dass es hier nicht steht.")
        return treffer

    async def knowledge_save(ctx: ToolContext, args: dict):
        evidence = str(args.get("evidence", "")).strip()
        if not evidence:
            return "Wissen nicht gespeichert: Es fehlt ein überprüfbarer Nachweis oder eine eindeutige Nutzerangabe.", False
        content = str(args.get("content", "")).strip()
        if not content:
            return "Wissen nicht gespeichert: Inhalt fehlt.", False
        try:
            saved = wissensspeicher.save(title=str(args["title"]), content=content,
                                          summary=str(args.get("summary", "")),
                                          slug=str(args.get("name", "")), actor=ctx.principal.actor)
        except Exception as e:
            return str(e), False
        learning.record(kind="teacher", title=f"Wissen gelernt: {saved['title']}",
                        problem=str(args.get("summary", "")), lesson=saved["summary"],
                        verification=evidence, source=str(args.get("source", "mia-learning")),
                        conversation_id=ctx.conversation_id, confidence=float(args.get("confidence", 1.0)),
                        tags=["knowledge", saved["slug"]], actor=ctx.principal.actor)
        ctx.emit("learning", {"text": f"Wissen gespeichert: {saved['title']}"})
        return saved

    reg.register(ToolSpec("knowledge.list",
                          "What this server knows about itself and its surroundings — "
                          "each entry a document you can open.",
                          _obj({}), category="memory", risk="low", min_role="viewer",
                          handler=knowledge_list))
    reg.register(ToolSpec("knowledge.open",
                          "Read a knowledge document in full. Do this BEFORE answering about "
                          "this installation, its servers, services or devices — do not guess.",
                          _obj({"name": _s("name from knowledge.list")}, ["name"]),
                          category="memory", risk="low", min_role="viewer", handler=knowledge_open))
    reg.register(ToolSpec("knowledge.search",
                          "Find where something is written down in the knowledge base. "
                          "Returns the place and its surrounding lines, not whole documents.",
                          _obj({"query": _s("what to look for"),
                                "limit": {"type": "integer", "description": "how many, default 5"}},
                               ["query"]),
                          category="memory", risk="low", min_role="viewer", handler=knowledge_search))
    reg.register(ToolSpec("knowledge.save",
                          "Save stable verified knowledge for future sessions. Use for facts, environment knowledge, "
                          "documentation conclusions and explicit user-provided knowledge; never for guesses.",
                          _obj({"name": _s("optional stable slug"), "title": _s("knowledge title"),
                                "summary": _s("when this knowledge matters"),
                                "content": _s("verified knowledge in useful detail"),
                                "evidence": _s("source or verification proving this is reliable"),
                                "source": _s("user/docs/web/tool/teacher"),
                                "confidence": {"type": "number"}},
                               ["title", "content", "evidence"]),
                          category="learning", risk="low", handler=knowledge_save))

    # ── MCP: fremde Werkzeugserver ───────────────────────────────────────
    # Die Werkzeuge selbst melden sich beim Start an und heißen mcp.<server>.<werkzeug>.
    # Hier steht nur, was der Nutzen für das Modell ist: nachsehen und neu einlesen.
    mcp = st.services["mcp"]

    async def mcp_servers(ctx: ToolContext, args: dict):
        items = mcp.servers()
        if not items:
            return ("Es ist kein MCP-Server eingetragen. Das geht im Dashboard unter "
                    "Erweiterungen — dort kommen auch die Werkzeuge her, die Claude benutzt.")
        return [{"server": s["name"], "status": s["status"], "detail": s["detail"],
                 "tools": s["tool_count"], "enabled": s["enabled"]} for s in items]

    async def mcp_refresh(ctx: ToolContext, args: dict):
        done = await mcp.refresh_all()
        st.tools.snapshot(st.db)
        return {"servers": len(done), "healthy": sum(1 for d in done if d["status"] == "healthy"),
                "tools": sum(d["tool_count"] for d in done)}

    reg.register(ToolSpec("mcp.servers", "Which MCP servers are connected, and how many tools each one "
                          "contributes. Their tools appear as mcp.<server>.<tool>.", _obj({}),
                          category="integrations", risk="low", min_role="viewer", handler=mcp_servers))
    reg.register(ToolSpec("mcp.refresh", "Re-read the tool list of every connected MCP server. Use this "
                          "after a server was added or changed.", _obj({}),
                          category="integrations", risk="medium", handler=mcp_refresh,
                          timeout_seconds=120))

    # ── sich selbst kennen ───────────────────────────────────────────────
    # „Was kannst du?" soll er beantworten, indem er nachsieht. Ein Systemtext
    # altert mit jedem angebundenen Server; ein Verzeichnis nicht.
    async def system_inventory(ctx: ToolContext, args: dict):
        from ..services.inventory import inventory
        return inventory(st)

    reg.register(ToolSpec("system.inventory",
                          "What you are made of right now: which tools work and which do not and why, "
                          "which integrations are connected, which MCP servers, skills, specialists and "
                          "devices exist. Look here before claiming you can or cannot do something.",
                          _obj({}), category="self", risk="low", min_role="viewer",
                          handler=system_inventory))

    # ── Quelltext holen: klonen und laden ────────────────────────────────
    # Alles landet im Arbeitsbereich, also greifen darauf sofort die
    # filesystem.*-Werkzeuge. Klonen braucht eine Genehmigung: Es holt fremden
    # Quelltext auf deinen Server, und das ist nichts, was nebenbei passiert.
    from ..services.repos import RepoError, RepoService, git_available
    repos = RepoService(files, st.log)
    git_ok = git_available()
    git_reason = "" if git_ok else "git ist im Server-Image nicht installiert"

    async def repo_clone(ctx: ToolContext, args: dict):
        try:
            res = await repos.clone(str(args["url"]), str(args.get("name", "")),
                                    branch=str(args.get("branch", "")))
        except RepoError as e:
            return str(e), False
        ctx.emit("repo", {"text": f"geklont: {res['path']}"})
        return {**res, "hinweis": "Lesbar mit filesystem.list und filesystem.read unter diesem Pfad."}

    async def repo_list(ctx: ToolContext, args: dict):
        items = repos.list()
        return items or "Es ist noch kein Repository geklont."

    async def repo_pull(ctx: ToolContext, args: dict):
        try:
            return await repos.pull(str(args["name"]))
        except RepoError as e:
            return str(e), False

    async def repo_remove(ctx: ToolContext, args: dict):
        try:
            gone = repos.remove(str(args["name"]))
        except RepoError as e:
            return str(e), False
        return {"removed": gone} if gone else (f"'{args['name']}' gibt es nicht.", False)

    async def web_download(ctx: ToolContext, args: dict):
        try:
            return await repos.download(str(args["url"]), str(args.get("name", "")))
        except RepoError as e:
            return str(e), False

    reg.register(ToolSpec("repo.clone",
                          "Clone a public git repository into the workspace so you can read it. "
                          "http(s) only, shallow, and never executed — reading is filesystem.read.",
                          _obj({"url": _s("https URL of the repository"),
                                "name": _s("folder name, defaults to the repository name"),
                                "branch": _s("branch, defaults to the default branch"),
                                "reason": _s("why this is needed")}, ["url", "reason"]),
                          category="code", risk="high", requires_approval=True, handler=repo_clone,
                          available=git_ok, reason=git_reason, timeout_seconds=360))
    reg.register(ToolSpec("repo.list", "Which repositories are cloned into the workspace.", _obj({}),
                          category="code", risk="low", min_role="viewer", handler=repo_list))
    reg.register(ToolSpec("repo.pull", "Fetch the latest commits of a cloned repository.",
                          _obj({"name": _s("folder name from repo.list")}, ["name"]),
                          category="code", risk="medium", handler=repo_pull,
                          available=git_ok, reason=git_reason, timeout_seconds=180))
    reg.register(ToolSpec("repo.remove", "Delete a cloned repository from the workspace.",
                          _obj({"name": _s("folder name from repo.list"),
                                "reason": _s("why")}, ["name", "reason"]),
                          category="code", risk="high", requires_approval=True, handler=repo_remove))
    reg.register(ToolSpec("web.download",
                          "Download one file from a link into the workspace (downloads/). "
                          "Addresses inside this server's own network are refused.",
                          _obj({"url": _s("https URL of the file"),
                                "name": _s("file name, defaults to the one in the URL"),
                                "reason": _s("why this is needed")}, ["url", "reason"]),
                          category="web", risk="high", requires_approval=True, handler=web_download,
                          timeout_seconds=120))

    # ── am eigenen Quelltext arbeiten ─────────────────────────────────────
    # Die Fehler, die diesen Server zuletzt lahmgelegt haben, lagen alle im
    # Quelltext: ein Schema, das der Anbieter ablehnt; eine Datei, die das
    # Dockerfile nicht ins Abbild kopierte. Mit filesystem.* war keiner davon
    # zu beheben — der Arbeitsbereich liegt unter /data, der Quelltext nicht.
    #
    # Also hier, auf dem Git-Arbeitsverzeichnis, und ausschließlich über Git:
    # jede Änderung ein Commit, jede Rücknahme ein revert. Was er getan hat,
    # steht in `git log`, auch für jemanden, der ihm nicht glaubt.
    from ..services.source import SourceError, SourceService, unavailable_reason as src_reason
    quelle = SourceService()
    src_ok = quelle.available()
    src_why = src_reason()

    async def source_read(ctx: ToolContext, args: dict):
        requested = str(args["path"])
        normalized = requested.replace("\\", "/").strip()
        # Mark-LIII is a separate, read-only project mount, not part of the
        # Command Center Git repository. Voice models sometimes choose
        # source.read for a Python file and may omit the leading slash. Route
        # all recognised spellings to the confined workspace reader instead
        # of incorrectly reporting that the real file does not exist.
        if normalized == "Mark-LIII" or normalized.startswith("Mark-LIII/") \
                or normalized == "/root/Mark-LIII" or normalized.startswith("/root/Mark-LIII/") \
                or normalized == "root/Mark-LIII" or normalized.startswith("root/Mark-LIII/"):
            try:
                res = files.read_text(normalized, max_bytes=200_000)
                return res["content"] + ("\n…[truncated]" if res["truncated"] else "")
            except WorkspaceError as e:
                return str(e), False
        try:
            return await quelle.read(requested)
        except SourceError as e:
            return str(e), False

    async def source_list(ctx: ToolContext, args: dict):
        try:
            return await quelle.list(str(args.get("path", "")))
        except SourceError as e:
            return str(e), False

    async def source_history(ctx: ToolContext, args: dict):
        try:
            return await quelle.history(str(args.get("path", "")), int(args.get("limit", 15)))
        except SourceError as e:
            return str(e), False

    async def source_diff(ctx: ToolContext, args: dict):
        try:
            return await quelle.diff(str(args.get("commit", "")))
        except SourceError as e:
            return str(e), False

    async def source_write(ctx: ToolContext, args: dict):
        try:
            res = await quelle.write(str(args["path"]), str(args["content"]), str(args["reason"]))
        except SourceError as e:
            return str(e), False
        ctx.emit("source", {"text": f"{res['path']} geändert ({res['commit']})"})
        return res

    async def source_delete(ctx: ToolContext, args: dict):
        try:
            res = await quelle.delete(str(args["path"]), str(args["reason"]))
        except SourceError as e:
            return str(e), False
        ctx.emit("source", {"text": f"{res['path']} entfernt ({res['commit']})"})
        return res

    async def source_revert(ctx: ToolContext, args: dict):
        try:
            res = await quelle.revert(str(args["commit"]))
        except SourceError as e:
            return str(e), False
        ctx.emit("source", {"text": f"zurückgenommen: {res['reverted']}"})
        return res

    reg.register(ToolSpec("source.read", "Read one file of this server's own source code. For the separate "
                          "Mark-LIII project, read-only paths /root/Mark-LIII/..., root/Mark-LIII/... and "
                          "Mark-LIII/... are accepted and routed to its workspace mount.",
                          _obj({"path": _s("repository-relative path, or a read-only Mark-LIII path")}, ["path"]),
                          category="code", risk="low", handler=source_read,
                          available=src_ok, reason=src_why))
    reg.register(ToolSpec("source.list",
                          "List the source files git tracks, optionally under one folder.",
                          _obj({"path": _s("folder, empty for the whole repository")}),
                          category="code", risk="low", handler=source_list,
                          available=src_ok, reason=src_why))
    reg.register(ToolSpec("source.history",
                          "Recent commits — for the whole repository or one file.",
                          _obj({"path": _s("file, empty for the whole repository"),
                                "limit": {"type": "integer", "description": "how many, max 50"}}),
                          category="code", risk="low", handler=source_history,
                          available=src_ok, reason=src_why))
    reg.register(ToolSpec("source.diff",
                          "Show a change: a commit by its id, or what is uncommitted right now.",
                          _obj({"commit": _s("commit id, empty for the working tree")}),
                          category="code", risk="low", handler=source_diff,
                          available=src_ok, reason=src_why))
    # Schreiben und Löschen am eigenen Quelltext ist das Heikelste, was dieser
    # Server kann: Wer ihn ändert, ändert, was der Server als Nächstes tut.
    # Deshalb `critical` und immer eine Freigabe — ein Mensch sieht den
    # Unterschied, bevor er Teil des Systems wird.
    reg.register(ToolSpec("source.write",
                          "Create or rewrite a file of this server's own source code. "
                          "The change is committed to git immediately and takes effect only "
                          "after a restart or a rebuild. Requires approval.",
                          _obj({"path": _s("path relative to the repository root"),
                                "content": _s("the complete new content of the file"),
                                "reason": _s("why — this becomes the commit message")},
                               ["path", "content", "reason"]),
                          category="code", risk="critical", requires_approval=True,
                          min_role="admin", handler=source_write,
                          available=src_ok, reason=src_why, timeout_seconds=60))
    reg.register(ToolSpec("source.delete",
                          "Delete a file from this server's own source code, committed to git. "
                          "Requires approval.",
                          _obj({"path": _s("path relative to the repository root"),
                                "reason": _s("why — this becomes the commit message")},
                               ["path", "reason"]),
                          category="code", risk="critical", requires_approval=True,
                          min_role="admin", handler=source_delete,
                          available=src_ok, reason=src_why, timeout_seconds=60))
    reg.register(ToolSpec("source.revert",
                          "Undo an earlier change by its commit id — as a new commit, so the "
                          "history stays readable. Requires approval.",
                          _obj({"commit": _s("commit id from source.history")}, ["commit"]),
                          category="code", risk="high", requires_approval=True,
                          min_role="admin", handler=source_revert,
                          available=src_ok, reason=src_why, timeout_seconds=60))

    # ── seine Umgebung: das Dashboard, das der Nutzer vor sich hat ────────
    # Er soll nicht nur handeln, sondern auch sehen, was der Nutzer sieht:
    # was auf Freigabe wartet, was gemeldet wurde, worüber schon gesprochen
    # wurde. Und er soll zeigen können, wovon er redet — dafür öffnet
    # dashboard.open die passende Seite im Browser des Nutzers.
    #
    # Was er ausdrücklich NICHT bekommt: Genehmigungen erteilen. Wer seine
    # eigenen Anträge bewilligen kann, hat kein Genehmigungstor, sondern eine
    # Formalität.
    async def approval_list(ctx: ToolContext, args: dict):
        items = st.services["approvals"].list(status=str(args.get("status", "pending")), limit=20)
        if not items:
            return "Es wartet nichts auf eine Freigabe."
        return [{"id": a["id"], "code": a["code"], "action": a["action"], "target": a["target"],
                 "risk": a["risk"], "reason": a["reason"], "status": a["status"],
                 "since": a["created_at"]} for a in items]

    async def notification_list(ctx: ToolContext, args: dict):
        items = st.services["notifications"].list(ctx.principal.id,
                                                  unread_only=bool(args.get("unread_only", True)),
                                                  limit=int(args.get("limit", 20)))
        if not items:
            return "Keine Meldungen."
        return [{"title": n["title"], "body": n["body"][:200], "severity": n["severity"],
                 "when": n["created_at"], "read": bool(n["read"])} for n in items]

    async def conversation_search(ctx: ToolContext, args: dict):
        rows = st.services["chat"].list_conversations(ctx.principal.id, q=str(args.get("query", "")),
                                                      limit=int(args.get("limit", 15)))
        if not rows:
            return "Dazu gibt es kein früheres Gespräch."
        return [{"id": r["id"], "title": r["title"], "last": r["updated_at"]} for r in rows]

    async def dashboard_open(ctx: ToolContext, args: dict):
        """Die passende Seite im Browser des Nutzers öffnen.

        Das ist kein Fernsteuern des Rechners, sondern ein Wink: Das Dashboard
        hört auf diesen Hinweis und wechselt die Seite. Läuft gerade keines,
        passiert nichts — deshalb sagt die Antwort, dass gezeigt wurde, nicht
        dass jemand hingesehen hat.
        """
        path = str(args["path"]).strip()
        if not path.startswith("/"):
            path = "/" + path
        st.bus.publish("ui.open", {"path": path, "reason": str(args.get("reason", "")),
                                   "by": ctx.principal.actor})
        ctx.emit("ui", {"text": f"Dashboard geöffnet: {path}"})
        return f"Im Dashboard {path} geöffnet — sofern gerade eines offen ist."

    reg.register(ToolSpec("approval.list", "What is waiting for the user's approval right now, with the "
                          "reason and the code they see. You cannot approve anything yourself.",
                          _obj({"status": _s("pending (default), approved, rejected")}),
                          category="dashboard", risk="low", min_role="viewer", handler=approval_list))
    reg.register(ToolSpec("notification.list", "Recent notifications in the user's notification centre.",
                          _obj({"unread_only": {"type": "boolean"}, "limit": _i("max rows, default 20")}),
                          category="dashboard", risk="low", min_role="viewer", handler=notification_list))
    reg.register(ToolSpec("conversation.search", "Find an earlier conversation by its words, so you can "
                          "refer to what was already discussed instead of asking again.",
                          _obj({"query": _s("what it was about"), "limit": _i("max rows")}),
                          category="dashboard", risk="low", min_role="viewer", handler=conversation_search))
    reg.register(ToolSpec("dashboard.open", "Open a page in the user's dashboard so they see what you are "
                          "talking about — e.g. /server, /tasks, /calendar, /approvals, /extensions.",
                          _obj({"path": _s("dashboard path, e.g. /server"),
                                "reason": _s("what they will see there")}, ["path"]),
                          category="dashboard", risk="low", handler=dashboard_open))

    # ── ein Browser, wenn ein Abruf nicht reicht ──────────────────────────
    # Zwei Wege, und das Modell soll den Unterschied kennen: Auf dem PC des
    # Nutzers läuft SEIN Browser mit seinen Anmeldungen (desktop.run mit
    # browser_control) — dafür muss der Rechner an sein. Hier auf dem Server
    # läuft einer ohne Profil, dafür jederzeit.
    from ..services.browser import BrowserError, BrowserSession
    from ..services.browser import available as browser_available
    browser = BrowserSession(files.root)
    br_ok, br_why = browser_available()

    def _br(fn):
        async def run(ctx: ToolContext, args: dict):
            try:
                return await fn(ctx, args)
            except BrowserError as e:
                return str(e), False
        return run

    @_br
    async def browser_open(ctx: ToolContext, args: dict):
        res = await browser.goto(str(args["url"]))
        ctx.emit("browser", {"text": f"geöffnet: {res['url']}"})
        return res

    @_br
    async def browser_read(ctx: ToolContext, args: dict):
        return await browser.read()

    @_br
    async def browser_click(ctx: ToolContext, args: dict):
        return await browser.click(str(args["what"]))

    @_br
    async def browser_type(ctx: ToolContext, args: dict):
        return await browser.type(str(args["field"]), str(args["text"]), bool(args.get("submit")))

    @_br
    async def browser_shot(ctx: ToolContext, args: dict):
        res = await browser.screenshot(str(args.get("name", "browser.png")))
        # Auch hier: anhängen, nicht nur ablegen. Ein Bild, das nur auf der
        # Platte liegt, hat er nicht gesehen.
        ctx.attach(res["path"])
        return res

    @_br
    async def browser_close(ctx: ToolContext, args: dict):
        await browser.close()
        return "Browser geschlossen."

    reg.register(ToolSpec("browser.open",
                          "Open a page in a real browser on this server and read what it actually says — "
                          "for pages that build themselves with JavaScript, where web.fetch returns "
                          "nothing useful. No logins, no profile: for the user's own accounts drive THEIR "
                          "browser with desktop.run + browser_control instead.",
                          _obj({"url": _s("https address")}, ["url"]),
                          category="web", risk="medium", handler=browser_open,
                          available=br_ok, reason=br_why, timeout_seconds=90))
    reg.register(ToolSpec("browser.read", "Read the page that is currently open, again — after something "
                          "loaded or changed.", _obj({}),
                          category="web", risk="low", min_role="viewer", handler=browser_read,
                          available=br_ok, reason=br_why, timeout_seconds=60))
    reg.register(ToolSpec("browser.click", "Click something on the open page, by its visible text or a CSS "
                          "selector. This acts on a real site — it needs approval.",
                          _obj({"what": _s("visible text or CSS selector"),
                                "reason": _s("why")}, ["what", "reason"]),
                          category="web", risk="high", requires_approval=True, handler=browser_click,
                          available=br_ok, reason=br_why, timeout_seconds=90))
    reg.register(ToolSpec("browser.type", "Type into a field on the open page, optionally pressing Enter. "
                          "Acts on a real site — needs approval.",
                          _obj({"field": _s("label or CSS selector of the field"), "text": _s("what to type"),
                                "submit": {"type": "boolean", "description": "press Enter afterwards"},
                                "reason": _s("why")}, ["field", "text", "reason"]),
                          category="web", risk="high", requires_approval=True, handler=browser_type,
                          available=br_ok, reason=br_why, timeout_seconds=90))
    reg.register(ToolSpec("browser.screenshot", "Take a picture of the open page — it is attached for you "
                          "to actually look at, and saved in the workspace for the user.",
                          _obj({"name": _s("file name, default browser.png")}),
                          category="web", risk="low", handler=browser_shot,
                          available=br_ok, reason=br_why, timeout_seconds=60))
    reg.register(ToolSpec("browser.close", "Close the browser on the server and free its memory.", _obj({}),
                          category="web", risk="low", handler=browser_close,
                          available=br_ok, reason=br_why))
