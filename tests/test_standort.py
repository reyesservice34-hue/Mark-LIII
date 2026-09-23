"""Standort und Pünktlichkeit — offline (Kartendienste und Handy durch Attrappen ersetzt).

Geprüft wird, was hier teuer wird, wenn es schiefgeht: eine erfundene Fahrzeit, eine Warnung zur falschen Zeit oder zweimal,
eine Warnung für eine Baustelle, auf der der Nutzer gar nicht eingeteilt ist, und ein offener Empfänger, den jeder benutzen kann.
"""
import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ["TZ"] = "Europe/Berlin"
time.tzset()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for k in ("STANDORT_TOKEN", "STANDORT_PUFFER_MIN", "JARVIS_CC_NTFY_TOPIC"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from command_center.backend.modules import standort as so  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


class DB:
    def __init__(self):
        self.d = {}

    def get_setting(self, k, default=None):
        return self.d.get(k, default)

    def set_setting(self, k, v):
        self.d[k] = v


class Notes:
    def __init__(self):
        self.rows = []

    def notify(self, **kw):
        self.rows.append(kw)


class Cal:
    def __init__(self, events):
        self.events = events
        self.local = SimpleNamespace(_load=lambda: self.events)

    def available(self):
        return True


def ev(uid, title, start, location="Bahnhofstraße 1, Neuberg", category="ich", notes=""):
    return SimpleNamespace(uid=uid, title=title, start=start.isoformat(timespec="seconds"), end=(start + timedelta(hours=1)).isoformat(timespec="seconds"),
                           location=location, category=category, notes=notes)


class Maps:
    """Nominatim + OSRM: zählt Aufrufe und kann ausfallen."""
    def __init__(self):
        self.geo_calls = 0
        self.osrm_seconds = 1800.0
        self.geo_ok = True
        self.osrm_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if "nominatim" in request.url.host:
            self.geo_calls += 1
            if not self.geo_ok:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"lat": "50.20", "lon": "8.90"}])
        if not self.osrm_ok:
            return httpx.Response(200, json={"code": "NoRoute"})
        return httpx.Response(200, json={"code": "Ok", "routes": [{"duration": self.osrm_seconds}]})


def state(events, pos_age=60, pos=(50.10, 8.60)):
    st = SimpleNamespace(db=DB(), services={"calendar": Cal(events), "notifications": Notes()})
    if pos:
        st.db.set_setting(so.LAST_KEY, {"lat": pos[0], "lon": pos[1], "ts": time.time() - pos_age})
    return st


class FakeRequest:
    def __init__(self, headers=None, query=None, body=None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.query_params = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("kein Body")
        return self._body


async def main():
    maps = Maps()
    so._transport = httpx.MockTransport(maps.handler)
    now = datetime(2026, 9, 24, 8, 0, 0)

    # ── reine Entscheidung ──
    d = lambda mins_to_start, drive_min, buf=10: so.decide(now, now + timedelta(minutes=mins_to_start), drive_min * 60, buf * 60)[0]  # noqa: E731
    check("Entscheidung: reichlich Zeit → nichts", d(120, 30) is None)
    check("Entscheidung: 10 Minuten bis zum spätesten Losfahren → bald", d(50, 30) == "bald")
    check("Entscheidung: spätester Zeitpunkt erreicht → los", d(40, 30) == "los")
    check("Entscheidung: Fahrt dauert länger als die Restzeit → spät", d(20, 30) == "spaet")
    check("Entscheidung: mit Puffer ist 'los' früher als ohne", d(45, 30, buf=10) == "bald" and d(45, 30, buf=20) == "los")

    # ── ohne belastbare Grundlage wird nichts gemeldet und nichts geschätzt ──
    st = state([ev("a", "Aufmaß", now + timedelta(minutes=35))], pos=None)
    r = await so.check(st, now)
    check("ohne Position: keine Meldung, ehrlicher Status", r["status"] == "keine Position" and not st.services["notifications"].rows, r)
    st = state([ev("a", "Aufmaß", now + timedelta(minutes=35))], pos_age=3600)
    r = await so.check(st, now)
    check("Position älter als 20 Minuten: keine Meldung", r["status"] == "Position zu alt" and not st.services["notifications"].rows, r)
    st = state([ev("a", "Aufmaß", now + timedelta(minutes=35), location="")])
    check("Termin ohne Ort: keine Meldung", (await so.check(st, now))["status"] == "kein Termin mit Ort")
    st = state([ev("a", "Geburtstag", datetime(2026, 9, 24, 0, 0, 0))])
    check("ganztägiger Termin zählt nicht", (await so.check(st, now))["status"] == "kein Termin mit Ort")
    st = state([ev("a", "Baustelle – Bad", now + timedelta(minutes=35), category="tour")])
    check("Baustelle, auf der der Nutzer nicht eingeteilt ist: keine Warnung", (await so.check(st, now))["status"] == "kein Termin mit Ort")
    st = state([ev("a", "Baustelle – Bad", now + timedelta(minutes=35), category="tour", notes="Team: Paul, Christoph")])
    check("Baustelle mit dem Nutzer im Team: wird beachtet", (await so.check(st, now))["status"] != "kein Termin mit Ort")
    st = state([ev("a", "Aufmaß", now + timedelta(hours=9))])
    check("Termin erst in 9 Stunden: noch kein Thema", (await so.check(st, now))["status"] == "kein Termin mit Ort")

    maps.geo_ok = False
    st = state([ev("a", "Aufmaß", now + timedelta(minutes=35), location="Irgendwo im Nirgendwo")])
    before = maps.geo_calls
    r = await so.check(st, now)
    check("Ort nicht auffindbar: keine Meldung, Grund genannt", r["status"] == "Ort nicht gefunden" and not st.services["notifications"].rows, r)
    check("… und das 'nicht gefunden' wird zwischengespeichert (kein Dauerfeuer an den Kartendienst)", (await so.check(st, now))["status"] == "Ort nicht gefunden" and maps.geo_calls == before + 1, (before, maps.geo_calls))
    maps.geo_ok = True
    maps.osrm_ok = False
    st = state([ev("a", "Aufmaß", now + timedelta(minutes=35))])
    r = await so.check(st, now)
    check("Routendienst ohne Antwort: KEINE geschätzte Fahrzeit, keine Meldung", r["status"] == "keine Fahrzeit" and not st.services["notifications"].rows, r)
    maps.osrm_ok = True

    # ── Warnungen ──
    maps.osrm_seconds = 1800
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=60))])
    r = await so.check(st, now)
    check("60 Minuten Puffer: alles gut, keine Meldung", r["status"] == "alles gut" and r["fahrt_minuten"] == 30 and not st.services["notifications"].rows, r)
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=50))])
    r = await so.check(st, now)
    check("knapp: 'bald losfahren'", r["stufe"] == "bald" and "In etwa 10 Minuten" in st.services["notifications"].rows[0]["body"], (r, st.services["notifications"].rows))
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=38))])
    await so.check(st, now)
    body = st.services["notifications"].rows[0]["body"]
    check("spätestens jetzt: 'du musst jetzt losfahren' mit Termin, Ort und Fahrzeit", "jetzt losfahren" in body and "Aufmaß Familie Test" in body and "08:38 Uhr" in body and "Neuberg" in body and "30 Minuten" in body, body)
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=20))])
    r = await so.check(st, now)
    body = st.services["notifications"].rows[0]["body"]
    check("schafft er es nicht mehr: klare Ansage mit Ankunftszeit", r["stufe"] == "spaet" and "nicht mehr rechtzeitig" in body and "08:30 Uhr" in body, body)
    check("Warnstufe 'zu spät' ist dringender (error) als 'bald' (warning)", st.services["notifications"].rows[0]["severity"] == "error")

    # ── keine doppelten Meldungen ──
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=38))])
    await so.check(st, now)
    r = await so.check(st, now + timedelta(minutes=5))
    check("dieselbe Stufe für denselben Termin nur einmal", r["status"] == "schon gemeldet" and len(st.services["notifications"].rows) == 1, r)
    r = await so.check(st, now + timedelta(minutes=13))
    check("die nächste Stufe (zu spät) kommt trotzdem noch", r.get("stufe") == "spaet" and len(st.services["notifications"].rows) == 2, r)

    # ── schon da ──
    st = state([ev("a", "Aufmaß Familie Test", now + timedelta(minutes=5))], pos=(50.2001, 8.9001))
    r = await so.check(st, now)
    check("wer schon vor Ort ist, wird nicht gewarnt", r["status"] == "schon da" and not st.services["notifications"].rows, r)

    # ── Empfänger für das Handy ──
    os.environ.pop("STANDORT_TOKEN", None)
    r = await so.ping(FakeRequest(query={"token": "x"}, body={"lat": 50, "lon": 8}), state([]))
    check("Empfänger ohne eingerichteten Schlüssel: 503, nichts gespeichert", r.status_code == 503)
    os.environ["STANDORT_TOKEN"] = "geheim123"
    st = state([], pos=None)
    r = await so.ping(FakeRequest(body={"lat": 50, "lon": 8}), st)
    check("ohne Schlüssel: 401, nichts gespeichert", r.status_code == 401 and st.db.get_setting(so.LAST_KEY) is None)
    r = await so.ping(FakeRequest(query={"token": "falsch"}, body={"lat": 50, "lon": 8}), st)
    check("mit falschem Schlüssel: 401", r.status_code == 401 and st.db.get_setting(so.LAST_KEY) is None)
    r = await so.ping(FakeRequest(query={"token": "geheim123"}, body={"_type": "location", "lat": 50.11, "lon": 8.68, "tst": int(time.time()) - 30}), st)
    saved = st.db.get_setting(so.LAST_KEY)
    check("OwnTracks-Meldung mit Schlüssel im Link: gespeichert, Antwort ist eine leere Liste", r.status_code == 200 and json.loads(r.body) == [] and saved["lat"] == 50.11, (r.status_code, saved))
    basic = base64.b64encode(b"handy:geheim123").decode()
    r = await so.ping(FakeRequest(headers={"Authorization": "Basic " + basic}, body={"lat": 50.2, "lon": 8.7}), st)
    check("Basic-Anmeldung (Passwort = Schlüssel) wird akzeptiert", r.status_code == 200 and st.db.get_setting(so.LAST_KEY)["lat"] == 50.2)
    r = await so.ping(FakeRequest(headers={"X-Standort-Token": "geheim123"}, query={"lat": "50.3", "lon": "8.8"}), st)
    check("Kurzbefehl mit Header und lat/lon im Link geht auch", r.status_code == 200 and st.db.get_setting(so.LAST_KEY)["lat"] == 50.3)
    r = await so.ping(FakeRequest(query={"token": "geheim123"}, body={"lat": 999, "lon": 8}), st)
    check("unmögliche Koordinaten werden abgewiesen", r.status_code == 400)
    r = await so.ping(FakeRequest(query={"token": "geheim123"}, body={"_type": "lwt"}), st)
    check("andere OwnTracks-Ereignisse werden ignoriert", r.status_code == 200 and json.loads(r.body) == [])
    r = await so.ping(FakeRequest(query={"token": "geheim123"}, body={"lat": 50.4, "lon": 8.9, "tst": int(time.time()) + 99999}), st)
    check("falsche Handy-Uhr (Zukunft): Zeitstempel wird durch jetzt ersetzt", abs(st.db.get_setting(so.LAST_KEY)["ts"] - time.time()) < 5)
    check("gespeichert wird nur die LETZTE Position, keine Historie", set(k for k in st.db.d if k.startswith("standort")) == {so.LAST_KEY})

    # ── Entfernung ──
    check("Luftlinie: Frankfurt–Neuberg grob 20 km", 15000 < so.meters((50.11, 8.68), (50.20, 8.90)) < 25000)


asyncio.run(main())
print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
