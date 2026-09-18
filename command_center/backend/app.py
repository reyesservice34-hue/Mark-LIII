"""
Application factory — wires storage, services, registries, modules and the
static frontend into one FastAPI app.

Request pipeline:
  rate limit → CSRF (cookie sessions only) → module routers (/api/…, /v1/…)
  → SPA fallback for everything else.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .adapters.integrations import IntegrationRegistry
from .adapters.workflows import WorkflowHub
from .auth import AuthService
from .config import Settings, get_settings
from .db import Database
from .deps import CSRF_COOKIE, SESSION_COOKIE, AppState, client_ip, resolve_principal
from .events import bus as global_bus
from .logbook import LogBook
from .modules import DEFAULT_MODULES, ModuleRegistry
from .modules.automations.scheduler import Scheduler
from .orchestrator.agent_registry import AgentRegistry
from .orchestrator.builtin_tools import register_builtin_tools
from .orchestrator.runtime import MasterRuntime
from .orchestrator.tool_registry import ToolRegistry
from .services.approvals import ApprovalService
from .services.calendar_service import CalendarService
from .services.chat_store import ChatStore
from .services.email_service import EmailService
from .services.external import GitHubService
from .services.files import FileService
from .services.metrics import MetricsService
from .services.notifications import NotificationService
from .services.tasks import TaskService
from .services.voice_service import VoiceService

VERSION = "0.1.0"
_logger = logging.getLogger("jarvis.cc")
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def build_state(settings: Settings | None = None) -> AppState:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    bus = global_bus
    log = LogBook(db, bus, retention_rows=settings.log_retention_rows)
    auth = AuthService(db, log, session_ttl_hours=settings.session_ttl_hours)
    state = AppState(settings=settings, db=db, bus=bus, log=log, auth=auth, version=VERSION)
    state.started_at = time.time()

    tasks = TaskService(db, bus, log)
    notifications = NotificationService(db, bus)
    approvals = ApprovalService(db, bus, log, tasks, notifications,
                                timeout_minutes=settings.approval_timeout_minutes)
    state.services.update({
        "tasks": tasks, "notifications": notifications, "approvals": approvals,
        "chat": ChatStore(db, bus),
        "files": FileService(settings.workspace_dir, db, bus, max_upload_mb=settings.max_upload_mb),
        "metrics": MetricsService(db, bus, docker_socket=settings.docker_socket,
                                  monitored_services=settings.monitored_services,
                                  history_points=settings.metrics_history_points),
        "calendar": CalendarService(),
        "email": EmailService(),
        "github": GitHubService(),
        "voice": VoiceService(),
    })
    state.integrations = IntegrationRegistry(db, bus)
    state.workflows = WorkflowHub(db, bus)
    state.tools = ToolRegistry(approval_threshold=os.environ.get("JARVIS_CC_APPROVAL_RISK", "high"))
    state.agents = AgentRegistry(db, bus, logger=lambda m: log.info("agents", m))
    state.agents.load(settings.agent_roster_path)
    state.runtime = MasterRuntime(state)
    register_builtin_tools(state.tools, state)
    state.tools.snapshot(db)
    state.scheduler = Scheduler(bus, log)
    return state


def create_app(settings: Settings | None = None) -> FastAPI:
    state = build_state(settings)
    s = state.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.bus.bind_loop(asyncio.get_running_loop())
        username, generated = state.auth.bootstrap_admin(s.admin_user, s.admin_password)
        if generated:
            msg = (f"No users existed — created admin '{username}' with a generated password. "
                   f"Set JARVIS_CC_ADMIN_PASSWORD to choose your own.")
            state.log.warning("auth", msg)
            print(f"\n[JARVIS CC] {msg}\n[JARVIS CC] Initial password: {generated}\n", flush=True)
        state.log.info("app", f"JARVIS Command Center {VERSION} starting — master agent mode: "
                              f"{state.runtime.mode}")
        for spec in registry.all():
            if spec.on_startup:
                res = spec.on_startup(state)
                if asyncio.iscoroutine(res):
                    await res
        await state.scheduler.start()
        state.bus.publish("system.started", {"version": VERSION})
        try:
            yield
        finally:
            await state.scheduler.stop()
            for spec in registry.all():
                if spec.on_shutdown:
                    res = spec.on_shutdown(state)
                    if asyncio.iscoroutine(res):
                        await res
            state.log.info("app", "JARVIS Command Center stopping")
            state.db.close()

    app = FastAPI(title="JARVIS Command Center", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan, root_path=s.root_path)
    app.state.jarvis = state

    if s.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.allowed_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    registry = ModuleRegistry()
    names = [m for m in (os.environ.get("JARVIS_CC_MODULES", "").split(",") if os.environ.get("JARVIS_CC_MODULES")
                         else DEFAULT_MODULES) if m.strip()]
    for spec in registry.load(names):
        app.include_router(spec.router)
    state.services["modules"] = registry

    # ── middleware: rate limit + CSRF + principal ─────────────────────────
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/") or path.startswith("/v1/")
        if is_api:
            ip = client_ip(request, s.trust_proxy) or "unknown"
            if not state.auth.limiter.allow(f"api:{ip}", s.api_rate_limit_per_minute):
                return JSONResponse({"detail": "Too many requests"}, status_code=429)
            principal = resolve_principal(request, state)
            request.state.principal = principal
            if (request.method in _MUTATING and principal is not None and principal.kind == "user"
                    and not path.startswith("/api/auth/login")):
                header = request.headers.get("x-csrf-token", "")
                expected = getattr(request.state, "csrf_token", "")
                if not header or header != expected:
                    return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        response = await call_next(request)
        if is_api:
            response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # ── static frontend (built by Vite) ─────────────────────────────────
    static_dir: Path = s.static_dir
    index = static_dir / "index.html"
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "v1/")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (static_dir / full_path) if full_path else index
        try:
            candidate = candidate.resolve()
            candidate.relative_to(static_dir.resolve())
        except (ValueError, OSError):
            candidate = index
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        if index.is_file():
            return FileResponse(str(index), headers={"Cache-Control": "no-cache"})
        return JSONResponse({"detail": "Frontend not built. Run `npm run build` in command_center/frontend "
                                       "or use the Docker image."}, status_code=503)

    return app


def cookie_kwargs(request: Request, settings: Settings) -> dict:
    from .deps import request_is_https
    secure = {"true": True, "false": False}.get(settings.secure_cookies)
    if secure is None:
        secure = request_is_https(request, settings.trust_proxy)
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/",
            "max_age": settings.session_ttl_hours * 3600}


__all__ = ["create_app", "build_state", "cookie_kwargs", "SESSION_COOKIE", "CSRF_COOKIE"]
