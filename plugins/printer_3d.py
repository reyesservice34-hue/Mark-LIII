"""
printer_3d — the 3D printer, over OctoPrint's REST API.

OctoPrint runs on the Pi next to the printer, is free, and already fronts
Marlin, Klipper (via the OctoKlipper bridge) and everything else that takes
G-code. One API key from its settings page and this works; there is no cloud
account anywhere in the path.

**Cancelling is the only gated action.** Pausing is undone by resuming and
starting is undone by cancelling, but a cancel throws away hours of print time
and the filament that went with it — irreversible in every sense that matters
to whoever was waiting for the part. So it goes through core/confirm.py, where
the CONFIRM button is issued by the interface and cannot be filled in by the
model. Everything else runs immediately, because an assistant that asks before
reading a temperature is one nobody asks anything.
"""
from __future__ import annotations

import requests

from core import confirm as confirm_gate
from memory.config_manager import get_plugin_setting

NAMESPACE = "printer_3d"
TIMEOUT = 15
MAX_FILES = 12


class PrinterError(Exception):
    """Something the user needs to hear, phrased for speaking aloud."""


def _setting(key: str, default=""):
    value = get_plugin_setting(NAMESPACE, key, default)
    return default if value in (None, "") else value


def _base() -> str:
    url = str(_setting("url", "")).strip().rstrip("/")
    if not url:
        raise PrinterError("No OctoPrint address is configured. Put it in plugin settings, "
                           "for example http://octopi.local")
    return url if url.startswith(("http://", "https://")) else "http://" + url


def _key() -> str:
    key = str(_setting("api_key", "")).strip()
    if not key:
        raise PrinterError("No OctoPrint API key is configured. Generate one under "
                           "OctoPrint → Settings → API and paste it into plugin settings.")
    return key


def _call(method: str, path: str, payload: dict | None = None):
    url = f"{_base()}/api/{path.lstrip('/')}"
    try:
        resp = requests.request(method, url, headers={"X-Api-Key": _key()},
                                json=payload, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise PrinterError(f"OctoPrint is not answering at {_base()}. Is the printer's "
                           f"Pi switched on?")
    except requests.exceptions.Timeout:
        raise PrinterError("OctoPrint took too long to answer.")
    except Exception as e:
        raise PrinterError(f"Could not reach OctoPrint: {e}")

    if resp.status_code in (401, 403):
        raise PrinterError("OctoPrint rejected the API key. Generate a new one under "
                           "Settings → API.")
    if resp.status_code == 409:
        # OctoPrint's way of saying "the printer is not in a state for that".
        raise PrinterError("The printer is not in a state for that right now — it may be "
                           "offline, already printing, or not connected.")
    if resp.status_code >= 400:
        raise PrinterError(f"OctoPrint refused the request ({resp.status_code}).")
    try:
        return resp.json()
    except Exception:
        return {}


def _fmt_minutes(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if total <= 0:
        return "done"
    hours, minutes = divmod(total // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


# ── Actions ─────────────────────────────────────────────────────────────────

def _status(p: dict) -> str:
    printer = _call("GET", "printer")
    job = _call("GET", "job")

    state = ((printer.get("state") or {}).get("text")) or "unknown"
    temps = printer.get("temperature") or {}

    bits = [f"Printer: {state}"]
    for label, key in (("nozzle", "tool0"), ("bed", "bed")):
        entry = temps.get(key) or {}
        actual, target = entry.get("actual"), entry.get("target")
        if actual is not None:
            piece = f"{label} {actual:.0f}°"
            if target:
                piece += f" of {target:.0f}°"
            bits.append(piece)

    progress = job.get("progress") or {}
    name = ((job.get("job") or {}).get("file") or {}).get("name")
    completion = progress.get("completion")
    if name and completion is not None:
        bits.append(f"{name} at {completion:.0f}%, {_fmt_minutes(progress.get('printTimeLeft'))} left")
    elif name:
        bits.append(f"loaded: {name}")

    return " · ".join(bits)


def _files(p: dict) -> str:
    data = _call("GET", "files")
    names = []

    def walk(entries):
        for entry in entries or []:
            if entry.get("type") == "folder":
                walk(entry.get("children"))
            elif entry.get("name", "").lower().endswith((".gcode", ".gco", ".g")):
                names.append(entry["name"])

    walk(data.get("files"))
    if not names:
        return "There are no G-code files on the printer."
    shown = sorted(names)[:MAX_FILES]
    more = f"\n(and {len(names) - len(shown)} more)" if len(names) > len(shown) else ""
    return "On the printer:\n" + "\n".join(f"- {n}" for n in shown) + more


def _resolve_file(query: str) -> str:
    data = _call("GET", "files")
    found = []

    def walk(entries, prefix=""):
        for entry in entries or []:
            if entry.get("type") == "folder":
                walk(entry.get("children"), prefix + entry.get("name", "") + "/")
            elif entry.get("name", "").lower().endswith((".gcode", ".gco", ".g")):
                found.append((entry["name"], entry.get("path") or (prefix + entry["name"])))

    walk(data.get("files"))
    needle = query.lower().strip()
    hits = [f for f in found if needle in f[0].lower()]
    if not hits:
        raise PrinterError(f"No G-code file matching '{query}' is on the printer.")
    if len(hits) > 1:
        raise PrinterError(f"'{query}' matches {len(hits)} files: "
                           f"{', '.join(n for n, _ in hits[:5])}. Which one?")
    return hits[0][1]


def _print(p: dict) -> str:
    name = str(p.get("file", "") or p.get("name", "")).strip()
    if not name:
        return "Which file should I print?"
    path = _resolve_file(name)
    _call("POST", f"files/local/{path}", {"command": "select", "print": True})
    return f"Started printing {path}."


def _pause(p: dict) -> str:
    _call("POST", "job", {"command": "pause", "action": "pause"})
    return "Print paused."


def _resume(p: dict) -> str:
    _call("POST", "job", {"command": "pause", "action": "resume"})
    return "Print resumed."


def _cancel(p: dict) -> str:
    job = _call("GET", "job")
    name = ((job.get("job") or {}).get("file") or {}).get("name") or "the current print"
    progress = (job.get("progress") or {}).get("completion")
    detail = f"{name}"
    if progress is not None:
        detail += f"\n{progress:.0f}% done — this cannot be resumed."

    return confirm_gate.request(
        key="printer_cancel",
        title=f"Cancel the print: {name}",
        detail=detail,
        run=lambda: (_call("POST", "job", {"command": "cancel"}), f"{name} cancelled.")[1],
    )


_ACTIONS = {
    "status": _status, "state": _status, "": _status,
    "files": _files, "list": _files,
    "print": _print, "start": _print,
    "pause": _pause,
    "resume": _resume, "continue": _resume,
    "cancel": _cancel, "stop": _cancel, "abort": _cancel,
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "status")).strip().lower()
    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"I do not know the printer action '{action}'. I can report status, list "
                f"files, start, pause, resume or cancel a print.")
    try:
        result = handler(p)
    except PrinterError as e:
        result = str(e)
    except Exception as e:
        result = f"The printer failed: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result


def _test(values: dict) -> tuple[bool, str]:
    global get_plugin_setting
    original = get_plugin_setting
    get_plugin_setting = lambda ns, key, default=None: values.get(key, original(ns, key, default))
    try:
        version = _call("GET", "version")
        state = ((_call("GET", "printer").get("state") or {}).get("text")) or "unknown"
        return True, f"Connected to OctoPrint {version.get('server', '?')} — printer {state}."
    except PrinterError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Failed: {e}"
    finally:
        get_plugin_setting = original


PLUGIN_SETTINGS = {
    "namespace": NAMESPACE,
    "title": "🖨  3D PRINTER (OctoPrint)",
    "fields": [
        {"key": "url", "label": "OctoPrint address", "type": "text",
         "placeholder": "http://octopi.local"},
        {"key": "api_key", "label": "API key (OctoPrint → Settings → API)", "type": "password"},
    ],
    "action": {"label": "TEST CONNECTION", "run": _test},
}


PLUGIN = {
    "name": "printer_3d",
    "description": (
        "Reports and controls the 3D printer through OctoPrint: how far the print has got, "
        "nozzle and bed temperature, which files are on it, and starting, pausing, resuming "
        "or cancelling a print. Cancelling puts a confirmation on screen that the user must "
        "press, so never say a print was cancelled until the tool says it was."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status (default) | files | print | pause | resume | cancel"
            },
            "file": {
                "type": "STRING",
                "description": "Part of the G-code file name when starting a print"
            }
        },
        "required": []
    },
}
