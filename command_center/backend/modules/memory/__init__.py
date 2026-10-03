"""
Gedächtnis: was er weiß, und was du ihm sagst.

Zwei Dinge, die zusammengehören und bisher beide nur im Quelltext standen:

* **Anweisungen** — der Text, der in jedem Gespräch mitläuft. Hier steht, wie
  er sich verhalten soll: „Antworte kurz", „Frag bei Kunden immer nach",
  „Preise nie ohne Aufschlag nennen". Er gilt für den Chat und für die
  Sprachleitung gleichermaßen.
* **Gemerktes** — einzelne Tatsachen, die er sich über die Zeit notiert hat
  oder die du ihm einträgst. Jede einzeln löschbar, denn ein falscher
  Merksatz vergiftet still jedes weitere Gespräch, und bisher kam man an ihn
  nur über die Datenbank heran.

Der Unterschied ist wichtig: Anweisungen sind Regeln, Gemerktes sind
Tatsachen. Regeln gehören hierher, Tatsachen dürfen auch von ihm selbst
kommen — deshalb ist das eine ein Textfeld und das andere eine Liste.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...db import new_id, now_iso
from ...deps import AppState, current_principal, get_state, require_role
from ...services.memory_sources import terms
from .. import ModuleSpec

router = APIRouter(prefix="/api/memory", tags=["memory"])

SETTING_KEY = "master_instructions"
LEARNING_MODE_KEY = "learning_mode_enabled"
MAX_INSTRUCTIONS = 20_000
# Wie viele Sätze ins Hauptgedächtnis dürfen. Die Grenze ist keine Schikane,
# sondern Physik: Was hier steht, wird bei JEDER Anfrage mitgeschickt und
# jedes Mal bezahlt. Dreißig gute Sätze wirken; dreihundert ertränken sie.
MAX_PINNED = 30


class Instructions(BaseModel):
    text: str = Field(max_length=MAX_INSTRUCTIONS)


class Fact(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    pinned: bool = False


class LearningMode(BaseModel):
    enabled: bool


def learning_mode_enabled(state: AppState) -> bool:
    return bool(state.db.get_setting(LEARNING_MODE_KEY, False))


# ── Merk-Regeln ──────────────────────────────────────────────────────────
# Mia lernt aus Gesprächen, aber nicht blind. Was der Nutzer ausdrücklich sagt,
# darf sie sofort merken. Was sie selbst schließt oder aus fremdem Text (Web,
# Mail, Tool-Ergebnis) ableitet, wird nur ein Vorschlag und erst mit dem Ja des
# Nutzers zum Gedächtnis. Ob etwas "ausdrücklich gesagt" wurde, entscheidet nicht
# das Modell, sondern ein Abgleich mit den echten Nutzernachrichten im Chat.
KINDS = ("fact", "preference", "correction")
CLAIMS = ("user_said", "inferred")
MAX_PENDING = 30                 # offene Vorschläge insgesamt
MAX_PENDING_PER_CONVERSATION = 8
PROPOSAL_TTL_DAYS = 30
SIMILAR = 0.7                    # ab dieser Wortüberlappung gilt ein Satz als schon gemerkt
GROUNDED = 0.6                   # so viel vom Satz muss in den Nutzerworten vorkommen

_YES = re.compile(r"^\W*(ja|jo|jep|jap|ok|okay|klar|gerne|gern|genau|stimmt|passt|bitte|mach das|"
                  r"merk dir das|merk das|speicher das|speichere das)\b", re.I)
_NO = re.compile(r"\b(nein|nee|nicht|kein|keine|stopp|vergiss)\b", re.I)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def compose_text(text: str, kind: str, why: str = "") -> str:
    """Eine Korrektur trägt ihren Grund im Satz, sonst wendet sie sich später falsch an."""
    text, why = _norm(text), _norm(why)
    if kind == "correction" and why and "grund:" not in text.lower():
        text = f"{text} (Grund: {why})"
    return text[:2000]


def recent_user_texts(state: AppState, conversation_id: str | None, n: int = 3) -> list[str]:
    if not conversation_id:
        return []
    try:
        msgs = state.services["chat"].messages(conversation_id, limit=12)
    except Exception:  # noqa: BLE001 — ohne Verlauf gilt nichts als ausdrücklich gesagt
        return []
    return [str(m.get("content", "")) for m in msgs if m.get("role") == "user"][-n:]


def grounded_in_user_words(state: AppState, conversation_id: str | None, text: str) -> bool:
    mine = terms(text)
    if len(mine) < 2:
        return False
    said: set[str] = set()
    for t in recent_user_texts(state, conversation_id):
        said |= terms(t)
    return len(mine & said) / len(mine) >= GROUNDED


def user_confirmed_in_chat(state: AppState, conversation_id: str | None) -> bool:
    """Die letzte Nutzernachricht ist ein kurzes Ja (und kein "ja, aber nicht so")."""
    texts = recent_user_texts(state, conversation_id, 1)
    if not texts:
        return False
    t = texts[-1].strip()
    return len(t) <= 80 and bool(_YES.match(t)) and not _NO.search(t)


def find_similar(db, text: str, include_proposals: bool = True) -> dict | None:
    mine, flat = terms(text), _norm(text).lower()
    pools = [("SELECT id, text FROM memory", ())]
    if include_proposals:
        pools.append(("SELECT id, text FROM memory_proposals WHERE status='pending'", ()))
    for sql, params in pools:
        for r in db.fetchall(sql, params):
            if _norm(r["text"]).lower() == flat:
                return r
            other = terms(r["text"])
            if len(mine) >= 3 and other and len(mine & other) / len(mine | other) >= SIMILAR:
                return r
    return None


def route_remember(state: AppState, principal, conversation_id: str | None, text: str, claim: str) -> str:
    """'store' nur für Gesagtes eines angemeldeten Nutzers, das in seinen Worten steht; sonst 'propose'."""
    if (claim == "user_said" and principal.kind == "user" and principal.has_role("operator")
            and grounded_in_user_words(state, conversation_id, text)):
        return "store"
    return "propose"


def store_fact(state: AppState, *, text: str, kind: str, source: str, actor: str,
               conversation_id: str | None, pinned: bool = False) -> dict:
    row = {"id": new_id("mem"), "text": text, "actor": actor, "conversation_id": conversation_id,
           "created_at": now_iso(), "pinned": 1 if pinned else 0, "source": source, "kind": kind}
    state.db.insert("memory", row)
    return row


async def index_fact(state: AppState, row: dict) -> None:
    try:  # Bedeutungs-Vektor sofort anlegen; klappt es nicht, holt die Suche es später nach
        from ...ai import embeddings as _emb
        await _emb.index_one(state.db, row["id"], row["text"])
    except Exception:  # noqa: BLE001
        pass


def pending_proposals(db, limit: int = 50) -> list[dict]:
    db.execute("UPDATE memory_proposals SET status='expired', decided_at=? WHERE status='pending' "
               "AND datetime(created_at) < datetime('now', ?)", (now_iso(), f"-{PROPOSAL_TTL_DAYS} days"))
    return db.fetchall("SELECT * FROM memory_proposals WHERE status='pending' "
                       "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),))


def propose(state: AppState, *, text: str, kind: str, source: str, actor: str,
            conversation_id: str | None) -> tuple[str, dict | None]:
    """('proposed'|'duplicate'|'full', Zeile). Die Obergrenzen halten Spam durch fremden Text klein."""
    pending_proposals(state.db, 1)  # räumt Abgelaufene ab
    dup = find_similar(state.db, text)
    if dup:
        return "duplicate", dup
    total = state.db.scalar("SELECT COUNT(*) FROM memory_proposals WHERE status='pending'") or 0
    mine = state.db.scalar("SELECT COUNT(*) FROM memory_proposals WHERE status='pending' "
                           "AND conversation_id IS ?", (conversation_id,)) or 0
    if total >= MAX_PENDING or mine >= MAX_PENDING_PER_CONVERSATION:
        return "full", None
    row = {"id": new_id("prop"), "text": text, "kind": kind, "source": source, "actor": actor,
           "conversation_id": conversation_id, "status": "pending", "created_at": now_iso()}
    state.db.insert("memory_proposals", row)
    state.bus.publish("memory.proposal", {"id": row["id"], "kind": kind, "source": source})
    return "proposed", row


def latest_pending(db, conversation_id: str | None) -> dict | None:
    pending_proposals(db, 1)
    return db.fetchone("SELECT * FROM memory_proposals WHERE status='pending' AND conversation_id IS ? "
                       "ORDER BY created_at DESC LIMIT 1", (conversation_id,))


def accept_proposal(state: AppState, proposal_id: str, actor: str) -> dict | None:
    row = state.db.fetchone("SELECT * FROM memory_proposals WHERE id=? AND status='pending'", (proposal_id,))
    if not row:
        return None
    if find_similar(state.db, row["text"], include_proposals=False):
        mem = state.db.fetchone("SELECT * FROM memory WHERE text=?", (row["text"],)) or {"text": row["text"]}
    else:
        mem = store_fact(state, text=row["text"], kind=row["kind"], source="confirmed",
                         actor=row["actor"] or actor, conversation_id=row["conversation_id"])
    state.db.update("memory_proposals", proposal_id,
                    {"status": "accepted", "decided_at": now_iso(), "decided_by": actor})
    return mem


def reject_proposal(state: AppState, proposal_id: str, actor: str) -> bool:
    if not state.db.fetchone("SELECT id FROM memory_proposals WHERE id=? AND status='pending'", (proposal_id,)):
        return False
    state.db.update("memory_proposals", proposal_id,
                    {"status": "rejected", "decided_at": now_iso(), "decided_by": actor})
    return True


@router.get("")
async def overview(limit: int = 200, q: str = "", state: AppState = Depends(get_state),
                   _: Principal = Depends(current_principal)):
    if q:
        rows = state.db.fetchall(
            "SELECT * FROM memory WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{q}%", max(1, min(limit, 500))))
    else:
        rows = state.db.fetchall("SELECT * FROM memory ORDER BY created_at DESC LIMIT ?",
                                 (max(1, min(limit, 500)),))
    return {
        "instructions": state.db.get_setting(SETTING_KEY, "") or "",
        "facts": rows,
        "core": state.db.fetchall("SELECT * FROM memory WHERE pinned=1 ORDER BY created_at"),
        "max_core": MAX_PINNED,
        "total": state.db.scalar("SELECT COUNT(*) FROM memory") or 0,
        "learning_mode": learning_mode_enabled(state),
        "proposals": pending_proposals(state.db),
    }


@router.get("/learning-mode")
async def get_learning_mode(state: AppState = Depends(get_state),
                            _: Principal = Depends(current_principal)):
    return {"enabled": learning_mode_enabled(state)}


@router.put("/learning-mode")
async def set_learning_mode(body: LearningMode, state: AppState = Depends(get_state),
                            principal: Principal = Depends(require_role("operator"))):
    state.db.set_setting(LEARNING_MODE_KEY, bool(body.enabled))
    state.log.audit(actor_type="user", actor_id=principal.actor,
                    action="memory.learning_mode", status="ok",
                    meta={"enabled": bool(body.enabled)})
    state.bus.publish("memory.learning_mode", {"enabled": bool(body.enabled)})
    return {"enabled": bool(body.enabled)}


@router.put("/instructions")
async def set_instructions(body: Instructions, state: AppState = Depends(get_state),
                           principal: Principal = Depends(require_role("operator"))):
    """Die stehenden Anweisungen ändern. Wirkt sofort, ohne Neustart.

    Absichtlich kein Neustart: Wer eine Regel ändert, will sie im nächsten
    Satz wirksam sehen — nicht nach einem Neubau des Containers.
    """
    text = body.text.strip()
    state.db.set_setting(SETTING_KEY, text)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.instructions",
                    status="ok", meta={"length": len(text)})
    state.bus.publish("memory.instructions", {"length": len(text)})
    return {"instructions": text}


@router.post("/facts", status_code=201)
async def add_fact(body: Fact, state: AppState = Depends(get_state),
                   principal: Principal = Depends(require_role("operator"))):
    pinned = bool(body.pinned)
    if pinned and (state.db.scalar("SELECT COUNT(*) FROM memory WHERE pinned=1") or 0) >= MAX_PINNED:
        raise HTTPException(status_code=400,
                            detail=f"Das Hauptgedächtnis fasst {MAX_PINNED} Sätze. Erst einen herausnehmen.")
    row = {"id": new_id("mem"), "text": body.text.strip(), "actor": principal.actor,
           "conversation_id": None, "created_at": now_iso(), "pinned": 1 if pinned else 0,
           "source": "dashboard", "kind": "fact"}
    state.db.insert("memory", row)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.add",
                    target=row["id"], status="ok")
    return {"fact": row}


@router.patch("/facts/{fact_id}")
async def edit_fact(fact_id: str, body: Fact, state: AppState = Depends(get_state),
                    principal: Principal = Depends(require_role("operator"))):
    """Den Wortlaut ändern, ohne den Eintrag zu verlieren.

    Löschen und neu anlegen wäre der Umweg — und im Hauptgedächtnis kostet er
    den Platz, den man danach wieder suchen muss.
    """
    row = state.db.fetchone("SELECT * FROM memory WHERE id=?", (fact_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Diesen Eintrag gibt es nicht.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Ein leerer Satz merkt nichts.")
    state.db.update("memory", fact_id, {"text": text})
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.edit",
                    target=fact_id, status="ok")
    state.bus.publish("memory.core", {"id": fact_id, "edited": True})
    return {"fact": state.db.fetchone("SELECT * FROM memory WHERE id=?", (fact_id,))}


@router.post("/facts/{fact_id}/pin")
async def pin_fact(fact_id: str, pinned: bool = True, state: AppState = Depends(get_state),
                   principal: Principal = Depends(require_role("operator"))):
    """Einen Satz ins Hauptgedächtnis heben — oder wieder herausnehmen.

    Angeheftet heißt: Er steht in jedem Systemtext, bevor die erste Anfrage
    beantwortet wird. Nicht durchsuchbar-wenn-er-danach-sucht, sondern immer
    da. Genau deshalb ist die Zahl gedeckelt.
    """
    row = state.db.fetchone("SELECT * FROM memory WHERE id=?", (fact_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Diesen Eintrag gibt es nicht.")
    if pinned and not row["pinned"]:
        have = state.db.scalar("SELECT COUNT(*) FROM memory WHERE pinned=1") or 0
        if have >= MAX_PINNED:
            raise HTTPException(status_code=400, detail=(
                f"Im Hauptgedächtnis ist Platz für {MAX_PINNED} Sätze, und die sind belegt. "
                "Nimm erst einen heraus — alles, was hier steht, wird bei jeder Anfrage "
                "mitgeschickt."))
    state.db.update("memory", fact_id, {"pinned": 1 if pinned else 0})
    state.log.audit(actor_type="user", actor_id=principal.actor,
                    action="memory.pin" if pinned else "memory.unpin", target=fact_id, status="ok")
    state.bus.publish("memory.core", {"id": fact_id, "pinned": pinned})
    return {"fact": state.db.fetchone("SELECT * FROM memory WHERE id=?", (fact_id,))}


@router.delete("/facts/{fact_id}")
async def delete_fact(fact_id: str, state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("operator"))):
    if not state.db.fetchone("SELECT id FROM memory WHERE id=?", (fact_id,)):
        raise HTTPException(status_code=404, detail="Diesen Eintrag gibt es nicht.")
    state.db.execute("DELETE FROM memory WHERE id=?", (fact_id,))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.delete",
                    target=fact_id, status="ok")
    return {"ok": True}


@router.delete("/facts")
async def clear_facts(confirm: str = "", state: AppState = Depends(get_state),
                      principal: Principal = Depends(require_role("admin"))):
    """Alles vergessen. Nur mit ausgeschriebener Bestätigung.

    Ein Knopf, der wortlos das ganze Gedächtnis leert, ist eine Falle. Der
    Aufrufer muss das Wort nennen, sonst passiert nichts.
    """
    if confirm != "ALLES":
        raise HTTPException(status_code=400,
                            detail="Zum Leeren muss confirm=ALLES mitgegeben werden.")
    n = state.db.scalar("SELECT COUNT(*) FROM memory") or 0
    state.db.execute("DELETE FROM memory")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.clear",
                    status="ok", meta={"removed": n})
    return {"removed": n}


@router.post("/proposals/{proposal_id}/accept")
async def accept_proposal_route(proposal_id: str, state: AppState = Depends(get_state),
                                principal: Principal = Depends(require_role("operator"))):
    mem = accept_proposal(state, proposal_id, principal.actor)
    if mem is None:
        raise HTTPException(status_code=404, detail="Diesen Vorschlag gibt es nicht mehr.")
    if mem.get("id"):
        await index_fact(state, mem)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.proposal_accept",
                    target=proposal_id, status="ok")
    return {"fact": mem}


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal_route(proposal_id: str, state: AppState = Depends(get_state),
                                principal: Principal = Depends(require_role("operator"))):
    if not reject_proposal(state, proposal_id, principal.actor):
        raise HTTPException(status_code=404, detail="Diesen Vorschlag gibt es nicht mehr.")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.proposal_reject",
                    target=proposal_id, status="ok")
    return {"ok": True}


# ── Wissensspeicher ──────────────────────────────────────────────────────
# Der dritte Platz im Gedächtnis, und der einzige, der lange Texte verträgt:
# Das Hauptgedächtnis fasst 30 Sätze, weil es bei JEDER Anfrage mitreist. Ein
# Handbuch über diese Anlage passt da nicht hinein und gehört trotzdem zu dem,
# was er wissen muss. Hier liegt es — im Systemtext steht nur der Titel, den
# vollen Text holt er sich mit knowledge.open.
class KnowledgeBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=200_000)
    summary: str = Field(default="", max_length=300)
    slug: str = Field(default="", max_length=60)


@router.get("/knowledge")
async def list_knowledge(state: AppState = Depends(get_state),
                         _: Principal = Depends(current_principal)):
    kb = state.services["knowledge"]
    return {"documents": kb.all(), "catalogue": kb.catalogue()}


@router.get("/knowledge/{slug}")
async def read_knowledge(slug: str, state: AppState = Depends(get_state),
                         _: Principal = Depends(current_principal)):
    doc = state.services["knowledge"].get(slug)
    if not doc:
        raise HTTPException(status_code=404, detail="Gibt es nicht")
    return {"document": {"slug": doc["slug"], "title": doc["title"],
                         "summary": doc["summary"], "content": doc["content"],
                         "enabled": bool(doc["enabled"]), "uses": doc["uses"],
                         "updated_at": doc["updated_at"]}}


@router.post("/knowledge", status_code=201)
async def save_knowledge(body: KnowledgeBody, state: AppState = Depends(get_state),
                         principal: Principal = Depends(require_role("operator"))):
    from ...services.knowledge import KnowledgeError
    try:
        doc = state.services["knowledge"].save(
            title=body.title, content=body.content, summary=body.summary,
            slug=body.slug, actor=principal.actor)
    except KnowledgeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state.log.audit(actor_type="user", actor_id=principal.actor, action="knowledge.save",
                    target=doc["slug"], status="ok")
    return {"document": doc}


@router.patch("/knowledge/{slug}")
async def toggle_knowledge(slug: str, enabled: bool, state: AppState = Depends(get_state),
                           principal: Principal = Depends(require_role("operator"))):
    doc = state.services["knowledge"].set_enabled(slug, enabled)
    if not doc:
        raise HTTPException(status_code=404, detail="Gibt es nicht")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="knowledge.toggle",
                    target=slug, status="ok", meta={"enabled": enabled})
    return {"document": doc}


@router.delete("/knowledge/{slug}")
async def delete_knowledge(slug: str, state: AppState = Depends(get_state),
                           principal: Principal = Depends(require_role("operator"))):
    if not state.services["knowledge"].remove(slug):
        raise HTTPException(status_code=404, detail="Gibt es nicht")
    state.log.audit(actor_type="user", actor_id=principal.actor, action="knowledge.delete",
                    target=slug, status="ok")
    return {"ok": True}


def _startup(state: AppState) -> None:
    """Das mitgelieferte Hauptgedächtnis einmalig einspielen.

    Ohne das stünde der Wissensspeicher beim ersten Start leer da, während die
    Datei im Abbild liegt und niemand sie sieht — genau der Zustand, den sie
    beheben sollte. Eingespielt wird nur, was noch nicht da ist: Wer den Text
    im Dashboard bearbeitet, bekommt ihn beim nächsten Neustart nicht wieder
    überschrieben.
    """
    from pathlib import Path

    kb = state.services.get("knowledge")
    if kb is None:
        return
    mitgeliefert = (
        ("jarvis-hauptgedaechtnis", "HAUPTGEDAECHTNIS.md", "JARVIS Hauptgedächtnis",
         "Aufbau dieser Anlage: Server, Dienste, Geräte, Entscheidungen und offene Punkte. "
         "Hier nachsehen statt raten."),
        ("jarvis-stehende-anweisungen", "STEHENDE_ANWEISUNGEN.md", "JARVIS Stehende Anweisungen",
         "Alle geltenden Regeln mit Angabe, welche der Code erzwingt und welche nur im Text "
         "stehen. Nachsehen, bevor du über Grenzen oder Freigaben sprichst."),
    )
    for slug, dateiname, titel, zweck in mitgeliefert:
        if kb.get(slug):
            continue
        for kandidat in (Path("/app/.claude") / dateiname,
                         Path(__file__).resolve().parents[4] / ".claude" / dateiname):
            if not kandidat.is_file():
                continue
            try:
                kb.save(slug=slug, title=titel, summary=zweck,
                        content=kandidat.read_text(encoding="utf-8"), actor="system")
                state.log.info("memory", f"{titel} eingespielt aus {kandidat}")
            except Exception as e:  # noqa: BLE001 — ein Startfehler darf den Server nicht aufhalten
                state.log.warn("memory", f"{titel} nicht einspielbar: {e}")
            break


MODULE = ModuleSpec(
    id="memory", title="Gedächtnis", router=router, icon="book-open", path="/memory", order=64,
    min_role="operator", description="Anweisungen, Gemerktes und Wissensspeicher",
    commands=[{"id": "memory.open", "title": "Gedächtnis öffnen", "path": "/memory"}],
    on_startup=_startup,
)
