"""
Abgleich mit MIA-KNOWLEDGE-01 — Sicherung der Erinnerungen, Archiv der Gespräche.

Läuft von selbst alle paar Minuten (Scheduler) und ist aus, solange MIA_KNOWLEDGE_TOKEN fehlt. Hier gibt es nur
Einblick: was zuletzt gesendet wurde, ob KNOWLEDGE-01 erreichbar ist, einen Knopf zum sofortigen Abgleich und die
Bestätigung, dass eine große Löschung (z. B. „Gedächtnis leeren") auch in der Sicherung gelten soll.
Die Arbeit selbst steht in services/knowledge_sync.py.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import AppState, get_state, require_role
from ...services import knowledge_sync as ks
from .. import ModuleSpec

router = APIRouter(prefix="/api/knowledge-sync", tags=["knowledge-sync"])

DEFAULT_INTERVAL = 300


def _interval() -> int:
    try:
        return max(60, int(os.environ.get("MIA_KNOWLEDGE_SYNC_INTERVAL", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


_sync: ks.KnowledgeSync | None = None


def _require_sync() -> ks.KnowledgeSync:
    if _sync is None:
        raise HTTPException(409, "Abgleich ist nicht eingeschaltet (MIA_KNOWLEDGE_TOKEN fehlt)")
    return _sync


@router.get("/status")
async def status(state: AppState = Depends(get_state), _: Principal = Depends(require_role("viewer"))):
    if _sync is None:
        return {"enabled": False, "reason": "MIA_KNOWLEDGE_TOKEN nicht gesetzt oder MIA_KNOWLEDGE_SYNC=0"}
    return {"enabled": True, **_sync.overview()}


@router.post("/run")
async def run_now(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    sync = _require_sync()
    await sync.run_once()
    return {"enabled": True, **sync.overview()}


@router.post("/confirm-deletes")
async def confirm_deletes(state: AppState = Depends(get_state), principal: Principal = Depends(require_role("admin"))):
    """Die Sicherung soll eine angehaltene Massenlöschung mitmachen. Gilt für den nächsten Lauf, der sofort läuft."""
    sync = _require_sync()
    sync.confirm_deletes()
    state.log.audit(actor_type="user", actor_id=principal.actor, action="knowledge_sync.confirm_deletes",
                    status="ok", meta={})
    await sync.run_once()
    return {"enabled": True, **sync.overview()}


def _startup(state: AppState) -> None:
    global _sync
    if not ks.enabled():
        state.log.info("knowledge_sync", "Abgleich mit KNOWLEDGE-01 ist aus (MIA_KNOWLEDGE_TOKEN nicht gesetzt)")
        return
    _sync = ks.KnowledgeSync(state.db, state.log)

    async def job() -> None:
        await _sync.run_once()

    state.scheduler.add("knowledge_sync", "Abgleich mit KNOWLEDGE-01", _interval(), job, silent=True,
                        description="Sichert Erinnerungen und archiviert Gespräche auf MIA-KNOWLEDGE-01",
                        run_immediately=True)


MODULE = ModuleSpec(id="knowledge_sync", title="Abgleich KNOWLEDGE-01", router=router, nav=False, order=3,
                    description="Sicherung der Erinnerungen und Gesprächsarchiv auf MIA-KNOWLEDGE-01",
                    on_startup=_startup)
