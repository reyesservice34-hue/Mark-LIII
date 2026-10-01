"""Standort und Pünktlichkeit: „Du musst losfahren, sonst kommst du zu spät.“

Das Handy meldet seine Position (OwnTracks im HTTP-Modus oder ein iPhone-Kurzbefehl). Alle fünf Minuten sieht Jarvis nach
dem nächsten Termin mit Ort, der den Nutzer betrifft, rechnet die Fahrzeit von der letzten Position dorthin und warnt aufs
Handy, wenn es knapp wird: erst „bald losfahren“, dann „jetzt losfahren“, und wenn es nicht mehr reicht „du schaffst es nicht
mehr“. Jede Stufe pro Termin nur einmal.

Was hier bewusst NICHT passiert:
  * Es wird nichts geraten. Ohne frische Position (älter als 20 Minuten), ohne auffindbaren Ort oder ohne Fahrzeit meldet
    Jarvis nichts und sagt im Status, woran es liegt. Eine erfundene Fahrzeit wäre schlimmer als keine Warnung.
  * Es gibt keinen Anruf. Jarvis hat keinen Telefonanschluss; die Warnung kommt als Push (ntfy) und in der Live-Konsole.
  * Es wird nur die LETZTE Position gespeichert, keine Bewegungshistorie, und Koordinaten stehen in keinem Log.
  * Die Fahrzeit kommt aus dem öffentlichen OSRM-Dienst ohne Verkehrsdaten; deshalb gibt es einen Puffer
    (STANDORT_PUFFER_MIN, Standard 10 Minuten). Orte werden über OpenStreetMap (Nominatim) gefunden.
"""
from __future__ import annotations

import base64
import hmac
import math
import os
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...auth import Principal
from ...deps import AppState, current_principal, get_state
from ...services.calendar_service import involves_owner
from .. import ModuleSpec

router = APIRouter(prefix="/api/standort", tags=["standort"])

INTERVAL_SECONDS = 300
FRESH_SECONDS = 20 * 60          # ältere Position gilt als unbekannt
LOOKAHEAD_HOURS = 6
SOON_SECONDS = 15 * 60           # „bald losfahren“ ab so viel Restzeit
ARRIVED_METERS = 300             # näher dran: schon da, keine Warnung
LAST_KEY, GEO_KEY, SENT_KEY = "standort_last", "standort_geo", "standort_sent"
UA = "Jarvis-ReyesService/1.0 (Terminerinnerung, geringes Volumen)"
_transport = None                # nur für Tests
_stats: dict = {"last_check": None, "result": "noch nicht gelaufen"}


def token() -> str:
    return os.environ.get("STANDORT_TOKEN", "").strip()


def _authorized(request: Request) -> bool:
    """Token als Header, als ?token= oder als Passwort einer Basic-Anmeldung (so meldet sich OwnTracks)."""
    want = token()
    if not want:
        return False
    given = request.headers.get("x-standort-token") or request.query_params.get("token") or ""
    if not given:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                given = base64.b64decode(auth[6:]).decode("utf-8", "ignore").split(":", 1)[-1]
            except Exception:  # noqa: BLE001
                given = ""
    return bool(given) and hmac.compare_digest(given.encode(), want.encode())


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Luftlinie in Metern (Haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def concerns_owner(ev) -> bool:
    """Eine Baustelle betrifft den Nutzer nur, wenn er eingeteilt ist; persönliche Termine immer."""
    cat = (getattr(ev, "category", "") or "ich")
    return involves_owner(ev) if cat == "tour" else True


def decide(now: datetime, start: datetime, drive_s: float, buffer_s: float) -> tuple[str | None, float]:
    """(Stufe, Minuten bis zum spätesten Losfahren). Stufen: spaet, los, bald oder None."""
    to_start = (start - now).total_seconds()
    remaining = to_start - drive_s - buffer_s
    if to_start < drive_s:
        return "spaet", remaining / 60
    if remaining <= 0:
        return "los", remaining / 60
    if remaining <= SOON_SECONDS:
        return "bald", remaining / 60
    return None, remaining / 60


def message(level: str, ev, drive_s: float, now: datetime, start: datetime, remaining_min: float) -> str:
    fahrt = max(1, round(drive_s / 60))
    wann = f"{start:%H:%M} Uhr"
    ort = f" in {ev.location}" if ev.location else ""
    if level == "spaet":
        ankunft = datetime.fromtimestamp(now.timestamp() + drive_s)
        return (f"Achtung: Du schaffst „{ev.title}“ um {wann}{ort} nicht mehr rechtzeitig. Die Fahrt dauert etwa {fahrt} Minuten, "
                f"du wärst gegen {ankunft:%H:%M} Uhr da. Sag dem Termin am besten gleich Bescheid.")
    if level == "los":
        return f"Du musst jetzt losfahren: „{ev.title}“ um {wann}{ort}. Fahrt etwa {fahrt} Minuten, sonst kommst du zu spät."
    return (f"In etwa {max(1, round(remaining_min))} Minuten musst du losfahren: „{ev.title}“ um {wann}{ort}, "
            f"Fahrt etwa {fahrt} Minuten.")


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=12, transport=_transport, headers={"User-Agent": UA})


async def geocode(state: AppState, text: str) -> tuple[float, float] | None:
    """Ort → Koordinaten, mit Zwischenspeicher. None, wenn nichts gefunden wird (dann wird nichts geraten)."""
    key = text.strip().lower()
    cache = state.db.get_setting(GEO_KEY, {}) or {}
    if key in cache:
        v = cache[key]
        return (v[0], v[1]) if v else None
    try:
        async with await _client() as c:
            r = await c.get(os.environ.get("STANDORT_GEOCODER", "https://nominatim.openstreetmap.org").rstrip("/") + "/search",
                            params={"q": text, "format": "json", "limit": 1, "countrycodes": "de"})
        rows = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None                                    # Netzfehler: nicht zwischenspeichern, später erneut versuchen
    found = (float(rows[0]["lat"]), float(rows[0]["lon"])) if rows else None
    cache[key] = list(found) if found else None
    state.db.set_setting(GEO_KEY, dict(list(cache.items())[-200:]))
    return found


async def drive_seconds(a: tuple[float, float], b: tuple[float, float]) -> float | None:
    base = os.environ.get("STANDORT_OSRM", "https://router.project-osrm.org").rstrip("/")
    try:
        async with await _client() as c:
            r = await c.get(f"{base}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}", params={"overview": "false"})
        data = r.json()
        return float(data["routes"][0]["duration"]) if r.status_code == 200 and data.get("code") == "Ok" else None
    except Exception:  # noqa: BLE001
        return None


def next_event(state: AppState, now: datetime):
    """Der früheste Termin mit Ort in den nächsten Stunden, der den Nutzer betrifft."""
    cal = state.services["calendar"]
    if not cal.available():
        return None
    best = None
    for ev in cal.local._load():
        if not ev.location.strip() or "T" not in ev.start or ev.start.endswith("T00:00:00"):
            continue
        try:
            start = datetime.fromisoformat(ev.start)
        except ValueError:
            continue
        if not (-600 <= (start - now).total_seconds() <= LOOKAHEAD_HOURS * 3600) or not concerns_owner(ev):
            continue
        if best is None or start < best[1]:
            best = (ev, start)
    return best


async def check(state: AppState, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    _stats["last_check"] = now.strftime("%Y-%m-%d %H:%M:%S")

    def done(**r):
        _stats["result"] = r
        return r
    pos = state.db.get_setting(LAST_KEY, None)
    if not pos:
        return done(status="keine Position", hinweis="Das Handy hat noch keine Position gemeldet.")
    age = time.time() - float(pos["ts"])
    if age > FRESH_SECONDS:
        return done(status="Position zu alt", alter_minuten=round(age / 60), hinweis="Ohne frische Position wird nichts berechnet.")
    found = next_event(state, now)
    if not found:
        return done(status="kein Termin mit Ort", hinweis="In den nächsten Stunden steht nichts mit Ort an, das dich betrifft.")
    ev, start = found
    dest = await geocode(state, ev.location)
    if not dest:
        return done(status="Ort nicht gefunden", termin=ev.title, hinweis="Der Ort ließ sich nicht auf einer Karte finden.")
    here = (float(pos["lat"]), float(pos["lon"]))
    if meters(here, dest) < ARRIVED_METERS:
        return done(status="schon da", termin=ev.title)
    drive = await drive_seconds(here, dest)
    if drive is None:
        return done(status="keine Fahrzeit", termin=ev.title, hinweis="Der Routendienst hat nicht geantwortet; es wird nichts geschätzt.")
    buffer_s = float(os.environ.get("STANDORT_PUFFER_MIN", "10")) * 60
    level, remaining_min = decide(now, start, drive, buffer_s)
    if not level:
        return done(status="alles gut", termin=ev.title, fahrt_minuten=round(drive / 60), losfahren_in_minuten=round(remaining_min))
    sent = state.db.get_setting(SENT_KEY, {}) or {}
    sent = {k: v for k, v in sent.items() if time.time() - v < 2 * 86400}
    key = f"{ev.uid}:{level}"
    if key in sent:
        return done(status="schon gemeldet", stufe=level, termin=ev.title)
    text = message(level, ev, drive, now, start, remaining_min)
    sent[key] = time.time()
    state.db.set_setting(SENT_KEY, sent)
    from ..heartbeat import _say, push
    severity = "warning" if level == "bald" else "error"
    state.services["notifications"].notify(category="system", severity=severity, title="Losfahren", body=text)
    _say(text, severity)
    pushed = await push("Jarvis – Losfahren" if level != "spaet" else "Jarvis – Verspätung", text, severity)
    return done(status="gemeldet", stufe=level, termin=ev.title, push=pushed)


@router.post("/ping")
async def ping(request: Request, state: AppState = Depends(get_state)):
    """Das Handy meldet seine Position. Antwortet für OwnTracks mit einer leeren Liste."""
    if not token():
        return JSONResponse({"detail": "Standort ist nicht eingerichtet (STANDORT_TOKEN fehlt)."}, status_code=503)
    if not _authorized(request):
        return JSONResponse({"detail": "Nicht erlaubt."}, status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    q = request.query_params
    if not isinstance(body, dict):
        body = {}
    if body.get("_type", "location") != "location":
        return JSONResponse([])                             # OwnTracks schickt auch andere Ereignisse: ignorieren
    try:
        lat, lon = float(body.get("lat", q.get("lat"))), float(body.get("lon", q.get("lon")))
    except (TypeError, ValueError):
        return JSONResponse({"detail": "lat und lon fehlen."}, status_code=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JSONResponse({"detail": "Ungültige Koordinaten."}, status_code=400)
    ts = float(body.get("tst") or time.time())
    if ts > time.time() + 300 or ts < time.time() - 86400:      # Uhr des Handys falsch oder uralte Meldung
        ts = time.time()
    state.db.set_setting(LAST_KEY, {"lat": lat, "lon": lon, "ts": ts})
    return JSONResponse([])


@router.get("")
async def status(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    pos = state.db.get_setting(LAST_KEY, None)
    return {**_stats, "eingerichtet": bool(token()), "letzte_position_vor_minuten": round((time.time() - float(pos["ts"])) / 60) if pos else None,
            "intervall_sekunden": INTERVAL_SECONDS}


def _startup(state: AppState) -> None:
    async def job() -> None:
        try:
            await check(state)
        except Exception as e:  # noqa: BLE001
            _stats["result"] = {"status": "Fehler", "hinweis": f"{e.__class__.__name__}"}

    state.scheduler.add("standort_watch", "Pünktlichkeit prüfen", INTERVAL_SECONDS, job, silent=True,
                        description="Fahrzeit zum nächsten Termin prüfen und rechtzeitig zum Losfahren mahnen",
                        enabled=bool(token()), run_immediately=False)


MODULE = ModuleSpec(id="standort", title="Standort", router=router, nav=False, order=5,
                    description="Fahrzeit zum nächsten Termin und Losfahr-Warnung", on_startup=_startup)
