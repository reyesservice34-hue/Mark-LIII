"""Notification center storage. `user_id='*'` means every user sees it."""
from __future__ import annotations

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus

CATEGORIES = ("task", "agent", "approval", "server", "workflow", "integration",
              "deployment", "security", "system")
SEVERITIES = ("info", "success", "warning", "error", "critical")


class NotificationService:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    @staticmethod
    def _row(row: dict) -> dict:
        out = dict(row)
        out["read"] = bool(row["read"])
        out["meta"] = loads(row.get("meta"), {})
        return out

    def notify(self, *, category: str, title: str, body: str = "", severity: str = "info",
               user_id: str = "*", link: str = "", meta: dict | None = None) -> dict:
        row = {
            "id": new_id("ntf"), "user_id": user_id or "*",
            "category": category if category in CATEGORIES else "system",
            "title": title[:200], "body": body[:2000],
            "severity": severity if severity in SEVERITIES else "info",
            "read": 0, "created_at": now_iso(), "link": link, "meta": dumps(meta or {}),
        }
        self.db.insert("notifications", row)
        out = self._row(row)
        self.bus.publish("notification.created", out,
                         user_id=None if out["user_id"] == "*" else out["user_id"])
        return out

    def list(self, user_id: str, *, unread_only: bool = False, category: str = "",
             limit: int = 100, before: str = "") -> list[dict]:
        clauses, params = ["(user_id='*' OR user_id=?)"], [user_id]
        if unread_only:
            clauses.append("read=0")
        if category:
            clauses.append("category=?")
            params.append(category)
        if before:
            clauses.append("created_at < ?")
            params.append(before)
        rows = self.db.fetchall(
            "SELECT * FROM notifications WHERE %s ORDER BY created_at DESC LIMIT ?"
            % " AND ".join(clauses), [*params, max(1, min(limit, 500))])
        return [self._row(r) for r in rows]

    def unread_count(self, user_id: str) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM notifications WHERE (user_id='*' OR user_id=?) AND read=0",
            (user_id,)) or 0)

    def mark_read(self, user_id: str, ids: list[str] | None = None, read: bool = True) -> int:
        if ids:
            marks = ",".join("?" for _ in ids)
            cur = self.db.execute(
                f"UPDATE notifications SET read=? WHERE id IN ({marks}) AND (user_id='*' OR user_id=?)",
                [1 if read else 0, *ids, user_id])
        else:
            cur = self.db.execute(
                "UPDATE notifications SET read=? WHERE (user_id='*' OR user_id=?)",
                (1 if read else 0, user_id))
        self.bus.publish("notification.read", {"ids": ids or [], "all": not ids, "read": read},
                         user_id=user_id)
        return cur.rowcount

    def delete(self, user_id: str, notification_id: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM notifications WHERE id=? AND (user_id='*' OR user_id=?)",
            (notification_id, user_id))
        return cur.rowcount > 0
