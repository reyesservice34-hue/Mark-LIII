#!/usr/bin/env python3
"""Procedural Brain (Gerüst): speichert gelernte Lösungen pro Problem in SQLite."""
import os

from flask import jsonify, request

from _common import create_app, data_dir, open_db, run

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS procedures ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, problem TEXT NOT NULL, "
    "solution TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);"
)

app = create_app("mia-procedural-brain")
DB_PATH = os.path.join(data_dir("PROCEDURE_DB_PATH"), "procedures.db")


@app.post("/procedures")
def add_procedure():
    body = request.get_json(silent=True) or {}
    problem, solution = body.get("problem"), body.get("solution")
    if not problem or not solution:
        return jsonify(error="problem und solution erforderlich"), 400
    conn = open_db(DB_PATH, SCHEMA)
    with conn:
        cur = conn.execute("INSERT INTO procedures (problem, solution) VALUES (?, ?)", (problem, solution))
    conn.close()
    return jsonify(id=cur.lastrowid), 201


@app.get("/procedures")
def find_procedures():
    query = request.args.get("q", "")
    conn = open_db(DB_PATH, SCHEMA)
    rows = conn.execute(
        "SELECT id, problem, solution, created FROM procedures WHERE problem LIKE ? ORDER BY id DESC LIMIT 50",
        (f"%{query}%",),
    ).fetchall()
    conn.close()
    return jsonify(procedures=[dict(r) for r in rows])


if __name__ == "__main__":
    run(app)
