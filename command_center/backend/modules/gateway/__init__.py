"""
/v1 gateway — the control-plane contract the desktop client already speaks.

  POST /v1/commands  {actor, command, conversation_id?}   header X-Jarvis-Token
    -> 202 {job_id, conversation_id, status, risk, approval_required, approval_code?}
  GET  /v1/commands/{job_id}
    -> {status, routed_to, result, error, finished_at}
  POST /v1/memory ; GET /v1/memory/search

A "job" is a Master Agent run on a conversation owned by the token. Approval
requests raised during the run surface as `awaiting_approval`, exactly as the
desktop bridge expects.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...db import new_id, now_iso
from ...deps import AppState, current_principal, get_state
from .. import ModuleSpec

router = APIRouter(prefix="/v1", tags=["gateway"])


class CommandBody(BaseModel):
    actor: str = ""
    command: str = Field(min_length=1, max_length=40_000)
    conversation_id: str | None = None


class MemoryBody(BaseModel):
    actor: str = ""
    text: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


def _principal(principal: Principal) -> Principal:
    if principal.kind != "token":
        # Browser sessions may use it too (handy for testing) but the desktop uses tokens.
        return principal
    return principal


def _job_view(state: AppState, run_id: str) -> dict:
    row = state.db.fetchone("SELECT * FROM agent_runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    handle = state.runtime.get_run(run_id)
    status = handle.status if handle else row["status"]
    pending = state.db.fetchone(
        "SELECT * FROM approvals WHERE run_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (run_id,))
    msg = state.services["chat"].get_message(row["message_id"]) if row["message_id"] else None
    result_text = (msg or {}).get("content", "") if msg else ""
    out = {"job_id": run_id, "conversation_id": row["conversation_id"], "routed_to": row["agent_id"],
           "status": {"planning": "running", "executing": "running", "delegated": "running",
                      "waiting": "awaiting_approval", "completed": "completed", "failed": "failed",
                      "cancelled": "cancelled"}.get(status, status),
           "result": result_text if status in ("completed", "waiting") else "",
           "error": row["error"] or ((msg or {}).get("meta", {}).get("error", "") if msg else ""),
           "finished_at": row["finished_at"], "risk": pending["risk"] if pending else "",
           "approval_required": bool(pending), "approval_code": pending["code"] if pending else ""}
    if pending:
        out["result"] = (f"{pending['action']} on {pending['target']} needs your approval "
                         f"({pending['reason']}). Approve it in the Command Center.")
    return out


@router.post("/commands", status_code=202)
async def submit_command(body: CommandBody, state: AppState = Depends(get_state),
                         principal: Principal = Depends(current_principal)):
    if principal.role == "viewer":
        raise HTTPException(status_code=403, detail="token lacks operator role")
    chat = state.services["chat"]
    owner = principal.id
    conv = chat.get_conversation(body.conversation_id, owner) if body.conversation_id else None
    if conv is None:
        conv = chat.create_conversation(owner, title=body.command[:60], actor=principal.actor)
    user_msg = chat.add_message(conv["id"], "user", body.command, meta={"actor": body.actor or principal.actor,
                                                                        "via": "gateway"})
    state.log.audit(actor_type="token", actor_id=principal.actor, action="gateway.command", target=conv["id"],
                    status="ok", meta={"desktop_actor": body.actor})
    result = await state.runtime.start_chat_run(conv, user_msg, principal)
    view = _job_view(state, result["run"]["id"])
    view["status"] = "queued"
    return view


@router.get("/commands/{job_id}")
async def get_command(job_id: str, state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    if job_id == "health-probe":
        return {"status": "ok"}
    row = state.db.fetchone("SELECT conversation_id FROM agent_runs WHERE id=?", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="job not found")
    conv = state.services["chat"].get_conversation(row["conversation_id"]) if row["conversation_id"] else None
    if conv and conv["user_id"] != principal.id and principal.role != "admin":
        raise HTTPException(status_code=403, detail="not your job")
    return _job_view(state, job_id)


@router.post("/memory")
async def remember(body: MemoryBody, state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    state.db.insert("memory", {"id": new_id("mem"), "text": body.text, "actor": body.actor or principal.actor,
                               "conversation_id": body.conversation_id, "created_at": now_iso()})
    return {"ok": True}


@router.get("/memory/search")
async def search_memory(q: str = "", state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    rows = state.db.fetchall("SELECT text, actor, created_at FROM memory WHERE text LIKE ? ORDER BY created_at DESC LIMIT 20",
                             (f"%{q}%",))
    return {"results": rows}


MODULE = ModuleSpec(id="gateway", title="Gateway", router=router, nav=False, order=200)
