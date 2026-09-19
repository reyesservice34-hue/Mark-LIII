"""
travel_reminder.py — background helper (no TOOL, not model-callable).

Runs on a timer from main.py's _run_travel_reminder_monitor(). For every
upcoming appointment (actions/appointments.py) that has an address, it:

  1. Geocodes the address once (OpenRouteService Pelias) and caches lat/lon
     back onto the appointment record.
  2. Reads the paired phone's last known location from the dashboard
     (dashboard/server.py's DashboardServer.get_current_location()).
  3. Asks OpenRouteService for the driving time between the two.
  4. Once "now" reaches (appointment time − drive time − a safety buffer),
     rings the phone (DashboardServer.send_call) exactly once.

Silently does nothing if no ORS API key is configured, no phone has ever
reported a location, or python-requests can't reach the API — this is a
best-effort convenience, never a hard dependency of the reminder feature.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import requests

from actions.appointments import _load, _save, _prune_past

_ORS_GEOCODE_URL    = "https://api.openrouteservice.org/geocode/search"
_ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
_LEAVE_BUFFER_SECS  = 5 * 60     # arrive 5 min early, not exactly on time
_HTTP_TIMEOUT       = 8


def _geocode(address: str, api_key: str) -> tuple[float, float] | None:
    try:
        r = requests.get(
            _ORS_GEOCODE_URL,
            params={"api_key": api_key, "text": address, "size": 1},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        return lat, lon
    except Exception as e:
        print(f"[Travel] ⚠️ Geocoding failed for '{address}': {e}")
        return None


def _drive_seconds(origin: tuple[float, float], dest: tuple[float, float], api_key: str) -> float | None:
    try:
        r = requests.get(
            _ORS_DIRECTIONS_URL,
            params={
                "api_key": api_key,
                "start": f"{origin[1]},{origin[0]}",   # lon,lat
                "end":   f"{dest[1]},{dest[0]}",
            },
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["features"][0]["properties"]["summary"]["duration"]
    except Exception as e:
        print(f"[Travel] ⚠️ Directions request failed: {e}")
        return None


def check_and_notify(dashboard) -> list[str]:
    """Runs one pass over all appointments. Returns log lines for the caller
    to write to the desktop UI (never raises — a bad address or a flaky API
    must not take down the background loop)."""
    from memory.config_manager import get_ors_api_key

    api_key = get_ors_api_key()
    if not api_key:
        return []

    appointments = _prune_past(_load())
    if not appointments:
        return []

    logs: list[str] = []
    changed = False
    now = datetime.now()

    for key, appt in appointments.items():
        if appt.get("notified_leave") or not appt.get("address"):
            continue

        try:
            target_dt = datetime.strptime(f"{appt['date']} {appt['time']}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if target_dt <= now:
            continue

        if appt.get("lat") is None or appt.get("lon") is None:
            coords = _geocode(appt["address"], api_key)
            if coords is None:
                continue
            appt["lat"], appt["lon"] = coords
            changed = True

        location = dashboard.get_current_location()
        if not location:
            continue   # no phone has reported a position yet

        origin = (location["lat"], location["lon"])
        dest   = (appt["lat"], appt["lon"])
        duration = _drive_seconds(origin, dest, api_key)
        if duration is None:
            continue

        leave_by = target_dt - timedelta(seconds=duration) - timedelta(seconds=_LEAVE_BUFFER_SECS)
        if now < leave_by:
            continue

        minutes = max(1, round(duration / 60))
        message = (
            f"Zeit loszufahren! Die Fahrt zu '{appt['title']}' dauert ca. "
            f"{minutes} Minuten, dein Termin ist um {appt['time']} Uhr."
        )
        result = dashboard.send_call("JARVIS — Abfahrt", message)
        appt["notified_leave"] = True
        changed = True
        if result.get("sent"):
            logs.append(f"SYS: Abfahrts-Erinnerung gesendet — {appt['title']} (~{minutes} Min Fahrt).")
        else:
            logs.append(f"SYS: Abfahrts-Erinnerung für '{appt['title']}' fällig, aber kein Gerät mit Push erreichbar.")

    if changed:
        _save(appointments)

    return logs
