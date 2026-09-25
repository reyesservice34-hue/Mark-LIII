"""
Client for the MIA-KNOWLEDGE-01 services (see knowledge_services/docker-compose.yml).

These are optional, self-hosted Flask services that back up long-term memory,
archive session transcripts, remember solved coding problems, and serve a
static knowledge base. They are reached over HTTP with a shared bearer token
(the server's MIA_SERVICE_TOKEN) and are OFF by default — nothing here is
called unless "knowledge_host" is set in config/api_keys.json.

Every function in this module is best-effort: on any failure (service not
configured, unreachable, wrong token, bad response) it logs a short warning
and returns an empty/False/None result. Nothing here ever raises, and nothing
here is allowed to block the assistant's main flow for more than a couple of
seconds — see _TIMEOUT.

Ports match knowledge_services/docker-compose.yml exactly:
    8001  memory service        8002  knowledge base
    8003  embedding service     8004  session archive
    8005  procedural brain
"""
import json
import sys
from pathlib import Path

import requests


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

_TIMEOUT = 5  # seconds — a hung remote service must never freeze the assistant

_PORTS = {
    "memory":            8001,
    "knowledge":         8002,
    "embedding":         8003,
    "session_archive":   8004,
    "procedural_brain":  8005,
}


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_host() -> str:
    """Base host (e.g. 'http://203.0.113.5' or 'https://mia.example.com'), no port, no trailing slash."""
    return (_load_config().get("knowledge_host") or "").rstrip("/")


def get_token() -> str:
    return (_load_config().get("knowledge_service_token") or "").strip()


def is_enabled() -> bool:
    return bool(get_host() and get_token())


def _url(service: str, path: str) -> str:
    return f"{get_host()}:{_PORTS[service]}{path}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}


def _request(method: str, service: str, path: str, **kwargs):
    """Returns the parsed JSON body on 2xx, or None on any failure (logged, never raised)."""
    if not is_enabled():
        return None
    try:
        resp = requests.request(
            method, _url(service, path),
            headers=_headers(), timeout=_TIMEOUT, **kwargs,
        )
        if not resp.ok:
            print(f"[Knowledge] {service}{path} -> HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        return resp.json() if resp.content else {}
    except requests.exceptions.RequestException as e:
        print(f"[Knowledge] {service}{path} unreachable: {e}")
        return None
    except Exception as e:
        print(f"[Knowledge] {service}{path} failed: {e}")
        return None


# ── Memory service (long-term memory backup) ─────────────────────────────────

def memory_upsert(item_id: str, text: str, actor: str = "", pinned: bool = False, created_at: str = "") -> bool:
    body = {"items": [{
        "id": item_id, "text": text, "actor": actor,
        "pinned": bool(pinned), "created_at": created_at,
    }]}
    return _request("POST", "memory", "/memory/bulk", json=body) is not None


def memory_delete(ids: list[str], purge: bool = False) -> bool:
    if not ids:
        return True
    body = {"ids": ids, "purge": purge}
    return _request("POST", "memory", "/memory/delete", json=body) is not None


# ── Procedural brain (remembered coding fixes) ───────────────────────────────

def procedure_find(query: str) -> list[dict]:
    data = _request("GET", "procedural_brain", "/procedures", params={"q": query})
    return (data or {}).get("procedures", [])


def procedure_add(problem: str, solution: str) -> bool:
    if not problem or not solution:
        return False
    body = {"problem": problem[:4000], "solution": solution[:20000]}
    return _request("POST", "procedural_brain", "/procedures", json=body) is not None


# ── Session archive (full conversation transcripts) ──────────────────────────

def archive_session(session_id: str, title: str, actor: str, messages: list[dict]) -> bool:
    if not session_id or not messages:
        return False
    body = {"title": title, "actor": actor, "messages": messages}
    return _request("PUT", "session_archive", f"/sessions/{session_id}", json=body) is not None


# ── Knowledge base (static reference documents) ──────────────────────────────

def knowledge_list() -> list[str]:
    data = _request("GET", "knowledge", "/knowledge")
    return (data or {}).get("documents", [])


def knowledge_get(name: str) -> dict | None:
    return _request("GET", "knowledge", f"/knowledge/{name}")
