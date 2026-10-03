"""
core/knowledge.py — lokale Wissenssuche ueber den Embedding-Index aus
build_knowledge_index.py (bge-m3 via Ollama, keine Cloud). Gleiches Muster
wie memory.search_memory: MIA ruft search_knowledge auf, statt zu raten.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
INDEX = BASE / "knowledge" / "index.json"
OLLAMA = "http://127.0.0.1:11434"

_cache: dict | None = None


def _load() -> dict | None:
    global _cache
    if _cache is None and INDEX.exists():
        _cache = json.loads(INDEX.read_text(encoding="utf-8"))
    return _cache


def _embed(text: str, model: str) -> list[float]:
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": model, "input": text, "keep_alive": "30m"}, timeout=60)
    r.raise_for_status()
    return r.json()["embeddings"][0]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def search_knowledge(query: str, limit: int = 4) -> str:
    # Use the same scoped, secret-filtered local source path as ordinary chat.
    from core.memory_sources import local_sources, wanted_scope
    from core.local_brain import _CONVERSATION_SCOPE
    scope = wanted_scope(query)
    if scope == "general": scope = _CONVERSATION_SCOPE.get()
    return local_sources(query, scope) or "Keine passende freigegebene Wissensquelle im gewählten Bereich gefunden."


def stats() -> str:
    idx = _load()
    if not idx:
        return "kein Index"
    files = sorted({e["file"] for e in idx["entries"]})
    return f"{len(files)} Dateien, {len(idx['entries'])} Chunks, Modell {idx['model']}"
