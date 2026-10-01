"""
Approval gate — enforced on the server, not painted in the UI.

An agent that wants to run a high-risk tool calls `request()` and then
*awaits* `wait()`. Nothing executes until a human with operator rights calls
`decide()`. The gate is also what the /v1 gateway surfaces to the desktop as
`awaiting_approval`, so a desktop-issued command and a dashboard-issued one
go through the same door.
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus
from ..logbook import LogBook
from .notifications import NotificationService
from .tasks import TaskService

RISKS = ("low", "medium", "high", "critical")


class ApprovalService:
    def __init__(self, db: Database, bus: EventBus, log: LogBook, tasks: TaskService,
                 notifications: NotificationService, timeout_minutes: int = 30):
        self.db = db
        self.bus = bus
        self.log = log
        self.tasks = tasks
        self.notifications = notifications
        self.timeout = timedelta(minutes=timeout_minutes)
        self._waiters: dict[str, asyncio.Future] = {}

    @staticmethod
    def _row(row: dict) -> dict:
        out = dict(row)
        out["payload"] = loads(row.get("payload"), {})
        return out

    # ── request ──────────────────────────────────────────────────────────
    def request(self, *, action: str, reason: str, target: str, risk: str, requested_by: str,
                agent_id: str = "", task_id: str | None = None, run_id: str | None = None,
                payload: dict | None = None) -> dict:
        now = datetime.now(timezone.utc)
        row = {
            "id": new_id("apr"), "task_id": task_id, "run_id": run_id, "agent_id": agent_id,
            "action": action[:200], "reason": reason[:2000], "target": target[:500],
            "risk": risk if risk in RISKS else "high", "requested_by": requested_by,
            "status": "pending", "created_at": now_iso(),
            "expires_at": (now + self.timeout).isoformat(), "decided_at": None,
            "decided_by": None, "decision_note": "", "payload": dumps(payload or {}),
            "code": "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(4)),
        }
        self.db.insert("approvals", row)
        approval = self._row(row)
        if task_id:
            self.tasks.set_status(task_id, "WAITING_FOR_APPROVAL",
                                  note=f"Waiting for approval: {action}")
        self.bus.publish("approval.requested", approval)
        self.notifications.notify(
            category="approval", severity="warning", title=f"Approval needed: {action}",
            body=reason or target, link=f"/approvals/{approval['id']}",
            meta={"approval_id": approval["id"], "risk": approval["risk"]})
        self.log.warning("approvals", f"Approval requested by {agent_id or requested_by}: {action} → {target}",
                         task_id=task_id, agent_id=agent_id or None, run_id=run_id,
                         data={"approval_id": approval["id"], "risk": approval["risk"]})
        return approval

    async def wait(self, approval_id: str) -> dict:
        """Block until a human decides or the request expires."""
        loop = asyncio.get_running_loop()
        fut = self._waiters.get(approval_id)
        if fut is None:
            fut = loop.create_future()
            self._waiters[approval_id] = fut
        current = self.get(approval_id)
        if current and current["status"] != "pending":
            self._waiters.pop(approval_id, None)
            return current
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout.total_seconds())
        except asyncio.TimeoutError:
            return self.expire(approval_id) or self.get(approval_id)
        finally:
            self._waiters.pop(approval_id, None)

    # ── decide ───────────────────────────────────────────────────────────
    def decide(self, approval_id: str, *, approve: bool, decided_by: str, note: str = "") -> dict | None:
        approval = self.get(approval_id)
        if not approval or approval["status"] != "pending":
            return approval
        status = "approved" if approve else "rejected"
        self.db.update("approvals", approval_id, {
            "status": status, "decided_at": now_iso(), "decided_by": decided_by,
            "decision_note": note[:1000]})
        approval = self.get(approval_id)
        self.bus.publish("approval.decided", approval)
        self.log.audit(actor_type="user", actor_id=decided_by, action=f"approval.{status}",
                       target=approval["target"], status="ok", agent_id=approval["agent_id"],
                       tool=approval["action"], task_id=approval["task_id"], run_id=approval["run_id"],
                       meta={"approval_id": approval_id, "note": note})
        self.notifications.notify(
            category="approval", severity="success" if approve else "info",
            title=f"{'Approved' if approve else 'Rejected'}: {approval['action']}",
            body=note or approval["target"], link=f"/approvals/{approval_id}")
        if approval["task_id"]:
            task = self.tasks.get(approval["task_id"])
            if task and task["status"] == "WAITING_FOR_APPROVAL":
                self.tasks.set_status(approval["task_id"], "RUNNING" if approve else "RUNNING",
                                      note=f"Approval {status}: {approval['action']}")
        fut = self._waiters.get(approval_id)
        if fut and not fut.done():
            fut.set_result(approval)
        return approval

    def expire(self, approval_id: str) -> dict | None:
        approval = self.get(approval_id)
        if not approval or approval["status"] != "pending":
            return approval
        self.db.update("approvals", approval_id, {"status": "expired", "decided_at": now_iso()})
        approval = self.get(approval_id)
        self.bus.publish("approval.decided", approval)
        self.log.warning("approvals", f"Approval expired: {approval['action']}",
                         task_id=approval["task_id"], run_id=approval["run_id"])
        fut = self._waiters.get(approval_id)
        if fut and not fut.done():
            fut.set_result(approval)
        return approval

    def expire_overdue(self) -> int:
        rows = self.db.fetchall(
            "SELECT id FROM approvals WHERE status='pending' AND expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),))
        for r in rows:
            self.expire(r["id"])
        return len(rows)

    # ── read ─────────────────────────────────────────────────────────────
    def get(self, approval_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM approvals WHERE id=?", (approval_id,))
        return self._row(row) if row else None

    def list(self, *, status: str = "", limit: int = 100) -> list[dict]:
        if status:
            rows = self.db.fetchall("SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT ?",
                                    (status, limit))
        else:
            rows = self.db.fetchall("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def pending_count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM approvals WHERE status='pending'") or 0)
