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
    8005  procedural brain      6333  vector DB (Qdrant, own auth — see below)

Cost note: embeddings run on a small local model (all-MiniLM-L6-v2, CPU-only,
no external API) and Qdrant is self-hosted — the associative-memory path below
adds zero API cost. It only ever touches your own server.
"""
import json
import sys
import uuid
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
    "vector_db":         6333,
}

_MEMORY_COLLECTION = "mia_memory"


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


def get_vector_api_key() -> str:
    """Qdrant's own key (its docker-compose QDRANT_API_KEY), separate from the
    Flask services' bearer token above — Qdrant is a different piece of software
    with its own auth scheme."""
    return (_load_config().get("qdrant_api_key") or "").strip()


def is_enabled() -> bool:
    return bool(get_host() and get_token())


def is_semantic_enabled() -> bool:
    """Associative memory needs the host plus the embedding service and Qdrant —
    both included in the same docker-compose stack, so this is just is_enabled()
    today, kept separate in case that ever changes."""
    return is_enabled()


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


# ── Embedding service (text -> vector, local model, no API cost) ─────────────

def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if not texts:
        return None
    data = _request("POST", "embedding", "/embed", json={"texts": texts})
    if not data:
        return None
    vectors = data.get("embeddings")
    return vectors if isinstance(vectors, list) else None


# ── Vector DB (Qdrant — associative memory) ───────────────────────────────────
# Qdrant is not one of the bearer-token Flask services above: it's a separate
# piece of software with its own auth header ("api-key") and its own REST API
# shape, so it gets its own small request helper instead of reusing _request().

def _qdrant_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    key = get_vector_api_key()
    if key:
        headers["api-key"] = key
    return headers


def _qdrant_request(method: str, path: str, **kwargs):
    if not is_semantic_enabled():
        return None
    try:
        resp = requests.request(
            method, _url("vector_db", path),
            headers=_qdrant_headers(), timeout=_TIMEOUT, **kwargs,
        )
        if not resp.ok:
            print(f"[Vector] {path} -> HTTP {resp.status_code}: {resp.text[:150]}")
            return None
        return resp.json() if resp.content else {}
    except requests.exceptions.RequestException as e:
        print(f"[Vector] {path} unreachable: {e}")
        return None
    except Exception as e:
        print(f"[Vector] {path} failed: {e}")
        return None


def _ensure_collection(name: str, vector_size: int) -> bool:
    existing = _qdrant_request("GET", f"/collections/{name}")
    if existing is not None:
        return True
    return _qdrant_request(
        "PUT", f"/collections/{name}",
        json={"vectors": {"size": vector_size, "distance": "Cosine"}},
    ) is not None


def _point_id(item_id: str) -> str:
    """Qdrant point IDs must be an integer or a UUID — derive a stable UUID from
    our own string IDs so upserting the same fact twice overwrites it."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, item_id))


def semantic_memory_upsert(item_id: str, text: str, payload: dict | None = None) -> bool:
    """Embeds `text` (free, local model) and stores it in Qdrant under a point ID
    derived from item_id, so re-saving the same fact overwrites its vector."""
    vectors = embed_texts([text])
    if not vectors:
        return False
    vec = vectors[0]
    if not _ensure_collection(_MEMORY_COLLECTION, len(vec)):
        return False
    point = {
        "id": _point_id(item_id),
        "vector": vec,
        "payload": {**(payload or {}), "source_id": item_id, "text": text},
    }
    return _qdrant_request(
        "PUT", f"/collections/{_MEMORY_COLLECTION}/points",
        json={"points": [point]},
    ) is not None


def semantic_memory_delete(item_id: str) -> bool:
    return _qdrant_request(
        "POST", f"/collections/{_MEMORY_COLLECTION}/points/delete",
        json={"points": [_point_id(item_id)]},
    ) is not None


def semantic_memory_search(query: str, limit: int = 5) -> list[dict]:
    """Finds facts related to `query` by meaning, not just shared words — the
    fallback for when the instant local keyword search in memory_manager.py
    finds nothing. Returns [] if disabled, unreachable, or empty."""
    vectors = embed_texts([query])
    if not vectors:
        return []
    data = _qdrant_request(
        "POST", f"/collections/{_MEMORY_COLLECTION}/points/search",
        json={"vector": vectors[0], "limit": limit, "with_payload": True},
    )
    if not data:
        return []
    return [
        {"score": r.get("score"), **(r.get("payload") or {})}
        for r in data.get("result", [])
    ]
