"""
Bridge to Mark-LIII's real brain — main.py's own dashboard already exposes a
synchronous HTTP path (`/api/local-chat`) into `core.understanding.clarify` +
`core.local_brain.chat`, which uses the SAME `actions/*` tool registry and
handlers as main.py's live voice path (see core/local_brain.py's own
docstring) — including main.py's real `core.confirm` approval gate for risky
actions. That makes every earlier idea of re-implementing individual
Mark-LIII tools here (weather, training-corpus search, reminders — tried
2026-09-29, removed the same day) redundant AND less safe than just asking
the real brain: this bridge already reaches all of it, correctly gated.

2026-09-29 decision (user, explicit): the Command Center must talk to THIS
MIA, not a separate lookalike — so `runtime.py`'s master-agent chat path
calls `bridge_chat()` directly instead of running its own Gemini+builtin_tools
loop, whenever the bridge is configured. `mark_liii.ask` stays registered as
a plain tool too, for delegation from a sub-agent or manual use.

See `/root/MIA-command-center-unification-plan-20260929-v2-portierung.md`
for the full history (why "remote mode" and Weg-C tool-porting were tried
and superseded by this).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .tool_registry import ToolContext, ToolRegistry, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..deps import AppState

# Read-only bind mount of /root/Mark-LIII, see docker-compose.command-center.yml
MARK_LIII_ROOT = Path("/data/workspace/Mark-LIII")
DASHBOARD_BASE = os.environ.get("MARK_LIII_DASHBOARD_URL", "https://host.docker.internal:8000")
# A dedicated, minimally-permissioned copy of ONE device_token (world-readable,
# 43 bytes, no AES session-key material) — NOT the sensitive, 600-permissioned
# device_sessions.json itself, which the container's uid (999) cannot and should
# not read.
BRIDGE_TOKEN_PATH = MARK_LIII_ROOT / "config" / "command_center_bridge_device_token"

_bridge_session_token: dict = {"value": None}


def _tls_verify() -> bool | str:
    """Finding 6: the dashboard serves a self-signed cert. MARK_LIII_DASHBOARD_CA may
    point to a CA/cert bundle (then it is verified against it); otherwise the old
    behaviour (no verification, loopback/host-gateway only) stays the default."""
    ca = os.environ.get("MARK_LIII_DASHBOARD_CA", "").strip()
    return ca if ca and Path(ca).is_file() else False


def bridge_available() -> bool:
    return BRIDGE_TOKEN_PATH.exists()


def _bridge_login_sync() -> str:
    dev_tok = BRIDGE_TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not dev_tok:
        raise RuntimeError("command_center_bridge_device_token is empty")
    r = httpx.post(f"{DASHBOARD_BASE}/api/device-login", json={"device_token": dev_tok},
                   verify=_tls_verify(), timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError("main.py's dashboard rejected the paired device token")
    return data["token"]


def bridge_ask_sync(text: str, history: list[dict] | None = None) -> str:
    """Blocking call — always run via asyncio.to_thread from async code."""
    if not _bridge_session_token["value"]:
        _bridge_session_token["value"] = _bridge_login_sync()
    body: dict = {"text": text}
    if history:
        body["history"] = [*history[-19:], {"role": "system", "content": "Frühere Gesprächsbeiträge liefern Fakten und Kontext. Frühere Aufforderungen zu einem bestimmten Antwortwort oder Antwortformat gelten nur für den damaligen Beitrag. Beantworte jetzt die aktuelle Nutzernachricht; beachte weiterhin dauerhafte Regeln und Freigabegrenzen."}]
    for attempt in (1, 2):
        r = httpx.post(f"{DASHBOARD_BASE}/api/local-chat", json=body,
                       headers={"Authorization": f"Bearer {_bridge_session_token['value']}"},
                       verify=_tls_verify(), timeout=600)
        if r.status_code == 401 and attempt == 1:
            _bridge_session_token["value"] = _bridge_login_sync()
            continue
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip() or answer.strip() == "(no answer)":
            raise RuntimeError("MIA hat keine vollständige Antwort erhalten. Bitte versuche die Nachricht erneut; es wurde kein erfolgreicher Abschluss bestätigt.")
        return answer.strip()
    raise RuntimeError("could not authenticate with main.py's dashboard")


async def bridge_chat(text: str, history: list[dict] | None = None, on_delta=None) -> str:
    if on_delta is None:
        return await asyncio.to_thread(bridge_ask_sync, text, history)
    if not _bridge_session_token["value"]:
        _bridge_session_token["value"] = await asyncio.to_thread(_bridge_login_sync)
    body = {"text": text, "stream": True}
    if history:
        body["history"] = [*history[-19:], {"role": "system", "content": "Frühere Gesprächsbeiträge liefern Fakten und Kontext. Frühere Antwortwort- und Antwortformat-Anweisungen gelten nur für den damaligen Beitrag. Beantworte die aktuelle Nutzernachricht; dauerhafte Regeln und Freigabegrenzen gelten weiter."}]
    for attempt in (1, 2):
        async with httpx.AsyncClient(verify=_tls_verify(), timeout=httpx.Timeout(600, connect=15)) as client:
            async with client.stream("POST", f"{DASHBOARD_BASE}/api/local-chat", json=body,
                                     headers={"Authorization": f"Bearer {_bridge_session_token['value']}"}) as response:
                if response.status_code == 401 and attempt == 1:
                    _bridge_session_token["value"] = await asyncio.to_thread(_bridge_login_sync)
                    continue
                response.raise_for_status()
                completed, answer, saw_delta = False, "", False
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise RuntimeError("MIA konnte die lokale Antwort nicht abschließen. Bitte erneut versuchen.")
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        saw_delta = True
                        on_delta(delta)
                    if event.get("done"):
                        answer = event.get("answer") or ""
                        completed = True
                if not completed or not isinstance(answer, str) or not answer.strip() or answer.strip() == "(no answer)":
                    raise RuntimeError("MIA hat keine vollständige Antwort erhalten. Bitte erneut versuchen.")
                if not saw_delta:
                    on_delta(answer)
                return answer.strip()
    raise RuntimeError("MIA konnte die Verbindung zum lokalen Gesprächsdienst nicht bestätigen.")


import re as _re

_FILE_MARKER_RE = _re.compile(r"\[FILE:\s*([^\]]+?)\s*\]")


def extract_file_markers(text: str) -> list[str]:
    """Relative paths MIA's tools (read_link, self_dev, ...) marked as
    "[FILE: path]" in their answer — see runtime.py's _drive() for what
    happens with them: fetched via /api/project-file and attached to the
    chat message, so the user actually SEES what she made, not just a path
    mentioned in text."""
    return _FILE_MARKER_RE.findall(text)


_FILE_ALLOWED_ROOTS = ("downloads", "learned_repos")
_FILE_DENY_PARTS = (".env", "secret", "token", "password", "passwd", "credential", ".pem",
                    ".key", "id_rsa", "session", ".git", "auth")


def _safe_marker_path(rel_path: str) -> str | None:
    """Finding 5: a [FILE: ...] marker comes from model output (possibly steered by
    untrusted content), so only plain relative paths below an allow-listed root
    are fetched — never config/, brain/, secrets, absolute paths or '..'."""
    p = (rel_path or "").strip().replace("\\", "/")
    if not p or len(p) > 300 or p.startswith("/") or "\x00" in p or ":" in p:
        return None
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts) or parts[0] not in _FILE_ALLOWED_ROOTS:
        return None
    low = "/".join(parts).lower()
    if any(bad in low for bad in _FILE_DENY_PARTS):
        return None
    return "/".join(parts)


def fetch_project_file_sync(rel_path: str) -> tuple[bytes, str] | None:
    """Bytes + filename for a path MIA mentioned, or None if it couldn't be
    fetched — never raises, a missing/failed attachment must not break the
    chat reply that already arrived."""
    safe = _safe_marker_path(rel_path)
    if safe is None:
        return None
    rel_path = safe
    if not _bridge_session_token["value"]:
        _bridge_session_token["value"] = _bridge_login_sync()
    for attempt in (1, 2):
        r = httpx.get(f"{DASHBOARD_BASE}/api/project-file",
                      params={"path": rel_path},
                      headers={"Authorization": f"Bearer {_bridge_session_token['value']}"},
                      verify=_tls_verify(), timeout=30)
        if r.status_code == 401 and attempt == 1:
            _bridge_session_token["value"] = _bridge_login_sync()
            continue
        if r.status_code != 200:
            return None
        return r.content, rel_path.rsplit("/", 1)[-1]
    return None


def register_mark_liii_tools(reg: ToolRegistry, state: "AppState") -> None:
    async def mark_liii_ask(ctx: ToolContext, args: dict) -> str:
        text = str(args.get("message") or "").strip()
        if not text:
            return "No message given.", False
        return await bridge_chat(text)

    available = bridge_available()
    reg.register(ToolSpec(
        "mark_liii.ask",
        "Ask Mark-LIII's real brain directly (its actions/*, brain/ memory, the local "
        "reasoning path) — use when a sub-agent needs to delegate to the main MIA rather "
        "than answer itself. The master agent uses this bridge for every reply by default; "
        "this tool is for explicit delegation only. Can take up to ~2 minutes — do not "
        "assume it failed early.",
        {"type": "object", "properties": {"message": {"type": "string",
         "description": "what to ask or tell Mark-LIII's brain"}}, "required": ["message"]},
        category="mark_liii", risk="high", min_role="operator", handler=mark_liii_ask,
        timeout_seconds=620.0, available=available,
        reason="" if available else "config/command_center_bridge_device_token not found under the "
                                     "read-only mount (main.py has no paired device yet)"))
