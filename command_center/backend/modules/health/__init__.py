"""Health + global status bar data."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import AppState, current_principal, get_state
from .. import ModuleSpec

router = APIRouter(prefix="/api", tags=["health"])


def _overall(parts: dict) -> str:
    statuses = [p.get("status") for p in parts.values()]
    if any(s == "offline" for s in statuses):
        return "degraded" if parts["application"]["status"] == "healthy" else "offline"
    if any(s == "degraded" for s in statuses):
        return "degraded"
    return "healthy"


@router.get("/health")
async def health(state: AppState = Depends(get_state)):
    """Unauthenticated liveness/readiness summary — structure only, no secrets."""
    try:
        state.db.scalar("SELECT 1")
        db_status = {"status": "healthy", "detail": "sqlite ok"}
    except Exception as e:  # noqa: BLE001
        db_status = {"status": "offline", "detail": str(e)[:100]}
    master = state.runtime.status()
    ph = master["provider_health"].get("status", "unknown")
    gateway = {"status": ("healthy" if master["online"] and ph in ("healthy", "unknown") else
                          "degraded" if master["online"] else "offline"),
               "detail": master["label"], "mode": master["mode"]}
    integ = state.integrations.summary()
    integrations = {"status": "healthy" if integ["degraded"] == 0 else "degraded",
                    "detail": f"{integ['connected']} connected, {integ['degraded']} with problems, "
                              f"{integ['total'] - integ['configured']} not configured"}
    metrics = state.services["metrics"]
    svc = metrics.service_status()
    server = {"status": "offline" if any(s["status"] == "offline" for s in svc) else
              ("degraded" if any(s["status"] == "degraded" for s in svc) else "healthy"),
              "detail": f"{len(svc)} monitored services" if svc else "no monitored services configured",
              "docker": metrics.overview()["docker"]}
    parts = {"application": {"status": "healthy", "detail": f"v{state.version}",
                             "uptime_seconds": int(time.time() - state.started_at)},
             "database": db_status, "agent_gateway": gateway, "integrations": integrations,
             "server_services": server}
    return {"status": _overall(parts), "components": parts, "version": state.version}


@router.get("/status")
async def status(state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    """Everything the top status bar shows, in one call."""
    metrics = state.services["metrics"]
    sample = metrics.latest() or metrics.sample()
    master = state.runtime.status()
    tasks = state.services["tasks"].counts()
    return {
        "jarvis": {"online": True, "label": master["label"], "version": state.version,
                   "uptime_seconds": int(time.time() - state.started_at)},
        "master": master,
        "server": {"connected": True, "hostname": metrics.overview()["hostname"], "cpu": sample["cpu"],
                   "ram": sample["ram"], "disk": sample["disk"], "load": sample["load"],
                   "net_rx": sample["net_rx"], "net_tx": sample["net_tx"], "uptime": sample["uptime"],
                   "docker": metrics.overview()["docker"]},
        "tasks": tasks,
        "agents": state.agents.counts(),
        "approvals_pending": state.services["approvals"].pending_count(),
        "notifications_unread": state.services["notifications"].unread_count(principal.id),
        "integrations": state.integrations.summary(),
        "events_subscribers": state.bus.subscriber_count,
        "user": principal.public(),
    }


MODULE = ModuleSpec(id="health", title="Health", router=router, nav=False, order=1)
