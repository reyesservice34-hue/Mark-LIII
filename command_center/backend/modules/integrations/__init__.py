"""Integration registry API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
async def list_integrations(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"integrations": state.integrations.list(), "summary": state.integrations.summary()}


@router.post("/{integration_id}/check")
async def check(integration_id: str, state: AppState = Depends(get_state),
                principal: Principal = Depends(require_role("operator"))):
    try:
        result = await state.integrations.check(integration_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Integration not found")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="integration.check", target=integration_id,
                    status=result["status"], result=result.get("detail", ""))
    return {"integration": result}


@router.post("/check-all")
async def check_all(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    return {"integrations": await state.integrations.check_all()}


MODULE = ModuleSpec(
    id="integrations", title="Integrations", router=router, icon="plug", path="/integrations", order=90,
    description="Connected services",
)
