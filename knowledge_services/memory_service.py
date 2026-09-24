#!/usr/bin/env python3
"""Memory Service (Gerüst): einfacher Key-Value-Speicher auf SQLite."""
import os

from flask import jsonify, request

from _common import create_app, data_dir, open_db, run

SCHEMA = "CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT DEFAULT CURRENT_TIMESTAMP);"

app = create_app("mia-memory-service")
DB_PATH = os.path.join(data_dir("MEMORY_DB_PATH"), "memory.db")


@app.put("/memory/<key>")
def put_memory(key):
    value = (request.get_json(silent=True) or {}).get("value")
    if value is None:
        return jsonify(error="value fehlt"), 400
    conn = open_db(DB_PATH, SCHEMA)
    with conn:
        conn.execute(
            "INSERT INTO memory (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = CURRENT_TIMESTAMP",
            (key, str(value)),
        )
    conn.close()
    return jsonify(key=key, stored=True)


@app.get("/memory/<key>")
def get_memory(key):
    conn = open_db(DB_PATH, SCHEMA)
    row = conn.execute("SELECT key, value, updated FROM memory WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return jsonify(error="nicht gefunden"), 404
    return jsonify(dict(row))


if __name__ == "__main__":
    run(app)
