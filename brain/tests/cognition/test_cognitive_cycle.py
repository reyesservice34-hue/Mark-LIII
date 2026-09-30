from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.cognition.cognitive_cycle import CognitiveEngine


def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        hits = {
            "MIA": "MIA ist das aktuelle System.",
            "Kunde": "Projekt Kunde A: Badmodernisierung.",
        }

        def search(query: str, limit: int = 8):
            return [v for k, v in hits.items() if k.casefold() in query.casefold()][:limit]

        engine = CognitiveEngine(Path(tmp) / "brain", memory_search=search)
        state = engine.observe_turn(
            "Baue MIA ein kognitives Gehirn und prüfe ihren aktuellen Status.",
            "Ich habe die Architektur geprüft und den nächsten Schritt vorbereitet.",
            "de-DE",
        )
        context = engine.prompt_context()
        health = engine.health_snapshot()

        secret_state = engine.observe_turn(
            "password=supersecret123",
            "Das speichere ich nicht.",
            "de-DE",
        )

        result = {
            "intent": state.current_intent,
            "goal_present": bool(state.active_goals),
            "topic_present": bool(state.recent_topics),
            "context_present": "COGNITIVE CONTEXT" in context,
            "health_ok": health.get("ok") is True,
            "secret_not_persisted": secret_state.last_user != "password=supersecret123",
            "health_file_exists": engine.health_path.exists(),
            "state_file_exists": engine.state_path.exists(),
        }
        result["ok"] = all([
            result["goal_present"],
            result["topic_present"],
            result["context_present"],
            result["health_ok"],
            result["secret_not_persisted"],
            result["health_file_exists"],
            result["state_file_exists"],
        ])
        return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
