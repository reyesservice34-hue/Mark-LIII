"""
Log center storage + audit trail.

`LogBook.log()` is the one call every service uses to leave a trace. It writes
the row, publishes a `log.entry` event for the live viewer and mirrors WARNING
and above into the Python logger so container logs stay useful on their own.

`LogBook.audit()` is separate on purpose: audit rows answer "who did what to
which target with what outcome" and are never trimmed by log retention.
"""
from __future__ import annotations

import logging
from typing import Any

from .db import Database, dumps, loads, new_id, now_iso
from .events import EventBus

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_py_logger = logging.getLogger("jarvis.cc")


class LogBook:
    def __init__(self, db: Database, bus: EventBus, retention_rows: int = 50_000):
        self.db = db
        self.bus = bus
        self.retention_rows = retention_rows
        self._since_trim = 0

    def log(self, level: str, source: str, message: str, *, task_id: str | None = None,
            agent_id: str | None = None, run_id: str | None = None,
            data: dict[str, Any] | None = None) -> dict:
        level = level.upper() if level.upper() in LEVELS else "INFO"
        row = {
            "id": new_id("log"), "ts": now_iso(), "level": level, "source": source,
            "message": message[:4000], "task_id": task_id, "agent_id": agent_id,
            "run_id": run_id, "data": dumps(data or {}),
        }
        self.db.insert("logs", row)
        row["data"] = data or {}
        self.bus.publish("log.entry", row)
        if level in ("WARNING", "ERROR", "CRITICAL"):
            getattr(_py_logger, level.lower() if level != "CRITICAL" else "critical")(
                "[%s] %s", source, message)
        else:
            _py_logger.debug("[%s] %s", source, message)
        self._since_trim += 1
        if self._since_trim >= 500:
            self._since_trim = 0
            self.trim()
        return row

    def info(self, source: str, message: str, **kw) -> dict:
        return self.log("INFO", source, message, **kw)

    def warning(self, source: str, message: str, **kw) -> dict:
        return self.log("WARNING", source, message, **kw)

    def error(self, source: str, message: str, **kw) -> dict:
        return self.log("ERROR", source, message, **kw)

    def debug(self, source: str, message: str, **kw) -> dict:
        return self.log("DEBUG", source, message, **kw)

    def trim(self) -> None:
        total = self.db.scalar("SELECT COUNT(*) FROM logs") or 0
        if total > self.retention_rows:
            cutoff = self.db.scalar(
                "SELECT seq FROM logs ORDER BY seq DESC LIMIT 1 OFFSET ?", (self.retention_rows,))
            if cutoff is not None:
                self.db.execute("DELETE FROM logs WHERE seq <= ?", (cutoff,))

    def query(self, *, q: str = "", level: str = "", source: str = "", task_id: str = "",
              agent_id: str = "", run_id: str = "", before: str = "", limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if q:
            clauses.append("message LIKE ?")
            params.append(f"%{q}%")
        if level:
            wanted = [l for l in LEVELS if LEVELS.index(l) >= LEVELS.index(level.upper())] \
                if level.upper() in LEVELS else [level.upper()]
            clauses.append("level IN (%s)" % ",".join("?" for _ in wanted))
            params.extend(wanted)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if before:
            clauses.append("ts < ?")
            params.append(before)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 1000))
        rows = self.db.fetchall(
            f"SELECT id, ts, level, source, message, task_id, agent_id, run_id, data "
            f"FROM logs {where} ORDER BY seq DESC LIMIT ?", [*params, limit])
        for r in rows:
            r["data"] = loads(r["data"], {})
        return rows

    def sources(self) -> list[str]:
        return [r["source"] for r in self.db.fetchall("SELECT DISTINCT source FROM logs ORDER BY source")]

    # ── audit ────────────────────────────────────────────────────────────
    def audit(self, *, actor_type: str, actor_id: str, action: str, status: str,
              target: str = "", agent_id: str = "", tool: str = "", result: str = "",
              error: str = "", task_id: str | None = None, run_id: str | None = None,
              meta: dict[str, Any] | None = None) -> dict:
        row = {
            "id": new_id("aud"), "ts": now_iso(), "actor_type": actor_type, "actor_id": actor_id,
            "agent_id": agent_id or "", "tool": tool or "", "action": action, "target": target or "",
            "status": status, "result": (result or "")[:2000], "error": (error or "")[:2000],
            "task_id": task_id, "run_id": run_id, "meta": dumps(meta or {}),
        }
        self.db.insert("audit_events", row)
        row["meta"] = meta or {}
        self.bus.publish("audit.event", row)
        return row

    def audit_query(self, *, q: str = "", actor_id: str = "", agent_id: str = "",
                    task_id: str = "", before: str = "", limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if q:
            clauses.append("(action LIKE ? OR target LIKE ? OR tool LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if before:
            clauses.append("ts < ?")
            params.append(before)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.fetchall(
            f"SELECT * FROM audit_events {where} ORDER BY ts DESC LIMIT ?",
            [*params, max(1, min(int(limit), 1000))])
        for r in rows:
            r["meta"] = loads(r["meta"], {})
        return rows
