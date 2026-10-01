"""Conversations and messages — persistent, per user, searchable."""
from __future__ import annotations

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus


class ChatStore:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus

    # ── conversations ────────────────────────────────────────────────────
    @staticmethod
    def _conv(row: dict) -> dict:
        out = dict(row)
        out["archived"] = bool(row["archived"])
        out["meta"] = loads(row.get("meta"), {})
        return out

    def create_conversation(self, user_id: str, title: str = "", actor: str = "") -> dict:
        ts = now_iso()
        row = {"id": new_id("conv"), "user_id": user_id, "title": title.strip()[:120] or "New conversation",
               "created_at": ts, "updated_at": ts, "archived": 0, "actor": actor, "meta": "{}"}
        self.db.insert("conversations", row)
        conv = self._conv(row)
        self.bus.publish("conversation.created", conv, user_id=user_id)
        return conv

    def get_conversation(self, conv_id: str, user_id: str | None = None) -> dict | None:
        if user_id is None:
            row = self.db.fetchone("SELECT * FROM conversations WHERE id=?", (conv_id,))
        else:
            row = self.db.fetchone("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))
        return self._conv(row) if row else None

    def list_conversations(self, user_id: str, *, q: str = "", archived: bool = False,
                           limit: int = 100) -> list[dict]:
        params: list = [user_id, 1 if archived else 0]
        where = "WHERE c.user_id=? AND c.archived=?"
        if q:
            where += (" AND (c.title LIKE ? OR EXISTS (SELECT 1 FROM messages m "
                      "WHERE m.conversation_id=c.id AND m.content LIKE ?))")
            params.extend([f"%{q}%", f"%{q}%"])
        rows = self.db.fetchall(
            f"SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count, "
            f"(SELECT substr(content,1,160) FROM messages m WHERE m.conversation_id=c.id "
            f"ORDER BY created_at DESC LIMIT 1) AS preview "
            f"FROM conversations c {where} ORDER BY c.updated_at DESC LIMIT ?", [*params, limit])
        return [self._conv(r) for r in rows]

    def update_conversation(self, conv_id: str, user_id: str, *, title: str | None = None,
                            archived: bool | None = None) -> dict | None:
        values: dict = {"updated_at": now_iso()}
        if title is not None:
            values["title"] = title.strip()[:120] or "Untitled"
        if archived is not None:
            values["archived"] = 1 if archived else 0
        self.db.execute("UPDATE conversations SET %s WHERE id=? AND user_id=?" %
                        ", ".join(f"{k}=?" for k in values), [*values.values(), conv_id, user_id])
        conv = self.get_conversation(conv_id, user_id)
        if conv:
            self.bus.publish("conversation.updated", conv, user_id=user_id)
        return conv

    def delete_conversation(self, conv_id: str, user_id: str) -> bool:
        cur = self.db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))
        if cur.rowcount:
            self.db.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            self.bus.publish("conversation.deleted", {"id": conv_id}, user_id=user_id)
            return True
        return False

    def touch(self, conv_id: str) -> None:
        self.db.update("conversations", conv_id, {"updated_at": now_iso()})

    def maybe_title(self, conv_id: str, first_message: str) -> None:
        row = self.db.fetchone("SELECT title, user_id FROM conversations WHERE id=?", (conv_id,))
        if row and row["title"] == "New conversation":
            title = " ".join(first_message.strip().split())[:60] or "New conversation"
            self.db.update("conversations", conv_id, {"title": title})
            conv = self.get_conversation(conv_id)
            if conv:
                self.bus.publish("conversation.updated", conv, user_id=row["user_id"])

    # ── messages ─────────────────────────────────────────────────────────
    @staticmethod
    def _msg(row: dict) -> dict:
        out = dict(row)
        out["blocks"] = loads(row.get("blocks"), [])
        out["meta"] = loads(row.get("meta"), {})
        return out

    def add_message(self, conv_id: str, role: str, content: str = "", *, blocks: list | None = None,
                    status: str = "complete", run_id: str | None = None,
                    meta: dict | None = None, message_id: str | None = None) -> dict:
        row = {"id": message_id or new_id("msg"), "conversation_id": conv_id, "role": role,
               "content": content, "blocks": dumps(blocks or []), "status": status,
               "run_id": run_id, "created_at": now_iso(), "meta": dumps(meta or {})}
        self.db.insert("messages", row)
        self.touch(conv_id)
        msg = self._msg(row)
        self.bus.publish("message.created", msg)
        return msg

    def update_message(self, message_id: str, *, content: str | None = None, blocks: list | None = None,
                       status: str | None = None, meta: dict | None = None) -> dict | None:
        values: dict = {}
        if content is not None:
            values["content"] = content
        if blocks is not None:
            values["blocks"] = dumps(blocks)
        if status is not None:
            values["status"] = status
        if meta is not None:
            values["meta"] = dumps(meta)
        if values:
            self.db.update("messages", message_id, values)
        msg = self.get_message(message_id)
        if msg:
            self.bus.publish("message.updated", msg)
        return msg

    def get_message(self, message_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM messages WHERE id=?", (message_id,))
        return self._msg(row) if row else None

    def messages(self, conv_id: str, *, limit: int = 200, before: str = "") -> list[dict]:
        if before:
            rows = self.db.fetchall(
                "SELECT * FROM messages WHERE conversation_id=? AND created_at < ? "
                "ORDER BY created_at DESC LIMIT ?", (conv_id, before, limit))
        else:
            rows = self.db.fetchall(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conv_id, limit))
        return [self._msg(r) for r in reversed(rows)]

    def delete_messages_from(self, conv_id: str, message_id: str) -> int:
        row = self.db.fetchone("SELECT created_at FROM messages WHERE id=? AND conversation_id=?",
                               (message_id, conv_id))
        if not row:
            return 0
        cur = self.db.execute("DELETE FROM messages WHERE conversation_id=? AND created_at >= ?",
                              (conv_id, row["created_at"]))
        return cur.rowcount

    def search(self, user_id: str, q: str, limit: int = 50) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT m.id, m.conversation_id, m.role, substr(m.content,1,240) AS snippet, m.created_at, "
            "c.title FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE c.user_id=? AND m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?",
            (user_id, f"%{q}%", limit))
        return rows

    def message_counts(self, days: int = 14) -> list[dict]:
        return self.db.fetchall(
            "SELECT substr(created_at,1,10) AS day, role, COUNT(*) AS n FROM messages "
            "WHERE created_at >= datetime('now', ?) GROUP BY day, role ORDER BY day",
            (f"-{int(days)} days",))
