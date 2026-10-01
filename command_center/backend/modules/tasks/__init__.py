"""Task manager API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.tasks import STATUSES
from .. import ModuleSpec

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: str = "normal"
    assigned_agent: str = ""
    parent_id: str | None = None
    start: bool = True


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    assigned_agent: str | None = None
    status: str | None = None


@router.get("")
async def list_tasks(status: str = "", agent: str = "", q: str = "", limit: int = 100, offset: int = 0,
                     roots_only: bool = False, state: AppState = Depends(get_state),
                     _: Principal = Depends(current_principal)):
    svc = state.services["tasks"]
    return {"tasks": svc.list(status=status, agent=agent, q=q, limit=limit, offset=offset,
                              include_children=not roots_only), "counts": svc.counts()}


@router.get("/timeline")
async def timeline(limit: int = 30, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"tasks": state.services["tasks"].timeline(limit)}


@router.post("", status_code=201)
async def create_task(body: TaskCreate, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("operator"))):
    svc = state.services["tasks"]
    agent_id = state.agents.resolve(body.assigned_agent) or (state.agents.master_id() if body.start else "")
    task = svc.create(title=body.title, description=body.description, priority=body.priority,
                      assigned_agent=agent_id, created_by=principal.actor, parent_id=body.parent_id)
    state.log.audit(actor_type=principal.kind, actor_id=principal.actor, action="task.create", target=task["id"],
                    status="ok", task_id=task["id"], agent_id=agent_id)
    run = None
    if body.start and agent_id:
        spec = state.agents.get(agent_id)
        if not spec or not spec.enabled:
            raise HTTPException(status_code=400, detail=f"Agent '{agent_id}' is not available")
        run = (await state.runtime.start_task_run(task, principal, agent_id))["run"]
    return {"task": svc.get(task["id"]), "run": run}


@router.get("/{task_id}")
async def get_task(task_id: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    svc = state.services["tasks"]
    task = svc.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task["subtasks"] = svc.children(task_id)
    task["logs"] = svc.logs(task_id)
    task["runs"] = state.agents.runs(task_id=task_id, limit=20)
    task["approvals"] = [a for a in state.services["approvals"].list(limit=200) if a["task_id"] == task_id]
    task["files"] = [f for f in state.services["files"].recent(limit=200) if f.get("task_id") == task_id]
    return {"task": task}


@router.patch("/{task_id}")
async def patch_task(task_id: str, body: TaskPatch, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    svc = state.services["tasks"]
    if not svc.get(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    task = svc.update(task_id, title=body.title, description=body.description, priority=body.priority,
                      assigned_agent=body.assigned_agent)
    if body.status:
        if body.status.upper() not in STATUSES:
            raise HTTPException(status_code=400, detail="Unknown status")
        if body.status.upper() == "CANCELLED":
            task = svc.cancel(task_id, by=principal.actor)
            for run in state.runtime.active_runs():
                if run["task_id"] == task_id:
                    await state.runtime.cancel_run(run["id"], by=principal.actor)
        else:
            task = svc.set_status(task_id, body.status.upper(), note=f"Status set by {principal.actor}")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="task.update", target=task_id, status="ok",
                    task_id=task_id, meta={k: v for k, v in body.model_dump().items() if v is not None})
    return {"task": task}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("operator"))):
    svc = state.services["tasks"]
    if not svc.get(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    for run in state.runtime.active_runs():
        if run["task_id"] == task_id:
            await state.runtime.cancel_run(run["id"], by=principal.actor)
    return {"task": svc.cancel(task_id, by=principal.actor)}


@router.post("/{task_id}/retry", status_code=202)
async def retry_task(task_id: str, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    svc = state.services["tasks"]
    task = svc.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] not in ("FAILED", "CANCELLED", "COMPLETED", "QUEUED"):
        raise HTTPException(status_code=400, detail="Task is still active")
    agent_id = state.agents.resolve(task["assigned_agent"]) or state.agents.master_id()
    svc.set_status(task_id, "QUEUED", error="", note=f"Retry requested by {principal.actor}")
    result = await state.runtime.start_task_run(svc.get(task_id), principal, agent_id)
    return {"task": svc.get(task_id), **result}


@router.delete("/{task_id}")
async def delete_task(task_id: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("operator"))):
    """Eine Aufgabe samt ihrer Einträge löschen.

    Eine laufende wird vorher gestoppt: Ein Lauf, dessen Aufgabe verschwunden
    ist, schreibt ins Leere und taucht als Geist in der Zeitleiste wieder auf.
    """
    svc = state.services["tasks"]
    if not svc.get(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    for run in state.runtime.active_runs():
        if run["task_id"] == task_id:
            await state.runtime.cancel_run(run["id"], by=principal.actor)
    state.db.execute("DELETE FROM task_logs WHERE task_id=?", (task_id,))
    state.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="task.delete",
                    target=task_id, status="ok", task_id=task_id)
    state.bus.publish("task.deleted", {"id": task_id})
    return {"ok": True}


@router.get("/{task_id}/logs")
async def task_logs(task_id: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"logs": state.services["tasks"].logs(task_id)}


MODULE = ModuleSpec(
    id="tasks", title="Aufgaben", router=router, icon="list-checks", path="/tasks", order=40, mobile_priority=90,
    description="Was zu tun ist",
    commands=[{"id": "tasks.new", "title": "New Task", "path": "/tasks?new=1", "shortcut": "g t"},
              {"id": "tasks.open", "title": "Open Tasks", "path": "/tasks"}],
)
