"""
Termine (appointments) — the location-aware counterpart to reminder.py.

A plain reminder.py entry only knows a date and time. An appointment also
carries an address, which actions/travel_reminder.py uses to work out the
drive time from the phone's last known location (shared via the Geräte
pairing in dashboard/server.py) and ping the phone to leave in time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime


def _slug(title: str, date_str: str, time_str: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.lower().strip())[:30].strip("_") or "termin"
    return f"{base}_{date_str}_{time_str}".replace(":", "")


def _load() -> dict:
    from memory.memory_manager import load_memory
    data = load_memory().get("appointments", {})
    return data if isinstance(data, dict) else {}


def _save(appointments: dict) -> None:
    from memory.memory_manager import load_memory, MEMORY_PATH, _lock
    memory = load_memory()
    memory["appointments"] = appointments
    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _prune_past(appointments: dict) -> dict:
    """Drop appointments more than a day past their time — keeps the list from
    growing forever without needing an explicit 'done' step from the user."""
    now = datetime.now()
    kept = {}
    for key, appt in appointments.items():
        try:
            dt = datetime.strptime(f"{appt['date']} {appt['time']}", "%Y-%m-%d %H:%M")
            if (now - dt).total_seconds() < 86400:
                kept[key] = appt
        except Exception:
            kept[key] = appt   # malformed entry — keep it rather than silently lose it
    return kept


def add_appointment(title: str, date_str: str, time_str: str, address: str) -> str:
    title = (title or "").strip()
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    address = (address or "").strip()

    if not title or not date_str or not time_str:
        return "I need a title, a date and a time for the appointment."

    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "I couldn't parse that date or time. Please use YYYY-MM-DD and HH:MM."

    if target_dt <= datetime.now():
        return "That time has already passed."

    appointments = _prune_past(_load())
    key = _slug(title, date_str, time_str)
    appointments[key] = {
        "title": title,
        "date": date_str,
        "time": time_str,
        "address": address,
        "lat": None,
        "lon": None,
        "notified_leave": False,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _save(appointments)

    friendly = target_dt.strftime("%B %d at %I:%M %p")
    if address:
        return (f"Appointment '{title}' set for {friendly} at {address}. "
                f"I'll work out the drive time and tell you when to leave, "
                f"once your phone is paired and sharing its location.")
    return f"Appointment '{title}' set for {friendly}. No address given, so I can't compute a departure reminder for it."


def list_appointments() -> str:
    appointments = _prune_past(_load())
    if not appointments:
        return "No upcoming appointments."
    rows = sorted(
        appointments.values(),
        key=lambda a: f"{a.get('date', '')} {a.get('time', '')}",
    )
    lines = []
    for a in rows:
        loc = f" @ {a['address']}" if a.get("address") else ""
        lines.append(f"- {a.get('title')}: {a.get('date')} {a.get('time')}{loc}")
    return "Upcoming appointments:\n" + "\n".join(lines)


def remove_appointment(identifier: str) -> str:
    identifier = (identifier or "").strip().lower()
    if not identifier:
        return "Which appointment should I remove?"
    appointments = _load()
    for key, appt in list(appointments.items()):
        if identifier in key or identifier in appt.get("title", "").lower():
            title = appointments.pop(key)["title"]
            _save(appointments)
            return f"Removed appointment: {title}"
    return f"No appointment found matching: {identifier}"


def manage_appointments(parameters: dict, player=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    if action == "add":
        result = add_appointment(
            parameters.get("title", ""),
            parameters.get("date", ""),
            parameters.get("time", ""),
            parameters.get("address", ""),
        )
    elif action == "list":
        result = list_appointments()
    elif action == "remove":
        result = remove_appointment(parameters.get("title", ""))
    else:
        result = "Specify action (add/list/remove)."

    if player:
        try:
            player.write_log(f"[Termine] {result[:80]}")
        except Exception:
            pass
    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "manage_appointments",
    "description": (
        "Manages the user's Termine (appointments) — each one has a title, a date/time, "
        "and optionally a street address. Use this instead of `reminder` whenever the user "
        "gives a physical location for something (a doctor's appointment, a meeting, a flight) "
        "so JARVIS can later work out the drive time and remind them when to leave to arrive on "
        "time. Requires the user's phone to be paired (Geräte) and sharing its location for the "
        "departure reminder to actually fire — say so if they haven't set that up."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "One of: add | list | remove"},
            "title": {"type": "STRING", "description": "Short title of the appointment"},
            "date": {"type": "STRING", "description": "Date in YYYY-MM-DD format (for 'add')"},
            "time": {"type": "STRING", "description": "Time in HH:MM 24h format (for 'add')"},
            "address": {"type": "STRING", "description": "Street address / place name (for 'add')"},
        },
        "required": ["action"],
    },
    "handler": manage_appointments,
}
