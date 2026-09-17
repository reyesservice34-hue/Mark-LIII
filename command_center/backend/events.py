"""
Event bus — one in-process fan-out for everything the dashboard watches live.

Every meaningful state change (chat delta, agent status, task transition,
metric sample, notification, approval, log line) is published here once and
delivered to every subscriber: SSE streams, the WebSocket endpoint, and
internal listeners such as the run tracker. Events carry a monotonically
increasing id so a reconnecting client can resume with `Last-Event-ID` and
miss nothing that is still in the replay buffer.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

REPLAY_BUFFER = 2000


@dataclass
class Event:
    id: int
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)
    user_id: str | None = None          # None → broadcast to everyone

    def sse(self) -> str:
        payload = json.dumps({"id": self.id, "type": self.type, "ts": self.ts, "data": self.data},
                             ensure_ascii=False, default=str)
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._next_id = 1
        self._buffer: deque[Event] = deque(maxlen=REPLAY_BUFFER)
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._listeners: list[Callable[[Event], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ── publish ──────────────────────────────────────────────────────────
    def publish(self, type_: str, data: dict[str, Any] | None = None, *,
                user_id: str | None = None) -> Event:
        ev = Event(id=self._next_id, type=type_, data=data or {}, user_id=user_id)
        self._next_id += 1
        self._buffer.append(ev)
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # A subscriber that cannot keep up loses events rather than
                # stalling the producer; it will re-sync via Last-Event-ID.
                pass
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def publish_threadsafe(self, type_: str, data: dict[str, Any] | None = None, *,
                           user_id: str | None = None) -> None:
        """For worker threads (e.g. the remote control-plane poller)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self.publish, type_, data, **{"user_id": user_id})

    # ── subscribe ────────────────────────────────────────────────────────
    def add_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Event], None]) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def replay_since(self, last_id: int) -> list[Event]:
        return [e for e in self._buffer if e.id > last_id]

    async def subscribe(self, *, last_id: int = 0, user_id: str | None = None,
                        types: set[str] | None = None,
                        predicate: Callable[[Event], bool] | None = None) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=5000)
        self._subscribers.add(q)
        try:
            if last_id:
                for ev in self.replay_since(last_id):
                    if self._visible(ev, user_id, types, predicate):
                        yield ev
            while True:
                ev = await q.get()
                if self._visible(ev, user_id, types, predicate):
                    yield ev
        finally:
            self._subscribers.discard(q)

    @staticmethod
    def _visible(ev: Event, user_id: str | None, types: set[str] | None,
                 predicate: Callable[[Event], bool] | None) -> bool:
        if ev.user_id is not None and user_id is not None and ev.user_id != user_id:
            return False
        if types is not None and ev.type not in types and not any(
                ev.type.startswith(t[:-1]) for t in types if t.endswith("*")):
            return False
        if predicate is not None and not predicate(ev):
            return False
        return True

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
