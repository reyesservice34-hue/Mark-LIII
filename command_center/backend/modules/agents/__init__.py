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


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    instructions: str | None = Field(default=None, max_length=20_000)
    tools: list[str] | None = None


@router.patch("/agents/{agent_id}")
async def patch_agent(agent_id: str, body: AgentPatch, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    """Einen Agenten ändern — vor allem seine Anweisungen.

    Ein aus einer Vorführung gelernter Spezialist hat gelegentlich einen Satz
    drin, der so nicht gemeint war. Ihn deswegen wegzuwerfen und die ganze
    Vorführung zu wiederholen, wäre viel Arbeit für einen Halbsatz.
    """
    if not state.agents.get(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        spec = state.agents.update(agent_id, **body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="agent.update",
                    target=agent_id, status="ok",
                    meta={k: v for k, v in body.model_dump().items() if v is not None})
    state.bus.publish("agent.updated", {"id": agent_id, "name": spec.name})
    return {"agent": state.agents.public(agent_id, state.tools, _provider_info(state))}


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("admin"))):
    """Einen selbst angelegten Agenten entfernen. Eingebaute bleiben."""
    spec = state.agents.get(agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Agent not found")
    name = spec.name
    try:
        state.agents.unregister(agent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="agent.delete",
                    target=agent_id, status="ok", meta={"name": name})
    state.bus.publish("agent.removed", {"id": agent_id, "name": name})
    return {"ok": True, "removed": agent_id}


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
    id="agents", title="Agenten", router=router, icon="bot", path="/agents", order=30, mobile_priority=60,
    description="Leitstand der Agenten",
    commands=[{"id": "agents.open", "title": "Open Agents", "path": "/agents", "shortcut": "g a"}],
)
