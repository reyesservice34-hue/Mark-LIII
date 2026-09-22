"""
actions/background_tasks.py — echte Aufgaben-Persistenz fuer MIA.

Eine Aufgabe ist NICHT an den Chat gebunden: sie wird als Datei in
tasks/queue/ abgelegt und von einem eigenen Worker (tasks_worker.py, laeuft
als systemd-Timer alle 60 s) ueber den lokalen Denk-Pfad abgearbeitet -
auch wenn der Nutzer den Chat laengst geschlossen hat. Ergebnisse landen in
tasks/done/ und im Langzeitgedaechtnis, damit MIA sie beim naechsten Kontakt
von selbst berichten kann.

Tool-Aktionen: create (neue Aufgabe), list (offen/erledigt), result (Ergebnis holen).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

QUEUE = _BASE / "tasks" / "queue"
DONE = _BASE / "tasks" / "done"


def _as_text(v) -> str:
    if isinstance(v, dict):
        v = " ".join(str(x) for x in v.values())
    elif isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    return str(v or "").strip()


def _load_all(folder: Path) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(folder.glob("*.json")):
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return items


def background_tasks(parameters: dict, player=None, session_memory=None) -> str:
    action = _as_text(parameters.get("action")).lower() or "create"
    goal = _as_text(parameters.get("goal"))

    if action == "create":
        if not goal:
            return "Ich brauche ein Ziel fuer die Aufgabe (goal)."
        QUEUE.mkdir(parents=True, exist_ok=True)
        tid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        task = {"id": tid, "goal": goal, "status": "pending", "created": time.time(),
                "attempts": 0, "result": None}
        (QUEUE / f"{tid}.json").write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        return (f"Aufgabe {tid} angelegt und in die Warteschlange gestellt. Sie wird im Hintergrund "
                f"abgearbeitet - auch wenn dieses Gespraech endet. Frag mich spaeter nach dem Ergebnis.")

    if action == "list":
        pending = _load_all(QUEUE)
        done = _load_all(DONE)
        lines = [f"Offen: {len(pending)} | Erledigt: {len(done)}"]
        for t in pending[-5:]:
            lines.append(f"  [offen] {t['id']}: {t['goal'][:80]} (Status {t['status']})")
        for t in done[-5:]:
            lines.append(f"  [erledigt] {t['id']}: {t['goal'][:60]} -> {str(t.get('result') or '')[:80]}")
        return "\n".join(lines)

    if action == "result":
        tid = _as_text(parameters.get("task_id"))
        for t in _load_all(DONE):
            if not tid or t["id"] == tid:
                if tid or t is _load_all(DONE)[-1]:
                    return f"Ergebnis von {t['id']} ({t['goal'][:60]}):\n{t.get('result') or '(kein Ergebnis)'}"
        for t in _load_all(QUEUE):
            if t["id"] == tid:
                return f"Aufgabe {tid} ist noch nicht fertig (Status {t['status']}, {t['attempts']} Versuche)."
        return "Keine passende Aufgabe gefunden."

    return "Unbekannte Aktion. Nutze create, list oder result."


TOOL = {
    "name": "background_task",
    "description": (
        "Persistent background task engine. Use 'create' when the user hands over a task that "
        "takes time or should be completed even after the conversation ends - it is queued and "
        "executed by a background worker independently of the chat. 'list' shows open/finished "
        "tasks, 'result' fetches a finished task's outcome. Never claim a queued task is already "
        "done - report its real status."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | list | result"},
            "goal": {"type": "STRING", "description": "The task to complete (for create)"},
            "task_id": {"type": "STRING", "description": "Task id (for result), optional"},
        },
        "required": ["action"],
    },
    "handler": background_tasks,
}
