"""
Workflow adapters — n8n today, anything with the same four verbs tomorrow.

The frontend and the tools only ever talk to `WorkflowHub`; the n8n REST
details stay in `N8nAdapter`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus


@dataclass
class WorkflowRecord:
    id: str
    provider: str
    external_id: str
    name: str
    active: bool
    trigger: str
    last_execution_at: str | None
    next_execution_at: str | None
    last_status: str
    meta: dict

    def public(self) -> dict:
        return self.__dict__.copy()


class WorkflowAdapter:
    provider = "base"
    label = "Workflow engine"

    def configured(self) -> bool:
        return False

    async def list_workflows(self) -> list[WorkflowRecord]:
        return []

    async def list_runs(self, external_id: str | None = None, limit: int = 20) -> list[dict]:
        return []

    async def get_run(self, run_id: str) -> dict | None:
        return None

    async def trigger(self, external_id: str, payload: dict | None = None) -> dict:
        raise RuntimeError("triggering is not supported by this adapter")

    async def set_active(self, external_id: str, active: bool) -> dict:
        raise RuntimeError("activation is not supported by this adapter")

    async def retry(self, run_id: str) -> dict:
        raise RuntimeError("retry is not supported by this adapter")


class N8nAdapter(WorkflowAdapter):
    provider = "n8n"
    label = "n8n"

    def __init__(self) -> None:
        self.base = os.environ.get("N8N_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("N8N_API_KEY", "")
        self.webhook_base = (os.environ.get("N8N_WEBHOOK_BASE_URL") or self.base).rstrip("/")

    def configured(self) -> bool:
        return bool(self.base and self.key)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"{self.base}/api/v1", timeout=20.0,
                                 headers={"X-N8N-API-KEY": self.key, "Accept": "application/json"})

    @staticmethod
    def _trigger_of(workflow: dict) -> tuple[str, str]:
        """(trigger label, webhook path or '')"""
        for node in workflow.get("nodes") or []:
            t = (node.get("type") or "").lower()
            if "webhook" in t:
                return "webhook", str((node.get("parameters") or {}).get("path") or "")
            if "cron" in t or "schedule" in t:
                return "schedule", ""
            if "trigger" in t:
                return t.split(".")[-1].replace("trigger", "").strip() or "trigger", ""
        return "manual", ""

    async def list_workflows(self) -> list[WorkflowRecord]:
        async with self._client() as c:
            r = await c.get("/workflows", params={"limit": 100})
            r.raise_for_status()
            items = r.json().get("data", [])
            out = []
            for w in items:
                trigger, path = self._trigger_of(w)
                out.append(WorkflowRecord(
                    id=f"n8n:{w['id']}", provider="n8n", external_id=str(w["id"]), name=w.get("name", "?"),
                    active=bool(w.get("active")), trigger=trigger, last_execution_at=None,
                    next_execution_at=None, last_status="",
                    meta={"webhook_path": path, "updated_at": w.get("updatedAt"), "tags": [
                        t.get("name") for t in (w.get("tags") or [])]}))
            return out

    @staticmethod
    def _run(e: dict) -> dict:
        started = e.get("startedAt")
        stopped = e.get("stoppedAt")
        duration = None
        if started and stopped:
            from datetime import datetime
            try:
                duration = int((datetime.fromisoformat(stopped.replace("Z", "+00:00")) -
                                datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() * 1000)
            except ValueError:
                duration = None
        status = e.get("status") or ("success" if e.get("finished") else "error")
        return {"id": f"n8n:{e['id']}", "external_id": str(e["id"]), "provider": "n8n",
                "workflow_external_id": str(e.get("workflowId", "")), "status": status,
                "started_at": started, "finished_at": stopped, "duration_ms": duration,
                "mode": e.get("mode", ""), "error": "", "retry_of": e.get("retryOf")}

    async def list_runs(self, external_id: str | None = None, limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if external_id:
            params["workflowId"] = external_id
        async with self._client() as c:
            r = await c.get("/executions", params=params)
            r.raise_for_status()
            return [self._run(e) for e in r.json().get("data", [])]

    async def get_run(self, run_id: str) -> dict | None:
        ext = run_id.split(":", 1)[-1]
        async with self._client() as c:
            r = await c.get(f"/executions/{ext}", params={"includeData": "true"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            e = r.json()
            out = self._run(e)
            data = e.get("data") or {}
            result = (data.get("resultData") or {})
            err = result.get("error") or {}
            out["error"] = err.get("message", "") if isinstance(err, dict) else str(err)
            out["last_node"] = result.get("lastNodeExecuted", "")
            out["node_results"] = list((result.get("runData") or {}).keys())
            return out

    async def trigger(self, external_id: str, payload: dict | None = None) -> dict:
        workflows = await self.list_workflows()
        wf = next((w for w in workflows if w.external_id == str(external_id)), None)
        if not wf:
            raise RuntimeError("workflow not found")
        path = wf.meta.get("webhook_path")
        if not path:
            raise RuntimeError(f"'{wf.name}' has no webhook trigger; n8n can only be triggered through a webhook node")
        url = f"{self.webhook_base}/webhook/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(url, json=payload or {})
        return {"ok": r.status_code < 400, "http_status": r.status_code, "url": url,
                "response": r.text[:1000]}

    async def set_active(self, external_id: str, active: bool) -> dict:
        async with self._client() as c:
            r = await c.post(f"/workflows/{external_id}/{'activate' if active else 'deactivate'}")
            r.raise_for_status()
            return {"ok": True, "active": bool(r.json().get("active", active))}

    async def retry(self, run_id: str) -> dict:
        ext = run_id.split(":", 1)[-1]
        async with self._client() as c:
            r = await c.post(f"/executions/{ext}/retry")
            r.raise_for_status()
            return {"ok": True, "execution": self._run(r.json())}


class WorkflowHub:
    def __init__(self, db: Database, bus: EventBus):
        self.db = db
        self.bus = bus
        self._adapters: dict[str, WorkflowAdapter] = {}
        self.register(N8nAdapter())
        self._last_error: dict[str, str] = {}

    def register(self, adapter: WorkflowAdapter) -> None:
        self._adapters[adapter.provider] = adapter

    def providers(self) -> list[dict]:
        return [{"provider": a.provider, "label": a.label, "configured": a.configured(),
                 "error": self._last_error.get(a.provider, "")} for a in self._adapters.values()]

    def configured(self) -> bool:
        return any(a.configured() for a in self._adapters.values())

    def _adapter_for(self, workflow_id: str) -> tuple[WorkflowAdapter, str]:
        provider, _, ext = workflow_id.partition(":")
        adapter = self._adapters.get(provider)
        if not adapter or not adapter.configured():
            raise RuntimeError(f"workflow provider '{provider}' is not configured")
        return adapter, ext

    async def sync(self) -> list[dict]:
        """Refresh the cached workflow list from every configured adapter."""
        out = []
        for adapter in self._adapters.values():
            if not adapter.configured():
                continue
            try:
                items = await adapter.list_workflows()
                runs = await adapter.list_runs(limit=100)
                self._last_error[adapter.provider] = ""
            except Exception as e:  # noqa: BLE001
                self._last_error[adapter.provider] = f"{e.__class__.__name__}: {str(e)[:200]}"
                continue
            latest: dict[str, dict] = {}
            for r in runs:
                key = r["workflow_external_id"]
                if key not in latest or (r["started_at"] or "") > (latest[key]["started_at"] or ""):
                    latest[key] = r
                self.db.upsert("workflow_runs", {
                    "id": r["id"], "workflow_id": f"{adapter.provider}:{key}", "provider": adapter.provider,
                    "external_id": r["external_id"], "status": r["status"], "started_at": r["started_at"],
                    "finished_at": r["finished_at"], "duration_ms": r["duration_ms"], "error": r.get("error", ""),
                    "meta": dumps({"mode": r.get("mode")})})
            for w in items:
                last = latest.get(w.external_id)
                if last:
                    w.last_execution_at = last["started_at"]
                    w.last_status = last["status"]
                self.db.upsert("workflows", {
                    "id": w.id, "provider": w.provider, "external_id": w.external_id, "name": w.name,
                    "active": 1 if w.active else 0, "trigger": w.trigger,
                    "last_execution_at": w.last_execution_at, "next_execution_at": w.next_execution_at,
                    "last_status": w.last_status, "meta": dumps(w.meta), "updated_at": now_iso()})
                out.append(w.public())
        self.bus.publish("workflow.synced", {"count": len(out)})
        return out

    def cached(self) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM workflows ORDER BY name")
        for r in rows:
            r["active"] = bool(r["active"])
            r["meta"] = loads(r["meta"], {})
        return rows

    def cached_runs(self, workflow_id: str = "", limit: int = 50) -> list[dict]:
        if workflow_id:
            rows = self.db.fetchall("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY started_at DESC LIMIT ?",
                                    (workflow_id, limit))
        else:
            rows = self.db.fetchall("SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = loads(r["meta"], {})
        return rows

    async def trigger(self, workflow_id: str, payload: dict | None, by: str) -> dict:
        adapter, ext = self._adapter_for(workflow_id)
        result = await adapter.trigger(ext, payload)
        self.bus.publish("workflow.triggered", {"workflow_id": workflow_id, "by": by, "result": result})
        return result

    async def set_active(self, workflow_id: str, active: bool) -> dict:
        adapter, ext = self._adapter_for(workflow_id)
        result = await adapter.set_active(ext, active)
        self.db.execute("UPDATE workflows SET active=?, updated_at=? WHERE id=?",
                        (1 if result.get("active", active) else 0, now_iso(), workflow_id))
        return result

    async def run_detail(self, run_id: str) -> dict | None:
        adapter, _ = self._adapter_for(run_id)
        return await adapter.get_run(run_id)

    async def retry(self, run_id: str) -> dict:
        adapter, _ = self._adapter_for(run_id)
        return await adapter.retry(run_id)

    def stats(self, days: int = 7) -> dict:
        rows = self.db.fetchall(
            "SELECT status, COUNT(*) AS n, AVG(duration_ms) AS avg_ms FROM workflow_runs "
            "WHERE started_at >= datetime('now', ?) GROUP BY status", (f"-{int(days)} days",))
        return {r["status"]: {"count": r["n"], "avg_ms": r["avg_ms"]} for r in rows}
