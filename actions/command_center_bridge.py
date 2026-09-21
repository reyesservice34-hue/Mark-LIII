"""actions/command_center_bridge.py — delegate to the Command Center.

Mark-LIII's Gemini Live session is real-time voice; the actual capability
(agents, tools, memory, integrations) lives in the separate Command Center
service (command_center/backend, running as its own Docker container). This
tool lets the voice session hand anything requiring real work over to it and
speak back the answer, instead of Mark-LIII needing its own copy of every
capability — one brain behind two different front doors.

Command Center's "voice" channel already returns short, spoken-friendly text
(no markdown, no code blocks — see orchestrator/runtime.py's VOICE_HINT), so
the response can be spoken essentially as-is.

Requires config/api_keys.json to hold:
  "command_center_url":   e.g. "http://localhost:8080"
  "command_center_token": a machine token created via POST /api/auth/tokens
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

# A voice exchange that runs this long has likely gone off the rails for a
# live conversation — better to say so and let the user ask again than to
# leave the session hanging.
_RUN_TIMEOUT = 75.0

# Kept across calls in this process so Command Center sees one continuous
# conversation rather than a new, context-free one on every question.
_conversation_id: str | None = None


def _config() -> tuple[str, str]:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    url   = data.get("command_center_url")
    token = data.get("command_center_token")
    if not url or not token:
        raise RuntimeError("not configured")
    return url.rstrip("/"), token


def _request(url: str, token: str, method: str = "GET", body: dict | None = None, timeout: float = 15.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "X-Jarvis-Token": token,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ensure_conversation(base_url: str, token: str) -> str:
    global _conversation_id
    if _conversation_id:
        return _conversation_id
    result = _request(f"{base_url}/api/chat/conversations", token, method="POST",
                       body={"title": "Live-Konsole (Mark-LIII)"})
    _conversation_id = result["conversation"]["id"]
    return _conversation_id


def _run_and_wait(base_url: str, token: str, conv_id: str, query: str) -> bool:
    """POST the message and consume its event stream until run.finished (or
    the timeout). Returns whether it actually finished."""
    url = f"{base_url}/api/chat/conversations/{conv_id}/messages"
    payload = json.dumps({"content": query, "channel": "voice", "stream": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "X-Jarvis-Token": token,
        "Accept": "text/event-stream",
    })
    deadline = time.monotonic() + _RUN_TIMEOUT
    event_name = None
    with urllib.request.urlopen(req, timeout=_RUN_TIMEOUT) as resp:
        for raw_line in resp:
            if time.monotonic() > deadline:
                return False
            line = raw_line.decode("utf-8", errors="ignore").rstrip("\n")
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:") and event_name == "run.finished":
                return True
    return False


def ask_command_center(parameters: dict) -> str:
    query = str(parameters.get("query", "")).strip()
    if not query:
        return "Kein Anliegen übergeben."

    try:
        base_url, token = _config()
    except Exception:
        return "Das Command Center ist nicht verbunden."

    try:
        conv_id = _ensure_conversation(base_url, token)
    except Exception as e:
        return f"Verbindung zum Command Center fehlgeschlagen: {e}"

    try:
        finished = _run_and_wait(base_url, token, conv_id, query)
    except Exception as e:
        return f"Das Command Center antwortet nicht: {e}"

    if not finished:
        return "Das dauert beim Command Center länger als üblich — ich sag Bescheid, sobald es fertig ist."

    try:
        conv = _request(f"{base_url}/api/chat/conversations/{conv_id}?limit=5", token)
        for msg in reversed(conv.get("messages", [])):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
    except Exception as e:
        return f"Antwort konnte nicht gelesen werden: {e}"
    return "Das Command Center hat keine Antwort geliefert."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "ask_command_center",
    "description": (
        "Delegates anything requiring real capability — files, code, calendar, tasks, "
        "research, integrations, or memory beyond a simple fact — to the full Command "
        "Center agent system and returns its answer to speak aloud. Use this instead of "
        "guessing, or instead of claiming you did something you have no tool for."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "The user's request, in their own words, in whatever language they used."
            }
        },
        "required": ["query"],
    },
    "handler": ask_command_center,
}
