"""
Shared calendar machinery — parsing, the event record, and the local backend.

Underscore-prefixed, so plugin discovery skips it: this is a helper module for
``plugins/calendar.py``, not a skill of its own.

Two things live here that are worth stating plainly:

**Parsing is deliberately narrow.** Gemini extracts tool parameters, so the
plugin asks for `YYYY-MM-DD` and `HH:MM` and gets them almost always. What it
additionally accepts is the small set of forms a model actually emits when the
user speaks German or English — `morgen`, `tomorrow`, `Montag`, `14.03.`,
`14 Uhr`. It does NOT try to be a natural-language date library: an input it
cannot read with certainty is refused with a message naming what it expected,
because a meeting silently booked on the wrong day is worse than one not booked.

**Times are floating local time.** An appointment at 14:00 means 14:00 where the
user is; writing it with a UTC offset would move it if they travel, and shipping
a whole VTIMEZONE block for a one-line reminder is not worth the surface. The
Google backend, which talks to a server in another place, does send an offset.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path
from typing import Optional


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR   = get_base_dir()
STORE_DIR  = BASE_DIR / "memory" / "calendar"
STORE_FILE = STORE_DIR / "events.json"

DEFAULT_DURATION = 60


class CalendarError(Exception):
    """Something the user needs to hear about, phrased for speaking aloud."""


# ── Parsing ──────────────────────────────────────────────────────────────────

_WEEKDAYS = {
    "monday": 0, "montag": 0, "mo": 0, "mon": 0,
    "tuesday": 1, "dienstag": 1, "di": 1, "tue": 1,
    "wednesday": 2, "mittwoch": 2, "mi": 2, "wed": 2,
    "thursday": 3, "donnerstag": 3, "do": 3, "thu": 3,
    "friday": 4, "freitag": 4, "fr": 4, "fri": 4,
    "saturday": 5, "samstag": 5, "sonnabend": 5, "sa": 5, "sat": 5,
    "sunday": 6, "sonntag": 6, "so": 6, "sun": 6,
}
_TODAY    = {"today", "heute"}
_TOMORROW = {"tomorrow", "morgen"}
_DAY_AFTER = {"übermorgen", "uebermorgen", "day after tomorrow", "overmorrow"}


def parse_date(raw: str, *, today: Optional[date] = None) -> date:
    """`YYYY-MM-DD`, `DD.MM.YYYY`, `DD.MM.`, today/tomorrow/übermorgen, or a
    weekday name meaning its next occurrence. Raises CalendarError otherwise."""
    today = today or date.today()
    text = (raw or "").strip().lower()
    if not text:
        raise CalendarError("I need a date — say it as YYYY-MM-DD, or 'tomorrow'.")

    if text in _TODAY:
        return today
    if text in _TOMORROW:
        return today + timedelta(days=1)
    if text in _DAY_AFTER:
        return today + timedelta(days=2)

    if text in _WEEKDAYS:
        # "on Monday" means the next Monday; today only counts as next Monday
        # when it IS Monday and the caller has not told us the time has passed.
        delta = (_WEEKDAYS[text] - today.weekday()) % 7
        return today + timedelta(days=delta)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)), raw)

    # German-style 14.03.2026 / 14.03. / 14.3
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", text)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        parsed = _safe_date(year, int(m.group(2)), int(m.group(1)), raw)
        # A bare "14.03." that already passed this year means next year.
        if not m.group(3) and parsed < today:
            parsed = _safe_date(year + 1, int(m.group(2)), int(m.group(1)), raw)
        return parsed

    raise CalendarError(
        f"I could not read '{raw}' as a date. Use YYYY-MM-DD, DD.MM.YYYY, "
        f"'today', 'tomorrow', or a weekday name."
    )


def _safe_date(year: int, month: int, day: int, raw: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as e:
        raise CalendarError(f"'{raw}' is not a real date ({e}).")


def parse_time(raw: str) -> dtime:
    """`HH:MM`, `HH.MM`, `HH Uhr`, `HH`, with optional am/pm."""
    text = (raw or "").strip().lower().replace("uhr", "").strip()
    if not text:
        raise CalendarError("I need a time — say it as HH:MM, for example 14:30.")

    suffix = ""
    m = re.search(r"\s*(am|pm)$", text)
    if m:
        suffix = m.group(1)
        text = text[:m.start()].strip()

    m = re.fullmatch(r"(\d{1,2})(?:[:.h](\d{2}))?", text)
    if not m:
        raise CalendarError(f"I could not read '{raw}' as a time. Use HH:MM, for example 14:30.")

    hour = int(m.group(1))
    minute = int(m.group(2) or 0)

    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CalendarError(f"'{raw}' is not a real time of day.")
    return dtime(hour, minute)


def parse_duration(raw, default: int = DEFAULT_DURATION) -> int:
    """Minutes. Accepts an int, `90`, `1h`, `1h30`, `1:30`, `90 min`, `2 Stunden`."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, (int, float)):
        minutes = int(raw)
        return minutes if minutes > 0 else default

    text = str(raw).strip().lower()

    m = re.fullmatch(r"(\d{1,2})[:h](\d{2})", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    m = re.fullmatch(r"(\d+)\s*(h|hr|hrs|hour|hours|std|stunde|stunden)", text)
    if m:
        return int(m.group(1)) * 60

    m = re.fullmatch(r"(\d+)\s*(m|min|mins|minute|minuten)?", text)
    if m:
        minutes = int(m.group(1))
        return minutes if minutes > 0 else default

    return default


# ── The event record ─────────────────────────────────────────────────────────

@dataclass
class Event:
    uid: str
    title: str
    start: str                    # ISO 8601, local, no offset: 2026-09-15T14:00:00
    end: str
    location: str = ""
    notes: str = ""
    backend: str = "local"
    remote_id: str = ""           # the backend's own id, when it has one
    file: str = ""                # the .ics written for it, when there is one

    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.start)

    def end_dt(self) -> datetime:
        return datetime.fromisoformat(self.end)

    def spoken(self) -> str:
        start = self.start_dt()
        end = self.end_dt()
        where = f" at {self.location}" if self.location else ""
        return (f"{start.strftime('%a %d %b %H:%M')}–{end.strftime('%H:%M')}: "
                f"{self.title}{where}")


def build_event(title: str, when_date: date, when_time: dtime, minutes: int,
                location: str = "", notes: str = "", backend: str = "local") -> Event:
    start = datetime.combine(when_date, when_time)
    end = start + timedelta(minutes=minutes)
    return Event(
        uid=f"{uuid.uuid4().hex}@jarvis",
        title=title.strip() or "Appointment",
        start=start.isoformat(timespec="seconds"),
        end=end.isoformat(timespec="seconds"),
        location=location.strip(),
        notes=notes.strip(),
        backend=backend,
    )


# ── iCalendar output ─────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\")
                .replace(";", r"\;")
                .replace(",", r"\,")
                .replace("\r\n", r"\n")
                .replace("\n", r"\n"))


def _fold(line: str) -> str:
    """RFC 5545 §3.1: content lines are folded at 75 octets, continuations
    starting with a space. Folding counts BYTES, not characters — an umlaut in
    a title is two octets, and a naive character-count split writes a file some
    calendar apps reject."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, start = [], 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # never split a multi-byte character
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74          # continuation lines carry a leading space
    return "\r\n ".join(chunks)


def to_ics(events: list[Event]) -> str:
    """A complete VCALENDAR. CRLF line endings are required by the spec, so this
    returns them regardless of the platform it runs on."""
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MARK LIII//JARVIS Calendar//EN",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev.uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{ev.start_dt().strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{ev.end_dt().strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_escape(ev.title)}",
        ]
        if ev.location:
            lines.append(f"LOCATION:{_escape(ev.location)}")
        if ev.notes:
            lines.append(f"DESCRIPTION:{_escape(ev.notes)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def open_in_default_app(path: Path) -> bool:
    """Hand the .ics to whatever the OS uses for calendar files. Best effort —
    a headless machine has no handler, and that is not an error worth speaking."""
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))          # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ── Local backend ────────────────────────────────────────────────────────────

class LocalCalendar:
    """Events in a JSON file next to the assistant's memory, each also written
    as an .ics so the user's real calendar app can take it. No account, no
    network, nothing to configure — this is what runs when Google is not set up."""

    name = "local"

    def __init__(self, store_file: Path = STORE_FILE, open_files: bool = True):
        self.store_file = Path(store_file)
        self.open_files = open_files

    # -- storage --
    def _load(self) -> list[Event]:
        try:
            raw = json.loads(self.store_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        out = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(Event(**item))
            except Exception:
                continue          # one corrupt record never hides the rest
        return out

    def _save(self, events: list[Event]) -> None:
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_file.write_text(
            json.dumps([asdict(e) for e in events], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- operations --
    def create(self, event: Event) -> Event:
        event.backend = self.name
        ics_path = self.store_file.parent / f"{event.uid.split('@')[0]}.ics"
        ics_path.parent.mkdir(parents=True, exist_ok=True)
        ics_path.write_bytes(to_ics([event]).encode("utf-8"))
        event.file = str(ics_path)

        events = self._load()
        events.append(event)
        self._save(events)

        if self.open_files:
            open_in_default_app(ics_path)
        return event

    def list(self, days: int = 7, query: str = "") -> list[Event]:
        now = datetime.now()
        until = now + timedelta(days=max(1, days))
        found = [
            e for e in self._load()
            if now.date() <= e.start_dt().date() <= until.date()
            and (not query or query.lower() in e.title.lower())
        ]
        return sorted(found, key=lambda e: e.start)

    def find(self, query: str) -> list[Event]:
        q = (query or "").lower().strip()
        upcoming = [e for e in self._load() if e.end_dt() >= datetime.now()]
        if not q:
            return sorted(upcoming, key=lambda e: e.start)
        return sorted([e for e in upcoming if q in e.title.lower()], key=lambda e: e.start)

    def delete(self, event: Event) -> None:
        remaining = [e for e in self._load() if e.uid != event.uid]
        self._save(remaining)
        try:
            if event.file:
                Path(event.file).unlink(missing_ok=True)
        except Exception:
            pass

    def move(self, event: Event, new_start: datetime) -> Event:
        duration = event.end_dt() - event.start_dt()
        events = self._load()
        for e in events:
            if e.uid == event.uid:
                e.start = new_start.isoformat(timespec="seconds")
                e.end = (new_start + duration).isoformat(timespec="seconds")
                if e.file:
                    try:
                        Path(e.file).write_bytes(to_ics([e]).encode("utf-8"))
                    except Exception:
                        pass
                self._save(events)
                return e
        raise CalendarError("That appointment is no longer in the calendar.")
