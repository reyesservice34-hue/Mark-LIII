"""
free_llm — one text completion, on a backend that bills nothing.

Extracted from core/agency.py the moment a second caller appeared. Two consumers
each carrying their own copy of "Gemini if there is a key, otherwise the local
model" is how the two quietly drift apart until one of them starts costing
money.

  "auto"   — Gemini when a key is configured, the local model otherwise.
  "gemini" — always Gemini (free tier).
  "local"  — always the model core/llm_client.py serves on this machine
             (Ollama / LM Studio), which costs nothing per call by definition.

Neither path is metered. That is the point, and it is why there is no OpenAI
branch here: adding one would put a bill behind a call the user cannot see.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
DEFAULT_MODEL   = "gemini-flash-latest"


def gemini_key() -> Optional[str]:
    try:
        key = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8")).get("gemini_api_key", "")
        return key.strip() or None
    except Exception:
        return None


def complete(prompt: str, system: str = "", model: str = DEFAULT_MODEL,
             backend: str = "auto") -> str:
    backend = (backend or "auto").strip().lower()
    key = None if backend == "local" else gemini_key()
    if backend == "gemini" and not key:
        raise RuntimeError("pinned to Gemini, but no API key is configured")

    if key:
        from google import genai      # imported lazily: this module stays importable
        client = genai.Client(api_key=key)   # without google-genai installed
        resp = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=f"{system}\n\n{prompt}" if system else prompt,
        )
        return (resp.text or "").strip()

    from core.llm_client import call_llm_text
    return call_llm_text(prompt=prompt, system=system or None)


def extract_json(text: str):
    """The first JSON value in a model reply, fences and surrounding prose and
    all. Returns None rather than raising — every caller here turns that into a
    correction, not a crash."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    candidates = [cleaned]
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None
