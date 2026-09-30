"""
Persistent background task tool for MIA.

Tasks are stored on disk and executed by the independent mia-tasks.timer worker.
The authoritative lifecycle is managed by brain.cognition.AutonomousBrain.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from brain.cognition.autonomous_core import AutonomousBrain

QUEUE = _BASE / "tasks" / "queue"
DONE = _BASE / "tasks" / "done"
_BRAIN = AutonomousBrain(_BASE)


def _as_text(value) -> str:
    if isinstance(value, dict):
        value = " ".join(str(x) for x in value.values())
    elif isinstance(value, (list, tuple)):
        value = " ".join(str(x) for x in value)
    return str(value or "").strip()


def _load_all(folder: Path) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(row, dict):
                items.append(row)
        except Exception:
            continue
    return items


def _state(task: dict) -> str:
    state = str(task.get("state") or "").upper().strip()
    if state:
        return state
    status = str(task.get("status") or "").lower()
    return {
        "pending": "QUEUED",
        "in_progress": "EXECUTING",
        "done": "DONE",
        "failed": "FAILED",
        "waiting_approval": "WAITING_FOR_APPROVAL",
    }.get(status, status.upper() or "UNKNOWN")


def background_tasks(parameters: dict, player=None, session_memory=None) -> str:
    action = _as_text(parameters.get("action")).lower() or "create"
    goal = _as_text(parameters.get("goal"))

    if action == "create":
        if not goal:
            return "Ich brauche ein Ziel fuer die Aufgabe (goal)."
        task = _BRAIN.create_task(
            goal,
            source="user",
            autonomy="auto",
            reason="direct_background_task",
        )
        return (
            f"Aufgabe {task['id']} angelegt. Status: {task['state']}. "
            "Der echte Hintergrund-Worker uebernimmt sie unabhaengig vom Chat."
        )

    if action == "schedule":
        run_at = _as_text(parameters.get("run_at"))
        if not goal:
            return "Ich brauche ein Ziel fuer den Zeit-Trigger (goal)."
        if not run_at:
            return "Ich brauche den Ausfuehrungszeitpunkt als ISO-Zeit in run_at."
        try:
            trigger = _BRAIN.register_time_trigger(
                goal,
                run_at,
                source="user",
                autonomy="auto",
                reason="user_scheduled_followup",
            )
        except Exception as exc:
            return f"Zeit-Trigger konnte nicht angelegt werden: {type(exc).__name__}: {exc}"
        return (
            f"Zeit-Trigger {trigger['id']} angelegt fuer {trigger['run_at']}. "
            "Wenn er faellig wird, erzeugt MIA selbststaendig eine echte Aufgabe."
        )

    if action == "triggers":
        rows = _BRAIN.list_triggers(include_finished=False)
        if not rows:
            return "Keine offenen Zeit-Trigger."
        return "\n".join(
            [f"Offene Zeit-Trigger: {len(rows)}"]
            + [
                f"  [{row.get('state')}] {row.get('id')} @ {row.get('run_at')}: "
                f"{str(row.get('goal') or '')[:90]}"
                for row in rows[-10:]
            ]
        )

    if action == "cancel_trigger":
        trigger_id = _as_text(parameters.get("trigger_id"))
        if not trigger_id:
            return "Zum Abbrechen brauche ich die trigger_id."
        if _BRAIN.cancel_trigger(trigger_id):
            return f"Zeit-Trigger {trigger_id} wurde abgebrochen."
        return f"Zeit-Trigger {trigger_id} wurde nicht als offen gefunden."

    if action == "list":
        pending = _load_all(QUEUE)
        done = _load_all(DONE)
        lines = [f"Offen: {len(pending)} | Abgeschlossen: {len(done)}"]
        for task in pending[-8:]:
            lines.append(
                f"  [{_state(task)}] {task.get('id')}: "
                f"{str(task.get('goal') or '')[:80]}"
            )
        for task in done[-5:]:
            lines.append(
                f"  [{_state(task)}] {task.get('id')}: "
                f"{str(task.get('goal') or '')[:55]} -> "
                f"{str(task.get('result') or '')[:90]}"
            )
        return "\n".join(lines)

    if action == "approve":
        task_id = _as_text(parameters.get("task_id"))
        if not task_id:
            return "Fuer die Freigabe brauche ich die task_id."
        path = QUEUE / f"{task_id}.json"
        if not path.exists():
            return f"Aufgabe {task_id} wurde nicht in der offenen Queue gefunden."
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Aufgabe {task_id} konnte nicht gelesen werden: {type(exc).__name__}."
        if _state(task) != "WAITING_FOR_APPROVAL":
            return f"Aufgabe {task_id} wartet nicht auf Freigabe. Aktueller Status: {_state(task)}."
        task["policy"] = "approved_by_user"
        _BRAIN.transition(task, "QUEUED", approved_by="user")
        path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"Aufgabe {task_id} ist freigegeben und steht jetzt auf QUEUED."

    if action == "cancel":
        task_id = _as_text(parameters.get("task_id"))
        if not task_id:
            return "Zum Abbrechen brauche ich die task_id."
        path = QUEUE / f"{task_id}.json"
        if not path.exists():
            return f"Aufgabe {task_id} wurde nicht in der offenen Queue gefunden."
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"Aufgabe {task_id} konnte nicht gelesen werden: {type(exc).__name__}."
        _BRAIN.transition(task, "CANCELLED", result="Vom Nutzer abgebrochen.")
        (DONE / path.name).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        path.unlink(missing_ok=True)
        return f"Aufgabe {task_id} wurde abgebrochen."

    if action == "result":
        task_id = _as_text(parameters.get("task_id"))
        done = _load_all(DONE)
        if task_id:
            for task in done:
                if str(task.get("id")) == task_id:
                    return (
                        f"Ergebnis von {task_id} ({str(task.get('goal') or '')[:60]}):\n"
                        f"{task.get('result') or '(kein Ergebnis)'}"
                    )
            for task in _load_all(QUEUE):
                if str(task.get("id")) == task_id:
                    return (
                        f"Aufgabe {task_id} ist noch nicht fertig. "
                        f"Status: {_state(task)}, Versuche: {int(task.get('attempts', 0))}."
                    )
            return "Keine passende Aufgabe gefunden."

        if not done:
            pending = _load_all(QUEUE)
            if pending:
                task = pending[-1]
                return (
                    f"Die neueste Aufgabe {task.get('id')} ist noch offen. "
                    f"Status: {_state(task)}."
                )
            return "Es gibt noch keine Hintergrund-Aufgabe."

        task = max(done, key=lambda row: float(row.get("finished") or row.get("created") or 0))
        return (
            f"Letztes Ergebnis von {task.get('id')} "
            f"({str(task.get('goal') or '')[:60]}):\n"
            f"{task.get('result') or '(kein Ergebnis)'}"
        )

    return "Unbekannte Aktion. Nutze create, schedule, triggers, cancel_trigger, list, result, approve oder cancel."


TOOL = {
    "name": "background_task",
    "description": (
        "Persistente echte Hintergrund-Aufgaben und operative Proaktivitaet. create legt "
        "sofort einen Task an; schedule registriert einen dauerhaften Zeit-Trigger; triggers "
        "zeigt offene Trigger; cancel_trigger bricht einen Trigger ab. list/result zeigen reale "
        "Task-Zustaende. Nie eine Aufgabe als erledigt behaupten, bevor ihr Zustand DONE ist."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | schedule | triggers | cancel_trigger | list | result | approve | cancel"},
            "goal": {"type": "STRING", "description": "Aufgabenziel fuer create oder schedule"},
            "task_id": {"type": "STRING", "description": "Task-ID fuer result, approve oder cancel"},
            "run_at": {"type": "STRING", "description": "ISO-8601-Zeitpunkt fuer schedule, z.B. 2026-10-01T09:00:00+02:00"},
            "trigger_id": {"type": "STRING", "description": "Trigger-ID fuer cancel_trigger"},
        },
        "required": ["action"],
    },
    "handler": background_tasks,
}
