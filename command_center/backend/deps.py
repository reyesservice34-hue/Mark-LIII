"""
FastAPI dependencies and the shared application state container.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Request, WebSocket, status

from .auth import AuthService, Principal
from .config import Settings
from .db import Database
from .events import EventBus
from .logbook import LogBook

if TYPE_CHECKING:  # pragma: no cover
    from .orchestrator.agent_registry import AgentRegistry
    from .orchestrator.runtime import MasterRuntime
    from .orchestrator.tool_registry import ToolRegistry
    from .modules.automations.scheduler import Scheduler
    from .adapters.integrations import IntegrationRegistry
    from .adapters.workflows import WorkflowHub

SESSION_COOKIE = "jcc_session"
CSRF_COOKIE = "jcc_csrf"


@dataclass
class AppState:
    settings: Settings
    db: Database
    bus: EventBus
    log: LogBook
    auth: AuthService
    tools: "ToolRegistry | None" = None
    agents: "AgentRegistry | None" = None
    runtime: "MasterRuntime | None" = None
    scheduler: "Scheduler | None" = None
    integrations: "IntegrationRegistry | None" = None
    workflows: "WorkflowHub | None" = None
    services: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    version: str = "0.1.0"


def get_state(request: Request) -> AppState:
    return request.app.state.jarvis


def _bearer(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def resolve_principal(request: Request | WebSocket, state: AppState) -> Principal | None:
    headers = request.headers
    token = headers.get("x-jarvis-token", "").strip() or _bearer(headers.get("authorization", ""))
    if token:
        principal = state.auth.resolve_api_token(token)
        if principal:
            return principal
        return None
    raw = request.cookies.get(SESSION_COOKIE, "")
    if raw:
        resolved = state.auth.resolve_session(raw)
        if resolved:
            principal, csrf = resolved
            try:
                request.state.csrf_token = csrf
            except Exception:
                pass
            return principal
    return None


def current_principal(request: Request, state: AppState = Depends(get_state)) -> Principal:
    principal = getattr(request.state, "principal", None) or resolve_principal(request, state)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    request.state.principal = principal
    return principal


def require_role(minimum: str):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.has_role(minimum):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Requires role '{minimum}'")
        return principal
    return _dep


def client_ip(request: Request | WebSocket, trust_proxy: bool = True) -> str:
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("x-real-ip", "")
        if real:
            return real.strip()
    client = request.client
    return client.host if client else ""


def request_is_https(request: Request | WebSocket, trust_proxy: bool = True) -> bool:
    if trust_proxy:
        proto = request.headers.get("x-forwarded-proto", "")
        if proto:
            return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme in ("https", "wss")
