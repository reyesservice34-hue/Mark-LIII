"""
control_plane — Mark-LIII as a CLIENT of the server-side JARVIS control plane.

The desktop app used to answer with its own local session and its own persona.
That is precisely why WhatsApp and Desktop disagreed — two identities, two brains.
This routes desktop commands to the one control plane, so both faces share a
single identity, memory, queue, calendar and voice. The desktop stops being a
second brain and becomes a client that submits a command, waits for the server
to do the work, and reads back exactly what the server says.

Contract (given, not invented):
  POST /v1/commands  {actor, command, conversation_id?}   header X-Jarvis-Token
    -> 202 {job_id, conversation_id, status, risk, approval_required, approval_code?}
  GET  /v1/commands/{job_id}                               header X-Jarvis-Token
    -> {status, routed_to, result, error, finished_at}
  POST /v1/memory ; GET /v1/memory/search

Secrets never live in git. The token is read from the JARVIS_GATEWAY_TOKEN
environment variable first, then from config/api_keys.json (which is gitignored)
— never hardcoded, never committed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_BASE_URL = "https://jarvis-reyes.de/jarvis-api"
DEFAULT_ACTOR    = "mark-liii-windows"    # stable and EXACT: the server's ownership and
                                          # calendar authorisation check for this actor by name
DEFAULT_TIMEOUT  = 30
POLL_INTERVAL    = 1.5
MAX_WAIT         = 180                     # a multi-step order can take a while; do not give up early

# Status vocabulary is normalised because the server may phrase it more than one
# way. Only these end the wait; anything else means "still working, keep polling".
_TERMINAL_OK   = {"completed", "done", "succeeded", "success"}
_TERMINAL_FAIL = {"failed", "error", "cancelled", "canceled", "rejected"}
_AWAITING      = {"awaiting_approval", "needs_approval", "approval_required", "pending_approval"}


class ControlPlaneError(Exception):
    """Base for every control-plane failure — phrased for speaking aloud."""


class ControlPlaneOffline(ControlPlaneError):
    """The control plane could not be reached at all (network / DNS / timeout)."""


class ControlPlaneAuthError(ControlPlaneError):
    """The gateway token was missing or rejected (401 / 403)."""


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class CommandResult:
    status: str
    job_id: str = ""
    conversation_id: str = ""
    result: str = ""
    error: str = ""
    routed_to: str = ""
    risk: str = ""
    approval_required: bool = False
    approval_code: str = ""
    finished_at: str = ""
    raw: dict = field(default_factory=dict)

    def _s(self) -> str:
        return (self.status or "").strip().lower()

    def is_terminal(self) -> bool:
        return self._s() in _TERMINAL_OK or self._s() in _TERMINAL_FAIL or self.awaiting_approval()

    def ok(self) -> bool:
        return self._s() in _TERMINAL_OK

    def failed(self) -> bool:
        return self._s() in _TERMINAL_FAIL

    def awaiting_approval(self) -> bool:
        return self._s() in _AWAITING or bool(self.approval_required)


def _result_from(payload: dict, *, fallback_job: str = "", fallback_conv: str = "") -> CommandResult:
    payload = payload if isinstance(payload, dict) else {}
    return CommandResult(
        status=str(payload.get("status", "") or ""),
        job_id=str(payload.get("job_id", fallback_job) or fallback_job),
        conversation_id=str(payload.get("conversation_id", fallback_conv) or fallback_conv),
        result=str(payload.get("result", "") or ""),
        error=str(payload.get("error", "") or ""),
        routed_to=str(payload.get("routed_to", "") or ""),
        risk=str(payload.get("risk", "") or ""),
        approval_required=bool(payload.get("approval_required", False)),
        approval_code=str(payload.get("approval_code", "") or ""),
        finished_at=str(payload.get("finished_at", "") or ""),
        raw=payload,
    )


class ControlPlane:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 actor: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT):
        cfg = _load_config()
        self.base_url = (base_url or cfg.get("jarvis_gateway_url") or DEFAULT_BASE_URL).rstrip("/")
        # env first (never touches disk), then the gitignored config file.
        self._token = (token
                       or os.environ.get("JARVIS_GATEWAY_TOKEN")
                       or cfg.get("jarvis_gateway_token") or "").strip()
        self.actor = (actor or cfg.get("jarvis_actor") or DEFAULT_ACTOR).strip() or DEFAULT_ACTOR
        self.timeout = timeout

    # -- is this even set up? --
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict:
        return {"X-Jarvis-Token": self._token, "Content-Type": "application/json"}

    def _request(self, method: str, path: str, payload: Optional[dict] = None):
        if not self._token:
            raise ControlPlaneAuthError("no gateway token configured")
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.request(method, url, headers=self._headers(),
                                    json=payload, timeout=self.timeout)
        except requests.exceptions.ConnectionError:
            raise ControlPlaneOffline(f"cannot reach the control plane at {self.base_url}")
        except requests.exceptions.Timeout:
            raise ControlPlaneOffline("the control plane did not answer in time")
        except Exception as e:
            raise ControlPlaneError(f"request to the control plane failed: {e}")

        if resp.status_code in (401, 403):
            raise ControlPlaneAuthError("the control plane rejected the gateway token")
        if resp.status_code >= 400:
            raise ControlPlaneError(f"the control plane refused the request ({resp.status_code})")
        try:
            return resp.json()
        except Exception:
            return {}

    # -- one command in --
    def submit(self, command: str, conversation_id: Optional[str] = None) -> CommandResult:
        body = {"actor": self.actor, "command": command}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return _result_from(self._request("POST", "/v1/commands", body))

    def fetch(self, job_id: str, conversation_id: str = "") -> CommandResult:
        return _result_from(self._request("GET", f"/v1/commands/{job_id}"),
                            fallback_job=job_id, fallback_conv=conversation_id)

    def run(self, command: str, conversation_id: Optional[str] = None,
            on_status: Optional[Callable[[CommandResult], None]] = None,
            poll_interval: float = POLL_INTERVAL, max_wait: float = MAX_WAIT,
            _clock: Callable[[], float] = time.monotonic,
            _sleep: Callable[[float], None] = time.sleep) -> CommandResult:
        """Submit, then poll until the server reaches a terminal state. A
        multi-step order is ONE job here — we never stop after the first part;
        the server owns the orchestration and we wait for it to finish all of it."""
        res = self.submit(command, conversation_id)
        if on_status:
            on_status(res)
        if res.is_terminal():
            return res

        job_id = res.job_id
        conv = res.conversation_id
        if not job_id:
            # No job to poll and not terminal — the server gave us nothing usable.
            raise ControlPlaneError("the control plane returned no job id to follow")

        deadline = _clock() + max_wait
        while _clock() < deadline:
            _sleep(poll_interval)
            res = self.fetch(job_id, conv)
            if not res.conversation_id:
                res.conversation_id = conv
            if on_status:
                on_status(res)
            if res.is_terminal():
                return res
        raise ControlPlaneOffline("the control plane is still working after the time limit "
                                  "(the job may still finish on the server)")

    # -- shared memory --
    def remember(self, text: str, conversation_id: Optional[str] = None) -> dict:
        body = {"actor": self.actor, "text": text}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return self._request("POST", "/v1/memory", body) or {}

    def search_memory(self, query: str) -> dict:
        from urllib.parse import quote
        return self._request("GET", f"/v1/memory/search?q={quote(query)}") or {}
