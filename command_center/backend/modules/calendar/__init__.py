"""
Kalender: die Termine, mit denen er ohnehin schon arbeitet — jetzt auch sichtbar.

Den Dienst gibt es längst (lokaler Speicher auf dem Server, Google Calendar
wenn verbunden), und der Master Agent legt darüber Termine an. Nur nachsehen
konnte man nirgends, außer ihn zu fragen. Das ist die Lücke, die diese Seite
schließt — dieselben Daten, derselbe Dienst, kein zweiter Weg.

Ist nichts konfiguriert, sagt die Seite das und nennt die fehlende Variable,
statt einen leeren Kalender zu zeigen, der wie „keine Termine" aussieht.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    when: str = Field(min_length=1, max_length=100)     # "morgen", "2026-09-20", "Freitag 14:00"
    at: str = ""                                        # "14:00", falls nicht in `when`
    duration: str | int = 60
    location: str = ""
    notes: str = ""
    category: str = ""                                  # tour | ich | privat


class EventUpdate(BaseModel):
    title: str | None = None
    when: str | None = None
    at: str | None = None
    duration: str | int | None = None
    location: str | None = None
    notes: str | None = None
    category: str | None = None


class EventMove(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    when: str = Field(min_length=1, max_length=100)
    at: str = ""


@router.get("")
async def list_events(days: int = 14, q: str = "", start: str = "", end: str = "",
                      state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    cal = state.services["calendar"]
    if not cal.available():
        return {"available": False, "detail": cal.unavailable_reason(), "backend": "",
                "events": [], "days": days}
    try:
        if start and end:                                   # Kalenderansicht: beliebiger Zeitraum
            events, backend = await cal.range(start, end, query=q)
        else:
            events, backend = await cal.list(days=max(1, min(days, 90)), query=q)
    except Exception as e:  # noqa: BLE001
        return {"available": False, "detail": f"{e.__class__.__name__}: {e}", "backend": "",
                "events": [], "days": days}
    return {"available": True, "detail": "", "backend": backend, "events": events, "days": days}


@router.get("/health")
async def health(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return await state.services["calendar"].health()


@router.post("", status_code=201)
async def create_event(body: EventCreate, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    cal = state.services["calendar"]
    try:
        event, backend, note = await cal.create(title=body.title, when=body.when, at=body.at,
                                                duration=body.duration, location=body.location,
                                                notes=body.notes, category=body.category)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="calendar.create",
                    target=body.title, status="ok", meta={"backend": backend})
    state.bus.publish("calendar.changed", {"action": "create", "title": body.title})
    return {"event": event, "backend": backend, "note": note}


@router.put("/{uid}")
async def update_event(uid: str, body: EventUpdate, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    try:
        event = await state.services["calendar"].update_any(uid, **body.model_dump())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="calendar.update",
                    target=event.get("title", uid), status="ok")
    state.bus.publish("calendar.changed", {"action": "update", "title": event.get("title", "")})
    return {"event": event}


@router.delete("/{uid}")
async def delete_event(uid: str, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    try:
        event = await state.services["calendar"].delete_any(uid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="calendar.delete",
                    target=event.get("title", uid), status="ok")
    state.bus.publish("calendar.changed", {"action": "delete", "title": event.get("title", "")})
    return {"event": event}


@router.post("/move")
async def move_event(body: EventMove, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    try:
        event, backend = await state.services["calendar"].move(body.query, body.when, at=body.at)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="calendar.move",
                    target=body.query, status="ok", meta={"backend": backend})
    state.bus.publish("calendar.changed", {"action": "move", "title": body.query})
    return {"event": event, "backend": backend}


@router.delete("")
async def cancel_event(query: str, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    """Absagen über den Titel — so, wie der Dienst es auch für den Agenten tut.

    Ein Termin aus Google trägt eine fremde Kennung, ein lokaler eine eigene;
    beide über denselben Titel zu suchen hält die Seite von der Frage frei,
    welcher Speicher gerade dahintersteht.
    """
    try:
        event, backend = await state.services["calendar"].cancel(query)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="calendar.cancel",
                    target=query, status="ok", meta={"backend": backend})
    state.bus.publish("calendar.changed", {"action": "cancel", "title": query})
    return {"event": event, "backend": backend}


def _startup(state: AppState) -> None:
    from ...services.classify import make_classifier
    cal = state.services["calendar"]
    cal.classifier = make_classifier(state)                  # erkennt beim Eintragen: was, wie, wo

    async def job() -> None:
        await cal.sync_google()

    state.scheduler.add("calendar_sync", "Google-Termine übernehmen", 180, job, silent=True,
                        description="Termine aus dem Google Kalender in den lokalen Kalender übernehmen (Spiegel; "
                                    "Änderungen von Jarvis gehen zuerst nach Google)",
                        enabled=cal.bridge.configured(), run_immediately=True)


MODULE = ModuleSpec(
    id="calendar", title="Kalender", router=router, icon="calendar", path="/calendar", order=35,
    mobile_priority=40, description="Termine — lokal oder über Google", on_startup=_startup,
    commands=[{"id": "calendar.open", "title": "Kalender öffnen", "path": "/calendar", "shortcut": "g k"}],
)
