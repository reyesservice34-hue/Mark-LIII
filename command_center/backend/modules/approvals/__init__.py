"""Approval gate API — decisions are enforced by the orchestrator, not the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecisionBody(BaseModel):
    note: str = ""


@router.get("")
async def list_approvals(status: str = "", limit: int = 100, state: AppState = Depends(get_state),
                         _: Principal = Depends(current_principal)):
    svc = state.services["approvals"]
    return {"approvals": svc.list(status=status, limit=limit), "pending": svc.pending_count()}


@router.get("/{approval_id}")
async def get_approval(approval_id: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    a = state.services["approvals"].get(approval_id)
    if not a:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval": a}


@router.post("/{approval_id}/approve")
async def approve(approval_id: str, body: DecisionBody, state: AppState = Depends(get_state),
                  principal: Principal = Depends(require_role("operator"))):
    a = state.services["approvals"].decide(approval_id, approve=True, decided_by=principal.actor, note=body.note)
    if not a:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval": a}


@router.post("/{approval_id}/reject")
async def reject(approval_id: str, body: DecisionBody, state: AppState = Depends(get_state),
                 principal: Principal = Depends(require_role("operator"))):
    a = state.services["approvals"].decide(approval_id, approve=False, decided_by=principal.actor, note=body.note)
    if not a:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {"approval": a}


MODULE = ModuleSpec(id="approvals", title="Freigaben", router=router, icon="shield-check", path="/approvals",
                    order=115, nav=False, mobile_priority=70, description="Wartende Freigaben")
