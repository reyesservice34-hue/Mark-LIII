"""Workflow center API (adapter-backed; n8n today)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class TriggerBody(BaseModel):
    payload: dict = {}


class ActiveBody(BaseModel):
    active: bool


@router.get("")
async def list_workflows(refresh: bool = False, state: AppState = Depends(get_state),
                         _: Principal = Depends(current_principal)):
    hub = state.workflows
    if refresh and hub.configured():
        await hub.sync()
    return {"workflows": hub.cached(), "providers": hub.providers(), "configured": hub.configured(),
            "stats": hub.stats()}


@router.get("/runs")
async def runs(workflow_id: str = "", limit: int = 50, state: AppState = Depends(get_state),
               _: Principal = Depends(current_principal)):
    return {"runs": state.workflows.cached_runs(workflow_id, limit)}


@router.get("/runs/{run_id:path}")
async def run_detail(run_id: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    try:
        detail = await state.workflows.run_detail(run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Workflow engine error: {e}")
    if not detail:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"run": detail}


@router.post("/{workflow_id:path}/trigger")
async def trigger(workflow_id: str, body: TriggerBody, state: AppState = Depends(get_state),
                  principal: Principal = Depends(require_role("operator"))):
    try:
        result = await state.workflows.trigger(workflow_id, body.payload, by=principal.actor)
    except Exception as e:  # noqa: BLE001
        state.log.audit(actor_type="user", actor_id=principal.actor, action="workflow.trigger", target=workflow_id,
                        status="error", error=str(e))
        raise HTTPException(status_code=502, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="workflow.trigger", target=workflow_id,
                    status="ok" if result.get("ok") else "error", result=str(result.get("http_status", "")))
    return {"result": result}


@router.post("/{workflow_id:path}/active")
async def set_active(workflow_id: str, body: ActiveBody, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    try:
        result = await state.workflows.set_active(workflow_id, body.active)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="workflow.activate" if body.active else
                    "workflow.deactivate", target=workflow_id, status="ok")
    return {"result": result}


@router.post("/runs/{run_id:path}/retry")
async def retry(run_id: str, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    try:
        result = await state.workflows.retry(run_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="workflow.retry", target=run_id, status="ok")
    return {"result": result}


def _startup(state: AppState) -> None:
    hub = state.workflows
    state.scheduler.add("workflow_sync", "Workflow sync", 60, hub.sync, silent=True,
                        description="Workflows und Läufe aus den verbundenen Systemen holen",
                        enabled=hub.configured(), run_immediately=True)


MODULE = ModuleSpec(
    id="workflows", title="Workflows", router=router, icon="workflow", path="/workflows", order=50,
    description="Abläufe in n8n", on_startup=_startup,
    commands=[{"id": "workflows.open", "title": "Run Workflow", "path": "/workflows", "shortcut": "g w"}],
)
