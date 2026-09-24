#!/usr/bin/env python3
"""Session Archive Service: archivierte Gespräche des Command Centers, durchsuchbar.

Ein Gespräch wird unter seiner Command-Center-ID abgelegt (PUT ist idempotent: gleiche ID
überschreibt). Das Archiv vergisst aber nicht: Enthielte der neue Stand weniger Nachrichten als der
gespeicherte, lehnt PUT mit 409 ab (überschreiben trotzdem mit ?force=1). Durchsucht wird der
Nachrichtentext per einfacher Textsuche.
"""
import json
import os
from contextlib import closing

from flask import jsonify, request

from _common import create_app, json_body, open_db, run

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  message_count INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL DEFAULT '[]',
  archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
"""
MAX_MESSAGES = 2000
MAX_CONTENT = 20000
MAX_TEXT = 200000
MAX_ID = 200
MAX_QUERY = 200
SNIPPET = 80

app = create_app("mia-session-archive")
ARCHIVE_DIR = os.environ.get("ARCHIVE_PATH", "/data/sessions")
os.makedirs(ARCHIVE_DIR, exist_ok=True)
DB_PATH = os.path.join(ARCHIVE_DIR, "sessions.db")
META = "id, title, actor, created_at, updated_at, message_count, archived_at"


def _db():
    return closing(open_db(DB_PATH, SCHEMA))


def _page():
    limit = max(1, min(int(request.args.get("limit", 50)), 500))
    offset = max(0, int(request.args.get("offset", 0)))
    return limit, offset


def _snippet(text: str, needle: str) -> str:
    at = text.lower().find(needle.lower())
    if at < 0:
        return text[:2 * SNIPPET]
    return text[max(0, at - SNIPPET): at + len(needle) + SNIPPET]


@app.put("/sessions/<session_id>")
def put_session(session_id):
    if not 0 < len(session_id) <= MAX_ID:
        return jsonify(error="ungültige ID"), 400
    body = json_body()
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        return jsonify(error=f"messages: Liste mit höchstens {MAX_MESSAGES} Einträgen erwartet"), 400
    clean, truncated = [], 0
    for m in messages:
        if not isinstance(m, dict):
            return jsonify(error="jede Nachricht muss ein Objekt sein"), 400
        content = str(m.get("content") or "")
        truncated += len(content) > MAX_CONTENT
        clean.append({"role": str(m.get("role") or "")[:40], "content": content[:MAX_CONTENT],
                      "created_at": str(m.get("created_at") or "")[:40]})
    text = "\n".join(f"{m['role']}: {m['content']}" for m in clean)[:MAX_TEXT]
    with _db() as conn, conn:
        stored = conn.execute("SELECT message_count FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if stored is not None and len(clean) < stored["message_count"] and request.args.get("force") != "1":
            return jsonify(error="Archiv hat mehr Nachrichten als der neue Stand (mit ?force=1 überschreiben)",
                           stored=stored["message_count"], received=len(clean)), 409
        conn.execute(
            "INSERT INTO sessions (id, title, actor, created_at, updated_at, message_count, text, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
            "actor = excluded.actor, created_at = excluded.created_at, updated_at = excluded.updated_at, "
            "message_count = excluded.message_count, text = excluded.text, data = excluded.data, "
            "archived_at = CURRENT_TIMESTAMP",
            (session_id, str(body.get("title") or "")[:300], str(body.get("actor") or "")[:100],
             str(body.get("created_at") or "")[:40], str(body.get("updated_at") or "")[:40],
             len(clean), text, json.dumps(clean, ensure_ascii=False)),
        )
    return jsonify(id=session_id, messages=len(clean), truncated=truncated)


@app.get("/sessions")
def list_sessions():
    try:
        limit, offset = _page()
    except ValueError:
        return jsonify(error="limit und offset müssen Zahlen sein"), 400
    with _db() as conn:
        rows = conn.execute(
            f"SELECT {META} FROM sessions ORDER BY updated_at DESC, id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    return jsonify(total=total, sessions=[dict(r) for r in rows])


@app.get("/sessions/search")
def search_sessions():
    query = (request.args.get("q") or "").strip()[:MAX_QUERY]
    if not query:
        return jsonify(error="q fehlt"), 400
    like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with _db() as conn:
        rows = conn.execute(
            f"SELECT {META}, text FROM sessions WHERE text LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT 50", (like, like)).fetchall()
    hits = []
    for r in rows:
        item = {k: r[k] for k in r.keys() if k != "text"}
        item["snippet"] = _snippet(r["text"], query)
        hits.append(item)
    return jsonify(results=hits)


@app.get("/sessions/<session_id>")
def get_session(session_id):
    with _db() as conn:
        row = conn.execute(f"SELECT {META}, data FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return jsonify(error="nicht gefunden"), 404
    item = {k: row[k] for k in row.keys() if k != "data"}
    item["messages"] = json.loads(row["data"])
    return jsonify(item)


if __name__ == "__main__":
    run(app)
