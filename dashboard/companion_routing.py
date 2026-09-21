"""
Reuses the OSRM/Nominatim approach already present in the Command Center.
No traffic claim, no customer messages, no background or push delivery.
"""
import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_geo_cache = {}
_geo_lock = asyncio.Lock()
_last_geo = 0.0
_route_lock = asyncio.Lock()
_route_cache = {}


def request_json(url):
    request = Request(url, headers={"User-Agent": "JarvisCompanion-ReyesService/1.0 (+https://jarvis.jarvis-reyes.de)"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Response too large")
    return json.loads(raw)


def validate_position(data):
    lat, lon = float(data['lat']), float(data['lon'])
    timestamp, accuracy = float(data['timestamp']), float(data['accuracy'])
    if not all(math.isfinite(v) for v in (lat, lon, timestamp, accuracy)):
        raise ValueError('Ungültiger Standort.')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= accuracy <= 1000):
        raise ValueError('Standort zu ungenau oder ungültig. Bitte neu erfassen.')
    if not -30 <= time.time() - timestamp <= 180:
        raise ValueError('Standort älter als drei Minuten. Bitte neu erfassen.')
    return lat, lon


async def destinations(address, config):
    global _last_geo
    async with _geo_lock:
        if address in _geo_cache:
            return _geo_cache[address]
        await asyncio.sleep(max(0, 1.1 - (time.monotonic() - _last_geo)))
        _last_geo = time.monotonic()
        base = config.get('geocoder_url', 'https://nominatim.openstreetmap.org').rstrip('/')
        rows = await asyncio.to_thread(request_json, base + '/search?' + urlencode({
            'q': address, 'format': 'json', 'limit': 3, 'countrycodes': 'de'}))
        result = []
        for row in rows:
            lat, lon = float(row['lat']), float(row['lon'])
            if not math.isfinite(lat) or not math.isfinite(lon):
                continue
            ident = hashlib.sha256(f'{address}|{lat}|{lon}'.encode()).hexdigest()[:24]
            result.append({'id': ident, 'label': str(row['display_name'])[:600], 'lat': lat, 'lon': lon})
        if len(_geo_cache) >= 200:
            _geo_cache.pop(next(iter(_geo_cache)))
        _geo_cache[address] = result
        return result


def forecast(start, seconds, now):
    arrival = now + timedelta(seconds=seconds)
    departure = start - timedelta(seconds=seconds + 600)
    remaining = (departure - now).total_seconds()
    level = 'spaet' if arrival > start else 'los' if remaining <= 0 else 'bald' if remaining <= 900 else 'zeit'
    return {'arrival': arrival.isoformat(), 'departure': departure.isoformat(),
            'durationMinutes': math.ceil(seconds / 60), 'bufferMinutes': 10, 'level': level}


async def calculate(event, data, config):
    lat, lon = validate_position(data)
    if event['allDay'] or not event['location']:
        raise ValueError('Für diesen Termin fehlen Uhrzeit oder Adresse.')
    start = datetime.fromisoformat(event['start'].replace('Z', '+00:00'))
    now = datetime.now(start.tzinfo)
    if start <= now:
        raise ValueError('Dieser Termin hat bereits begonnen. Keine Abfahrtsprognose.')
    options = await destinations(event['location'], config)
    if not options:
        raise ValueError('Adresse nicht gefunden. Bitte den Kalenderort prüfen.')
    destination = next((x for x in options if x['id'] == data.get('destinationId')), None)
    if destination is None:
        return {'needsConfirmation': True, 'destinations': options}
    key = (round(lat, 4), round(lon, 4), destination['id'])
    async with _route_lock:
        cached = _route_cache.get(key)
        if cached and time.time() - cached[0] < 180:
            measured, seconds = cached
        else:
            base = config.get('routing_url', 'https://router.project-osrm.org').rstrip('/')
            url = f"{base}/route/v1/driving/{lon},{lat};{destination['lon']},{destination['lat']}?overview=false"
            result = await asyncio.to_thread(request_json, url)
            if result.get('code') != 'Ok' or not result.get('routes'):
                raise ValueError('Keine Route verfügbar.')
            seconds = float(result['routes'][0]['duration'])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Ungültige Fahrzeit.')
            measured = time.time()
            if len(_route_cache) >= 200:
                _route_cache.pop(next(iter(_route_cache)))
            _route_cache[key] = (measured, seconds)
    return {**forecast(start, seconds, now), 'needsConfirmation': False,
            'eventId': event['id'], 'destination': destination['label'],
            'calculatedAt': datetime.fromtimestamp(measured, start.tzinfo).isoformat(),
            'uncertainty': 'Schätzung ohne Live-Verkehr. 10 Minuten Planungspuffer; keine Ankunftsgarantie.',
            'source': 'OSRM / OpenStreetMap'}
