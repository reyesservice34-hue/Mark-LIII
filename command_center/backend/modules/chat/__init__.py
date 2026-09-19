"""JARVIS Chat: conversations, messages, streaming runs, attachments."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from ...services.files import WorkspaceError
from .. import ModuleSpec

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ConversationCreate(BaseModel):
    title: str = ""


class ConversationPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


class Attachment(BaseModel):
    path: str
    name: str = ""
    mime: str = ""
    size: int = 0


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=40_000)
    attachments: list[Attachment] = Field(default_factory=list)
    agent_id: str | None = None
    stream: bool = True
    channel: str = ""            # "voice" = Live-Konsole: Antwort wird vorgelesen


def _conv_or_404(state: AppState, conv_id: str, principal: Principal) -> dict:
    conv = state.services["chat"].get_conversation(conv_id, principal.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/conversations")
async def list_conversations(q: str = "", archived: bool = False, state: AppState = Depends(get_state),
                             principal: Principal = Depends(current_principal)):
    return {"conversations": state.services["chat"].list_conversations(principal.id, q=q, archived=archived)}


@router.post("/conversations", status_code=201)
async def create_conversation(body: ConversationCreate, state: AppState = Depends(get_state),
                              principal: Principal = Depends(current_principal)):
    return {"conversation": state.services["chat"].create_conversation(principal.id, body.title, actor=principal.actor)}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, limit: int = 200, before: str = "", state: AppState = Depends(get_state),
                           principal: Principal = Depends(current_principal)):
    conv = _conv_or_404(state, conv_id, principal)
    messages = state.services["chat"].messages(conv_id, limit=limit, before=before)
    active = [r for r in state.runtime.active_runs() if r["conversation_id"] == conv_id]
    return {"conversation": conv, "messages": messages, "active_runs": active}


@router.patch("/conversations/{conv_id}")
async def patch_conversation(conv_id: str, body: ConversationPatch, state: AppState = Depends(get_state),
                             principal: Principal = Depends(current_principal)):
    _conv_or_404(state, conv_id, principal)
    return {"conversation": state.services["chat"].update_conversation(conv_id, principal.id, title=body.title,
                                                                        archived=body.archived)}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, state: AppState = Depends(get_state),
                              principal: Principal = Depends(current_principal)):
    _conv_or_404(state, conv_id, principal)
    state.services["chat"].delete_conversation(conv_id, principal.id)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="conversation.delete", target=conv_id,
                    status="ok")
    return {"ok": True}


@router.get("/search")
async def search(q: str = Query(min_length=1), state: AppState = Depends(get_state),
                 principal: Principal = Depends(current_principal)):
    return {"results": state.services["chat"].search(principal.id, q)}


_RUN_EVENTS = {"chat.delta", "chat.tool_call", "chat.tool_result", "run.status", "run.activity",
               "run.finished", "approval.requested", "message.updated"}


class RunSubscription:
    """Captures a run's events from *before* the run starts, so a fast run can
    never lose its first deltas between task creation and stream start."""

    def __init__(self, state: AppState):
        self.state = state
        self.queue: asyncio.Queue = asyncio.Queue()
        self.run_id = ""
        self.message_id = ""
        state.bus.add_listener(self._on)

    def _on(self, ev) -> None:
        if ev.type in _RUN_EVENTS:
            self.queue.put_nowait(ev)

    def bind(self, run_id: str, message_id: str) -> None:
        self.run_id, self.message_id = run_id, message_id

    def matches(self, ev) -> bool:
        d = ev.data
        return (d.get("run_id") == self.run_id
                or (ev.type in ("run.status", "run.finished") and d.get("id") == self.run_id)
                or (ev.type == "message.updated" and d.get("id") == self.message_id)
                or (ev.type == "run.activity" and d.get("parent_run_id") == self.run_id))

    def close(self) -> None:
        self.state.bus.remove_listener(self._on)

    async def stream(self):
        try:
            yield f"event: run.accepted\ndata: {json.dumps({'run_id': self.run_id, 'message_id': self.message_id})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(self.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    handle = self.state.runtime.get_run(self.run_id)
                    if handle is None or handle.status in ("completed", "failed", "cancelled"):
                        # the run ended before we bound (or its finish event was lost) — close cleanly
                        data = handle.public() if handle else {"id": self.run_id, "status": "completed"}
                        yield f"event: run.finished\ndata: {json.dumps({'type': 'run.finished', 'data': data}, default=str)}\n\n"
                        return
                    yield ": ping\n\n"
                    continue
                if not self.matches(ev):
                    continue
                yield ev.sse()
                if ev.type == "run.finished":
                    break
        finally:
            self.close()


def _stream_response(sub: RunSubscription, extra_headers: dict | None = None) -> StreamingResponse:
    return StreamingResponse(sub.stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Run-Id": sub.run_id, "X-Message-Id": sub.message_id,
                                      **(extra_headers or {})})


@router.post("/conversations/{conv_id}/messages", status_code=202)
async def send_message(conv_id: str, body: MessageCreate, state: AppState = Depends(get_state),
                       principal: Principal = Depends(require_role("operator"))):
    conv = _conv_or_404(state, conv_id, principal)
    if not body.content.strip() and not body.attachments:
        raise HTTPException(status_code=400, detail="Message is empty")
    files = state.services["files"]
    attachments = []
    for a in body.attachments[:10]:
        try:
            path = files.resolve(a.path, must_exist=True)
        except WorkspaceError:
            raise HTTPException(status_code=400, detail=f"Attachment not found: {a.path}")
        info = files.entry(path)
        attachments.append({"path": info["path"], "name": a.name or info["name"], "mime": a.mime or info["mime"],
                            "size": info["size"]})
    chat = state.services["chat"]
    user_msg = chat.add_message(conv_id, "user", body.content.strip(),
                                meta={"attachments": attachments, "actor": principal.actor})
    teaching = state.services.get("teaching")
    if teaching is not None and teaching.active_for(principal.id):
        teaching.record_message(user_id=principal.id, role="user", text=body.content.strip())
    state.log.audit(actor_type=principal.kind, actor_id=principal.actor, action="chat.message", target=conv_id,
                    status="ok", meta={"message_id": user_msg["id"], "attachments": len(attachments)})
    sub = RunSubscription(state) if body.stream else None
    try:
        result = await state.runtime.start_chat_run(conv, user_msg, principal, agent_id=body.agent_id,
                                                    channel=body.channel)
    except Exception:
        if sub:
            sub.close()
        raise
    if not body.stream:
        return {"user_message": user_msg, **result}
    sub.bind(result["run"]["id"], result["message"]["id"])
    return _stream_response(sub, {"X-User-Message-Id": user_msg["id"]})


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, state: AppState = Depends(get_state), principal: Principal = Depends(current_principal)):
    handle = state.runtime.get_run(run_id)
    if not handle:
        raise HTTPException(status_code=404, detail="Run not found or already finished")
    sub = RunSubscription(state)
    sub.bind(run_id, handle.message_id or "")
    return _stream_response(sub)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    ok = await state.runtime.cancel_run(run_id, by=principal.actor)
    return {"ok": ok}


@router.post("/conversations/{conv_id}/messages/{message_id}/regenerate", status_code=202)
async def regenerate(conv_id: str, message_id: str, state: AppState = Depends(get_state),
                     principal: Principal = Depends(require_role("operator"))):
    conv = _conv_or_404(state, conv_id, principal)
    chat = state.services["chat"]
    target = chat.get_message(message_id)
    if not target or target["conversation_id"] != conv_id:
        raise HTTPException(status_code=404, detail="Message not found")
    msgs = chat.messages(conv_id)
    idx = next((i for i, m in enumerate(msgs) if m["id"] == message_id), -1)
    user_msg = None
    for m in reversed(msgs[: idx + 1]):
        if m["role"] == "user":
            user_msg = m
            break
    if not user_msg:
        raise HTTPException(status_code=400, detail="No user message to regenerate from")
    # drop everything after the user message (the old answer included)
    following = [m for m in msgs if m["created_at"] > user_msg["created_at"]]
    if following:
        chat.delete_messages_from(conv_id, following[0]["id"])
    sub = RunSubscription(state)
    try:
        result = await state.runtime.start_chat_run(conv, user_msg, principal)
    except Exception:
        sub.close()
        raise
    sub.bind(result["run"]["id"], result["message"]["id"])
    return _stream_response(sub)


@router.post("/attachments", status_code=201)
async def upload_attachment(file: UploadFile = File(...), state: AppState = Depends(get_state),
                            principal: Principal = Depends(require_role("operator"))):
    files = state.services["files"]

    async def chunks():
        while True:
            chunk = await file.read(1024 * 256)
            if not chunk:
                break
            yield chunk

    data = [c async for c in chunks()]
    try:
        info = files.save_upload(file.filename or "upload", iter(data), subdir="uploads", owner=principal.actor)
    except WorkspaceError as e:
        raise HTTPException(status_code=413, detail=str(e))
    return {"attachment": info}


MODULE = ModuleSpec(
    id="chat", title="Chat", router=router, icon="message-square", path="/chat", order=20, mobile_priority=100,
    description="Mit JARVIS sprechen",
    commands=[{"id": "chat.new", "title": "Talk to JARVIS", "path": "/chat?new=1", "shortcut": "g c"}],
)
