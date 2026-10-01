"""Server control center API: host metrics, containers, services, gated actions."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/server", tags=["server"])


class ReasonBody(BaseModel):
    reason: str = ""


@router.get("/overview")
async def overview(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    m = state.services["metrics"]
    ov = m.overview()
    recent_errors = state.log.query(level="ERROR", limit=10)
    return {"overview": ov, "disks": m.disks(), "network": m.network(), "services": m.service_status(),
            "recent_errors": recent_errors,
            "capabilities": {"docker_actions": state.settings.allow_docker_actions,
                             "service_restart": state.settings.allow_service_restart,
                             "terminal": state.settings.allow_terminal}}


@router.get("/metrics/history")
async def history(limit: int = 720, hours: int = 0, state: AppState = Depends(get_state),
                  _: Principal = Depends(current_principal)):
    m = state.services["metrics"]
    if hours:
        return {"points": m.stored_history(hours), "source": "persisted"}
    return {"points": m.history_points(limit), "source": "live"}


@router.get("/processes")
async def processes(limit: int = 20, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"processes": await asyncio.to_thread(state.services["metrics"].processes, limit)}


@router.get("/containers")
async def containers(stats: bool = True, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return await state.services["metrics"].docker_containers(with_stats=stats)


@router.get("/containers/{cid}/logs")
async def container_logs(cid: str, tail: int = 200, state: AppState = Depends(get_state),
                         _: Principal = Depends(require_role("operator"))):
    try:
        return {"logs": await state.services["metrics"].docker_logs(cid, tail)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


async def _gated(state: AppState, principal: Principal, *, action: str, target: str, reason: str, risk: str, run):
    """Human-initiated sensitive action: recorded as an approval the actor grants themselves,
    so the audit trail shows reason + actor, then executed."""
    approvals = state.services["approvals"]
    approval = approvals.request(action=action, reason=reason or "requested from the server page", target=target,
                                 risk=risk, requested_by=principal.actor, agent_id="")
    approvals.decide(approval["id"], approve=True, decided_by=principal.actor, note="confirmed in dashboard")
    try:
        result = await run()
    except Exception as e:  # noqa: BLE001
        state.log.audit(actor_type="user", actor_id=principal.actor, action=action, target=target, status="error",
                        error=str(e))
        raise HTTPException(status_code=502, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action=action, target=target, status="ok",
                    result=str(result)[:500])
    state.services["notifications"].notify(category="server", severity="success", title=f"{action}: {target}",
                                            body=reason)
    return result


@router.post("/containers/{cid}/restart")
async def restart_container(cid: str, body: ReasonBody, state: AppState = Depends(get_state),
                            principal: Principal = Depends(require_role("admin"))):
    if not state.settings.allow_docker_actions:
        raise HTTPException(status_code=403, detail="Docker actions are disabled (JARVIS_CC_ALLOW_DOCKER_ACTIONS)")
    m = state.services["metrics"]
    return {"result": await _gated(state, principal, action="docker.restart_container", target=cid,
                                   reason=body.reason, risk="high", run=lambda: m.docker_action(cid, "restart"))}


@router.post("/services/{name}/restart")
async def restart_service(name: str, body: ReasonBody, state: AppState = Depends(get_state),
                          principal: Principal = Depends(require_role("admin"))):
    if not state.settings.allow_service_restart:
        raise HTTPException(status_code=403, detail="Service restarts are disabled (JARVIS_CC_ALLOW_SERVICE_RESTART)")
    m = state.services["metrics"]
    return {"result": await _gated(state, principal, action="server.restart_service", target=name,
                                   reason=body.reason, risk="critical",
                                   run=lambda: asyncio.to_thread(m.restart_service, name))}


MODULE = ModuleSpec(
    id="server", title="Server", router=router, icon="server", path="/server", order=70, mobile_priority=40,
    description="Leitstand des Servers",
    commands=[{"id": "server.open", "title": "Open Server", "path": "/server", "shortcut": "g s"}],
)
