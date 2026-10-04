"""
Reminders — ported from Mark-LIII `actions/reminder.py`. There, a reminder was
an OS-level scheduled task (Task Scheduler / launchd / systemd-run) that pops
a desktop notification on the machine main.py runs on. The Command Center
runs headless in a container with no user desktop to notify directly, so a
due reminder becomes a `notifications.notify()` call instead — which already
has a working push path (ntfy) via `NotificationService._maybe_push()`.

`RemindersService.check_due()` is registered with the app's `Scheduler` as a
recurring low-interval job (see app.py) rather than scheduling one OS task per
reminder — one poller for all reminders, not one mechanism per reminder.
"""
from __future__ import annotations

from ..db import Database, new_id, now_iso


class RemindersService:
    def __init__(self, db: Database, notifications) -> None:
        self.db = db
        self.notifications = notifications

    def create(self, *, message: str, due_at: str, user_id: str = "*") -> dict:
        row = {"id": new_id("rem"), "user_id": user_id or "*", "message": message,
               "due_at": due_at, "created_at": now_iso(), "fired": 0, "meta": "{}"}
        self.db.insert("reminders", row)
        return row

    def check_due(self) -> None:
        now = now_iso()
        due = self.db.fetchall(
            "SELECT * FROM reminders WHERE fired=0 AND due_at<=? ORDER BY due_at", (now,))
        for r in due:
            self.notifications.notify(
                category="task", severity="info", title="Erinnerung",
                body=r["message"], user_id=r["user_id"], meta={"push": True, "call": True, "speak": True})
            self.db.update("reminders", r["id"], {"fired": 1})
