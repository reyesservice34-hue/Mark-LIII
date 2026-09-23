"""Google Kalender über die n8n-Brücke: lesen, anlegen, ändern, löschen, abgleichen — offline.

Die Brücke wird durch einen kleinen Google-Ersatz im Speicher ersetzt (httpx.MockTransport). Geprüft wird das, was
schiefgehen kann: dass Google zuerst geschrieben wird und der lokale Spiegel nur folgt, dass beim Ändern nur die
geänderten Felder gesendet werden, dass die Zeiten stimmen (Berlin, mit Offset), dass Fehler im Klartext ankommen
und dass ohne Schreib-URL alles beim Alten bleibt.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["TZ"] = "Europe/Berlin"
time.tzset()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import httpx  # noqa: E402

from command_center.backend.services.calendar_service import CalendarError, CalendarService  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


class FakeGoogle:
    """Ein Google-Kalender im Speicher mit demselben Auftragsformat wie die n8n-Brücke."""

    def __init__(self):
        self.events = {
            "ev1": {"id": "ev1", "summary": "Baustelle – EG Treppenhaus", "start": {"dateTime": "2026-10-05T08:00:00+02:00"},
                    "end": {"dateTime": "2026-10-05T16:00:00+02:00"}, "location": "Bahnhofstraße 1", "description": "Putz", "status": "confirmed"},
            "ev2": {"id": "ev2", "summary": "Geburtstag", "start": {"date": "2026-10-07"}, "end": {"date": "2026-10-08"},
                    "status": "confirmed"}}
        self.calls = []
        self.mode = "ok"          # ok | forbidden | error | garbage | down
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append(body)
        if self.mode == "down":
            raise httpx.ConnectError("nicht erreichbar")
        if request.headers.get("X-Jarvis-Bridge") != "t" or self.mode == "forbidden":
            return httpx.Response(403, json={"message": "Authorization data is wrong!"})
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>Cloudflare</html>")
        if self.mode == "error":
            return httpx.Response(200, json={"ok": False, "status": 404, "error": "Not Found"})
        act = body.get("action") or ("list" if "timeMin" in body else "")
        if act == "list":
            return httpx.Response(200, json={"ok": True, "events": list(self.events.values()), "truncated": False})
        if act == "create":
            self.n += 1
            eid = f"new{self.n}abcdef"
            self.events[eid] = {"id": eid, "summary": body["summary"], "start": {"dateTime": body["start"]},
                                "end": {"dateTime": body["end"]}, "location": body["location"],
                                "description": body["description"], "status": "confirmed"}
            return httpx.Response(200, json={"ok": True, "event": self.events[eid]})
        if act == "update":
            e = self.events.get(body["eventId"])
            if not e:
                return httpx.Response(200, json={"ok": False, "status": 404, "error": "Not Found"})
            for k_in, k_out in (("summary", "summary"), ("location", "location"), ("description", "description")):
                if k_in in body:
                    e[k_out] = body[k_in]
            if "start" in body:
                e["start"], e["end"] = {"dateTime": body["start"]}, {"dateTime": body["end"]}
            return httpx.Response(200, json={"ok": True, "event": e})
        if act == "delete":
            self.events.pop(body["eventId"], None)
            return httpx.Response(200, json={"ok": True, "deleted": True, "id": body["eventId"]})
        return httpx.Response(200, json={"ok": False, "error": "Unknown action"})


def make(g: FakeGoogle, write: bool = True) -> CalendarService:
    os.environ.update({"CALENDAR_BRIDGE_READ_URLS": "http://bridge/x", "CALENDAR_BRIDGE_TOKEN": "t",
                       "CALENDAR_BRIDGE_HEADER": "X-Jarvis-Bridge"})
    if write:
        os.environ.update({"CALENDAR_BRIDGE_WRITE_URL": "http://bridge/x", "CALENDAR_WRITE_BACKEND": "n8n"})
    else:
        os.environ.pop("CALENDAR_BRIDGE_WRITE_URL", None)
        os.environ["CALENDAR_WRITE_BACKEND"] = "local"
    svc = CalendarService(store_file=Path(tempfile.mkdtemp()) / "events.json")
    svc.bridge._transport = httpx.MockTransport(g.handler)
    return svc


def mirror(svc):
    return {e.uid: e for e in svc.local._load()}


async def main():
    g = FakeGoogle()
    svc = make(g)
    check("Schreiben über die Brücke ist eingeschaltet", svc._write_bridge() and svc.bridge.can_write())

    # Lesen / Abgleich
    r = await svc.sync_google(days=40)
    m = mirror(svc)
    check("Abgleich holt beide Termine", r["synced"] == 2 and set(m) == {"g-ev1", "g-ev2"}, r)
    check("Uhrzeit stimmt (08:00 Berlin, nicht 06:00 UTC)", m["g-ev1"].start == "2026-10-05T08:00:00", m["g-ev1"].start)
    check("Ort und Beschreibung kommen mit", m["g-ev1"].location == "Bahnhofstraße 1" and m["g-ev1"].notes == "Putz")
    check("ganztägiger Termin wird erkannt", m["g-ev2"].start == "2026-10-07T00:00:00")

    # Anlegen: Google zuerst
    ev, backend, note = await svc.create(title="Aufmaß Familie Test", when="2026-10-09", at="14:00", duration=90,
                                         location="Karben", notes="Bad")
    made = [c for c in g.calls if c.get("action") == "create"][0]
    check("Anlegen geht an Google mit Offset (14:00+02:00)", made["start"] == "2026-10-09T14:00:00+02:00" and made["end"] == "2026-10-09T15:30:00+02:00", made)
    check("Antwort nennt Google", backend == "n8n" and "Google" in note, (backend, note))
    check("Spiegel hat den Termin sofort", ev["uid"] in mirror(svc) and ev["backend"] == "google")
    new_uid = ev["uid"]

    # Verschieben: Dauer bleibt
    moved, b2 = await svc.move("Aufmaß Familie", "2026-10-12", at="09:00")
    upd = [c for c in g.calls if c.get("action") == "update"][-1]
    check("Verschieben sendet neue Zeit mit gleicher Länge", upd["start"] == "2026-10-12T09:00:00+02:00" and upd["end"] == "2026-10-12T10:30:00+02:00", upd)
    check("Spiegel folgt", mirror(svc)[new_uid].start == "2026-10-12T09:00:00")

    # Ändern: nur geänderte Felder
    calls_before = len(g.calls)
    out = await svc.update_any(new_uid, title="Aufmaß Familie Test 2", location="Rosbach")
    upd = g.calls[calls_before]
    check("Ändern sendet nur Titel und Ort, keine Zeit", set(upd) == {"action", "eventId", "summary", "location"}, upd)
    check("Spiegel zeigt die Änderung", out["title"] == "Aufmaß Familie Test 2" and mirror(svc)[new_uid].location == "Rosbach")
    out = await svc.update_any(new_uid, at="16:00")
    check("Zeit ändern: Dauer bleibt (90 min)", g.calls[-1]["end"] == "2026-10-12T17:30:00+02:00", g.calls[-1])

    # Bestehender Google-Termin wird ebenso geändert
    await svc.update_any("g-ev1", notes="Putz + Schleifen")
    check("Termin, den es schon in Google gab, lässt sich ändern", g.events["ev1"]["description"] == "Putz + Schleifen")

    # Eigenes Etikett ist rein lokal
    n_calls = len(g.calls)
    await svc.update_any("g-ev1", category="tour")
    check("Nur die Kategorie ändern ruft Google nicht auf", len(g.calls) == n_calls and mirror(svc)["g-ev1"].category == "tour")
    await svc.sync_google(days=40)
    check("Kategorie überlebt den Abgleich", mirror(svc)["g-ev1"].category == "tour")

    # Löschen
    gone, b3 = await svc.cancel("Aufmaß Familie Test 2")
    check("Löschen entfernt den Termin in Google", new_uid.replace("g-", "") not in g.events)
    check("… und im Spiegel", new_uid not in mirror(svc))
    d = await svc.delete_any("g-ev2")
    check("delete_any löscht Google-Termin", "ev2" not in g.events and "g-ev2" not in mirror(svc) and d["title"] == "Geburtstag")

    # Änderungen direkt in Google kommen an, Gelöschtes verschwindet
    g.events["ev1"]["summary"] = "Baustelle – umbenannt in Google"
    g.events["ev3"] = {"id": "ev3", "summary": "Neu in Google", "start": {"dateTime": "2026-10-06T07:00:00+02:00"},
                       "end": {"dateTime": "2026-10-06T08:00:00+02:00"}, "status": "confirmed"}
    await svc.sync_google(days=40)
    m = mirror(svc)
    check("Änderung aus Google erscheint hier", m["g-ev1"].title == "Baustelle – umbenannt in Google")
    check("neuer Termin aus Google erscheint hier", "g-ev3" in m)
    del g.events["ev3"]
    await svc.sync_google(days=40)
    check("in Google gelöschter Termin verschwindet hier", "g-ev3" not in mirror(svc))

    # Fehler: Google zuerst, nichts im Spiegel bei Misserfolg
    before = set(mirror(svc))
    g.mode = "error"
    try:
        await svc.create(title="Fehlversuch", when="2026-10-10", at="10:00")
        check("Fehler von Google kommt an", False, "keine Ausnahme")
    except CalendarError as e:
        check("Fehler von Google kommt im Klartext an", "Not Found" in str(e), str(e))
    check("Bei Fehler steht nichts im Spiegel", set(mirror(svc)) == before)
    g.mode = "forbidden"
    try:
        await svc.update_any("g-ev1", title="x")
        check("falscher Schlüssel wird benannt", False, "keine Ausnahme")
    except CalendarError as e:
        check("falscher Schlüssel wird benannt", "lehnt den Schlüssel ab" in str(e), str(e))
    g.mode = "garbage"
    try:
        await svc.delete_any("g-ev1")
        check("unlesbare Antwort wird benannt", False, "keine Ausnahme")
    except CalendarError as e:
        check("unlesbare Antwort wird benannt", "unlesbar" in str(e), str(e))
    check("Termin steht nach Fehlversuchen noch im Spiegel", "g-ev1" in mirror(svc))
    g.mode = "down"
    try:
        await svc.update_any("g-ev1", title="x")
        check("Brücke nicht erreichbar wird benannt", False, "keine Ausnahme")
    except CalendarError as e:
        check("Brücke nicht erreichbar wird benannt", "nicht erreichbar" in str(e), str(e))
    g.mode = "ok"

    # Ohne Schreib-URL: alles wie bisher
    g2 = FakeGoogle()
    ro = make(g2, write=False)
    await ro.sync_google(days=40)
    check("ohne Schreib-URL ist Schreiben aus", not ro._write_bridge())
    try:
        await ro.update_any("g-ev1", title="x")
        check("ohne Schreib-URL: Google-Termine bleiben nur lesbar", False, "keine Ausnahme")
    except CalendarError as e:
        check("ohne Schreib-URL: Google-Termine bleiben nur lesbar", "nur lesen" in str(e), str(e))
    ev, backend, note = await ro.create(title="Lokal", when="2026-10-11", at="10:00")
    check("ohne Schreib-URL: neue Termine bleiben lokal", backend == "local" and not [c for c in g2.calls if c.get("action") == "create"])


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
