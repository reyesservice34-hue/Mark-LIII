"""File center API — sandboxed workspace."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.files import WorkspaceError
from .. import ModuleSpec

router = APIRouter(prefix="/api/files", tags=["files"])


class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    path: str
    new_name: str


class MoveBody(BaseModel):
    path: str
    destination: str


class WriteBody(BaseModel):
    path: str
    content: str


class TerminalBody(BaseModel):
    path: str = ""
    command: str
    timeout: int = 30


def _wrap(fn):
    try:
        return fn()
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_dir(path: str = "", state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return _wrap(lambda: state.services["files"].list(path))


@router.get("/search")
async def search(q: str = Query(min_length=1), state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"results": state.services["files"].search(q)}


@router.get("/recent")
async def recent(source: str = "", limit: int = 30, state: AppState = Depends(get_state),
                 _: Principal = Depends(current_principal)):
    return {"files": state.services["files"].recent(limit=limit, source=source),
            "usage": state.services["files"].usage()}


@router.get("/preview")
async def preview(path: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return _wrap(lambda: state.services["files"].read_text(path, max_bytes=256 * 1024))


@router.get("/download")
async def download(path: str, inline: bool = False, state: AppState = Depends(get_state),
                   _: Principal = Depends(current_principal)):
    files = state.services["files"]
    target = _wrap(lambda: files.resolve(path, must_exist=True))
    if target.is_dir():
        raise HTTPException(status_code=400, detail="is a directory")
    headers = {"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{target.name}\""}
    return FileResponse(str(target), headers=headers)


@router.post("/terminal")
async def terminal(body: TerminalBody, state: AppState = Depends(get_state),
                   principal: Principal = Depends(require_role("admin"))):
    if not state.settings.allow_terminal:
        raise HTTPException(status_code=403, detail="terminal is disabled")
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is empty")
    if len(command) > 4000:
        raise HTTPException(status_code=400, detail="command is too long")
    files = state.services["files"]
    directory = _wrap(lambda: files.resolve(body.path, must_exist=True))
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="terminal path is not a directory")
    timeout = max(1, min(int(body.timeout or 30), 60))
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh", "-lc", command,
        cwd=str(directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    timed_out = False
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        output, _ = await proc.communicate()
    text = output.decode("utf-8", "replace")
    if len(text) > 64_000:
        text = text[-64_000:]
        text = "...[output truncated to last 64 KB]\n" + text
    state.log.audit(
        actor_type="user", actor_id=principal.actor, action="file.terminal",
        target=files.rel(directory), status="timeout" if timed_out else ("ok" if proc.returncode == 0 else "error"),
        meta={"exit_code": proc.returncode, "timeout": timed_out},
    )
    return {
        "cwd": files.rel(directory),
        "output": text,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
    }


@router.post("/upload", status_code=201)
async def upload(file: UploadFile = File(...), path: str = Form(""), state: AppState = Depends(get_state),
                 principal: Principal = Depends(require_role("operator"))):
    files = state.services["files"]
    data = []
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        data.append(chunk)
    try:
        info = files.save_upload(file.filename or "upload", iter(data), subdir=path or "uploads",
                                 owner=principal.actor)
    except WorkspaceError as e:
        state.log.audit(actor_type="user", actor_id=principal.actor, action="file.upload",
                        target=file.filename or "upload", status="denied", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    meta = {"size": info["size"]}
    if info.get("meta", {}).get("security", {}).get("flags"):
        meta["security_flags"] = info["meta"]["security"]["flags"]
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.upload", target=info["path"],
                    status="ok", meta=meta)
    return {"file": info}


@router.post("/write")
async def write(body: WriteBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    try:
        info = state.services["files"].write_text(body.path, body.content, source="user", owner=principal.actor)
    except WorkspaceError as e:
        state.log.audit(actor_type="user", actor_id=principal.actor, action="file.write",
                        target=body.path, status="denied", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.write", target=info["path"], status="ok")
    return {"file": info}


@router.post("/mkdir")
async def mkdir(body: PathBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    return {"entry": _wrap(lambda: state.services["files"].mkdir(body.path))}


@router.post("/rename")
async def rename(body: RenameBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    entry = _wrap(lambda: state.services["files"].rename(body.path, body.new_name))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.rename", target=body.path, status="ok",
                    result=entry["path"])
    return {"entry": entry}


@router.post("/move")
async def move(body: MoveBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    entry = _wrap(lambda: state.services["files"].move(body.path, body.destination))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.move", target=body.path, status="ok",
                    result=entry["path"])
    return {"entry": entry}


@router.get("/versions")
async def versions(path: str, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    return {"versions": _wrap(lambda: state.services["files"].versions(path))}


@router.post("/versions/{version_id}/restore")
async def restore_version(version_id: str, state: AppState = Depends(get_state),
                          principal: Principal = Depends(require_role("operator"))):
    info = _wrap(lambda: state.services["files"].restore_version(version_id, owner=principal.actor))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.version.restore",
                    target=info["path"], status="ok", meta={"version_id": version_id})
    state.bus.publish("file.changed", info)
    return {"file": info}


@router.post("/delete")
async def delete(body: PathBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    result = _wrap(lambda: state.services["files"].delete(body.path))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.delete", target=body.path, status="ok",
                    result=str(result))
    return {"result": result}


MODULE = ModuleSpec(
    id="files", title="Dateien", router=router, icon="folder", path="/files", order=80,
    description="Dateien im Arbeitsbereich",
    commands=[{"id": "files.search", "title": "Search Files", "path": "/files?focus=search", "shortcut": "g f"}],
)
