"""File center API — sandboxed workspace."""
from __future__ import annotations

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
    info = _wrap(lambda: files.save_upload(file.filename or "upload", iter(data), subdir=path or "uploads",
                                           owner=principal.actor))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="file.upload", target=info["path"],
                    status="ok", meta={"size": info["size"]})
    return {"file": info}


@router.post("/write")
async def write(body: WriteBody, state: AppState = Depends(get_state), principal: Principal = Depends(require_role("operator"))):
    info = _wrap(lambda: state.services["files"].write_text(body.path, body.content, source="user", owner=principal.actor))
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
