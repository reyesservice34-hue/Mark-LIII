"""
Google Calendar backend for plugins/calendar.py.

Underscore-prefixed: a helper, not a discoverable skill.

Everything Google needs is imported lazily inside the methods. The libraries are
already in requirements.txt (they were listed for the Gmail/Calendar plugins
before either existed), but an install that skipped them must still be able to
load the calendar plugin and use the local backend — an ImportError at module
level would take the whole skill down for someone who never asked for Google.

Credentials live in config/ under names the repository's .gitignore already
covers (``client_secret*.json``, ``token*.json``), so connecting an account
cannot leak it into a commit.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from plugins._calendar_core import CalendarError, Event

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR      = get_base_dir()
CONFIG_DIR    = BASE_DIR / "config"
DEFAULT_SECRET = CONFIG_DIR / "client_secret_google_calendar.json"
TOKEN_FILE     = CONFIG_DIR / "token_google_calendar.json"

SETUP_HINT = (
    "Google Calendar is not connected yet. Create an OAuth client (Desktop app) "
    "in the Google Cloud console with the Calendar API enabled, save the file as "
    f"{DEFAULT_SECRET.name} in the config folder, then press CONNECT in "
    "settings → plugin settings."
)


def _iso(dt: datetime) -> str:
    """RFC 3339 with the machine's own UTC offset. The offset is what makes a
    time unambiguous to a server in another timezone — the local backend can do
    without it, this one cannot."""
    return dt.astimezone().isoformat(timespec="seconds")


class GoogleCalendar:
    """Thin wrapper over the Calendar v3 API, shaped like LocalCalendar so
    plugins/calendar.py can hold one code path for both."""

    name = "google"

    def __init__(self, secret_file: Optional[Path] = None, calendar_id: str = "primary"):
        self.secret_file = Path(secret_file) if secret_file else DEFAULT_SECRET
        self.calendar_id = (calendar_id or "primary").strip() or "primary"

    # -- credentials --
    def available(self) -> bool:
        """True when a token exists AND the libraries are installed — i.e. when
        a call would actually have a chance of working."""
        if not TOKEN_FILE.exists():
            return False
        try:
            import google.oauth2.credentials  # noqa: F401
            import googleapiclient.discovery   # noqa: F401
        except Exception:
            return False
        return True

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise CalendarError(
                "The Google libraries are missing. Install them with: "
                "pip install google-api-python-client google-auth-oauthlib"
            )

        if not TOKEN_FILE.exists():
            raise CalendarError(SETUP_HINT)

        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            raise CalendarError(f"The stored Google token is unreadable ({e}). Press CONNECT again.")

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            except Exception as e:
                raise CalendarError(f"The Google login expired and could not be refreshed ({e}). "
                                    f"Press CONNECT again.")

        if not creds or not creds.valid:
            raise CalendarError(SETUP_HINT)
        return creds

    def _service(self):
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise CalendarError(
                "The Google libraries are missing. Install them with: "
                "pip install google-api-python-client google-auth-oauthlib"
            )
        return build("calendar", "v3", credentials=self._credentials(), cache_discovery=False)

    def connect(self) -> tuple[bool, str]:
        """Runs the browser OAuth flow once and stores the token. This is what
        the CONNECT button in plugin settings calls."""
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            return False, ("Missing library. Run: pip install google-api-python-client "
                           "google-auth-oauthlib")

        if not self.secret_file.exists():
            return False, f"No OAuth client file at {self.secret_file}"

        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(self.secret_file), SCOPES)
            creds = flow.run_local_server(port=0)
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            return False, f"Authorisation failed: {e}"

        try:
            summary = self._service().calendars().get(calendarId=self.calendar_id).execute()
            return True, f"Connected to '{summary.get('summary', self.calendar_id)}'."
        except Exception as e:
            return False, f"Authorised, but the calendar could not be read: {e}"

    # -- conversion --
    def _to_event(self, item: dict) -> Event:
        start_raw = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date")
        end_raw = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date")

        def _clean(value: str, fallback: datetime) -> datetime:
            if not value:
                return fallback
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return fallback
            # Drop the offset: everything above this layer speaks local wall time.
            return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed

        start = _clean(start_raw, datetime.now())
        end = _clean(end_raw, start + timedelta(hours=1))
        return Event(
            uid=item.get("iCalUID") or item.get("id", ""),
            title=item.get("summary", "(no title)"),
            start=start.isoformat(timespec="seconds"),
            end=end.isoformat(timespec="seconds"),
            location=item.get("location", "") or "",
            notes=item.get("description", "") or "",
            backend=self.name,
            remote_id=item.get("id", ""),
        )

    # -- operations --
    def create(self, event: Event) -> Event:
        body = {
            "summary": event.title,
            "start": {"dateTime": _iso(event.start_dt())},
            "end": {"dateTime": _iso(event.end_dt())},
        }
        if event.location:
            body["location"] = event.location
        if event.notes:
            body["description"] = event.notes
        try:
            created = self._service().events().insert(
                calendarId=self.calendar_id, body=body).execute()
        except CalendarError:
            raise
        except Exception as e:
            raise CalendarError(f"Google refused the appointment: {e}")
        event.backend = self.name
        event.remote_id = created.get("id", "")
        return event

    def list(self, days: int = 7, query: str = "") -> list[Event]:
        now = datetime.now()
        params = {
            "calendarId": self.calendar_id,
            "timeMin": _iso(now),
            "timeMax": _iso(now + timedelta(days=max(1, days))),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 50,
        }
        if query:
            params["q"] = query
        try:
            items = self._service().events().list(**params).execute().get("items", [])
        except CalendarError:
            raise
        except Exception as e:
            raise CalendarError(f"Google would not hand over the calendar: {e}")
        return [self._to_event(i) for i in items]

    def find(self, query: str) -> list[Event]:
        # 90 days is the window a spoken "cancel the Müller appointment" can
        # plausibly mean; beyond that the user would name a date.
        return self.list(days=90, query=query)

    def delete(self, event: Event) -> None:
        if not event.remote_id:
            raise CalendarError("That appointment has no Google id — I cannot delete it.")
        try:
            self._service().events().delete(
                calendarId=self.calendar_id, eventId=event.remote_id).execute()
        except CalendarError:
            raise
        except Exception as e:
            raise CalendarError(f"Google refused to delete it: {e}")

    def move(self, event: Event, new_start: datetime) -> Event:
        if not event.remote_id:
            raise CalendarError("That appointment has no Google id — I cannot move it.")
        duration = event.end_dt() - event.start_dt()
        body = {
            "start": {"dateTime": _iso(new_start)},
            "end": {"dateTime": _iso(new_start + duration)},
        }
        try:
            updated = self._service().events().patch(
                calendarId=self.calendar_id, eventId=event.remote_id, body=body).execute()
        except CalendarError:
            raise
        except Exception as e:
            raise CalendarError(f"Google refused to move it: {e}")
        return self._to_event(updated)
