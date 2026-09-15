"""
calendar — appointments, for real.

JARVIS could set a reminder (an OS notification that fires once) but had no way
to put anything in a calendar: no duration, no place, nothing that reaches a
phone or a colleague. This is that skill.

ONE tool, two backends. Two competing calendar tools would be a routing hazard —
the model would have to guess which one the user meant — so the choice happens
here instead:

  * **google** — the user's real Google Calendar. Needs a one-time OAuth
    connect from ⚙ → PLUGIN SETTINGS.
  * **local**  — a JSON store plus an .ics file per appointment, handed to
    whatever the OS opens calendar files with. No account, works offline, and
    it is what `auto` falls back to when Google is not connected.

Everything the tool returns is a short English sentence: tool output is data for
the model, and JARVIS speaks the user's own language from it (see the LANGUAGE
section of core/prompt.txt).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from memory.config_manager import get_plugin_setting
from plugins._calendar_core import (
    DEFAULT_DURATION,
    CalendarError,
    Event,
    LocalCalendar,
    STORE_FILE,
    build_event,
    parse_date,
    parse_duration,
    parse_time,
)
from plugins._calendar_google import DEFAULT_SECRET, GoogleCalendar

NAMESPACE = "calendar"
MAX_LISTED = 12


# ── Backend selection ────────────────────────────────────────────────────────

def _google_from_settings(values: dict | None = None) -> GoogleCalendar:
    values = values or {}
    secret = (values.get("client_secret")
              or get_plugin_setting(NAMESPACE, "client_secret", "")
              or str(DEFAULT_SECRET))
    calendar_id = (values.get("calendar_id")
                   or get_plugin_setting(NAMESPACE, "calendar_id", "primary"))
    return GoogleCalendar(secret_file=Path(secret), calendar_id=calendar_id)


def _pick_backend():
    """Returns (backend, note). `note` is non-empty only when the choice is not
    what the user asked for — the caller appends it so a silent downgrade to the
    local calendar can never be mistaken for a Google booking."""
    choice = str(get_plugin_setting(NAMESPACE, "backend", "auto") or "auto").lower()
    local = LocalCalendar(
        store_file=STORE_FILE,
        open_files=bool(get_plugin_setting(NAMESPACE, "open_ics", True)),
    )

    if choice == "local":
        return local, ""

    google = _google_from_settings()
    if google.available():
        return google, ""

    if choice == "google":
        # Explicitly asked for Google and it is not connected: say so rather
        # than booking somewhere the user is not looking.
        raise CalendarError(
            "Google Calendar is selected but not connected — press CONNECT in "
            "plugin settings, or switch the calendar backend to 'local'."
        )
    return local, " (saved locally — Google Calendar is not connected)"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _default_duration() -> int:
    return parse_duration(get_plugin_setting(NAMESPACE, "default_duration", DEFAULT_DURATION),
                          default=DEFAULT_DURATION)


def _format_list(events: list[Event], header: str) -> str:
    if not events:
        return "Nothing in the calendar for that period."
    shown = events[:MAX_LISTED]
    lines = "\n".join(f"- {e.spoken()}" for e in shown)
    more = (f"\n(and {len(events) - len(shown)} more)"
            if len(events) > len(shown) else "")
    return f"{header}\n{lines}{more}"


def _resolve_one(backend, query: str, what: str) -> Event:
    """Find exactly one appointment, or raise with something the user can act on.
    Never picks for them when several match — cancelling the wrong meeting is
    not a mistake an assistant gets to make on a guess."""
    if not query.strip():
        raise CalendarError(f"Which appointment should I {what}? Tell me its title.")
    matches = backend.find(query)
    if not matches:
        raise CalendarError(f"I found no upcoming appointment matching '{query}'.")
    if len(matches) > 1:
        listed = "; ".join(e.spoken() for e in matches[:5])
        raise CalendarError(
            f"That matches {len(matches)} appointments: {listed}. Which one?"
        )
    return matches[0]


# ── Actions ──────────────────────────────────────────────────────────────────

def _create(p: dict, backend, note: str) -> str:
    title = str(p.get("title", "")).strip()
    if not title:
        return "What should the appointment be called?"

    when_date = parse_date(str(p.get("date", "")))
    when_time = parse_time(str(p.get("time", "")))
    minutes = parse_duration(p.get("duration_minutes"), default=_default_duration())

    event = build_event(
        title=title,
        when_date=when_date,
        when_time=when_time,
        minutes=minutes,
        location=str(p.get("location", "")),
        notes=str(p.get("notes", "")),
    )
    saved = backend.create(event)
    return f"Booked — {saved.spoken()}.{note}"


def _list(p: dict, backend, note: str) -> str:
    try:
        days = int(p.get("days") or 7)
    except (TypeError, ValueError):
        days = 7
    days = max(1, min(days, 365))
    query = str(p.get("query", "")).strip()
    events = backend.list(days=days, query=query)
    header = (f"Next {days} day{'s' if days != 1 else ''}"
              + (f" matching '{query}'" if query else "") + ":")
    return _format_list(events, header) + note


def _cancel(p: dict, backend, note: str) -> str:
    query = str(p.get("query") or p.get("title") or "")
    event = _resolve_one(backend, query, "cancel")
    backend.delete(event)
    return f"Cancelled — {event.spoken()}.{note}"


def _move(p: dict, backend, note: str) -> str:
    query = str(p.get("query") or p.get("title") or "")
    event = _resolve_one(backend, query, "move")

    new_date_raw = str(p.get("new_date") or p.get("date") or "")
    new_time_raw = str(p.get("new_time") or p.get("time") or "")
    if not new_date_raw and not new_time_raw:
        return "What is the new date or time for that appointment?"

    old_start = event.start_dt()
    new_date = parse_date(new_date_raw) if new_date_raw else old_start.date()
    new_time = parse_time(new_time_raw) if new_time_raw else old_start.time()

    moved = backend.move(event, datetime.combine(new_date, new_time))
    return f"Moved — {moved.spoken()}.{note}"


_ACTIONS = {
    "create": _create, "add": _create, "new": _create, "book": _create,
    "list": _list, "agenda": _list, "show": _list, "get": _list,
    "cancel": _cancel, "delete": _cancel, "remove": _cancel,
    "move": _move, "reschedule": _move, "update": _move,
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "create")).strip().lower() or "create"
    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"I do not know the calendar action '{action}'. "
                f"I can create, list, move or cancel appointments.")

    try:
        backend, note = _pick_backend()
        result = handler(p, backend, note)
    except CalendarError as e:
        result = str(e)
    except Exception as e:
        result = f"The calendar failed: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result


# ── Settings form (⚙ → PLUGIN SETTINGS) ──────────────────────────────────────

def _connect(values: dict) -> tuple[bool, str]:
    return _google_from_settings(values).connect()


PLUGIN_SETTINGS = {
    "namespace": NAMESPACE,
    "title": "🗓  CALENDAR",
    "fields": [
        {"key": "backend", "label": "Calendar", "type": "choice",
         "options": ["auto", "google", "local"], "default": "auto"},
        {"key": "calendar_id", "label": "Google calendar id", "type": "text",
         "default": "primary", "placeholder": "primary"},
        {"key": "client_secret", "label": "OAuth client file", "type": "text",
         "default": str(DEFAULT_SECRET),
         "placeholder": "config/client_secret_google_calendar.json"},
        {"key": "default_duration", "label": "Default length (minutes)", "type": "text",
         "default": str(DEFAULT_DURATION), "placeholder": "60"},
        {"key": "open_ics", "label": "Open .ics in the calendar app (local mode)",
         "type": "toggle", "default": True},
    ],
    "action": {"label": "CONNECT GOOGLE", "run": _connect},
}


# ── Tool declaration (auto-discovered by core/plugin_loader.py) ──────────────
PLUGIN = {
    "name": "calendar",
    "description": (
        "Creates, lists, moves and cancels real calendar appointments — anything the user "
        "says about their calendar, schedule, diary, meetings or appointments ('put it in my "
        "calendar', 'what's on tomorrow', 'move the Müller appointment', 'cancel Friday'). "
        "Writes to Google Calendar when connected, otherwise to a local calendar file the "
        "user's calendar app opens. Use the 'reminder' tool instead when the user only wants "
        "a one-off notification at a time, with no appointment in their calendar."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "create (default) | list | move | cancel"
            },
            "title": {
                "type": "STRING",
                "description": "What the appointment is called"
            },
            "date": {
                "type": "STRING",
                "description": "Date as YYYY-MM-DD (also accepts today/tomorrow, DD.MM.YYYY, a weekday name)"
            },
            "time": {
                "type": "STRING",
                "description": "Start time as HH:MM (24h)"
            },
            "duration_minutes": {
                "type": "INTEGER",
                "description": "Length in minutes (default 60)"
            },
            "location": {
                "type": "STRING",
                "description": "Where it takes place"
            },
            "notes": {
                "type": "STRING",
                "description": "Extra detail for the appointment body"
            },
            "query": {
                "type": "STRING",
                "description": "Title to search for when moving, cancelling or filtering a list"
            },
            "new_date": {
                "type": "STRING",
                "description": "New date when moving an appointment"
            },
            "new_time": {
                "type": "STRING",
                "description": "New start time when moving an appointment"
            },
            "days": {
                "type": "INTEGER",
                "description": "How many days ahead to list (default 7)"
            }
        },
        "required": []
    },
}
