"""
Erweiterungen: was JARVIS über seinen eigenen Quelltext hinaus kann.

Drei Arten, eine Seite:

* **MCP-Server** — fremde Werkzeugserver nach dem Model Context Protocol,
  dieselben, die auch Claude benutzt. Ihre Werkzeuge landen im selben
  Verzeichnis wie die eingebauten und laufen durch dieselbe Rollenprüfung und
  dasselbe Genehmigungstor.
* **Fähigkeiten** — Anleitungen als Text, die er bei Bedarf aufschlägt.
* **Selbstgeschriebene Werkzeuge** — was er sich selbst gebaut hat; hier nur
  zum Nachsehen, freigegeben wird über die Genehmigungen.

Nichts davon ist an einen Anbieter gebunden: Werkzeuge gehen als Deklaration
an das Modell, das gerade denkt, und Fähigkeiten sind Text. Ob Anthropic,
OpenAI, Gemini oder ein lokales Modell antwortet, ändert daran nichts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.mcp import McpError
from ...services.skills import SkillError
from .. import ModuleSpec

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=500)
    token: str = ""
    requires_approval: bool = True


class ServerPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    enabled: bool | None = None
    requires_approval: bool | None = None


class SkillBody(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    content: str = Field(min_length=1, max_length=60_000)


@router.get("")
async def overview(state: AppState = Depends(get_state),
                   _: Principal = Depends(current_principal)):
    """Alles auf einen Blick — und zwar gezählt, nicht behauptet."""
    mcp = state.services["mcp"]
    skills = state.services["skills"]
    servers = mcp.servers()
    self_tools = state.services["selfext"].list()
    return {
        "servers": servers,
        "skills": skills.all(),
        "self_tools": self_tools,
        "totals": {
            "mcp_tools": sum(s["tool_count"] for s in servers if s["enabled"]),
            "mcp_online": sum(1 for s in servers if s["status"] == "healthy"),
            "skills": sum(1 for s in skills.all() if s["enabled"]),
            "self_active": sum(1 for t in self_tools if t.get("status") == "active"),
            "tools_total": len(state.tools.available()),
        },
    }


# ── MCP ──────────────────────────────────────────────────────────────────
@router.post("/servers", status_code=201)
async def add_server(body: ServerCreate, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("admin"))):
    mcp = state.services["mcp"]
    try:
        created = mcp.add(name=body.name, url=body.url, token=body.token,
                          requires_approval=body.requires_approval, actor=principal.actor)
        # Gleich nachsehen, ob er antwortet: ein Eintrag, der erst beim nächsten
        # Neustart auffällt, ist schlechter als gar keiner.
        checked = await mcp.refresh(created["id"])
    except McpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.tools.snapshot(state.db)
    return {"server": checked}


@router.patch("/servers/{server_id}")
async def patch_server(server_id: str, body: ServerPatch, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("admin"))):
    mcp = state.services["mcp"]
    updated = mcp.update(server_id, **body.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Diesen Server gibt es nicht.")
    if updated["enabled"]:
        try:
            updated = await mcp.refresh(server_id)
        except McpError as e:
            raise HTTPException(status_code=400, detail=str(e))
    state.tools.snapshot(state.db)
    return {"server": updated}


@router.post("/servers/{server_id}/check")
async def check_server(server_id: str, state: AppState = Depends(get_state),
                       _: Principal = Depends(require_role("operator"))):
    try:
        server = await state.services["mcp"].refresh(server_id)
    except McpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.tools.snapshot(state.db)
    return {"server": server}


@router.delete("/servers/{server_id}")
async def remove_server(server_id: str, state: AppState = Depends(get_state),
                        principal: Principal = Depends(require_role("admin"))):
    if not state.services["mcp"].remove(server_id, actor=principal.actor):
        raise HTTPException(status_code=404, detail="Diesen Server gibt es nicht.")
    state.tools.snapshot(state.db)
    return {"ok": True}


# ── Fähigkeiten ──────────────────────────────────────────────────────────
@router.post("/skills", status_code=201)
async def save_skill(body: SkillBody, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    try:
        saved = state.services["skills"].save(
            name=body.name, title=body.title, description=body.description,
            content=body.content, actor=principal.actor)
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"skill": saved}


@router.patch("/skills/{name}")
async def toggle_skill(name: str, enabled: bool, state: AppState = Depends(get_state),
                       _: Principal = Depends(require_role("operator"))):
    updated = state.services["skills"].set_enabled(name, enabled)
    if updated is None:
        raise HTTPException(status_code=404, detail="Diese Fähigkeit gibt es nicht.")
    return {"skill": updated}


@router.delete("/skills/{name}")
async def remove_skill(name: str, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    if not state.services["skills"].remove(name, actor=principal.actor):
        raise HTTPException(status_code=404, detail="Diese Fähigkeit gibt es nicht.")
    return {"ok": True}


def _on_startup(state) -> None:
    """Eingetragene MCP-Server nach einem Neustart wieder abklopfen.

    Im Hintergrund, nicht im Startpfad: Ein fremder Server, der hängt, darf
    nicht dazu führen, dass das eigene Dashboard nicht hochkommt.
    """
    import asyncio

    async def load() -> None:
        try:
            got = await state.services["mcp"].load_all()
            if got:
                state.tools.snapshot(state.db)
                state.log.info("mcp", f"{got} MCP-Server wieder angebunden")
        except Exception as e:  # noqa: BLE001
            state.log.warning("mcp", f"MCP-Server konnten nicht geladen werden: {e}")

    try:
        asyncio.get_running_loop().create_task(load())
    except RuntimeError:
        pass      # kein laufender Loop (Test ohne Lifespan) — dann eben nicht


MODULE = ModuleSpec(
    id="extensions", title="Erweiterungen", router=router, icon="puzzle", path="/extensions",
    order=62, min_role="operator",
    description="MCP-Server, Fähigkeiten und selbstgeschriebene Werkzeuge",
    commands=[{"id": "extensions.open", "title": "Erweiterungen öffnen", "path": "/extensions"}],
    on_startup=_on_startup,
)
