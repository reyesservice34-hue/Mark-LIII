"""Nur-Lese-Ansicht der übernommenen Hermes-Daten (Sitzungen, Nachrichten, Aufgaben).

Nicht in DEFAULT_MODULES eingetragen: aktiv wird das Modul erst, wenn es in
JARVIS_CC_MODULES ergänzt wird. Die Daten werden nur gelesen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...auth import Principal
from ...deps import current_principal
from ...services import hermes_archive
from .. import ModuleSpec

router = APIRouter(prefix="/api/hermes", tags=["hermes"])


@router.get("/summary")
async def summary(_: Principal = Depends(current_principal)):
    return hermes_archive.summary()


@router.get("/sessions")
async def sessions(limit: int = Query(50, ge=1, le=200), _: Principal = Depends(current_principal)):
    return {"items": hermes_archive.list_sessions(limit)}


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str, limit: int = Query(200, ge=1, le=500),
                   _: Principal = Depends(current_principal)):
    return {"items": hermes_archive.session_messages(session_id, limit)}


@router.get("/tasks")
async def tasks(limit: int = Query(100, ge=1, le=500), _: Principal = Depends(current_principal)):
    return {"items": hermes_archive.list_tasks(limit)}


MODULE = ModuleSpec(id="hermes_archive", title="Hermes-Archiv", router=router, nav=False, order=900)
