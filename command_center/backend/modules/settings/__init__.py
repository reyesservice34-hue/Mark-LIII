"""Settings: non-secret runtime configuration view + desktop pairing info."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, request_is_https
from .. import ModuleSpec

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings_view(request: Request, state: AppState = Depends(get_state),
                            principal: Principal = Depends(current_principal)):
    s = state.settings
    master = state.runtime.status()
    scheme = "https" if request_is_https(request, s.trust_proxy) else "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or f"localhost:{s.port}"
    base_url = f"{scheme}://{host}{s.root_path}"
    return {
        "version": state.version,
        "master": {"mode": master["mode"], "provider": master["provider"], "label": master["label"]},
        "providers": {
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")), "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            "local": bool(os.environ.get("LOCAL_LLM_URL")),
            "remote_control_plane": bool(os.environ.get("JARVIS_GATEWAY_TOKEN") and s.gateway_url),
        },
        "capabilities": {"terminal": s.allow_terminal, "docker_actions": s.allow_docker_actions,
                         "service_restart": s.allow_service_restart, "monitored_services": s.monitored_services,
                         "approval_threshold": state.tools.approval_threshold,
                         "approval_timeout_minutes": s.approval_timeout_minutes},
        "paths": {"data_dir": str(s.data_dir), "workspace_dir": str(s.workspace_dir),
                  "agent_roster": str(s.agent_roster_path) if s.agent_roster_path else ""},
        "security": {"secure_cookies": s.secure_cookies, "session_ttl_hours": s.session_ttl_hours,
                     "trust_proxy": s.trust_proxy, "allowed_origins": s.allowed_origins,
                     "login_rate_limit_per_minute": s.login_rate_limit_per_minute},
        "desktop_pairing": {
            "gateway_url": base_url,
            "instructions": (
                "On the desktop (Mark-LIII) set JARVIS_GATEWAY_TOKEN to a machine token created below and put "
                f"\"jarvis_gateway_url\": \"{base_url}\" plus \"jarvis_actor\": \"<token actor>\" into "
                "config/api_keys.json (see config/control_plane.example.json). The desktop then sends every "
                "command to POST /v1/commands here and reads the answer back."),
            "endpoints": ["POST /v1/commands", "GET /v1/commands/{job_id}", "POST /v1/memory",
                          "GET /v1/memory/search"],
        },
        "user": principal.public(),
    }


MODULE = ModuleSpec(id="settings", title="Settings", router=router, icon="settings", path="/settings", order=130,
                    description="Configuration, users and pairing")
