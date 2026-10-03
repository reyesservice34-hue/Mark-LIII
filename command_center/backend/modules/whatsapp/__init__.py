"""
WhatsApp (Twilio) — eine echte, verknüpfte Telefonnummer schreibt mit MIA.

POST /api/whatsapp/webhook ist öffentlich (Twilio ruft es ohne Cookie/Token
auf) und hat deshalb KEINE current_principal-Abhängigkeit. Seine Sicherheit
kommt aus zwei getrennten Prüfungen:

  1. Twilio-Signatur (services/whatsapp.verify_twilio_signature) — beweist,
     dass die Anfrage wirklich von Twilio kommt.
  2. whatsapp_links-Tabelle — beweist, dass die absendende Nummer wirklich
     einem MIA-Nutzer zugeordnet ist. Nur beides zusammen erreicht einen
     echten Agentenlauf; eine gültige Signatur allein öffnet nichts.

Der Webhook antwortet sofort mit leerem TwiML (Twilios Timeout ist kurz,
ein echter Agentenlauf kann Minuten dauern — siehe orchestrator/runtime.py).
Die fertige Antwort geht asynchron per Twilio-REST-API raus, sobald der Lauf
fertig ist. Freigabepflichtige/riskante Aktionen pausieren wie auf jedem
anderen Kanal auch (ToolRegistry) — WhatsApp bekommt dafür nur den Hinweis,
im Dashboard zu bestätigen, nie eine "Antworte mit JA"-Abkürzung.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...auth import Principal
from ...deps import AppState, get_state, require_role
from ...services import whatsapp
from .. import ModuleSpec

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])

_EMPTY_TWIML = "<Response></Response>"


def _empty() -> Response:
    return Response(content=_EMPTY_TWIML, media_type="application/xml")


def _outcome_text(state: AppState, run_id: str) -> str:
    """Den fertigen Lauf in eine WhatsApp-taugliche Textantwort übersetzen.
    Nie den rohen Fehlertext zeigen — WhatsApp ist ein Sperrbildschirm-sichtbarer,
    niedriger vertrauenswürdiger Kanal als das Dashboard."""
    row = state.db.fetchone("SELECT * FROM agent_runs WHERE id=?", (run_id,))
    if not row:
        return "Ich konnte kein Ergebnis finden."
    pending = state.db.fetchone(
        "SELECT * FROM approvals WHERE run_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (run_id,))
    if pending:
        return (f"{pending['action']} auf {pending['target']} braucht deine Freigabe "
                f"({pending['reason']}). Bitte im Command Center bestätigen.")
    if row["status"] == "failed":
        return "❌ Das hat leider nicht geklappt. Bitte schau im Command Center nach."
    msg = state.services["chat"].get_message(row["message_id"]) if row["message_id"] else None
    text = (msg or {}).get("content", "").strip() if msg else ""
    return text or "✅ Erledigt."


async def _deliver_when_done(state: AppState, phone: str, run_id: str) -> None:
    """Fire-and-forget-Task: alles hier drin muss selbst für Logging sorgen,
    sonst verschwindet ein Fehler nur als "Task exception was never retrieved"
    auf stdout — ohne Spur im strukturierten Log und ohne Antwort an den Nutzer."""
    try:
        handle = state.runtime.get_run(run_id)
        if handle is not None and handle.task is not None:
            try:
                await handle.task
            except Exception:  # noqa: BLE001 — der Lauf selbst loggt seinen Fehler bereits (runtime.py)
                pass
        text = _outcome_text(state, run_id)
        await whatsapp.send_whatsapp(phone, text)
    except Exception as e:  # noqa: BLE001 — letzte Sicherung, damit nichts lautlos verschwindet
        state.log.error("whatsapp", f"delivery failed: {type(e).__name__}: {e}", run_id=run_id)
    finally:
        whatsapp.end_inflight(phone)


@router.post("/webhook")
async def webhook(request: Request, state: AppState = Depends(get_state)):
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("x-twilio-signature", "")
    webhook_url = os.environ.get("JARVIS_CC_WHATSAPP_URL", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()

    if not webhook_url or not whatsapp.verify_twilio_signature(auth_token, webhook_url, params, signature):
        state.log.audit(actor_type="external", actor_id=whatsapp.normalize_phone(params.get("From", "")) or "unknown",
                        action="whatsapp.webhook", status="denied", meta={"reason": "bad_signature"})
        raise HTTPException(status_code=403, detail="invalid signature")

    phone = whatsapp.normalize_phone(params.get("From", ""))
    body = (params.get("Body", "") or "").strip()
    if not phone or not body:
        return _empty()

    link = whatsapp.get_link(state.db, phone)
    if not link or link["disabled"]:
        state.log.audit(actor_type="external", actor_id=phone, action="whatsapp.webhook", status="denied",
                        meta={"reason": "unlinked"})
        asyncio.create_task(whatsapp.send_whatsapp(
            phone, "Diese Nummer ist nicht mit MIA verknüpft. Bitte im Command Center freischalten lassen."))
        return _empty()

    if not state.auth.limiter.allow(f"whatsapp:{phone}", 20, 60.0):
        asyncio.create_task(whatsapp.send_whatsapp(phone, "Zu viele Nachrichten kurz hintereinander — bitte kurz warten."))
        return _empty()

    if not whatsapp.start_inflight(phone):
        asyncio.create_task(whatsapp.send_whatsapp(phone, "Ich arbeite noch an deiner letzten Nachricht …"))
        return _empty()

    try:
        user = state.auth.get_user(link["user_id"])
        if not user or user.get("disabled"):
            asyncio.create_task(whatsapp.send_whatsapp(phone, "Dieser Zugang ist deaktiviert."))
            return _empty()

        principal = Principal(kind="user", id=user["id"], name=user.get("display_name") or user["username"],
                              role=user["role"], actor=user["username"])

        chat = state.services["chat"]
        conv = chat.get_conversation(link["conversation_id"]) if link["conversation_id"] else None
        if conv is None:
            conv = chat.create_conversation(principal.id, title=body[:60], actor=f"whatsapp:{phone}")
            whatsapp.set_link_conversation(state.db, phone, conv["id"])
        user_msg = chat.add_message(conv["id"], "user", body, meta={"via": "whatsapp", "phone": phone})
        state.log.audit(actor_type="user", actor_id=principal.actor, action="whatsapp.message", target=conv["id"],
                        status="ok", meta={"phone": phone})

        result = await state.runtime.start_chat_run(conv, user_msg, principal, channel="whatsapp")
    except Exception:
        whatsapp.end_inflight(phone)
        raise

    run_id = result["run"]["id"]
    asyncio.create_task(_deliver_when_done(state, phone, run_id))
    return _empty()


# ── Verwaltung (admin) ────────────────────────────────────────────────────

class LinkBody(BaseModel):
    phone_number: str = Field(min_length=3, max_length=32)
    user_id: str = Field(min_length=1)


@router.get("/links")
async def get_links(state: AppState = Depends(get_state), _: Principal = Depends(require_role("admin"))):
    return {"links": whatsapp.list_links(state.db)}


@router.post("/links", status_code=201)
async def create_link(body: LinkBody, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    phone = whatsapp.normalize_phone(body.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="Ungültige Telefonnummer")
    user = state.auth.get_user(body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
    link = whatsapp.upsert_link(state.db, phone, body.user_id, principal.actor)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="whatsapp.link.create", target=phone,
                    status="ok", meta={"user_id": body.user_id})
    return {"link": link}


@router.delete("/links/{phone_number}")
async def remove_link(phone_number: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    phone = whatsapp.normalize_phone(phone_number)
    if not whatsapp.delete_link(state.db, phone):
        raise HTTPException(status_code=404, detail="Verknüpfung nicht gefunden")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="whatsapp.link.delete", target=phone,
                    status="ok")
    return {"ok": True}


@router.get("/status")
async def get_status(state: AppState = Depends(get_state), _: Principal = Depends(require_role("admin"))):
    return whatsapp.status(state.db)


MODULE = ModuleSpec(id="whatsapp", title="WhatsApp", router=router, nav=False, order=206,
                    description="WhatsApp-Verknüpfung mit MIA (Twilio)")
