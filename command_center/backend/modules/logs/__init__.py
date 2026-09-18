"""Log center + audit trail API."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def query(q: str = "", level: str = "", source: str = "", task_id: str = "", agent_id: str = "",
                run_id: str = "", before: str = "", limit: int = 200, state: AppState = Depends(get_state),
                _: Principal = Depends(current_principal)):
    rows = state.log.query(q=q, level=level, source=source, task_id=task_id, agent_id=agent_id, run_id=run_id,
                           before=before, limit=limit)
    return {"logs": rows, "next_before": rows[-1]["ts"] if len(rows) >= limit else None}


@router.get("/sources")
async def sources(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"sources": state.log.sources()}


@router.get("/audit")
async def audit(q: str = "", actor_id: str = "", agent_id: str = "", task_id: str = "", before: str = "",
                limit: int = 200, state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    rows = state.log.audit_query(q=q, actor_id=actor_id, agent_id=agent_id, task_id=task_id, before=before, limit=limit)
    return {"events": rows, "next_before": rows[-1]["ts"] if len(rows) >= limit else None}


MODULE = ModuleSpec(
    id="logs", title="Protokoll", router=router, icon="scroll-text", path="/logs", order=100,
    description="Protokoll und Prüfspur",
    commands=[{"id": "logs.open", "title": "View Logs", "path": "/logs", "shortcut": "g l"}],
)
