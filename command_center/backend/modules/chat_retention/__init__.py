"""
Gesprächsgedächtnis auf Zeit — Verläufe ein, zwei Tage behalten, dann daraus lernen und löschen.

Läuft von selbst (Scheduler) und nur, solange kein Lauf aktiv ist. Hier gibt es nur Einblick und einen Knopf
zum sofortigen Durchlauf. Die Arbeit selbst steht in services/chat_retention.py.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import AppState, get_state, require_role
from ...services import chat_retention as cr
from .. import ModuleSpec

router = APIRouter(prefix="/api/chat-retention", tags=["chat-retention"])

DEFAULT_INTERVAL = 1800
LEARNING_MODE_KEY = "learning_mode_enabled"   # derselbe Schalter wie auf der Gedächtnis-Seite

_retention: cr.ChatRetention | None = None


def _interval() -> int:
    try:
        return max(300, int(os.environ.get("MIA_RETENTION_INTERVAL", DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


@router.get("/status")
async def status(state: AppState = Depends(get_state), _: Principal = Depends(require_role("viewer"))):
    if _retention is None:
        return {"enabled": False, "reason": "MIA_RETENTION=0 oder LOCAL_LLM_URL nicht gesetzt"}
    return {"enabled": True, **_retention.status}


@router.post("/run")
async def run_now(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    if _retention is None:
        raise HTTPException(409, "Gesprächsgedächtnis ist nicht eingeschaltet")
    return {"enabled": True, **await _retention.run_once()}


def _startup(state: AppState) -> None:
    global _retention
    if not cr.enabled():
        state.log.info("chat_retention", "Gesprächsgedächtnis ist aus (MIA_RETENTION=0 oder LOCAL_LLM_URL fehlt)")
        return
    _retention = cr.ChatRetention(state.db, state.log, active_runs=state.runtime.active_runs,
                                  learning_on=lambda: bool(state.db.get_setting(LEARNING_MODE_KEY, False)))

    async def job() -> None:
        await _retention.run_once()

    state.scheduler.add("chat_retention", "Gespräche lernen und aufräumen", _interval(), job, silent=True,
                        description=f"Verläufe älter als {cr.hours():g} Stunden: Dauerhaftes ins Gedächtnis, dann löschen",
                        run_immediately=False, backoff_max=3600)


MODULE = ModuleSpec(id="chat_retention", title="Gesprächsgedächtnis", router=router, nav=False, order=3,
                    description="Lernt aus alten Gesprächen und löscht sie nach ein, zwei Tagen", on_startup=_startup)
