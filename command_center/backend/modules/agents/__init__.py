"""Agent control center + tool registry API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api", tags=["agents"])


class AssignBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: str = "normal"


def _provider_info(state: AppState) -> dict:
    return (state.runtime.status().get("provider") or {})


@router.get("/agents")
async def list_agents(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"agents": state.agents.list(state.tools, _provider_info(state)), "master": state.runtime.status()}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    agent = state.agents.public(agent_id, state.tools, _provider_info(state))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    spec = state.agents.get(agent_id)
    agent["instructions"] = spec.instructions if spec else ""
    agent["config"] = spec.config if spec else {}
    agent["runs"] = state.agents.runs(agent_id, limit=30)
    agent["tasks"] = state.services["tasks"].list(agent=agent_id, limit=30)
    return {"agent": agent}


@router.post("/agents/{agent_id}/enable")
async def enable_agent(agent_id: str, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("admin"))):
    if not state.agents.set_enabled(agent_id, True):
        raise HTTPException(status_code=404, detail="Agent not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="agent.enable", target=agent_id, status="ok")
    return {"agent": state.agents.public(agent_id, state.tools, _provider_info(state))}


@router.post("/agents/{agent_id}/disable")
async def disable_agent(agent_id: str, state: AppState = Depends(get_state),
                        principal: Principal = Depends(require_role("admin"))):
    spec = state.agents.get(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if spec.kind == "master":
        raise HTTPException(status_code=400, detail="The master agent cannot be disabled")
    state.agents.set_enabled(agent_id, False)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="agent.disable", target=agent_id, status="ok")
    return {"agent": state.agents.public(agent_id, state.tools, _provider_info(state))}


@router.post("/agents/{agent_id}/stop")
async def stop_agent(agent_id: str, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    stopped = 0
    for run in state.runtime.active_runs():
        if run["agent_id"] == agent_id and await state.runtime.cancel_run(run["id"], by=principal.actor):
            stopped += 1
    return {"stopped": stopped}


@router.post("/agents/{agent_id}/assign", status_code=202)
async def assign(agent_id: str, body: AssignBody, state: AppState = Depends(get_state),
                 principal: Principal = Depends(require_role("operator"))):
    spec = state.agents.get(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not spec.enabled:
        raise HTTPException(status_code=400, detail="Agent is disabled")
    task = state.services["tasks"].create(title=body.title, description=body.description,
                                          created_by=principal.actor, assigned_agent=agent_id,
                                          priority=body.priority)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="task.assign", target=task["id"],
                    status="ok", agent_id=agent_id, task_id=task["id"])
    result = await state.runtime.start_task_run(task, principal, agent_id)
    return {"task": state.services["tasks"].get(task["id"]), **result}


@router.get("/agents/{agent_id}/runs")
async def agent_runs(agent_id: str, limit: int = 50, state: AppState = Depends(get_state),
                     _: Principal = Depends(current_principal)):
    return {"runs": state.agents.runs(agent_id, limit=limit)}


@router.get("/runs")
async def runs(limit: int = 50, task_id: str = "", state: AppState = Depends(get_state),
               _: Principal = Depends(current_principal)):
    return {"runs": state.agents.runs(limit=limit, task_id=task_id), "active": state.runtime.active_runs()}


@router.get("/tools")
async def tools(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"tools": [t.public() for t in state.tools.all()], "categories": state.tools.categories(),
            "approval_threshold": state.tools.approval_threshold}


@router.post("/master/check")
async def check_master(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    health = await state.runtime.check_provider()
    return {"health": health, "master": state.runtime.status()}


MODULE = ModuleSpec(
    id="agents", title="Agents", router=router, icon="bot", path="/agents", order=30, mobile_priority=60,
    description="Agent control center",
    commands=[{"id": "agents.open", "title": "Open Agents", "path": "/agents", "shortcut": "g a"}],
)
