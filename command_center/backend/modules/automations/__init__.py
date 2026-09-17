"""Automations: the command center's own background jobs + housekeeping."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/automations", tags=["automations"])


@router.get("/jobs")
async def jobs(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"jobs": state.scheduler.list()}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str, state: AppState = Depends(get_state),
                  principal: Principal = Depends(require_role("operator"))):
    job = await state.scheduler.run_now(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="job.run", target=job_id,
                    status=job.last_status)
    return {"job": job.public()}


@router.post("/jobs/{job_id}/enable")
async def enable_job(job_id: str, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("admin"))):
    job = state.scheduler.set_enabled(job_id, True)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="job.enable", target=job_id, status="ok")
    return {"job": job.public()}


@router.post("/jobs/{job_id}/disable")
async def disable_job(job_id: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    job = state.scheduler.set_enabled(job_id, False)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="job.disable", target=job_id, status="ok")
    return {"job": job.public()}


def _startup(state: AppState) -> None:
    sched = state.scheduler
    metrics = state.services["metrics"]
    approvals = state.services["approvals"]

    def sample():
        point = metrics.sample()
        state.bus.publish("server.metrics", point)

    sched.add("metrics_sample", "Server metrics sampler", 5, sample, silent=True,
              description="Sample CPU, RAM, disk, load and network every 5 s")
    sched.add("metrics_persist", "Metrics history", 60, metrics.persist_point, silent=True,
              description="Persist one metrics point per minute for analytics (14 days)")
    sched.add("integration_health", "Integration health checks", 180, state.integrations.check_all, silent=True,
              description="Verify every configured integration against its live API")
    sched.add("docker_probe", "Docker engine probe", 60, lambda: metrics.docker_containers(with_stats=False),
              silent=True, description="Keep the docker engine state current for the status bar")
    sched.add("master_health", "Master agent provider check", 300, state.runtime.check_provider, silent=True,
              description="Ping the configured AI provider")
    sched.add("approval_expiry", "Expire stale approvals", 60, approvals.expire_overdue, silent=True,
              description="Approvals nobody decided within the timeout are expired")
    sched.add("session_purge", "Purge expired sessions", 3600, state.auth.purge_expired_sessions, silent=True,
              description="Remove expired login sessions", run_immediately=False)
    sched.add("log_trim", "Log retention", 600, state.log.trim, silent=True,
              description=f"Keep the newest {state.settings.log_retention_rows} log rows", run_immediately=False)


MODULE = ModuleSpec(
    id="automations", title="Automations", router=router, icon="timer", path="/automations", order=60,
    description="Background jobs and schedules", on_startup=_startup,
)
