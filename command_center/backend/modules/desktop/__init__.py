"""
Desktop module — the runner's endpoints under /v1, the dashboard's under /api.

The runner on the PC authenticates with the same machine token the desktop
already uses for /v1/commands, so pairing is one token, not two.
"""
from __future__ import annotations

import os
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
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
    # Was der Rechner körperlich kann: Mikrofon, Lautsprecher, Tastatur- und
    # Mausteuerung, Bildschirm. Der Agent entscheidet danach, ob er etwas
    # vorschlagen kann — „ich sage es dir laut" auf einem Rechner ohne
    # Lautsprecher ist eine Zusage, die niemand hört.
    capabilities: dict = Field(default_factory=dict)
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
    meta = {**body.meta, "capabilities": body.capabilities} if body.capabilities else body.meta
    device = state.services["desktop"].register(
        name=body.name, actor=principal.actor, platform=body.platform, version=body.version,
        actions=body.actions, device_id=body.device_id, meta=meta)
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


# ── einen Rechner ankoppeln ──────────────────────────────────────────────
# Ein Gerät trägt sich nicht von Hand in eine Liste ein: Es meldet sich
# selbst, sobald auf ihm etwas läuft, das ein gültiges Token hat. „Gerät
# hinzufügen" heißt deshalb: ein Token erzeugen und den Weg zeigen, wie es
# auf den Rechner kommt. Genau das tut das hier — in einer Zeile zum
# Kopieren, statt in sechs Schritten zum Danebengehen.
class PairBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    system: str = "windows"


@router.post("/api/desktop/pair", status_code=201)
async def pair_device(body: PairBody, request: Request, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    """Ein Token erzeugen und den fertigen Installationsbefehl zurückgeben."""
    from ...services.installer import one_liner

    actor = "desktop-" + "".join(
        c if c.isalnum() else "-" for c in body.name.lower()).strip("-")[:40]
    try:
        row, raw = state.auth.create_api_token(
            name=f"Rechner {body.name}", actor=actor, role="operator",
            created_by=principal.actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    state.log.audit(actor_type="user", actor_id=principal.actor, action="desktop.pair",
                    target=actor, status="ok", meta={"token_id": row["id"], "device": body.name})
    base = _server_url(request)
    system = "windows" if body.system not in ("linux", "macos") else body.system
    return {"device_name": body.name, "token_id": row["id"], "actor": actor,
            "server_url": base, "system": system,
            "command": one_liner(base, raw, system),
            # Das Token steht im Befehl. Es wird HIER einmal gezeigt und nie
            # wieder — gespeichert ist nur sein Hash.
            "note": "Das Token steht im Befehl und wird nur dieses eine Mal gezeigt."}


def _server_url(request: Request) -> str:
    """Die Adresse, unter der dieser Server von außen erreichbar ist.

    Hinter einem Reverse Proxy ist das nicht die eigene Bindeadresse — der
    Rechner des Nutzers käme an 127.0.0.1:8080 nie heran. Also das, was der
    Proxy weitergibt, und nur ersatzweise das, was die Anfrage selbst sagt.
    """
    aus_env = os.environ.get("JARVIS_CC_PUBLIC_URL", "").strip()
    if aus_env:
        return aus_env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    if host:
        return f"{proto or 'https'}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


# Die Skripte selbst. Sie werden mit dem Token in der Adresse abgerufen — das
# ist zugleich der Nachweis, dass sie geholt werden darf: Wer kein gültiges
# Token hat, bekommt kein Skript. Das Skript enthält nichts, was nicht schon
# im Befehl stünde, den der Nutzer ohnehin in der Hand hat.
def _token_geprueft(state: AppState, token: str) -> None:
    if not token or state.auth.resolve_api_token(token) is None:
        raise HTTPException(status_code=401, detail="Ohne gültiges Gerätetoken gibt es kein Skript.")


@router.get("/api/desktop/bundle.zip")
async def desktop_bundle(token: str = "", state: AppState = Depends(get_state)):
    """Serve a minimal, current desktop bundle from the active MIA source.

    This deliberately excludes command_center, .env files, runtime data and
    config/api_keys.json so the desktop gets code, never server secrets.
    """
    _token_geprueft(state, token)
    from ...services.source import source_dir
    root = source_dir()
    if root is None:
        raise HTTPException(status_code=503, detail="MIA source directory is not available.")
    root = Path(root)
    include_dirs = ("actions", "core", "plugins", "memory")
    include_files = {
        "desktop_agent.py", "desktop_voice.py", "jarvis_desktop.py", "autostart.py",
        "check_connection.py", "install_desktop.py", "requirements.txt", "ui.py",
        "MIA.ps1", "MIA-Wake.ps1", "JARVIS.bat", "PRUEFEN.bat", "SPRECHEN.bat", "AUTOSTART.bat"
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(include_files):
            f = root / name
            if f.is_file():
                zf.write(f, name)
        cfg_init = root / "config" / "__init__.py"
        if cfg_init.is_file():
            zf.write(cfg_init, "config/__init__.py")
        for dname in include_dirs:
            base = root / dname
            if not base.is_dir():
                continue
            for f in base.rglob("*"):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                if f.name.lower().endswith((".env", ".key", ".pem")):
                    continue
                zf.write(f, f.relative_to(root).as_posix())
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=mia-desktop.zip"})


@router.get("/api/desktop/install.ps1")
async def install_powershell(request: Request, token: str = "", name: str = "",
                             state: AppState = Depends(get_state)):
    from ...services.installer import powershell
    _token_geprueft(state, token)
    return PlainTextResponse(powershell(_server_url(request), token, name),
                             media_type="text/plain; charset=utf-8")


@router.get("/api/desktop/install.sh")
async def install_shell(request: Request, token: str = "", name: str = "",
                        state: AppState = Depends(get_state)):
    from ...services.installer import shell
    _token_geprueft(state, token)
    return PlainTextResponse(shell(_server_url(request), token, name),
                             media_type="text/plain; charset=utf-8")


MODULE = ModuleSpec(
    id="desktop", title="Geräte", router=router, icon="monitor", path="/desktop", order=75,
    mobile_priority=30, description="Gekoppelte Rechner und was sie tun sollen",
    commands=[{"id": "desktop.open", "title": "Geräte öffnen", "path": "/desktop"}],
)
