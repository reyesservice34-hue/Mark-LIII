"""
Background job scheduler — the "automations" the command center itself runs.

Jobs are registered with an interval; each runs in its own asyncio loop with
error isolation, status tracking and a run-now hook. Modules add jobs with
`scheduler.add()`; nothing else needs to know about them.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ...db import now_iso
from ...events import EventBus
from ...logbook import LogBook


@dataclass
class Job:
    id: str
    name: str
    interval: float
    fn: Callable[[], Any | Awaitable[Any]]
    description: str = ""
    enabled: bool = True
    silent: bool = False              # do not log every successful run
    last_run_at: str | None = None
    next_run_at: float = 0.0
    last_status: str = "never"
    last_error: str = ""
    last_duration_ms: int = 0
    runs: int = 0
    errors: int = 0
    running: bool = False
    _task: asyncio.Task | None = field(default=None, repr=False)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "interval_seconds": self.interval,
                "enabled": self.enabled, "last_run_at": self.last_run_at,
                "next_run_in": max(0, int(self.next_run_at - time.monotonic())) if self.enabled else None,
                "last_status": self.last_status, "last_error": self.last_error,
                "last_duration_ms": self.last_duration_ms, "runs": self.runs, "errors": self.errors,
                "running": self.running}


class Scheduler:
    def __init__(self, bus: EventBus, log: LogBook):
        self.bus = bus
        self.log = log
        self._jobs: dict[str, Job] = {}
        self._started = False

    def add(self, job_id: str, name: str, interval: float, fn, *, description: str = "",
            enabled: bool = True, silent: bool = False, run_immediately: bool = True) -> Job:
        job = Job(id=job_id, name=name, interval=interval, fn=fn, description=description,
                  enabled=enabled, silent=silent)
        job.next_run_at = time.monotonic() + (0 if run_immediately else interval)
        self._jobs[job_id] = job
        if self._started and enabled:
            job._task = asyncio.create_task(self._loop(job))
        return job

    def list(self) -> list[dict]:
        return [j.public() for j in self._jobs.values()]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def start(self) -> None:
        self._started = True
        for job in self._jobs.values():
            if job.enabled:
                job._task = asyncio.create_task(self._loop(job))

    async def stop(self) -> None:
        self._started = False
        for job in self._jobs.values():
            if job._task:
                job._task.cancel()
        await asyncio.gather(*[j._task for j in self._jobs.values() if j._task], return_exceptions=True)

    def set_enabled(self, job_id: str, enabled: bool) -> Job | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.enabled = enabled
        if enabled and self._started and (job._task is None or job._task.done()):
            job.next_run_at = time.monotonic()
            job._task = asyncio.create_task(self._loop(job))
        elif not enabled and job._task:
            job._task.cancel()
            job._task = None
        self.bus.publish("job.updated", job.public())
        return job

    async def run_now(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        await self._run_once(job)
        return job

    async def _loop(self, job: Job) -> None:
        try:
            while job.enabled:
                delay = max(0.0, job.next_run_at - time.monotonic())
                await asyncio.sleep(delay)
                if not job.enabled:
                    break
                await self._run_once(job)
                job.next_run_at = time.monotonic() + job.interval
        except asyncio.CancelledError:
            pass

    async def _run_once(self, job: Job) -> None:
        if job.running:
            return
        job.running = True
        started = time.monotonic()
        try:
            result = job.fn()
            if inspect.isawaitable(result):
                await result
            job.last_status = "ok"
            job.last_error = ""
            if not job.silent:
                self.log.debug("scheduler", f"Job '{job.name}' ran", data={"job": job.id})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            job.last_status = "error"
            job.last_error = f"{e.__class__.__name__}: {str(e)[:300]}"
            job.errors += 1
            self.log.error("scheduler", f"Job '{job.name}' failed: {job.last_error}", data={"job": job.id})
        finally:
            job.running = False
            job.runs += 1
            job.last_run_at = now_iso()
            job.last_duration_ms = int((time.monotonic() - started) * 1000)
            self.bus.publish("job.updated", job.public())
