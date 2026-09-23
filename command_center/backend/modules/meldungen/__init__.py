"""Meldungen aufs Handy einrichten: Zustand des Push-Wegs (ntfy) und ein Testknopf.

Das Thema (`JARVIS_CC_NTFY_TOPIC`) ist wie ein Passwort: Wer es kennt, kann Jarvis' Meldungen mitlesen. Deshalb gibt es es nur nach
Anmeldung, und nur die App zeigt es an.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec
from ...services import phone
from ..heartbeat import push

router = APIRouter(prefix="/api/meldungen", tags=["meldungen"])


def info() -> dict:
    topic = os.environ.get("JARVIS_CC_NTFY_TOPIC", "").strip()
    server = os.environ.get("JARVIS_CC_NTFY_URL", "https://ntfy.sh").rstrip("/")
    host = server.split("://", 1)[-1]
    mn = os.environ.get("JARVIS_CC_PUSH_MIN", "warning").strip().lower()
    return {"konfiguriert": bool(topic), "server": server, "thema": topic, "abo_link": f"ntfy://{host}/{topic}" if topic else "",
            "web_link": f"{server}/{topic}" if topic else "", "aus": mn == "off",
            "regel": "Warnungen, Fehler, Freigaben und alles, was ein Agent ausdrücklich meldet" if mn != "off" else "aus",
            "anruf": phone.status()}


@router.get("/push")
async def push_info(_: Principal = Depends(current_principal)):
    return info()


@router.post("/test")
async def push_test(_: Principal = Depends(require_role("operator"))):
    if not info()["konfiguriert"]:
        return {"gesendet": False, "hinweis": "Es ist kein Thema eingerichtet (JARVIS_CC_NTFY_TOPIC)."}
    ok = await push("Jarvis", "Testmeldung: Wenn du das siehst, erreiche ich dich aufs Handy.", "info")
    return {"gesendet": ok, "hinweis": "Gesendet." if ok else "Der Push-Dienst hat die Meldung nicht angenommen."}


@router.post("/anruf-test")
async def call_test(_: Principal = Depends(require_role("operator"))):
    r = await phone.call("Hier ist Jarvis. Das ist ein Testanruf. Wenn du mich hörst, erreiche ich dich auch bei Notfällen.", force=True)
    return {"gesendet": r["angerufen"], "hinweis": r["hinweis"]}


MODULE = ModuleSpec(id="meldungen", title="Meldungen", router=router, nav=False, order=6, description="Meldungen aufs Handy")
