"""Agenten Office — Board-Übersicht aller Agenten als Abteilungen, plus Rechte pro Agent (und für Mia selbst).

Zeigt nur echte Daten aus der Agent Registry (Status, aktuelle Aufgabe, Queue-Länge, Werkzeuge) — keine
erfundenen Karten. Die Abteilungs-Zuordnung ist eine reine Anzeige-Gruppierung der real registrierten
Agenten nach ihrer tatsächlichen Rolle, kein eigenes Agentensystem. Die Rechte-Endpunkte schreiben direkt
in die Agent Registry (`AgentSpec.config["rights"]`) und werden vom `ToolExecutor` bei jedem Werkzeugaufruf
durchgesetzt (siehe `orchestrator/runtime.py`) — ein Toggle hier ohne Wirkung dort gibt es nicht.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/agents-office", tags=["agents-office"])

# Abteilung je Agent — abgeleitet aus der tatsächlichen Rolle in agent_registry.DEFAULT_AGENTS.
# Ein Agent, der dort nicht gelistet ist (z. B. selbst angelegt), bekommt "Sonstiges".
_DEPARTMENTS: dict[str, str] = {
    "master": "Koordination",
    "coding": "Entwicklung",
    "research": "Recherche",
    "server": "IT",
    "document": "Dokumente",
    "automation": "Automatisierung",
    "reyes-service": "Kundenservice",
    "email": "Kommunikation",
    "calendar": "Planung",
    "buchhaltung": "Finanzen",
}


def _provider_info(state: AppState) -> dict:
    return (state.runtime.status().get("provider") or {})


@router.get("")
async def board(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    agents = state.agents.list(state.tools, _provider_info(state))
    tasks = state.services["tasks"]
    out = []
    for a in agents:
        queue = len(tasks.list(agent=a["id"], status="active", limit=500))
        out.append({**a, "department": _DEPARTMENTS.get(a["id"], "Sonstiges"), "queue": queue,
                    "connected_to_mia": a["id"] == state.agents.master_id() or a["kind"] != "master"})
    return {"agents": out, "departments": sorted(set(_DEPARTMENTS.values()) | {d["department"] for d in out}),
            "categories": state.tools.categories(), "autonomy_levels": list(state.agents.AUTONOMY_LEVELS)}


@router.get("/{agent_id}/rights")
async def get_rights(agent_id: str, state: AppState = Depends(get_state),
                     _: Principal = Depends(current_principal)):
    if not state.agents.get(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "rights": state.agents.get_rights(agent_id),
            "categories": state.tools.categories(), "autonomy_levels": list(state.agents.AUTONOMY_LEVELS)}


class RightsBody(BaseModel):
    autonomy: str | None = None
    categories: list[str] | None = None


@router.put("/{agent_id}/rights")
async def put_rights(agent_id: str, body: RightsBody, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("admin"))):
    """Rechte setzen — auch für Mia selbst (id "master"), wie vom Nutzer gefordert."""
    if not state.agents.get(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        state.agents.set_rights(agent_id, autonomy=body.autonomy, categories=body.categories)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="agent.rights.update",
                    target=agent_id, status="ok", meta=body.model_dump(exclude_none=True))
    state.bus.publish("agent.updated", {"id": agent_id})
    return {"agent_id": agent_id, "rights": state.agents.get_rights(agent_id)}


MODULE = ModuleSpec(
    id="agents_office", title="Agenten Office", router=router, icon="layout-grid", path="/agents-office",
    order=32, mobile_priority=0, description="Board-Übersicht aller Agenten als Abteilungen, inkl. Rechte",
    commands=[{"id": "agents_office.open", "title": "Open Agenten Office", "path": "/agents-office"}],
)
