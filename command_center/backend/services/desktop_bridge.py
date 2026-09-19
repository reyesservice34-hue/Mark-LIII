"""
Desktop control — the server tells the desktop what to do, the desktop does it.

The direction matters. A desktop at home or in an office sits behind a router
that the server cannot dial into, so nothing here opens a connection *to* the
machine. Instead the desktop holds a long poll open against this server, picks
up the next command, runs it with the actions it already has
(`actions/open_app.py`, `computer_control`, `browser_control`,
`computer_settings`, …) and posts the result back. No port forwarding, no VPN,
no inbound hole in the user's network.

A command is therefore a *request*, never a guarantee: if the desktop is
asleep, the tool says exactly that instead of pretending it opened something.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus
from ..logbook import LogBook

DEFAULT_TIMEOUT = float(os.environ.get("JARVIS_CC_DESKTOP_TIMEOUT", "90") or 90)
OFFLINE_AFTER = 90.0            # seconds without a poll → the device counts as away
MAX_QUEUE_PER_DEVICE = 50


class DesktopError(Exception):
    """Something the user needs to hear, phrased plainly."""


class DesktopBridge:
    def __init__(self, db: Database, bus: EventBus, log: LogBook):
        self.db = db
        self.bus = bus
        self.log = log
        self._waiters: dict[str, asyncio.Future] = {}       # command_id → result future
        self._pollers: dict[str, asyncio.Event] = {}        # device_id → "work is waiting"
        self._last_poll: dict[str, float] = {}

    # ── devices ──────────────────────────────────────────────────────────
    def register(self, *, name: str, actor: str, platform: str = "", version: str = "",
                 actions: list[dict] | None = None, device_id: str = "", meta: dict | None = None) -> dict:
        actions = actions or []
        existing = None
        if device_id:
            existing = self.db.fetchone("SELECT * FROM desktop_devices WHERE id=?", (device_id,))
        if existing is None:
            existing = self.db.fetchone("SELECT * FROM desktop_devices WHERE name=? AND actor=?", (name, actor))
        row = {
            "id": (existing or {}).get("id") or device_id or new_id("dev"),
            "name": name.strip()[:80] or "desktop", "actor": actor, "platform": platform[:80],
            "version": version[:40], "actions": dumps(actions),
            "registered_at": (existing or {}).get("registered_at") or now_iso(),
            "last_seen_at": now_iso(), "meta": dumps(meta or {}),
        }
        self.db.upsert("desktop_devices", row)
        device = self.get(row["id"])
        self.log.info("desktop", f"Desktop '{device['name']}' registered with {len(actions)} actions",
                      data={"device_id": device["id"], "platform": platform})
        self.bus.publish("desktop.device", device)
        return device

    def get(self, device_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM desktop_devices WHERE id=?", (device_id,))
        return self._device(row) if row else None

    def rename(self, device_id: str, name: str) -> dict | None:
        """Ein Gerät umbenennen.

        Der Name kommt sonst vom Rechnernamen, und „DESKTOP-4K7J2L" sagt
        niemandem, welcher Rechner das ist. Der Name ist reine Anzeige — die
        Kennung bleibt, damit die laufende Kopplung nicht abreißt.
        """
        if not self.get(device_id):
            return None
        self.db.update("desktop_devices", device_id, {"name": name.strip()[:80] or "desktop"})
        device = self.get(device_id)
        self.bus.publish("desktop.device", device)
        return device

    def forget(self, device_id: str) -> bool:
        """Ein Gerät samt seiner Aufträge entfernen.

        Meldet sich derselbe Rechner später wieder, legt er sich neu an — das
        ist gewollt: Vergessen heißt hier „aus der Liste", nicht „für immer
        gesperrt". Wer ihn wirklich aussperren will, widerruft sein Token.
        """
        if not self.get(device_id):
            return False
        self.db.execute("DELETE FROM desktop_commands WHERE device_id=?", (device_id,))
        self.db.execute("DELETE FROM desktop_devices WHERE id=?", (device_id,))
        self._last_poll.pop(device_id, None)
        self.log.info("desktop", f"Gerät {device_id} entfernt")
        self.bus.publish("desktop.removed", {"id": device_id})
        return True

    def _device(self, row: dict) -> dict:
        out = dict(row)
        out["actions"] = loads(row.get("actions"), [])
        out["meta"] = loads(row.get("meta"), {})
        out["online"] = self.is_online(row["id"])
        # Fähigkeiten liegen in meta, gehören aber nach oben: Die Liste soll
        # zeigen können, welches Gerät Ohren und welches nur Augen hat.
        out["capabilities"] = (out["meta"] or {}).get("capabilities") or {}
        out["queued"] = int(self.db.scalar(
            "SELECT COUNT(*) FROM desktop_commands WHERE device_id=? AND status='queued'", (row["id"],)) or 0)
        return out

    def list(self) -> list[dict]:
        return [self._device(r) for r in self.db.fetchall(
            "SELECT * FROM desktop_devices ORDER BY last_seen_at DESC")]

    def is_online(self, device_id: str) -> bool:
        last = self._last_poll.get(device_id)
        if last is not None:
            return (asyncio.get_event_loop().time() - last) < OFFLINE_AFTER
        row = self.db.fetchone("SELECT last_seen_at FROM desktop_devices WHERE id=?", (device_id,))
        if not row or not row["last_seen_at"]:
            return False
        try:
            seen = datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
        return (datetime.now(timezone.utc) - seen).total_seconds() < OFFLINE_AFTER

    def online_devices(self) -> list[dict]:
        return [d for d in self.list() if d["online"]]

    def resolve(self, hint: str = "") -> dict:
        """The device a command is meant for. One online desktop needs no name."""
        devices = self.list()
        if not devices:
            raise DesktopError("No desktop is paired with this server yet. Start JARVIS on the PC with "
                               "the gateway token configured, and it will register itself.")
        if hint:
            match = [d for d in devices if hint.lower() in (d["name"].lower(), d["id"].lower())
                     or hint.lower() in d["name"].lower()]
            if not match:
                raise DesktopError(f"No desktop called '{hint}'. Known: "
                                   + ", ".join(d["name"] for d in devices))
            device = match[0]
        else:
            online = [d for d in devices if d["online"]]
            if len(online) == 1:
                device = online[0]
            elif not online:
                device = devices[0]
            else:
                raise DesktopError("Several desktops are online — name the one you mean: "
                                   + ", ".join(d["name"] for d in online))
        return device

    # ── commands ─────────────────────────────────────────────────────────
    async def dispatch(self, *, device_id: str, action: str, params: dict, requested_by: str,
                       agent_id: str = "", task_id: str | None = None, run_id: str | None = None,
                       timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Queue one command and wait for the desktop to report back."""
        device = self.get(device_id)
        if not device:
            raise DesktopError("That desktop is not registered.")
        known = {a.get("name") for a in device["actions"]}
        if known and action not in known:
            raise DesktopError(f"The desktop '{device['name']}' has no action '{action}'. "
                               f"It offers: {', '.join(sorted(known))}")
        queued = int(self.db.scalar("SELECT COUNT(*) FROM desktop_commands WHERE device_id=? AND status='queued'",
                                    (device_id,)) or 0)
        if queued >= MAX_QUEUE_PER_DEVICE:
            raise DesktopError("The desktop already has too many pending commands.")

        command = {
            "id": new_id("dcmd"), "device_id": device_id, "action": action, "params": dumps(params or {}),
            "status": "queued", "created_at": now_iso(), "dispatched_at": None, "finished_at": None,
            "result": "", "error": "", "requested_by": requested_by, "agent_id": agent_id,
            "task_id": task_id, "run_id": run_id,
        }
        self.db.insert("desktop_commands", command)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._waiters[command["id"]] = future
        self._wake(device_id)
        self.bus.publish("desktop.command", self._command(command))

        online = self.is_online(device_id)
        try:
            result = await asyncio.wait_for(future, timeout=timeout if online else min(timeout, 20))
        except asyncio.TimeoutError:
            self.db.update("desktop_commands", command["id"],
                           {"status": "timeout", "finished_at": now_iso(),
                            "error": "the desktop did not answer in time"})
            self.bus.publish("desktop.command", self.command(command["id"]))
            if not online:
                raise DesktopError(f"The desktop '{device['name']}' is not connected right now, so nothing "
                                   f"was done. It was last seen {device['last_seen_at']}.")
            raise DesktopError(f"The desktop '{device['name']}' did not finish '{action}' within "
                               f"{int(timeout)} seconds.")
        finally:
            self._waiters.pop(command["id"], None)
        return result

    def next_for(self, device_id: str) -> dict | None:
        row = self.db.fetchone(
            "SELECT * FROM desktop_commands WHERE device_id=? AND status='queued' ORDER BY created_at LIMIT 1",
            (device_id,))
        if not row:
            return None
        self.db.update("desktop_commands", row["id"], {"status": "running", "dispatched_at": now_iso()})
        command = self.command(row["id"])
        self.bus.publish("desktop.command", command)
        return command

    def complete(self, command_id: str, *, ok: bool, result: str = "", error: str = "") -> dict | None:
        row = self.db.fetchone("SELECT * FROM desktop_commands WHERE id=?", (command_id,))
        if not row:
            return None
        if row["status"] in ("done", "failed", "timeout"):
            return self.command(command_id)
        self.db.update("desktop_commands", command_id, {
            "status": "done" if ok else "failed", "finished_at": now_iso(),
            "result": (result or "")[:8000], "error": (error or "")[:2000]})
        command = self.command(command_id)
        self.bus.publish("desktop.command", command)
        self.log.log("INFO" if ok else "WARNING", "desktop",
                     f"{row['action']} → {'ok' if ok else 'failed'}: {(result or error)[:200]}",
                     task_id=row["task_id"], run_id=row["run_id"], data={"command_id": command_id})
        future = self._waiters.get(command_id)
        if future and not future.done():
            future.set_result(command)
        return command

    def command(self, command_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM desktop_commands WHERE id=?", (command_id,))
        return self._command(row) if row else None

    @staticmethod
    def _command(row: dict) -> dict:
        out = dict(row)
        out["params"] = loads(row.get("params"), {})
        return out

    def history(self, device_id: str = "", limit: int = 50) -> list[dict]:
        if device_id:
            rows = self.db.fetchall(
                "SELECT * FROM desktop_commands WHERE device_id=? ORDER BY created_at DESC LIMIT ?",
                (device_id, limit))
        else:
            rows = self.db.fetchall("SELECT * FROM desktop_commands ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._command(r) for r in rows]

    # ── long poll plumbing ───────────────────────────────────────────────
    def _wake(self, device_id: str) -> None:
        event = self._pollers.get(device_id)
        if event:
            event.set()

    async def wait_for_work(self, device_id: str, seconds: float) -> dict | None:
        """Used by GET /v1/desktop/poll: return at once if work is queued,
        otherwise hold the connection until something arrives or time runs out."""
        self._last_poll[device_id] = asyncio.get_event_loop().time()
        self.db.update("desktop_devices", device_id, {"last_seen_at": now_iso()})
        command = self.next_for(device_id)
        if command:
            return command
        event = self._pollers.setdefault(device_id, asyncio.Event())
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=max(1.0, min(seconds, 60)))
        except asyncio.TimeoutError:
            return None
        finally:
            self._last_poll[device_id] = asyncio.get_event_loop().time()
        return self.next_for(device_id)

    def stats(self) -> dict[str, Any]:
        devices = self.list()
        return {"devices": len(devices), "online": sum(1 for d in devices if d["online"]),
                "queued": sum(d["queued"] for d in devices)}
