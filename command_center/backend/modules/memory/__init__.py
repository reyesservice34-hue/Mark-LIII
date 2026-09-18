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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth import Principal
from ...db import new_id, now_iso
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/memory", tags=["memory"])

SETTING_KEY = "master_instructions"
MAX_INSTRUCTIONS = 20_000


class Instructions(BaseModel):
    text: str = Field(max_length=MAX_INSTRUCTIONS)


class Fact(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


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
        "total": state.db.scalar("SELECT COUNT(*) FROM memory") or 0,
    }


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
    row = {"id": new_id("mem"), "text": body.text.strip(), "actor": principal.actor,
           "conversation_id": None, "created_at": now_iso()}
    state.db.insert("memory", row)
    state.log.audit(actor_type="user", actor_id=principal.actor, action="memory.add",
                    target=row["id"], status="ok")
    return {"fact": row}


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


MODULE = ModuleSpec(
    id="memory", title="Gedächtnis", router=router, icon="book-open", path="/memory", order=64,
    min_role="operator", description="Anweisungen und Gemerktes",
    commands=[{"id": "memory.open", "title": "Gedächtnis öffnen", "path": "/memory"}],
)
