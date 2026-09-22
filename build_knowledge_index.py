"""
build_knowledge_index.py — macht die Trainings-/Wissensdateien fuer MIA
tatsaechlich abrufbar (statt tot auf der Platte zu liegen).

Chunkt alle Dateien in knowledge_src/, erzeugt Embeddings lokal via Ollama
(bge-m3, liegt bereits auf dem Brain-Server, keine Cloud) und speichert einen
kleinen JSON-Index nach knowledge/index.json. core/knowledge.py durchsucht ihn.
"""
import json
import re
import sys
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SRC = BASE / "knowledge_src"
OUT = BASE / "knowledge" / "index.json"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "bge-m3"
CHUNK_CHARS = 900
OVERLAP = 150


def chunks(text: str):
    text = re.sub(r"\n{3,}", "\n\n", text)
    i = 0
    while i < len(text):
        piece = text[i:i + CHUNK_CHARS].strip()
        if piece:
            yield piece
        i += CHUNK_CHARS - OVERLAP


def embed(texts: list[str]) -> list[list[float]]:
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": MODEL, "input": texts}, timeout=300)
    r.raise_for_status()
    return r.json()["embeddings"]


def main():
    files = sorted(p for p in SRC.iterdir() if p.is_file())
    entries = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"skip {f.name}: {e}")
            continue
        for n, c in enumerate(chunks(text)):
            entries.append({"file": f.name, "chunk": n, "text": c})
    print(f"{len(files)} Dateien -> {len(entries)} Chunks, embedde mit {MODEL} ...")
    vectors = []
    B = 16
    for i in range(0, len(entries), B):
        vectors.extend(embed([e["text"] for e in entries[i:i + B]]))
        print(f"  {min(i + B, len(entries))}/{len(entries)}", flush=True)
    for e, v in zip(entries, vectors):
        e["vec"] = v
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"model": MODEL, "entries": entries}), encoding="utf-8")
    print(f"Index geschrieben: {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
