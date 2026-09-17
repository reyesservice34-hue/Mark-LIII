"""Analytics: aggregated real data from tasks, runs, tools, workflows and metrics."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import AppState, current_principal, get_state
from .. import ModuleSpec

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/overview")
async def overview(days: int = 14, state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    db = state.db
    days = max(1, min(days, 90))
    tasks = state.services["tasks"]
    runs = db.fetchall(
        "SELECT agent_id, status, COUNT(*) AS n, "
        "AVG((julianday(COALESCE(finished_at, started_at)) - julianday(started_at)) * 86400) AS avg_secs "
        "FROM agent_runs WHERE started_at >= datetime('now', ?) GROUP BY agent_id, status", (f"-{days} days",))
    tool_calls = db.fetchall(
        "SELECT tool, status, COUNT(*) AS n FROM audit_events WHERE action='tool.call' AND ts >= datetime('now', ?) "
        "AND tool != '' GROUP BY tool, status ORDER BY n DESC", (f"-{days} days",))
    usage = db.fetchall(
        "SELECT substr(started_at,1,10) AS day, usage FROM agent_runs WHERE started_at >= datetime('now', ?)",
        (f"-{days} days",))
    from ...db import loads
    tokens_by_day: dict[str, dict] = {}
    for r in usage:
        u = loads(r["usage"], {})
        d = tokens_by_day.setdefault(r["day"], {"day": r["day"], "input_tokens": 0, "output_tokens": 0, "runs": 0})
        d["input_tokens"] += int(u.get("input_tokens", 0) or 0)
        d["output_tokens"] += int(u.get("output_tokens", 0) or 0)
        d["runs"] += 1
    approvals = db.fetchall(
        "SELECT status, COUNT(*) AS n FROM approvals WHERE created_at >= datetime('now', ?) GROUP BY status",
        (f"-{days} days",))
    log_levels = db.fetchall(
        "SELECT level, COUNT(*) AS n FROM logs WHERE ts >= datetime('now', ?) GROUP BY level", (f"-{days} days",))
    metrics = state.services["metrics"].stored_history(hours=min(days * 24, 24 * 14))
    return {
        "days": days,
        "tasks": {"by_status": tasks.stats(days), "daily": tasks.daily_counts(days), "counts": tasks.counts()},
        "runs": runs, "tool_calls": tool_calls,
        "tokens": sorted(tokens_by_day.values(), key=lambda d: d["day"]),
        "approvals": {r["status"]: r["n"] for r in approvals},
        "logs": {r["level"]: r["n"] for r in log_levels},
        "workflows": state.workflows.stats(days),
        "messages": state.services["chat"].message_counts(days),
        "metrics": [{"ts": m["ts"], "cpu": m["cpu"], "ram": m["ram"], "disk": m["disk"]} for m in metrics],
        "files": state.services["files"].usage(),
    }


MODULE = ModuleSpec(id="analytics", title="Analytics", router=router, icon="bar-chart-3", path="/analytics",
                    order=120, description="Usage and performance")
