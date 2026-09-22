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
    idx = _load()
    if not idx:
        return "Wissensindex nicht vorhanden - build_knowledge_index.py wurde noch nicht ausgefuehrt."
    q = str(query or "").strip()
    if not q:
        return "Kein Suchbegriff angegeben."
    try:
        qv = _embed(q, idx["model"])
    except Exception as e:
        return f"Wissenssuche fehlgeschlagen (Embedding): {e}"
    scored = sorted(((_cos(qv, e["vec"]), e) for e in idx["entries"]), key=lambda t: t[0], reverse=True)
    top = [e for s, e in scored[:limit] if s > 0.3]
    if not top:
        return f"Nichts Passendes zu '{q}' im Wissensbestand gefunden."
    out = [f"Wissensbestand zu '{q}' ({len(top)} Treffer):"]
    for e in top:
        # Chunks kuerzen: auf dieser CPU-only Hardware kostet jeder Prompt-Token
        # spuerbar Zeit - 4 volle Chunks (~3.6k Zeichen) liessen die finale
        # Antwort in den 120s-Timeout laufen.
        out.append(f"--- aus {e['file']} ---\n{e['text'][:600]}")
    return "\n".join(out)


def stats() -> str:
    idx = _load()
    if not idx:
        return "kein Index"
    files = sorted({e["file"] for e in idx["entries"]})
    return f"{len(files)} Dateien, {len(idx['entries'])} Chunks, Modell {idx['model']}"
