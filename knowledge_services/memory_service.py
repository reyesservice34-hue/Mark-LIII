#!/usr/bin/env python3
"""Memory Service: gespiegelte Erinnerungen des Command Centers (Sicherung).

Das Command Center schickt Neues und Geändertes per POST /memory/bulk und Gelöschtes per
POST /memory/delete. Gelöschtes bleibt hier als Markierung (deleted=1) erhalten, damit eine
Wiederherstellung einen absichtlich gelöschten Merksatz nicht zurückbringt.
"""
import os
from contextlib import closing

from flask import jsonify, request

from _common import create_app, data_dir, json_body, open_db, run

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT '',
  deleted INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at, id);
"""
MAX_BATCH = 500
MAX_TEXT = 20000
MAX_ID = 200

app = create_app("mia-memory-service")
DB_PATH = os.path.join(data_dir("MEMORY_DB_PATH"), "memory.db")


def _db():
    return closing(open_db(DB_PATH, SCHEMA))


def _valid_id(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_ID


@app.post("/memory/bulk")
def bulk_upsert():
    items = json_body().get("items")
    if not isinstance(items, list) or not items or len(items) > MAX_BATCH:
        return jsonify(error=f"items: Liste mit 1 bis {MAX_BATCH} Einträgen erwartet"), 400
    rows, truncated = [], 0
    for item in items:
        if not isinstance(item, dict) or not _valid_id(item.get("id")) or not isinstance(item.get("text"), str):
            return jsonify(error="jeder Eintrag braucht id (Text) und text (Text)"), 400
        truncated += len(item["text"]) > MAX_TEXT
        rows.append((
            item["id"], item["text"][:MAX_TEXT], str(item.get("actor") or "")[:100],
            1 if item.get("pinned") else 0, str(item.get("created_at") or "")[:40],
        ))
    with _db() as conn, conn:
        conn.executemany(
            "INSERT INTO memories (id, text, actor, pinned, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET text = excluded.text, actor = excluded.actor, "
            "pinned = excluded.pinned, created_at = excluded.created_at, deleted = 0, "
            "updated_at = CURRENT_TIMESTAMP",
            rows,
        )
    return jsonify(stored=len(rows), truncated=truncated)


@app.post("/memory/delete")
def delete_memories():
    body = json_body()
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH or not all(_valid_id(i) for i in ids):
        return jsonify(error=f"ids: Liste mit 1 bis {MAX_BATCH} Texten erwartet"), 400
    marks = ",".join("?" for _ in ids)
    with _db() as conn, conn:
        if body.get("purge") is True:
            cur = conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", ids)
        else:
            cur = conn.execute(
                f"UPDATE memories SET deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id IN ({marks})", ids)
    return jsonify(deleted=cur.rowcount)


@app.get("/memory")
def list_memories():
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 1000))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify(error="limit und offset müssen Zahlen sein"), 400
    where = "" if request.args.get("include_deleted") == "1" else "WHERE deleted = 0"
    with _db() as conn:
        rows = conn.execute(
            f"SELECT id, text, actor, pinned, created_at, deleted FROM memories {where} "
            "ORDER BY created_at, id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return jsonify(memories=[dict(r) for r in rows])


@app.get("/memory/count")
def count_memories():
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(deleted = 0), 0) AS live, COALESCE(SUM(deleted = 1), 0) AS deleted FROM memories"
        ).fetchone()
    return jsonify(live=row["live"], deleted=row["deleted"])


@app.get("/memory/<memory_id>")
def get_memory(memory_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT id, text, actor, pinned, created_at, deleted FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return jsonify(error="nicht gefunden"), 404
    return jsonify(dict(row))


if __name__ == "__main__":
    run(app)
