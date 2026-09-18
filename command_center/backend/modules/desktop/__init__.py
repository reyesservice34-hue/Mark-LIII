"""
Desktop module — the runner's endpoints under /v1, the dashboard's under /api.

The runner on the PC authenticates with the same machine token the desktop
already uses for /v1/commands, so pairing is one token, not two.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.desktop_bridge import DesktopError
from .. import ModuleSpec

router = APIRouter(tags=["desktop"])


class RegisterBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    platform: str = ""
    version: str = ""
    device_id: str = ""
    actions: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


class ResultBody(BaseModel):
    command_id: str
    ok: bool = True
    result: str = ""
    error: str = ""


class CommandBody(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)
    timeout: float = 90.0


# ── runner endpoints (machine token) ────────────────────────────────────────

@router.post("/v1/desktop/register", status_code=201)
async def register(body: RegisterBody, state: AppState = Depends(get_state),
                   principal: Principal = Depends(current_principal)):
    if principal.role == "viewer":
        raise HTTPException(status_code=403, detail="token lacks operator role")
    device = state.services["desktop"].register(
        name=body.name, actor=principal.actor, platform=body.platform, version=body.version,
        actions=body.actions, device_id=body.device_id, meta=body.meta)
    state.log.audit(actor_type=principal.kind, actor_id=principal.actor, action="desktop.register",
                    target=device["name"], status="ok", meta={"device_id": device["id"],
                                                              "actions": len(body.actions)})
    return {"device_id": device["id"], "name": device["name"], "poll_seconds": 25}


@router.get("/v1/desktop/poll")
async def poll(device_id: str, wait: float = 25.0, state: AppState = Depends(get_state),
               principal: Principal = Depends(current_principal)):
    bridge = state.services["desktop"]
    device = bridge.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="unknown device — register again")
    if device["actor"] and principal.kind == "token" and device["actor"] != principal.actor:
        raise HTTPException(status_code=403, detail="this device belongs to another token")
    command = await bridge.wait_for_work(device_id, wait)
    if not command:
        return {"command": None}
    return {"command": {"id": command["id"], "action": command["action"], "params": command["params"]}}


@router.post("/v1/desktop/result")
async def result(body: ResultBody, state: AppState = Depends(get_state),
                 principal: Principal = Depends(current_principal)):
    command = state.services["desktop"].complete(body.command_id, ok=body.ok, result=body.result,
                                                 error=body.error)
    if not command:
        raise HTTPException(status_code=404, detail="unknown command")
    teaching = state.services.get("teaching")
    if teaching:
        teaching.record_desktop(command, actor=principal.actor)
    return {"ok": True}


# ── dashboard endpoints ─────────────────────────────────────────────────────

@router.get("/api/desktop/devices")
async def devices(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    bridge = state.services["desktop"]
    return {"devices": bridge.list(), "stats": bridge.stats(),
            "history": bridge.history(limit=30)}


@router.post("/api/desktop/devices/{device_id}/command")
async def send_command(device_id: str, body: CommandBody, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    bridge = state.services["desktop"]
    try:
        command = await bridge.dispatch(device_id=device_id, action=body.action, params=body.params,
                                        requested_by=principal.actor, timeout=body.timeout)
    except DesktopError as e:
        state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.command",
                        target=f"{device_id}:{body.action}", status="error", error=str(e))
        raise HTTPException(status_code=409, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.command",
                    target=f"{device_id}:{body.action}", status=command["status"],
                    result=command["result"][:500], error=command["error"])
    return {"command": command}


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/api/desktop/devices/{device_id}")
async def rename_device(device_id: str, body: RenameBody, state: AppState = Depends(get_state),
                        principal: Principal = Depends(require_role("operator"))):
    device = state.services["desktop"].rename(device_id, body.name)
    if device is None:
        raise HTTPException(status_code=404, detail="Dieses Gerät gibt es nicht.")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.rename",
                    target=device_id, status="ok", result=body.name)
    return {"device": device}


@router.delete("/api/desktop/devices/{device_id}")
async def forget_device(device_id: str, state: AppState = Depends(get_state),
                        principal: Principal = Depends(require_role("operator"))):
    if not state.services["desktop"].forget(device_id):
        raise HTTPException(status_code=404, detail="Dieses Gerät gibt es nicht.")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.forget",
                    target=device_id, status="ok")
    return {"ok": True}


@router.post("/api/desktop/devices/{device_id}/screen")
async def screen(device_id: str, state: AppState = Depends(get_state),
                 principal: Principal = Depends(require_role("operator"))):
    """Ein Bildschirmfoto holen und im Arbeitsbereich ablegen.

    Denselben Weg nimmt das Werkzeug desktop.screen. Hier ohne Modell
    dazwischen: Du willst nachsehen, nicht fragen.
    """
    import base64
    import json as _json
    from pathlib import Path as _Path

    bridge = state.services["desktop"]
    device = bridge.get(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Dieses Gerät gibt es nicht.")
    if not any(a.get("name") == "screen_capture" for a in device["actions"]):
        raise HTTPException(status_code=409, detail=(
            "Dieser Rechner kann noch keine Bildschirmfotos schicken. Dort einmal git pull "
            "und JARVIS neu starten."))
    try:
        cmd = await bridge.dispatch(device_id=device_id, action="screen_capture", params={},
                                    requested_by=principal.actor, timeout=60)
    except DesktopError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if cmd["status"] != "done":
        raise HTTPException(status_code=409, detail=cmd["error"] or "Der PC hat kein Bild geschickt.")
    try:
        payload = _json.loads(cmd["result"] or "{}")
    except ValueError:
        raise HTTPException(status_code=502, detail="Der PC antwortete nicht in JSON.")
    if payload.get("error"):
        raise HTTPException(status_code=409, detail=str(payload["error"]))
    raw = payload.get("image_base64") or ""
    if not raw:
        raise HTTPException(status_code=502, detail="Der PC schickte kein Bild.")

    name = f"bildschirm-{device['name'].lower().replace(' ', '-')}.jpg"
    files = state.services["files"]
    target = _Path(files.root) / "downloads" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(raw))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.screen",
                    target=device_id, status="ok", result=f"downloads/{name}")
    return {"path": f"downloads/{name}", "width": payload.get("width"),
            "height": payload.get("height"), "bytes": payload.get("bytes")}


@router.get("/api/desktop/history")
async def history(device_id: str = "", limit: int = 50, state: AppState = Depends(get_state),
                  _: Principal = Depends(current_principal)):
    return {"history": state.services["desktop"].history(device_id, limit)}


MODULE = ModuleSpec(
    id="desktop", title="Geräte", router=router, icon="monitor", path="/desktop", order=75,
    mobile_priority=30, description="Gekoppelte Rechner und was sie tun sollen",
    commands=[{"id": "desktop.open", "title": "Geräte öffnen", "path": "/desktop"}],
)
