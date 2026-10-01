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

import asyncio
import os
import re
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


# ── Terminkollisionen ─────────────────────────────────────────────────────────────────────────────────────────
#
# Nicht jede Überschneidung ist ein Problem. Ein privater Termin des Nutzers und eine Baustelle, auf der er gar
# nicht eingeteilt ist, kollidieren nicht — das war ein Fehlalarm. Eine Kollision ist es nur, wenn beide Termine
# den Nutzer betreffen: zwei persönliche Termine, zwei Baustellen (Personal/Fahrzeug), oder ein persönlicher
# Termin und eine Baustelle, auf der der Nutzer ausdrücklich eingeteilt ist (sein Name steht im Titel oder in
# den Notizen, z. B. „Team: Paul, Christoph“). Steht bei einer Baustelle nichts zum Team, gilt er als nicht
# eingeteilt — lieber kein Alarm als ein falscher. Ganztägige Termine (Geburtstage, Urlaub) blockieren keine Zeit.

def owner_names() -> list[str]:
    """Wie der Nutzer in Titeln und Notizen vorkommt (CALENDAR_OWNER_NAMES, Komma-getrennt)."""
    return [n.strip().lower() for n in _env("CALENDAR_OWNER_NAMES", "Paul,Jan Paul").split(",") if n.strip()]


def _f(ev: Any, name: str) -> str:
    return str(ev.get(name, "") if isinstance(ev, dict) else getattr(ev, name, "") or "")


def involves_owner(ev: Any) -> bool:
    text = (_f(ev, "title") + " " + _f(ev, "notes")).lower()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text) for n in owner_names())


def _all_day(ev: Any) -> bool:
    return _f(ev, "start").endswith("T00:00:00") and _f(ev, "end").endswith(("T23:59:00", "T00:00:00"))


def clash_matters(a: Any, b: Any) -> bool:
    ca, cb = _f(a, "category") or "ich", _f(b, "category") or "ich"
    if "tour" in (ca, cb) and ca != cb:
        return involves_owner(a if ca == "tour" else b)
    return True


def find_conflicts(events: list[Any]) -> list[tuple[Any, Any]]:
    """Alle Paare, die sich zeitlich überschneiden UND den Nutzer wirklich betreffen (früherer Termin zuerst)."""
    timed = sorted((e for e in events if "T" in _f(e, "start") and _f(e, "end") and not _all_day(e)),
                   key=lambda e: _f(e, "start"))
    out = []
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if _f(b, "start") >= _f(a, "end"):
                break
            if clash_matters(a, b):
                out.append((a, b))
    return out


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


class N8nCalendarBridge:
    """Google Kalender über die n8n-Brücke: lesen immer, anlegen/ändern/löschen, wenn CALENDAR_BRIDGE_WRITE_URL gesetzt ist.

    Die Google-Zugangsdaten bleiben in n8n; der Server ruft nur einen geschützten Webhook auf. Was der Webhook
    liefert, wird wie jeder andere Termin behandelt — damit sehen Kalender-Seite, Kollisionscheck und Jarvis
    dieselben Termine. Ohne CALENDAR_BRIDGE_WRITE_URL bleibt es beim Lesen, wie bisher.
    """

    def __init__(self) -> None:
        self.urls = [u.strip() for u in _env("CALENDAR_BRIDGE_READ_URLS").split(",") if u.strip()]
        self.write_url = _env("CALENDAR_BRIDGE_WRITE_URL")
        self.token = _env("CALENDAR_BRIDGE_TOKEN")
        self.header = _env("CALENDAR_BRIDGE_HEADER", "X-Jarvis-Bridge")
        self._transport = None          # nur für Tests: ein Ersatz für das Netz

    def configured(self) -> bool:
        return bool(self.urls and self.token)

    def can_write(self) -> bool:
        return bool(self.write_url and self.token)

    @staticmethod
    def _aware(value: str) -> str:
        """Lokale Zeit ohne Offset (so speichert Jarvis Termine) → ISO mit Offset, wie Google sie braucht."""
        return datetime.fromisoformat(value).astimezone().isoformat(timespec="seconds")

    def _to_event(self, e: dict) -> "Event":
        return Event(uid=str(e.get("id", "")), title=str(e.get("summary") or "(ohne Titel)"),
                     start=self._local(e.get("start")), end=self._local(e.get("end"), True),
                     location=str(e.get("location") or ""), notes=str(e.get("description") or ""),
                     backend="n8n", remote_id=str(e.get("id", "")))

    async def _call(self, payload: dict) -> dict:
        """Ein Schreibauftrag an die Brücke. Jeder Fehler kommt als CalendarError mit Klartext an, nie still."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=45, transport=self._transport) as c:
                r = await c.post(self.write_url, headers={self.header: self.token}, json=payload)
        except httpx.HTTPError as e:
            raise CalendarError(f"Die Kalender-Brücke ist nicht erreichbar ({e.__class__.__name__}).") from e
        if r.status_code in (401, 403):
            raise CalendarError("Die Kalender-Brücke lehnt den Schlüssel ab (CALENDAR_BRIDGE_TOKEN prüfen).")
        try:
            data = r.json()
        except ValueError:
            raise CalendarError(f"Die Kalender-Brücke antwortete unlesbar (HTTP {r.status_code}).") from None
        if r.status_code >= 400 or data.get("ok") is not True:
            raise CalendarError("Google Kalender: " + str(data.get("error") or f"HTTP {r.status_code}"))
        return data

    async def create(self, event: "Event") -> "Event":
        data = await self._call({"action": "create", "requestId": event.uid, "summary": event.title,
                                 "start": self._aware(event.start), "end": self._aware(event.end),
                                 "location": event.location, "description": event.notes})
        return self._to_event(data["event"])

    async def update(self, remote_id: str, *, title=None, location=None, notes=None, start=None, end=None) -> "Event":
        """Nur die Felder senden, die sich ändern — alles andere bleibt in Google, wie es ist."""
        payload: dict = {"action": "update", "eventId": remote_id}
        if title is not None:
            payload["summary"] = title
        if location is not None:
            payload["location"] = location
        if notes is not None:
            payload["description"] = notes
        if start and end:
            payload["start"], payload["end"] = self._aware(start), self._aware(end)
        return self._to_event((await self._call(payload))["event"])

    async def delete(self, remote_id: str) -> None:
        await self._call({"action": "delete", "eventId": remote_id})

    @staticmethod
    def _local(value: Any, end_of_day: bool = False) -> str:
        """Google liefert {dateTime,...} oder {date}. Ergebnis: lokale ISO-Zeit ohne Offset wie im Rest des Systems."""
        if isinstance(value, dict):
            value = value.get("dateTime") or value.get("date") or ""
        value = str(value or "")
        if len(value) == 10:                                   # ganztägig
            return value + ("T23:59:00" if end_of_day else "T00:00:00")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            return value

    async def list(self, days: int = 7, query: str = "", offset: int = 0) -> list["Event"]:
        if not self.configured():
            return []
        import httpx
        start = (datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
                 + timedelta(days=max(-62, offset)))
        end = start + timedelta(days=max(1, min(days, 31)))
        events: list[Event] = []
        async with httpx.AsyncClient(timeout=35, transport=self._transport) as c:
            for url in self.urls:
                r = await c.post(url, headers={self.header: self.token},
                                 json={"timeMin": start.isoformat(), "timeMax": end.isoformat()})
                if r.status_code in (401, 403):
                    raise CalendarError("Die Kalender-Brücke lehnt den Schlüssel ab (CALENDAR_BRIDGE_TOKEN prüfen).")
                r.raise_for_status()
                data = r.json()
                if data.get("ok") is not True or not isinstance(data.get("events"), list):
                    raise CalendarError("Die Kalender-Brücke lieferte keine gültige Antwort.")
                for e in data["events"]:
                    if str(e.get("status", "")).lower() == "cancelled":
                        continue
                    ev = self._to_event(e)
                    if not query or query.lower() in (ev.title + " " + ev.location).lower():
                        events.append(ev)
        events.sort(key=lambda x: x.start)
        return events


class CalendarService:
    def __init__(self, store_file: Path | None = None):
        self.google = GoogleCalendarRest() if CORE_AVAILABLE else None
        self.bridge = N8nCalendarBridge()
        self.classifier = None          # async (title, when, notes, location) -> {category, location, team, vehicle}
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
        return ("google" if (self.google and self.google.configured())
                else "n8n" if self.bridge.configured() else "local")

    async def health(self) -> dict:
        if not CORE_AVAILABLE:
            return {"status": "offline", "detail": self.unavailable_reason()}
        if self.google and self.google.configured():
            return await self.google.health()
        if self.bridge.configured():
            try:
                found = await self.bridge.list(days=1)
                return {"status": "healthy", "detail": f"Google Kalender über die n8n-Brücke (nur lesen), "
                                                        f"heute {len(found)} Termine"}
            except Exception as e:  # noqa: BLE001
                return {"status": "degraded", "detail": f"n8n-Kalenderbrücke: {e}"[:200]}
        count = len(self.local._load()) if self.local else 0
        return {"status": "healthy",
                "detail": f"local calendar store ({count} appointments); connect Google to share them"}

    # -- operations --
    async def list(self, days: int = 7, query: str = "") -> tuple[list[dict], str]:
        self._require()
        if self.google and self.google.configured():
            events = await self.google.list(days=days, query=query)
            return [asdict(e) for e in events], "google"
        events = self.local.list(days=days, query=query)   # enthält die übernommenen Google-Termine
        return [asdict(e) for e in events], ("n8n" if self.bridge.configured() else "local")

    async def create(self, *, title: str, when: str, at: str = "", duration: str | int = 60,
                     location: str = "", notes: str = "", category: str = "") -> tuple[dict, str, str]:
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
        seen = ""
        if not category:                      # „Was, wie, wo“ selbst erkennen
            found: dict = {}
            if self.classifier:
                try:
                    found = await asyncio.wait_for(self.classifier(title, f"{when} {at}".strip(), notes, location), 20)
                except Exception:  # noqa: BLE001
                    found = {}
            from .classify import rule_category
            category = found.get("category") or rule_category(title, notes)
            bits = [{"tour": "Tour", "ich": "mein Termin", "privat": "privat"}.get(category, category)]
            if not location and found.get("location"):
                location = found["location"]; bits.append("Ort " + location)
            extra = []
            if found.get("team"):
                extra.append("Team: " + ", ".join(found["team"])); bits.append("Team " + ", ".join(found["team"]))
            if found.get("vehicle"):
                extra.append("Fahrzeug: " + found["vehicle"]); bits.append(found["vehicle"])
            if extra and "Team:" not in notes and "Fahrzeug:" not in notes:
                notes = (notes + "\n" if notes else "") + " · ".join(extra) + " (von Jarvis erkannt)"
            seen = " Erkannt: " + ", ".join(bits) + "."
        event = build_event(title, when_date, when_time, minutes, location=location, notes=notes,
                            category=category)
        if self._write_google():
            created = await self.google.create(event)
            return asdict(created), "google", ""
        if self._write_bridge():
            # Zuerst Google. Nur was dort wirklich angekommen ist, steht danach auch hier — nie umgekehrt.
            created = await self.bridge.create(event)
            m = self._mirror(created, event.category)
            return asdict(m), "n8n", "Im Google Kalender eingetragen." + seen + self._clash_note(m)
        created = self.local.create(event)
        return (asdict(created), "local",
                "Im lokalen Kalender auf dem Server gespeichert." + seen + self._clash_note(created))

    async def find(self, query: str) -> tuple[list[dict], str]:
        self._require()
        if self.google and self.google.configured():
            events = await self.google.list(days=365, query=query)
            return [asdict(e) for e in events], "google"
        return [asdict(e) for e in self.local.find(query)], ("n8n" if self.bridge.configured() else "local")

    async def range(self, start: str, end: str, query: str = "") -> tuple[list[dict], str]:
        """Alle Termine, die den Zeitraum berühren — für die Monats-, Wochen- und Tagesansicht (auch rückwirkend)."""
        self._require()
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        out = [e for e in self.local._load() if e.start_dt() < b and e.end_dt() >= a
               and (not query or query.lower() in (e.title + " " + e.location).lower())]
        out.sort(key=lambda e: e.start)
        return [asdict(e) for e in out], ("n8n" if self.bridge.configured() else "local")

    def update(self, uid: str, *, title=None, when=None, at=None, duration=None, location=None, notes=None,
               category=None) -> dict:
        """Eigenen Termin ändern. Google-Termine sind nur lesbar."""
        self._require()
        events = self.local._load()
        ev = next((e for e in events if e.uid == uid), None)
        if not ev:
            raise CalendarError("Diesen Termin gibt es nicht (mehr).")
        if ev.backend == "google":
            raise CalendarError("Das ist ein Google-Termin. Google-Termine kann Jarvis nur lesen — bitte direkt in Google ändern.")
        start, length = ev.start_dt(), ev.end_dt() - ev.start_dt()
        if when or at:
            d = parse_date(when) if when else start.date()
            t = parse_time(at) if at else start.time()
            start = datetime.combine(d, t)
        if duration is not None:
            length = timedelta(minutes=parse_duration(duration))
        ev.start = start.isoformat(timespec="seconds")
        ev.end = (start + length).isoformat(timespec="seconds")
        if title is not None and title.strip():
            ev.title = title.strip()
        if location is not None:
            ev.location = location.strip()
        if notes is not None:
            ev.notes = notes.strip()
        if category is not None:
            ev.category = category.strip().lower()
        self.local._save(events)
        try:
            if ev.file:
                Path(ev.file).write_text(to_ics([ev]), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return asdict(ev)

    def delete_uid(self, uid: str) -> dict:
        self._require()
        ev = next((e for e in self.local._load() if e.uid == uid), None)
        if not ev:
            raise CalendarError("Diesen Termin gibt es nicht (mehr).")
        if ev.backend == "google":
            raise CalendarError("Das ist ein Google-Termin. Google-Termine kann Jarvis nur lesen — bitte direkt in Google löschen.")
        self.local.delete(ev)
        return asdict(ev)

    def _write_google(self) -> bool:
        """Schreiben nach Google nur, wenn es ausdrücklich so eingestellt ist (Standard: lokal)."""
        return _env("CALENDAR_WRITE_BACKEND", "local").lower() == "google" and bool(self.google and self.google.configured())

    def _write_bridge(self) -> bool:
        """Schreiben nach Google über die n8n-Brücke: CALENDAR_WRITE_BACKEND=n8n und eine Schreib-URL."""
        return _env("CALENDAR_WRITE_BACKEND", "local").lower() in ("n8n", "bridge") and self.bridge.can_write()

    def _mirror(self, ev: "Event", category: str = "") -> "Event":
        """Einen Google-Termin sofort im lokalen Spiegel ablegen oder ersetzen — die Seite zeigt ihn ohne Warten
        auf den nächsten Abgleich. Der Abgleich bleibt die Wahrheit und korrigiert alles, was hier abweicht."""
        m = Event(uid="g-" + (ev.remote_id or ev.uid), title=ev.title, start=ev.start, end=ev.end,
                  location=ev.location, notes=ev.notes, backend="google", remote_id=ev.remote_id,
                  category=category or ev.category)
        events = [e for e in self.local._load() if e.uid != m.uid]
        events.append(m)
        events.sort(key=lambda x: x.start)
        self.local._save(events)
        return m

    def _clash_note(self, ev: "Event") -> str:
        """Ein Satz, wenn der neue Termin mit einem anderen kollidiert, der den Nutzer betrifft. Sonst nichts."""
        try:
            others = [e for e in self.local._load() if e.uid != ev.uid]
            hits = [b if a.uid == ev.uid else a for a, b in find_conflicts([*others, ev]) if ev.uid in (a.uid, b.uid)]
        except Exception:  # noqa: BLE001
            return ""
        if not hits:
            return ""
        return (" ACHTUNG, Kollision: überschneidet sich mit "
                + "; ".join(f"„{h.title}“ ({h.start[11:16]}–{h.end[11:16]} Uhr)" for h in hits[:3]) + ".")

    def _unmirror(self, uid: str) -> None:
        self.local._save([e for e in self.local._load() if e.uid != uid])

    def _bridge_event(self, ev: "Event") -> bool:
        """Ein Termin, den die Brücke ändern kann: aus Google übernommen, mit Google-Kennung, Schreiben eingeschaltet."""
        return ev.backend == "google" and bool(ev.remote_id) and self._write_bridge()

    async def update_any(self, uid: str, *, title=None, when=None, at=None, duration=None, location=None,
                         notes=None, category=None) -> dict:
        """Termin ändern — Google-Termine in Google, eigene lokale Termine lokal."""
        self._require()
        ev = next((e for e in self.local._load() if e.uid == uid), None)
        if ev is None or not self._bridge_event(ev):
            return self.update(uid, title=title, when=when, at=at, duration=duration, location=location,
                               notes=notes, category=category)
        start, length = ev.start_dt(), ev.end_dt() - ev.start_dt()
        moved = bool(when or at or duration is not None)
        if when or at:
            d = parse_date(when) if when else start.date()
            t = parse_time(at) if at else start.time()
            start = datetime.combine(d, t)
        if duration is not None:
            length = timedelta(minutes=parse_duration(duration))
        fields: dict = {}
        if title is not None and title.strip():
            fields["title"] = title.strip()
        if location is not None:
            fields["location"] = location.strip()
        if notes is not None:
            fields["notes"] = notes.strip()
        if moved:
            fields["start"] = start.isoformat(timespec="seconds")
            fields["end"] = (start + length).isoformat(timespec="seconds")
        keep = ev.category if category is None else category.strip().lower()
        if not fields:                        # nur die Farbe/Kategorie: das gibt es nur hier, nicht in Google
            ev.category = keep
            self._mirror(ev, keep)
            return asdict(ev)
        return asdict(self._mirror(await self.bridge.update(ev.remote_id, **fields), keep))

    async def delete_any(self, uid: str) -> dict:
        """Termin löschen — Google-Termine in Google, eigene lokale Termine lokal."""
        self._require()
        ev = next((e for e in self.local._load() if e.uid == uid), None)
        if ev is None or not self._bridge_event(ev):
            return self.delete_uid(uid)
        await self.bridge.delete(ev.remote_id)
        self._unmirror(ev.uid)
        return asdict(ev)

    async def _find_writable(self, query: str) -> tuple[list[dict], str]:
        """Treffer, die Jarvis ändern darf. Übernommene Google-Termine sind nur lesbar und werden klar abgelehnt."""
        if self._write_google():
            return await self.find(query)
        if self._write_bridge():
            return [asdict(e) for e in self.local.find(query)], "n8n"
        found = [asdict(e) for e in self.local.find(query)]
        own = [e for e in found if e.get("backend") != "google"]
        if not own and found:
            raise CalendarError(f"“{query}” ist ein Google-Termin. Google-Termine kann Jarvis nur lesen, "
                                "nicht ändern oder löschen — bitte direkt in Google ändern.")
        return own, "local"

    async def sync_google(self, days: int = 93) -> dict:
        """Google-Termine (über die n8n-Brücke, nur lesen) in den lokalen Kalender übernehmen.

        Ein Spiegel: Was in Google verschwindet, verschwindet auch hier. Bricht die Abfrage irgendwo ab, wird
        NICHTS geändert — ein halber Abgleich würde Termine löschen, die es noch gibt.
        """
        if not self.bridge.configured():
            return {"synced": 0, "skipped": "Kalender-Brücke nicht eingerichtet"}
        fresh: dict[str, Event] = {}
        prev = {e.uid: e for e in self.local._load() if e.backend == "google"}
        budget = 25                                         # höchstens so viele neue Termine pro Abgleich einordnen
        for off in range(-31, days, 31):                    # ab einem Monat zurück: die Seite zeigt auch Vergangenes
            for e in await self.bridge.list(days=min(31, days - off), offset=off):
                uid = "g-" + (e.remote_id or e.uid)
                cat = prev[uid].category if uid in prev and prev[uid].category not in ("", "google") else ""
                if not cat:
                    from .classify import rule_category
                    cat = rule_category(e.title, "")
                    if self.classifier and budget > 0:
                        budget -= 1
                        try:
                            cat = (await asyncio.wait_for(self.classifier(e.title, e.start, "", e.location), 20)).get("category") or cat
                        except Exception:  # noqa: BLE001
                            pass
                fresh[uid] = Event(uid=uid, title=e.title, start=e.start, end=e.end, location=e.location,
                                   notes=e.notes or ("" if self._write_bridge() else "Aus dem Google Kalender übernommen (nur lesbar)."),
                                   backend="google",
                                   remote_id=e.remote_id, category=cat)
        own = [e for e in self.local._load() if e.backend != "google"]
        merged = sorted([*own, *fresh.values()], key=lambda x: x.start)
        self.local._save(merged)
        return {"synced": len(fresh), "own": len(own)}

    async def move(self, query: str, new_when: str, at: str = "") -> tuple[dict, str]:
        self._require()
        matches, backend = await self._find_writable(query)
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
        if backend == "n8n":
            ev = Event(**event)
            if self._bridge_event(ev):
                new_end = new_start + (ev.end_dt() - ev.start_dt())
                updated = await self.bridge.update(ev.remote_id, start=new_start.isoformat(timespec="seconds"),
                                                   end=new_end.isoformat(timespec="seconds"))
                return asdict(self._mirror(updated, ev.category)), backend
            return asdict(self.local.move(ev, new_start)), "local"
        return asdict(self.local.move(Event(**event), new_start)), backend

    async def cancel(self, query: str) -> tuple[dict, str]:
        self._require()
        matches, backend = await self._find_writable(query)
        event = self._one(matches, query)
        if backend == "google":
            await self.google.delete(Event(**event))
        elif backend == "n8n" and self._bridge_event(Event(**event)):
            await self.bridge.delete(event["remote_id"])
            self._unmirror(event["uid"])
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
