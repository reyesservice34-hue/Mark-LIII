"""Meldungen aufs Handy und Anruf bei Dringendem — offline (ntfy und Twilio durch Attrappen ersetzt).

Geprüft wird, was hier teuer wird: eine Warnung, die nie ankommt; ein Push für jede Kleinigkeit; ein Anruf mitten in der Nacht wegen
etwas Unwichtigem; Dauerklingeln; ein Anruf bei einer Nummer, die nicht die des Nutzers ist; ein Anruf ohne eingerichteten Anbieter,
der so tut, als hätte er geklingelt.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for k in list(os.environ):
    if k.startswith(("TWILIO_", "JARVIS_CC_CALL", "JARVIS_CC_PUSH", "JARVIS_CC_NTFY")):
        os.environ.pop(k)

from command_center.backend.modules import heartbeat  # noqa: E402
from command_center.backend.modules.meldungen import info  # noqa: E402
from command_center.backend.services import notifications as nt, phone  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


pushes, calls = [], []


async def fake_push(title, text, severity="info"):
    pushes.append((title, text, severity))
    return True


async def fake_post(url, auth, data):
    calls.append((url, auth, data))
    return 201, "{}"


heartbeat.push = fake_push
phone._post = fake_post


class Bus:
    def publish(self, *a, **k):
        pass


class DB:
    def insert(self, table, row):
        return row

    def fetchone(self, *a, **k):
        return None


def make():
    svc = nt.NotificationService.__new__(nt.NotificationService)
    svc.db, svc.bus = DB(), Bus()
    return svc


def out(cat="server", sev="info", title="T", body="B"):
    return {"category": cat, "severity": sev, "title": title, "body": body}


async def run():
    svc = make()

    async def fire(o, meta=None):
        pushes.clear(); calls.clear()
        svc._maybe_push(o, meta or {})
        await asyncio.sleep(0.05)

    # ── Push ──
    await fire(out(sev="warning"))
    check("Push: eine Warnung geht aufs Handy", len(pushes) == 1 and pushes[0][0].startswith("Jarvis: "), pushes)
    await fire(out(sev="info"))
    check("Push: eine gewöhnliche Info nicht (kein Dauerbeschuss)", pushes == [], pushes)
    await fire(out(sev="info"), {"push": True})
    check("Push: was ein Agent ausdrücklich meldet, kommt an", len(pushes) == 1)
    await fire(out(cat="system", sev="error"))
    check("Push: Systemmeldungen (Herzschlag, Standort) nicht doppelt", pushes == [])
    await fire(out(sev="error"), {"push": False})
    check("Push: ausdrücklich abgeschaltet gilt", pushes == [])
    os.environ["JARVIS_CC_PUSH_MIN"] = "off"
    await fire(out(sev="critical"))
    check("Push: JARVIS_CC_PUSH_MIN=off schaltet alles ab", pushes == [] and calls == [])
    del os.environ["JARVIS_CC_PUSH_MIN"]
    pushes.clear()
    try:
        await asyncio.to_thread(svc._maybe_push, out(sev="error"), {})      # Thread = keine laufende Ereignisschleife
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    await asyncio.sleep(0.05)
    check("Push: ohne laufende Schleife (Skript) kein Absturz, nichts gesendet", ok and pushes == [], pushes)

    # ── Anruf ──
    await fire(out(sev="critical", title="Server down", body="Datenbank antwortet nicht"))
    check("Anruf: ohne eingerichteten Anbieter wird nicht angerufen, Push kommt trotzdem", calls == [] and len(pushes) == 1, (calls, pushes))
    r = await phone.call("Test")
    check("Anruf: ohne Zugangsdaten sagt es ehrlich, was fehlt", r["angerufen"] is False and "TWILIO_ACCOUNT_SID" in r["hinweis"], r)
    check("Meldungs-Info nennt, was für den Anruf fehlt", info()["anruf"]["konfiguriert"] is False and len(info()["anruf"]["fehlt"]) == 4)

    os.environ.update({"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_FROM": "+4930111", "JARVIS_CC_CALL_TO": "+491701234567"})
    phone._last_call = 0.0
    await fire(out(sev="critical", title="Server down", body="Datenbank <antwortet> nicht & so"))
    check("Anruf: kritische Meldung löst genau einen Anruf aus", len(calls) == 1, calls)
    url, auth, data = calls[0]
    check("Anruf: geht an Twilio mit den eigenen Zugangsdaten und nur an die hinterlegte Nummer", "api.twilio.com" in url and "ACtest" in url and auth == ("ACtest", "tok") and data["To"] == "+491701234567" and data["From"] == "+4930111", (url, data))
    check("Anruf: Text steht deutsch, zweimal, sicher maskiert im Sprachbefehl", data["Twiml"].count("<Say ") == 2 and 'language="de-DE"' in data["Twiml"] and "&lt;antwortet&gt;" in data["Twiml"] and "&amp;" in data["Twiml"], data["Twiml"])
    check("Anruf: Push kommt zusätzlich", len(pushes) == 1)
    await fire(out(sev="critical", title="Noch was"))
    check("Anruf: zweite kritische Meldung direkt danach klingelt nicht erneut (Abkühlzeit)", calls == [] and len(pushes) == 1, calls)
    r = await phone.call("Wichtig", force=True)
    check("Anruf: mit 'sofort' geht es trotz Abkühlzeit", r["angerufen"] is True and len(calls) == 1)
    phone._last_call = 0.0
    await fire(out(sev="error"))
    check("Anruf: Fehler (nicht kritisch) klingelt nicht", calls == [], calls)
    await fire(out(sev="critical"), {"call": False})
    check("Anruf: ausdrücklich ohne Anruf (Anruf-Werkzeug meldet selbst) gilt", calls == [])
    os.environ["JARVIS_CC_CALL_MIN"] = "off"
    await fire(out(sev="critical"))
    check("Anruf: JARVIS_CC_CALL_MIN=off schaltet Anrufe ab, Push bleibt", calls == [] and len(pushes) == 1)
    del os.environ["JARVIS_CC_CALL_MIN"]

    async def boom(url, auth, data):
        raise RuntimeError("Netz weg")
    phone._post = boom; phone._last_call = 0.0
    r = await phone.call("x")
    check("Anruf: Twilio nicht erreichbar → sagt es, wirft nicht, zählt nicht als angerufen", r["angerufen"] is False and phone._last_call == 0.0, r)
    async def reject(url, auth, data):
        return 401, "no"
    phone._post = reject
    r = await phone.call("x")
    check("Anruf: abgelehnt (falsche Zugangsdaten) → wird nicht als Erfolg gemeldet", r["angerufen"] is False and "401" in r["hinweis"], r)
    check("Info: Nummer wird nie ganz gezeigt", "1234" not in str(info()["anruf"]) and info()["anruf"]["nummer"].startswith("+49"), info()["anruf"])


asyncio.run(run())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
