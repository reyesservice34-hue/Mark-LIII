#!/usr/bin/env python3
"""Session Archive Service (Gerüst): legt Sessions als JSON-Dateien ab."""
import json
import os
import uuid
from datetime import datetime, timezone

from flask import jsonify, request

from _common import create_app, run

app = create_app("mia-session-archive")
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "/data/sessions")
os.makedirs(ARCHIVE_PATH, exist_ok=True)


@app.post("/sessions")
def archive_session():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(error="JSON-Body fehlt"), 400
    session_id = uuid.uuid4().hex
    record = {"id": session_id, "archived": datetime.now(timezone.utc).isoformat(), "data": payload}
    with open(os.path.join(ARCHIVE_PATH, f"{session_id}.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    return jsonify(id=session_id), 201


@app.get("/sessions")
def list_sessions():
    ids = sorted(f[:-5] for f in os.listdir(ARCHIVE_PATH) if f.endswith(".json"))
    return jsonify(sessions=ids)


if __name__ == "__main__":
    run(app)
