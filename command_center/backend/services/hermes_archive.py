"""Nur-Lese-Zugriff auf die übernommenen Hermes-Daten (Sitzungen, Nachrichten, Aufgaben).

Die Datenbanken liegen im MIA-Datenvolume unter HERMES_DATA_DIR (Standard /data/hermes).
Alle Verbindungen sind read-only; dieser Dienst schreibt nie in Hermes-Daten.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

HERMES_DATA_DIR = Path(os.environ.get("HERMES_DATA_DIR", "/data/hermes"))


def _connect(name: str) -> sqlite3.Connection | None:
    path = HERMES_DATA_DIR / name
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)


def list_sessions(limit: int = 50) -> list[dict]:
    """Die neuesten Hermes-Sitzungen, neueste zuerst."""
    conn = _connect("state.db")
    if conn is None:
        return []
    try:
        cols = [r[1] for r in conn.execute("pragma table_info(sessions)")]
        order = "started_at" if "started_at" in cols else "rowid"
        rows = conn.execute(f"select * from sessions order by {order} desc limit ?", (int(limit),)).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def session_messages(session_id: str, limit: int = 200) -> list[dict]:
    """Nachrichten einer Sitzung in Reihenfolge."""
    conn = _connect("state.db")
    if conn is None:
        return []
    try:
        cols = [r[1] for r in conn.execute("pragma table_info(messages)")]
        if "session_id" not in cols:
            return []
        rows = conn.execute(
            "select * from messages where session_id = ? order by rowid limit ?",
            (session_id, int(limit)),
        ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def list_tasks(limit: int = 100) -> list[dict]:
    """Hermes-Kanban-Aufgaben."""
    conn = _connect("kanban.db")
    if conn is None:
        return []
    try:
        cols = [r[1] for r in conn.execute("pragma table_info(tasks)")]
        rows = conn.execute("select * from tasks order by rowid desc limit ?", (int(limit),)).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def summary() -> dict:
    """Kurzübersicht: wie viel aus Hermes verfügbar ist."""
    out = {"data_dir": str(HERMES_DATA_DIR), "sessions": 0, "messages": 0, "tasks": 0}
    conn = _connect("state.db")
    if conn is not None:
        try:
            out["sessions"] = conn.execute("select count(*) from sessions").fetchone()[0]
            out["messages"] = conn.execute("select count(*) from messages").fetchone()[0]
        finally:
            conn.close()
    conn = _connect("kanban.db")
    if conn is not None:
        try:
            out["tasks"] = conn.execute("select count(*) from tasks").fetchone()[0]
        finally:
            conn.close()
    return out
