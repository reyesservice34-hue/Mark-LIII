"""Read-only calendar adapter for the existing Mark-LIII authentication.

The protected config is provisioned separately from the existing calendar bridge.
No Google credentials or upstream tokens are returned to the browser.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request as URLRequest, urlopen
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import JSONResponse

BERLIN = ZoneInfo("Europe/Berlin")


def normalize_event(item, source):
    def when(value):
        if isinstance(value, dict):
            return value.get("dateTime") or value.get("date") or ""
        return str(value or "")
    start, end = when(item.get("start")), when(item.get("end"))
    if not start:
        return None
    for value in (start, end):
        if value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if len(value) > 10 and parsed.tzinfo is None:
                raise ValueError("Calendar datetime has no timezone")
    raw_id = str(item.get("id") or item.get("iCalUID") or start + str(item.get("summary")))
    return {"id": hashlib.sha256((str(source) + raw_id).encode()).hexdigest()[:24],
            "title": str(item.get("summary") or "Ohne Titel")[:300],
            "start": start, "end": end, "allDay": len(start) == 10,
            "location": str(item.get("location") or "")[:1000]}


def fetch_calendar(config):
    now = datetime.now(BERLIN)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    payload = json.dumps({"timeMin": start.isoformat(),
                          "timeMax": (start + timedelta(days=7)).isoformat()}).encode()
    urls, token = config.get("read_urls", []), config.get("token", "")
    if not urls or not token:
        raise ValueError("Calendar bridge is not configured")
    events = []
    for index, url in enumerate(urls):
        req = URLRequest(url, data=payload, headers={
            "Content-Type": "application/json",
            config.get("header", "X-Jarvis-Bridge"): token}, method="POST")
        with urlopen(req, timeout=35) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Calendar response too large")
        data = json.loads(raw)
        if data.get("ok") is not True or not isinstance(data.get("events"), list):
            raise ValueError("Invalid calendar response")
        if data.get("nextPageToken") or data.get("next_page_token") or data.get("truncated"):
            raise ValueError("Calendar response is incomplete")
        for item in data["events"]:
            if item.get("status") == "cancelled":
                continue
            event = normalize_event(item, index)
            if event:
                events.append(event)
    events.sort(key=lambda event: event["start"])
    return {"available": True, "source": "Google Kalender über bestehende Brücke",
            "fetchedAt": datetime.now(BERLIN).isoformat(), "timeZone": "Europe/Berlin",
            "events": events}


def install_companion(app, authenticate, base_dir):
    config_path = Path(base_dir) / "config" / "companion-calendar.json"
    lock = asyncio.Lock()
    cache, fetched = None, 0.0

    @app.get("/api/companion/calendar")
    async def calendar(req: Request):
        nonlocal cache, fetched
        headers = {"Cache-Control": "no-store"}
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401, headers=headers)
        async with lock:
            if cache is not None and time.monotonic() - fetched < 60:
                return JSONResponse(cache, headers=headers)
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                data = await asyncio.to_thread(fetch_calendar, config)
            except Exception:
                # Do not expose upstream URLs, tokens or private exception text.
                return JSONResponse({"available": False,
                    "detail": "Kalender derzeit nicht erreichbar. Keine aktuelle Terminprüfung möglich."},
                    status_code=503, headers=headers)
            cache, fetched = data, time.monotonic()
            return JSONResponse(data, headers=headers)

    @app.post("/api/companion/eta")
    async def eta(req: Request):
        if not authenticate(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        try:
            if int(req.headers.get('content-length', '0')) > 4096:
                raise ValueError('Anfrage zu groß.')
            body = await req.body()
            if len(body) > 4096:
                raise ValueError('Anfrage zu groß.')
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Ungültige Anfrage.')
            if cache is None or time.monotonic() - fetched > 300:
                return JSONResponse({'error': 'Kalender zuerst aktualisieren.'}, status_code=409)
            event = next((event for event in cache['events'] if event['id'] == data.get('eventId')), None)
            if event is None:
                raise ValueError('Termin nicht im aktuellen Kalender.')
            from dashboard.companion_routing import calculate
            config = json.loads(config_path.read_text(encoding='utf-8'))
            result = await calculate(event, data, config)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, KeyError, TypeError):
            return JSONResponse({'error': 'Keine Prognose möglich. Termin, Adresse und Standortfreigabe prüfen; Standort darf höchstens drei Minuten alt und nicht zu ungenau sein.'}, status_code=400)
        except Exception:
            return JSONResponse({'error': 'Routendienst derzeit nicht erreichbar. Keine aktuelle Prognose möglich.'}, status_code=503)
