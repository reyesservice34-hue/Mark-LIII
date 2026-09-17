"""Notification center API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state
from .. import ModuleSpec

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadBody(BaseModel):
    ids: list[str] = []
    read: bool = True


@router.get("")
async def list_notifications(unread: bool = False, category: str = "", limit: int = 100, before: str = "",
                             state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    svc = state.services["notifications"]
    return {"notifications": svc.list(principal.id, unread_only=unread, category=category, limit=limit, before=before),
            "unread": svc.unread_count(principal.id)}


@router.post("/read")
async def mark_read(body: ReadBody, state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    svc = state.services["notifications"]
    n = svc.mark_read(principal.id, body.ids or None, read=body.read)
    return {"updated": n, "unread": svc.unread_count(principal.id)}


@router.delete("/{notification_id}")
async def delete(notification_id: str, state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    if not state.services["notifications"].delete(principal.id, notification_id):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


MODULE = ModuleSpec(
    id="notifications", title="Notifications", router=router, icon="bell", path="/notifications", order=110,
    mobile_priority=80, description="Notification center",
)
