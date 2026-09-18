"""
desktop_runner — lets the server drive this PC, without opening a port on it.

The Command Center cannot dial into a machine behind a home router, so the
direction is reversed: this runner holds a long poll open against the server,
picks up one command at a time, runs it through the *existing* action registry
(`actions/open_app.py`, `computer_control`, `browser_control`,
`computer_settings`, `desktop_control`, …) and posts the result back.

Nothing new has to be taught to those actions. Whatever the desktop can already
do by voice, the server can now ask for by name — and the server side of that
request is approval-gated, so a remote command still needs the user's yes
unless they turned that off deliberately.

Run it standalone with `python desktop_agent.py`, or let main.py start it in a
background thread when `desktop_runner_enabled` is set in config/api_keys.json.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
STATE_PATH = BASE_DIR / "memory" / "desktop_device.json"

POLL_SECONDS = 25
BACKOFF_START = 3.0
BACKOFF_MAX = 60.0

# Actions that only make sense with a live UI in front of them, or that would
# hand a remote caller more than "drive my desktop" is meant to mean. They are
# hidden from the server unless the user opts them back in.
DEFAULT_BLOCKED = {"shutdown_jarvis", "agency_agent", "dev_agent"}


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class DesktopRunner:
    def __init__(self, *, base_url: str = "", token: str = "", name: str = "",
                 logger: Callable[[str], None] = print, registry=None,
                 blocked: Optional[set[str]] = None):
        cfg = _load_config()
        self.base_url = (base_url or os.environ.get("JARVIS_GATEWAY_URL")
                         or cfg.get("jarvis_gateway_url") or "").rstrip("/")
        self.token = (token or os.environ.get("JARVIS_GATEWAY_TOKEN")
                      or cfg.get("jarvis_gateway_token") or "").strip()
        self.name = (name or cfg.get("desktop_device_name") or socket.gethostname() or "desktop")[:80]
        self.blocked = set(blocked if blocked is not None else
                           (cfg.get("desktop_blocked_actions") or DEFAULT_BLOCKED))
        self._log = logger
        self._registry = registry
        self._plugin_registry = None
        self._device_id = self._load_device_id()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self.connected = False

    # ── configuration ────────────────────────────────────────────────────
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict:
        return {"X-Jarvis-Token": self.token, "Content-Type": "application/json"}

    def _load_device_id(self) -> str:
        try:
            return str(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("device_id", ""))
        except Exception:
            return ""

    def _save_device_id(self, device_id: str) -> None:
        self._device_id = device_id
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps({"device_id": device_id}, indent=2), encoding="utf-8")
        except Exception:
            pass    # remembering the id is a convenience; re-registering also works

    # ── what this PC exposes: its actions *and* its plugins ──────────────
    # Both matter. The things the user most wants reached from outside —
    # WhatsApp, the calendar, the mailbox — are plugins, not actions, so a
    # runner that only knew actions could never send a WhatsApp message even
    # though the working implementation sits right here.
    def _actions(self):
        if self._registry is None:
            sys.path.insert(0, str(BASE_DIR))
            from core.action_loader import discover_actions
            self._registry = discover_actions(
                actions_dir=BASE_DIR / "actions", reserved_names=set(),
                logger=lambda m: self._log(f"[DesktopRunner] {m}"))
        return self._registry

    def _plugins(self):
        if self._plugin_registry is None:
            sys.path.insert(0, str(BASE_DIR))
            try:
                from core.plugin_loader import discover_plugins
                self._plugin_registry = discover_plugins(
                    plugins_dir=BASE_DIR / "plugins",
                    core_tool_names=self._actions().names(),
                    logger=lambda m: self._log(f"[DesktopRunner] {m}"))
            except Exception as e:  # noqa: BLE001 — a broken plugin never costs us the actions
                self._log(f"[DesktopRunner] Plugins unavailable: {e}")
                self._plugin_registry = _NoPlugins()
        return self._plugin_registry

    def declarations(self) -> list[dict]:
        out, seen = [], set()
        for decl in [*self._actions().get_tool_declarations(),
                     *self._plugins().get_tool_declarations()]:
            name = decl["name"]
            if name in self.blocked or name in seen:
                continue
            seen.add(name)
            out.append({"name": name, "description": decl.get("description", "")[:400],
                        "parameters": decl.get("parameters", {})})
        return out

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> bool:
        """Start polling in a background thread. False when not configured."""
        if not self.configured():
            self._log("[DesktopRunner] No gateway URL/token configured — the server cannot drive this PC.")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True, name="desktop-runner")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"configured": self.configured(), "connected": self.connected, "device_id": self._device_id,
                "name": self.name, "server": self.base_url, "last_error": self.last_error,
                "actions": len(self.declarations()) if self.configured() else 0}

    # ── the loop ─────────────────────────────────────────────────────────
    def register(self) -> bool:
        try:
            r = requests.post(f"{self.base_url}/v1/desktop/register", headers=self._headers(), timeout=20,
                              json={"name": self.name, "platform": f"{platform.system()} {platform.release()}",
                                    "version": platform.python_version(), "device_id": self._device_id,
                                    "actions": self.declarations()})
        except requests.RequestException as e:
            self.last_error = f"cannot reach {self.base_url}: {e.__class__.__name__}"
            return False
        if r.status_code in (401, 403):
            # „abgelehnt" allein hilft niemandem weiter. Die drei Ursachen sehen
            # gleich aus und werden ganz unterschiedlich behoben, also stehen
            # sie hier — samt dem, was vom Token wirklich ankam.
            tok = self.token
            shape = (f"{len(tok)} Zeichen, beginnt mit {tok[:6]!r}" if tok else "leer")
            detail = ("401" if r.status_code == 401 else "403 (Token gültig, aber die Rolle reicht nicht)")
            self.last_error = f"der Server hat das Token abgelehnt ({detail})"
            self._log(f"[DesktopRunner] {self.last_error}")
            self._log(f"[DesktopRunner]   Server:  {self.base_url}")
            self._log(f"[DesktopRunner]   Token:   {shape}")
            if r.status_code == 403:
                self._log("[DesktopRunner]   Das Token braucht die Rolle 'operator', nicht 'viewer'.")
            else:
                self._log("[DesktopRunner]   Häufigste Ursachen: nur ein Teil des Tokens eingefügt,")
                self._log("[DesktopRunner]   die Token-ID statt des Geheimnisses, oder ein Token,")
                self._log("[DesktopRunner]   das inzwischen widerrufen wurde.")
                self._log("[DesktopRunner]   Neu einrichten:  python install_desktop.py")
            return False
        if r.status_code >= 400:
            self.last_error = f"registration refused (HTTP {r.status_code})"
            return False
        self._save_device_id(r.json().get("device_id", ""))
        self.last_error = ""
        self._log(f"[DesktopRunner] Registered '{self.name}' with {len(self.declarations())} actions "
                  f"at {self.base_url}")
        return True

    def poll_once(self) -> bool:
        """One long poll. True when the connection worked, regardless of work."""
        try:
            r = requests.get(f"{self.base_url}/v1/desktop/poll", headers=self._headers(),
                             params={"device_id": self._device_id, "wait": POLL_SECONDS},
                             timeout=POLL_SECONDS + 15)
        except requests.RequestException as e:
            self.last_error = f"poll failed: {e.__class__.__name__}"
            return False
        if r.status_code == 404:
            self._device_id = ""
            return self.register()
        if r.status_code in (401, 403):
            self.last_error = "the server rejected the gateway token"
            return False
        if r.status_code >= 400:
            self.last_error = f"poll refused (HTTP {r.status_code})"
            return False
        self.last_error = ""
        command = (r.json() or {}).get("command")
        if command:
            self.execute(command)
        return True

    def execute(self, command: dict) -> None:
        action = str(command.get("action", ""))
        params = command.get("params") or {}
        self._log(f"[DesktopRunner] {action}({json.dumps(params, ensure_ascii=False)[:120]})")
        ok, result = True, ""
        if action in self.blocked:
            ok, result = False, f"'{action}' is not available for remote control on this machine."
        else:
            try:
                if self._plugins().has(action):
                    result = self._plugins().run(action, params) or "Done."
                else:
                    result = self._actions().run(action, params, ctx={}) or "Done."
                lowered = result.lower()
                ok = not (lowered.startswith(("action '", "plugin '")) and "not available" in lowered) \
                    and not lowered.startswith((f"tool '{action}' failed", f"the '{action}' plugin")) \
                    and not lowered.startswith(f"plugin '{action}' failed")
            except Exception as e:  # noqa: BLE001 — the server must hear about it either way
                ok, result = False, f"{e.__class__.__name__}: {e}"
        try:
            requests.post(f"{self.base_url}/v1/desktop/result", headers=self._headers(), timeout=20,
                          json={"command_id": command.get("id"), "ok": ok,
                                "result": str(result)[:8000] if ok else "",
                                "error": "" if ok else str(result)[:2000]})
        except requests.RequestException as e:
            self._log(f"[DesktopRunner] Could not report the result: {e}")

    def run_forever(self) -> None:
        backoff = BACKOFF_START
        while not self._stop.is_set():
            if not self._device_id and not self.register():
                self.connected = False
                self._sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue
            if self.poll_once():
                self.connected = True
                backoff = BACKOFF_START
            else:
                self.connected = False
                self._log(f"[DesktopRunner] {self.last_error} — retrying in {int(backoff)}s")
                self._sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
        self.connected = False

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)


class _NoPlugins:
    """Stand-in when plugin discovery fails, so the actions still work."""

    @staticmethod
    def get_tool_declarations() -> list[dict]:
        return []

    @staticmethod
    def has(_name: str) -> bool:
        return False

    @staticmethod
    def run(name: str, _parameters: dict) -> str:
        return f"Plugin '{name}' is not available."


def start_if_configured(logger: Callable[[str], None] = print) -> DesktopRunner | None:
    """Used by main.py: start the runner when the config asks for it."""
    cfg = _load_config()
    if cfg.get("desktop_runner_enabled") is False:
        return None
    runner = DesktopRunner(logger=logger)
    if not runner.configured():
        return None
    return runner if runner.start() else None
