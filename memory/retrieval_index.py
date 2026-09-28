from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
        "record_id UNINDEXED, kind, category, key, value, source UNINDEXED, updated UNINDEXED)"
    )
    return con


def upsert_record(db_path: Path, record_id: str, *, kind: str, category: str,
                  key: str, value: str, source: str = "", updated: str = "") -> None:
    if not record_id or not str(value or "").strip():
        return
    con = _connect(db_path)
    try:
        con.execute("DELETE FROM memory_fts WHERE record_id = ?", (record_id,))
        con.execute(
            "INSERT INTO memory_fts(record_id, kind, category, key, value, source, updated) VALUES(?,?,?,?,?,?,?)",
            (record_id, kind, category, key, value, source, updated),
        )
        con.commit()
    finally:
        con.close()


def delete_record(db_path: Path, record_id: str) -> None:
    con = _connect(db_path)
    try:
        con.execute("DELETE FROM memory_fts WHERE record_id = ?", (record_id,))
        con.commit()
    finally:
        con.close()


def search(db_path: Path, query: str, limit: int = 16) -> list[dict]:
    if not db_path.exists():
        return []
    words = [w.strip() for w in str(query or "").replace('"', ' ').split() if len(w.strip()) > 1]
    con = _connect(db_path)
    try:
        if not words:
            rows = con.execute(
                "SELECT record_id,kind,category,key,value,source,updated FROM memory_fts LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        else:
            fts_query = " OR ".join(f'"{w}"' for w in words)
            rows = con.execute(
                "SELECT record_id,kind,category,key,value,source,updated "
                "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts) LIMIT ?",
                (fts_query, max(1, limit)),
            ).fetchall()
        return [
            {"record_id": r[0], "kind": r[1], "category": r[2], "key": r[3],
             "value": r[4], "source": r[5], "updated": r[6]}
            for r in rows
        ]
    finally:
        con.close()


def reset(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()
