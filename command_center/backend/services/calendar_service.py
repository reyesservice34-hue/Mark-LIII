"""
Calendar — one service, two backends, the same contract the desktop uses.

The parsing, the `Event` record and the local JSON/.ics store already exist in
`plugins/_calendar_core.py` and are pure standard library, so they are reused
verbatim: a date the desktop refuses is refused here too, and an appointment
booked from the dashboard lands in the same store the desktop reads.

What is *not* reused is `plugins/_calendar_google.py`: it runs a browser OAuth
flow, which a headless server cannot do. The server path talks to the Google
Calendar REST API with a refresh token from the environment instead
(`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`,
optionally `GOOGLE_CALENDAR_ID`).

Backend choice is never a question for the user: Google when it is configured,
the local store otherwise — and the answer *says which one it was*, because an
appointment the user believes is in the shared calendar but is a file on one
machine is worse than a refusal.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..config import REPO_ROOT

# The desktop's calendar core is the single source of truth for parsing and the
# local store. Importing it keeps one set of rules for both faces of JARVIS.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:  # pragma: no cover - exercised implicitly by every call below
    from plugins._calendar_core import (  # type: ignore
        CalendarError,
        Event,
        LocalCalendar,
        build_event,
        parse_date,
        parse_duration,
        parse_time,
        split_datetime,
        to_ics,
    )
    CORE_AVAILABLE = True
except Exception as _err:  # pragma: no cover - only when the repo layout changes
    CORE_AVAILABLE = False
    _IMPORT_ERROR = str(_err)

    class CalendarError(Exception):  # type: ignore[no-redef]
        pass

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_API = "https://www.googleapis.com/calendar/v3"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class GoogleCalendarRest:
    """Minimal Google Calendar client: list, insert, patch, delete.

    Access tokens are fetched from the refresh token and cached in memory only.
    Nothing is written to disk, so a container restart simply fetches a new one.
    """

    name = "google"

    def __init__(self, calendar_id: str = ""):
        self.client_id = _env("GOOGLE_CLIENT_ID")
        self.client_secret = _env("GOOGLE_CLIENT_SECRET")
        self.refresh_token = _env("GOOGLE_REFRESH_TOKEN")
        self.calendar_id = calendar_id or _env("GOOGLE_CALENDAR_ID") or "primary"
        self._token = ""
        self._token_expires = 0.0

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(GOOGLE_TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise CalendarError(
                "Google refused the stored credentials — the refresh token is invalid or revoked. "
                "Set GOOGLE_REFRESH_TOKEN again.")
        data = r.json()
        self._token = data.get("access_token", "")
        self._token_expires = time.time() + float(data.get("expires_in", 3600))
        if not self._token:
            raise CalendarError("Google returned no access token.")
        return self._token

    async def _request(self, method: str, path: str, **kw) -> Any:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.request(method, f"{GOOGLE_API}{path}",
                                headers={"Authorization": f"Bearer {token}"}, **kw)
        if r.status_code == 404:
            raise CalendarError("Google could not find that calendar or appointment.")
        if r.status_code == 403:
            raise CalendarError("Google refused the request — the account lacks calendar permission.")
        if r.status_code >= 400:
            raise CalendarError(f"Google Calendar refused the request (HTTP {r.status_code}).")
        return r.json() if r.content else {}

    # -- mapping --
    @staticmethod
    def _to_event(item: dict) -> "Event":
        start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date", "")
        end = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date", "")
        # Drop the offset: the rest of JARVIS works in floating local time.
        start_local = start[:19] if "T" in start else f"{start}T00:00:00"
        end_local = end[:19] if "T" in end else f"{end}T23:59:59"
        return Event(uid=item.get("iCalUID") or item.get("id", ""), title=item.get("summary", "(no title)"),
                     start=start_local, end=end_local, location=item.get("location", "") or "",
                     notes=item.get("description", "") or "", backend="google",
                     remote_id=item.get("id", ""))

    async def list(self, days: int = 7, query: str = "") -> list["Event"]:
        now = datetime.now()
        params = {"timeMin": now.astimezone().isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "timeMax": (now + timedelta(days=max(1, days))).astimezone().isoformat(), "maxResults": "50"}
        if query:
            params["q"] = query
        data = await self._request("GET", f"/calendars/{self.calendar_id}/events", params=params)
        return [self._to_event(i) for i in data.get("items", [])]

    async def create(self, event: "Event") -> "Event":
        body = {"summary": event.title, "location": event.location, "description": event.notes,
                "start": {"dateTime": _with_offset(event.start)}, "end": {"dateTime": _with_offset(event.end)}}
        data = await self._request("POST", f"/calendars/{self.calendar_id}/events", json=body)
        created = self._to_event(data)
        created.uid = event.uid
        return created

    async def move(self, event: "Event", new_start: datetime) -> "Event":
        duration = event.end_dt() - event.start_dt()
        body = {"start": {"dateTime": _with_offset(new_start.isoformat(timespec="seconds"))},
                "end": {"dateTime": _with_offset((new_start + duration).isoformat(timespec="seconds"))}}
        data = await self._request("PATCH", f"/calendars/{self.calendar_id}/events/{event.remote_id}", json=body)
        return self._to_event(data)

    async def delete(self, event: "Event") -> None:
        await self._request("DELETE", f"/calendars/{self.calendar_id}/events/{event.remote_id}")

    async def health(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": "GOOGLE_CLIENT_ID / _SECRET / _REFRESH_TOKEN not set"}
        try:
            data = await self._request("GET", f"/calendars/{self.calendar_id}")
            return {"status": "healthy", "detail": f"calendar '{data.get('summary', self.calendar_id)}' reachable"}
        except CalendarError as e:
            return {"status": "offline", "detail": str(e)}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach Google ({e.__class__.__name__})"}


def _with_offset(iso_local: str) -> str:
    """Floating local time → an offset Google accepts, using the server's zone."""
    try:
        dt = datetime.fromisoformat(iso_local)
    except ValueError:
        return iso_local
    return dt.astimezone().isoformat(timespec="seconds")


class CalendarService:
    def __init__(self, store_file: Path | None = None):
        self.google = GoogleCalendarRest() if CORE_AVAILABLE else None
        self.local = None
        if CORE_AVAILABLE:
            # open_files=False: a server must never try to launch a desktop app.
            self.local = LocalCalendar(store_file or (REPO_ROOT / "memory" / "calendar" / "events.json"),
                                       open_files=False)

    # -- state --
    def available(self) -> bool:
        return CORE_AVAILABLE

    def unavailable_reason(self) -> str:
        return "" if CORE_AVAILABLE else f"calendar core not importable: {_IMPORT_ERROR}"

    def backend_name(self) -> str:
        return "google" if (self.google and self.google.configured()) else "local"

    async def health(self) -> dict:
        if not CORE_AVAILABLE:
            return {"status": "offline", "detail": self.unavailable_reason()}
        if self.google and self.google.configured():
            return await self.google.health()
        count = len(self.local._load()) if self.local else 0
        return {"status": "healthy",
                "detail": f"local calendar store ({count} appointments); connect Google to share them"}

    # -- operations --
    async def list(self, days: int = 7, query: str = "") -> tuple[list[dict], str]:
        self._require()
        if self.google and self.google.configured():
            events = await self.google.list(days=days, query=query)
            return [asdict(e) for e in events], "google"
        events = self.local.list(days=days, query=query)
        return [asdict(e) for e in events], "local"

    async def create(self, *, title: str, when: str, at: str = "", duration: str | int = 60,
                     location: str = "", notes: str = "") -> tuple[dict, str, str]:
        """Returns (event, backend, spoken note about the backend)."""
        self._require()
        date_part, time_part = split_datetime(when)
        if at:
            time_part = at
        # Parse the date first: a date nobody can read is the more useful
        # complaint, and reporting the missing time instead would hide it.
        when_date = parse_date(date_part)
        if not time_part:
            raise CalendarError("I need a time for the appointment, for example 14:00.")
        when_time = parse_time(time_part)
        minutes = parse_duration(duration)
        event = build_event(title, when_date, when_time, minutes, location=location, notes=notes)
        if self.google and self.google.configured():
            created = await self.google.create(event)
            return asdict(created), "google", ""
        created = self.local.create(event)
        return (asdict(created), "local",
                "Saved in the local calendar on this server — Google is not connected, "
                "so it is not in your shared calendar yet.")

    async def find(self, query: str) -> tuple[list[dict], str]:
        self._require()
        if self.google and self.google.configured():
            events = await self.google.list(days=365, query=query)
            return [asdict(e) for e in events], "google"
        return [asdict(e) for e in self.local.find(query)], "local"

    async def move(self, query: str, new_when: str, at: str = "") -> tuple[dict, str]:
        self._require()
        matches, backend = await self.find(query)
        event = self._one(matches, query)
        date_part, time_part = split_datetime(new_when)
        if at:
            time_part = at
        new_date = parse_date(date_part)
        new_time = parse_time(time_part) if time_part else Event(**event).start_dt().time()
        new_start = datetime.combine(new_date, new_time)
        if backend == "google":
            moved = await self.google.move(Event(**event), new_start)
            return asdict(moved), backend
        return asdict(self.local.move(Event(**event), new_start)), backend

    async def cancel(self, query: str) -> tuple[dict, str]:
        self._require()
        matches, backend = await self.find(query)
        event = self._one(matches, query)
        if backend == "google":
            await self.google.delete(Event(**event))
        else:
            self.local.delete(Event(**event))
        return event, backend

    # -- helpers --
    def _require(self) -> None:
        if not CORE_AVAILABLE:
            raise CalendarError(self.unavailable_reason())

    @staticmethod
    def _one(matches: list[dict], query: str) -> dict:
        """Never guess which appointment was meant."""
        if not matches:
            raise CalendarError(f"I found no appointment matching “{query}”.")
        if len(matches) > 1:
            listed = "; ".join(Event(**m).spoken() for m in matches[:5])
            raise CalendarError(f"“{query}” matches {len(matches)} appointments — which one? {listed}")
        return matches[0]

    @staticmethod
    def ics(events: list[dict]) -> str:
        return to_ics([Event(**e) for e in events])
