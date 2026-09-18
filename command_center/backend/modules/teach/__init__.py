"""
Teach module — record a demonstration, turn it into a procedure, run it later.

Procedures with a schedule trigger are run by the scheduler without being
asked, which is the proactive half of the feature: once JARVIS has learned
"every Monday morning, prepare the week's site list", it does it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.teaching import TeachingError
from .. import ModuleSpec

router = APIRouter(prefix="/api/teach", tags=["teach"])


class StartBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    goal: str = ""
    conversation_id: str | None = None


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DistillBody(BaseModel):
    create_agent: bool = True


class ProcedurePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    steps: list[dict] | None = None
    tools: list[str] | None = None
    trigger: dict | None = None
    enabled: bool | None = None


class RunBody(BaseModel):
    inputs: dict = Field(default_factory=dict)
    agent_id: str = ""


def _svc(state: AppState):
    return state.services["teaching"]


@router.get("/recordings")
async def recordings(state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    svc = _svc(state)
    return {"recordings": svc.list(), "active": svc.active_for(principal.id)}


@router.post("/recordings", status_code=201)
async def start(body: StartBody, state: AppState = Depends(get_state),
                principal: Principal = Depends(require_role("operator"))):
    try:
        recording = _svc(state).start(title=body.title, goal=body.goal, user_id=principal.id,
                                      actor=principal.actor, conversation_id=body.conversation_id)
    except TeachingError as e:
        raise HTTPException(status_code=409, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="teach.record.start",
                    target=recording["id"], status="ok")
    return {"recording": recording}


@router.get("/recordings/{recording_id}")
async def recording(recording_id: str, state: AppState = Depends(get_state),
                    _: Principal = Depends(current_principal)):
    rec = _svc(state).get(recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"recording": rec, "transcript": _svc(state).transcript(recording_id)}


@router.post("/recordings/{recording_id}/note")
async def note(recording_id: str, body: NoteBody, state: AppState = Depends(get_state),
               principal: Principal = Depends(require_role("operator"))):
    event = _svc(state).append(recording_id, kind="note", text=body.text, actor=principal.actor)
    if not event:
        raise HTTPException(status_code=409, detail="That recording is not running")
    return {"event": event}


@router.post("/recordings/{recording_id}/stop")
async def stop(recording_id: str, state: AppState = Depends(get_state),
               principal: Principal = Depends(require_role("operator"))):
    try:
        return {"recording": _svc(state).stop(recording_id, principal.id)}
    except TeachingError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/recordings/{recording_id}/distill", status_code=201)
async def distill(recording_id: str, body: DistillBody, state: AppState = Depends(get_state),
                  principal: Principal = Depends(require_role("operator"))):
    svc = _svc(state)
    if (svc.get(recording_id) or {}).get("status") == "recording":
        svc.stop(recording_id, principal.id)
    try:
        result = await svc.distill(state, recording_id, create_agent=body.create_agent,
                                   actor=principal.actor)
    except TeachingError as e:
        raise HTTPException(status_code=409, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="teach.distill",
                    target=result["procedure"]["id"], status="ok",
                    result=result["procedure"]["name"],
                    meta={"agent": (result.get("agent") or {}).get("id", "")})
    state.services["notifications"].notify(
        category="agent", severity="success", title=f"Learned: {result['procedure']['name']}",
        body=result["procedure"]["description"], link=f"/teach/{result['procedure']['id']}")
    return result


@router.get("/procedures")
async def procedures(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"procedures": _svc(state).procedures()}


@router.get("/procedures/{procedure_id}")
async def procedure(procedure_id: str, state: AppState = Depends(get_state),
                    _: Principal = Depends(current_principal)):
    proc = _svc(state).procedure(procedure_id)
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return {"procedure": proc, "briefing": _svc(state).briefing(proc)}


@router.patch("/procedures/{procedure_id}")
async def patch_procedure(procedure_id: str, body: ProcedurePatch, state: AppState = Depends(get_state),
                          principal: Principal = Depends(require_role("operator"))):
    svc = _svc(state)
    if not svc.procedure(procedure_id):
        raise HTTPException(status_code=404, detail="Procedure not found")
    proc = svc.update_procedure(procedure_id, **body.model_dump())
    state.services["scheduler_procedures"](state)      # re-arm schedules after a trigger change
    state.log.audit(actor_type="user", actor_id=principal.actor, action="procedure.update",
                    target=procedure_id, status="ok")
    return {"procedure": proc}


@router.delete("/procedures/{procedure_id}")
async def delete_procedure(procedure_id: str, state: AppState = Depends(get_state),
                           principal: Principal = Depends(require_role("operator"))):
    if not _svc(state).delete_procedure(procedure_id):
        raise HTTPException(status_code=404, detail="Procedure not found")
    state.services["scheduler_procedures"](state)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="procedure.delete",
                    target=procedure_id, status="ok")
    return {"ok": True}


@router.post("/procedures/{procedure_id}/run", status_code=202)
async def run_procedure(procedure_id: str, body: RunBody, state: AppState = Depends(get_state),
                        principal: Principal = Depends(require_role("operator"))):
    svc = _svc(state)
    proc = svc.procedure(procedure_id)
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")
    agent_id = body.agent_id or proc["agent_id"] or state.agents.master_id()
    spec = state.agents.get(agent_id)
    if not spec or not spec.enabled:
        raise HTTPException(status_code=400, detail=f"Agent '{agent_id}' is not available")
    briefing = svc.briefing(proc)
    if body.inputs:
        briefing += "\n\nValues for this run:\n" + "\n".join(f"- {k}: {v}" for k, v in body.inputs.items())
    task = state.services["tasks"].create(title=f"Procedure: {proc['name']}", description=briefing,
                                          created_by=principal.actor, assigned_agent=agent_id,
                                          meta={"procedure_id": procedure_id})
    svc.mark_run(procedure_id, "started")
    result = await state.runtime.start_task_run(state.services["tasks"].get(task["id"]), principal, agent_id)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="procedure.run",
                    target=procedure_id, status="ok", agent_id=agent_id, task_id=task["id"])
    return {"task": state.services["tasks"].get(task["id"]), **result}


def _arm_schedules(state: AppState) -> None:
    """(Re)register a scheduler job per scheduled procedure. This is the proactive
    half: a learned procedure with a schedule runs without being asked."""
    svc = state.services["teaching"]
    scheduler = state.scheduler
    wanted: dict[str, dict] = {}
    for proc in svc.procedures():
        trigger = proc.get("trigger") or {}
        if not proc["enabled"] or trigger.get("type") != "schedule":
            continue
        try:
            every = max(300.0, float(trigger.get("every_seconds") or 3600))
        except (TypeError, ValueError):
            continue
        wanted[f"procedure:{proc['id']}"] = {"proc": proc, "every": every}

    for job in scheduler.list():
        if job["id"].startswith("procedure:") and job["id"] not in wanted:
            scheduler.remove(job["id"])

    for job_id, cfg in wanted.items():
        proc = cfg["proc"]
        existing = scheduler.get(job_id)
        if existing and existing.interval == cfg["every"]:
            continue
        if existing:
            scheduler.remove(job_id)

        async def _run(procedure_id=proc["id"]) -> None:
            await _run_scheduled(state, procedure_id)

        scheduler.add(job_id, f"Procedure: {proc['name']}", cfg["every"], _run,
                      description=proc["description"] or proc["goal"], run_immediately=False)


async def _run_scheduled(state: AppState, procedure_id: str) -> None:
    from ...auth import Principal as P
    svc = state.services["teaching"]
    proc = svc.procedure(procedure_id)
    if not proc or not proc["enabled"]:
        return
    agent_id = proc["agent_id"] or state.agents.master_id()
    spec = state.agents.get(agent_id)
    if not spec or not spec.enabled:
        state.log.warning("teach", f"Scheduled procedure '{proc['name']}' skipped: agent unavailable")
        return
    principal = P(kind="system", id="system", name="Scheduler", role="operator", actor="scheduler")
    task = state.services["tasks"].create(title=f"Procedure: {proc['name']}",
                                          description=svc.briefing(proc), created_by="scheduler",
                                          assigned_agent=agent_id, meta={"procedure_id": procedure_id,
                                                                          "scheduled": True})
    svc.mark_run(procedure_id, "scheduled")
    state.log.info("teach", f"Running scheduled procedure '{proc['name']}'",
                   task_id=task["id"], agent_id=agent_id)
    await state.runtime.start_task_run(state.services["tasks"].get(task["id"]), principal, agent_id)


def _startup(state: AppState) -> None:
    state.services["scheduler_procedures"] = _arm_schedules
    _arm_schedules(state)


MODULE = ModuleSpec(
    id="teach", title="Teach", router=router, icon="graduation-cap", path="/teach", order=45,
    description="Show JARVIS once, keep the lesson", on_startup=_startup,
    commands=[{"id": "teach.record", "title": "Record a demonstration", "path": "/teach?record=1"},
              {"id": "teach.open", "title": "Open Teach", "path": "/teach"}],
)
