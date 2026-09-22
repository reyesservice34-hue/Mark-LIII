"""
actions/knowledge_search.py — macht MIAs lokalen Wissensbestand (26 Trainings-
dateien, 125 Abschnitte, bge-m3-Embeddings) als regulaeres Tool verfuegbar.

Wird von core/action_loader.discover_actions() automatisch gefunden - damit
steht die Suche in JEDEM Pfad zur Verfuegung (main.py/Gemini-Live UND dem
lokalen Ollama-Pfad), ohne dass irgendetwas kopiert werden muss.
"""
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))


def knowledge_search(parameters: dict, player=None, session_memory=None) -> str:
    from core.knowledge import search_knowledge  # lazy: Index kann fehlen, dann klare Meldung
    q = parameters.get("query", "")
    if isinstance(q, dict):
        q = " ".join(str(v) for v in q.values())
    elif isinstance(q, (list, tuple)):
        q = " ".join(str(v) for v in q)
    q = str(q or "").strip()
    if not q:
        return "Kein Suchbegriff angegeben."
    result = search_knowledge(q, limit=2)
    if player:
        try:
            player.write_log(f"MIA: [knowledge_search] {q} -> {result[:120]}")
        except Exception:
            pass
    return result


TOOL = {
    "name": "search_knowledge",
    "description": (
        "Searches MIA's local knowledge base (training material on business, finance, "
        "technology, skills, ethics rules, working principles). ALWAYS call this before "
        "answering questions about expertise, procedures, skills or 'what do you know "
        "about X' - never answer such questions from memory or invent content."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "Topic or question to look up"}
        },
        "required": ["query"],
    },
    "handler": knowledge_search,
}
