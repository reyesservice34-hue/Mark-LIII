"""
Task manager — the one record of work JARVIS is doing.

State machine:
  QUEUED → PLANNING → RUNNING → COMPLETED
                 ↘ WAITING_FOR_APPROVAL ↗ (approval resolves back to RUNNING)
                 ↘ PAUSED ↗
  any active state → FAILED | CANCELLED
"""
from __future__ import annotations

from typing import Any

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus
from ..logbook import LogBook

STATUSES = ("QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "PAUSED",
            "COMPLETED", "FAILED", "CANCELLED")
ACTIVE = {"QUEUED", "PLANNING", "RUNNING", "WAITING_FOR_APPROVAL", "PAUSED"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
PRIORITIES = ("low", "normal", "high", "critical")


class TaskService:
    def __init__(self, db: Database, bus: EventBus, log: LogBook):
        self.db = db
        self.bus = bus
        self.log = log

    # ── shape ────────────────────────────────────────────────────────────
    @staticmethod
    def _row(row: dict) -> dict:
        out = dict(row)
        out["attachments"] = loads(row.get("attachments"), [])
        out["meta"] = loads(row.get("meta"), {})
        return out

    # ── create / read ────────────────────────────────────────────────────
    def create(self, *, title: str, description: str = "", created_by: str = "",
               assigned_agent: str = "", priority: str = "normal", parent_id: str | None = None,
               conversation_id: str | None = None, run_id: str | None = None,
               status: str = "QUEUED", meta: dict | None = None,
               attachments: list | None = None) -> dict:
        if priority not in PRIORITIES:
            priority = "normal"
        if status not in STATUSES:
            status = "QUEUED"
        ts = now_iso()
        row = {
            "id": new_id("task"), "parent_id": parent_id, "title": title.strip()[:200] or "Untitled task",
            "description": description or "", "created_by": created_by, "assigned_agent": assigned_agent,
            "status": status, "priority": priority, "created_at": ts, "started_at": None,
            "completed_at": None, "updated_at": ts, "output": "", "error": "",
            "conversation_id": conversation_id, "run_id": run_id,
            "attachments": dumps(attachments or []), "meta": dumps(meta or {}),
        }
        self.db.insert("tasks", row)
        task = self._row(row)
        self.bus.publish("task.created", task)
        self.log.info("tasks", f"Task created: {task['title']}", task_id=task["id"],
                      agent_id=assigned_agent or None, run_id=run_id)
        return task

    def get(self, task_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM tasks WHERE id=?", (task_id,))
        return self._row(row) if row else None

    def list(self, *, status: str = "", agent: str = "", parent_id: str | None = None,
             include_children: bool = True, q: str = "", limit: int = 100, offset: int = 0,
             conversation_id: str = "") -> list[dict]:
        clauses, params = [], []
        if status:
            wanted = [s for s in status.upper().split(",") if s in STATUSES]
            if status.lower() == "active":
                wanted = sorted(ACTIVE)
            if wanted:
                clauses.append("status IN (%s)" % ",".join("?" for _ in wanted))
                params.extend(wanted)
        if agent:
            clauses.append("assigned_agent=?")
            params.append(agent)
        if parent_id is not None:
            clauses.append("parent_id=?")
            params.append(parent_id)
        elif not include_children:
            clauses.append("parent_id IS NULL")
        if conversation_id:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if q:
            clauses.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.fetchall(
            f"SELECT * FROM tasks {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*params, max(1, min(limit, 500)), max(0, offset)])
        return [self._row(r) for r in rows]

    def children(self, task_id: str) -> list[dict]:
        return [self._row(r) for r in self.db.fetchall(
            "SELECT * FROM tasks WHERE parent_id=? ORDER BY created_at", (task_id,))]

    def counts(self) -> dict[str, int]:
        rows = self.db.fetchall("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
        out = {s: 0 for s in STATUSES}
        for r in rows:
            out[r["status"]] = r["n"]
        out["active"] = sum(out[s] for s in ACTIVE)
        return out

    # ── transitions ──────────────────────────────────────────────────────
    def set_status(self, task_id: str, status: str, *, output: str | None = None,
                   error: str | None = None, agent: str | None = None,
                   run_id: str | None = None, note: str = "") -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status}")
        current = self.get(task_id)
        if not current:
            return None
        ts = now_iso()
        values: dict[str, Any] = {"status": status, "updated_at": ts}
        if status in ("PLANNING", "RUNNING") and not current.get("started_at"):
            values["started_at"] = ts
        if status in TERMINAL:
            values["completed_at"] = ts
        if output is not None:
            values["output"] = output
        if error is not None:
            values["error"] = error
        if agent is not None:
            values["assigned_agent"] = agent
        if run_id is not None:
            values["run_id"] = run_id
        self.db.update("tasks", task_id, values)
        task = self.get(task_id)
        self.bus.publish("task.updated", task)
        level = "ERROR" if status == "FAILED" else "INFO"
        self.log.log(level, "tasks", note or f"Task {current['status']} → {status}: {task['title']}",
                     task_id=task_id, agent_id=task.get("assigned_agent") or None,
                     run_id=task.get("run_id"))
        return task

    def update(self, task_id: str, **fields) -> dict | None:
        allowed = {"title", "description", "priority", "assigned_agent", "attachments", "meta"}
        values = {}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            values[k] = dumps(v) if k in ("attachments", "meta") else v
        if not values:
            return self.get(task_id)
        values["updated_at"] = now_iso()
        self.db.update("tasks", task_id, values)
        task = self.get(task_id)
        if task:
            self.bus.publish("task.updated", task)
        return task

    def cancel(self, task_id: str, by: str = "") -> dict | None:
        task = self.get(task_id)
        if not task or task["status"] in TERMINAL:
            return task
        for child in self.children(task_id):
            if child["status"] in ACTIVE:
                self.cancel(child["id"], by)
        return self.set_status(task_id, "CANCELLED", note=f"Task cancelled by {by or 'system'}")

    def add_log(self, task_id: str, level: str, message: str, data: dict | None = None) -> dict:
        row = {"id": new_id("tl"), "task_id": task_id, "ts": now_iso(), "level": level.upper(),
               "message": message[:4000], "data": dumps(data or {})}
        self.db.insert("task_logs", row)
        row["data"] = data or {}
        self.bus.publish("task.log", row)
        return row

    def logs(self, task_id: str, limit: int = 500) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM task_logs WHERE task_id=? ORDER BY ts LIMIT ?",
                                (task_id, limit))
        for r in rows:
            r["data"] = loads(r["data"], {})
        return rows

    def timeline(self, limit: int = 30) -> list[dict]:
        """Recent tasks ordered for the home timeline: active first, then latest finished."""
        active = self.list(status="active", limit=limit)
        rest = limit - len(active)
        finished = self.list(status="COMPLETED,FAILED,CANCELLED", limit=max(rest, 0)) if rest > 0 else []
        return active + finished

    def stats(self, days: int = 7) -> dict:
        rows = self.db.fetchall(
            "SELECT status, COUNT(*) AS n, "
            "AVG(CASE WHEN started_at IS NOT NULL AND completed_at IS NOT NULL THEN "
            "(julianday(completed_at)-julianday(started_at))*86400 END) AS avg_secs "
            "FROM tasks WHERE created_at >= datetime('now', ?) GROUP BY status",
            (f"-{int(days)} days",))
        return {r["status"]: {"count": r["n"], "avg_seconds": r["avg_secs"]} for r in rows}

    def daily_counts(self, days: int = 14) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT substr(created_at,1,10) AS day, "
            "SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS completed, "
            "SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed, COUNT(*) AS total "
            "FROM tasks WHERE created_at >= datetime('now', ?) GROUP BY day ORDER BY day",
            (f"-{int(days)} days",))
        return rows
