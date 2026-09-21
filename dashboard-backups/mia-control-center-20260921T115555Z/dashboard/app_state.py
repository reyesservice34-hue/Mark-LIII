from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse


def install_app_state(app, authenticate, base_dir: Path):
    data_dir = Path(base_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "companion_state.json"

    def default_state():
        return {"tasks": [], "notes": [], "events": []}

    def load_state():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return default_state()
        except Exception:
            return default_state()
        out = default_state()
        for key in out:
            if isinstance(data.get(key), list):
                out[key] = data[key]
        return out

    def save_state(data):
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(state_path)

    def item(kind, body):
        now = int(time.time())
        text = str(body.get("text") or body.get("title") or "").strip()
        if not text:
            return None
        return {
            "id": uuid.uuid4().hex[:12],
            "text": text[:500],
            "title": text[:500],
            "done": False,
            "priority": str(body.get("priority") or "normal")[:20],
            "when": str(body.get("when") or "")[:80],
            "created_at": now,
            "kind": kind,
        }

    @app.get("/api/app/state")
    async def app_state(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        data = load_state()
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/app/tasks")
    async def add_task(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        body = await req.json()
        new = item("task", body)
        if not new:
            return JSONResponse({"error": "Missing text"}, status_code=400)
        data = load_state()
        data["tasks"].insert(0, new)
        save_state(data)
        return JSONResponse({"ok": True, "task": new})

    @app.post("/api/app/notes")
    async def add_note(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        body = await req.json()
        new = item("note", body)
        if not new:
            return JSONResponse({"error": "Missing text"}, status_code=400)
        data = load_state()
        data["notes"].insert(0, new)
        save_state(data)
        return JSONResponse({"ok": True, "note": new})

    @app.post("/api/app/events")
    async def add_event(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        body = await req.json()
        new = item("event", body)
        if not new:
            return JSONResponse({"error": "Missing text"}, status_code=400)
        data = load_state()
        data["events"].insert(0, new)
        save_state(data)
        return JSONResponse({"ok": True, "event": new})

    @app.post("/api/app/tasks/{task_id}/toggle")
    async def toggle_task(task_id: str, req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        data = load_state()
        for task in data["tasks"]:
            if task.get("id") == task_id:
                task["done"] = not bool(task.get("done"))
                save_state(data)
                return JSONResponse({"ok": True, "task": task})
        return JSONResponse({"error": "Not found"}, status_code=404)
