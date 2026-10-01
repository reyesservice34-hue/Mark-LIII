"""
MIA persistent autonomous task worker.

Triggered every minute by mia-tasks.timer. It performs one runnable task per run,
uses the real local tool path, verifies execution evidence, persists lifecycle
state and sends completion notifications.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from brain.cognition.autonomous_core import AutonomousBrain

QUEUE = BASE / "tasks" / "queue"
DONE = BASE / "tasks" / "done"
NOTIFY_OUTBOX = BASE / "tasks" / "notify_outbox"
COMMAND_CENTER_GID = int(os.environ.get("MIA_COMMAND_CENTER_GID", "999"))
LOCK = BASE / "tasks" / ".worker.lock"
MAX_ATTEMPTS = 3
RUNNABLE_STATES = {"DETECTED", "PLANNED", "QUEUED"}
_BRAIN = AutonomousBrain(BASE)


def _lock_owner_alive() -> bool:
    if not LOCK.exists():
        return False
    try:
        raw = LOCK.read_text(encoding="utf-8").strip()
        data = json.loads(raw)
        pid = int(data.get("pid", 0)) if isinstance(data, dict) else 0
    except Exception:
        pid = 0
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _notify_completion(task: dict) -> None:
    """Persist completion delivery for the live Command Center to consume.

    Do not publish from this worker with a private EventBus. That writes state
    but cannot reach the running dashboard subscribers. The outbox survives
    restarts and is consumed inside the real Command Center process, where the
    shared EventBus, toast, live inbox and phone push are all available.
    """
    status = str(task.get("status") or "")
    if status not in {"done", "failed", "waiting_approval"}:
        return

    if status == "done":
        title = "MIA Aufgabe erledigt"
        body = str(task.get("result") or "")[:1200]
        severity = "success"
    elif status == "failed":
        title = "MIA Aufgabe fehlgeschlagen"
        body = str(task.get("result") or "")[:1200]
        severity = "error"
    else:
        title = "MIA braucht eine Freigabe"
        body = (
            f"Aufgabe {task.get('id')}: {str(task.get('goal') or '')[:900]} "
            "Freigabe mit der Task-ID erforderlich."
        )
        severity = "warning"

    task_id = str(task.get("id") or "")
    if not task_id:
        return
    payload = {
        "task_id": task_id,
        "status": status,
        "title": title,
        "body": body,
        "severity": severity,
        "created_epoch": time.time(),
    }
    try:
        NOTIFY_OUTBOX.mkdir(parents=True, exist_ok=True)
        # The Command Center container runs as GID 999 and must be able to
        # consume/delete delivered records from this host-created directory.
        # Keep access limited to root + that group, never world-writable.
        os.chown(NOTIFY_OUTBOX, 0, COMMAND_CENTER_GID)
        os.chmod(NOTIFY_OUTBOX, 0o770)
        target = NOTIFY_OUTBOX / f"{task_id}.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
        print(f"[tasks] completion queued for delivery: {task_id}", flush=True)
    except Exception as exc:
        print(f"[tasks] completion delivery queue failed for {task_id}: {type(exc).__name__}: {exc}", flush=True)


def _load_runnable() -> tuple[Path, dict] | None:
    for path in sorted(QUEUE.glob("*.json")):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        state = str(task.get("state") or "").upper().strip()
        if not state:
            status = str(task.get("status") or "").lower()
            state = "EXECUTING" if status == "in_progress" else "QUEUED"
            task["state"] = state
        if state in RUNNABLE_STATES and str(task.get("status") or "").lower() != "waiting_approval":
            return path, task
    return None


def _save(path: Path, task: dict) -> None:
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def _finish(path: Path, task: dict) -> None:
    DONE.mkdir(parents=True, exist_ok=True)
    (DONE / path.name).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    path.unlink(missing_ok=True)


def main() -> int:
    QUEUE.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)

    if _lock_owner_alive():
        return 0
    LOCK.unlink(missing_ok=True)
    LOCK.write_text(
        json.dumps({"pid": os.getpid(), "started": time.time()}),
        encoding="utf-8",
    )

    try:
        # If a previous worker died after marking work EXECUTING, this process
        # is the new exclusive owner and can safely return that work to QUEUED.
        recovered_now = _BRAIN.recover_stale_tasks(stale_after_seconds=0)
        if recovered_now:
            print(f"[brain] recovered {recovered_now} interrupted task(s)", flush=True)

        # This is MIAs heartbeat: recover old work and convert actionable
        # internal events into real queued tasks before selecting work.
        pulse = _BRAIN.pulse()
        print(
            f"[brain] pulse queue={pulse.get('queue')} executing={pulse.get('executing')} "
            f"approval={pulse.get('waiting_approval')} events={pulse.get('event_inbox')}",
            flush=True,
        )

        # Proactive tasks that cross the approval boundary notify the user once.
        for approval_path in sorted(QUEUE.glob("*.json")):
            try:
                approval_task = json.loads(approval_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (
                str(approval_task.get("state") or "").upper() == "WAITING_FOR_APPROVAL"
                and not approval_task.get("approval_notified")
            ):
                _notify_completion(approval_task)
                approval_task["approval_notified"] = True
                approval_task["updated_at"] = approval_task.get("updated_at") or time.time()
                _save(approval_path, approval_task)

        picked = _load_runnable()
        if picked is None:
            return 0

        path, task = picked
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task["started_epoch"] = time.time()
        _BRAIN.transition(task, "EXECUTING")
        _save(path, task)
        print(
            f"[tasks] execute {task.get('id')} attempt={task['attempts']}: "
            f"{str(task.get('goal') or '')[:100]}",
            flush=True,
        )

        try:
            os.environ["MIA_HEADLESS_WORKER"] = "1"
            from core import local_brain

            # Prevent recursive background_task -> background_task chains.
            local_brain._TOOL_KEYWORDS["background_task"] = []
            local_brain._FORCE_TOOL_EXECUTION = True

            answer, history = local_brain.chat(
                "Erledige diese Aufgabe jetzt vollstaendig und eigenstaendig mit deinen Tools. "
                "Berichte am Ende kurz das konkrete Ergebnis. "
                "VERBOTEN: reine Ankuendigungen oder erfundene Ausfuehrung. "
                "Wenn etwas nicht geht, nenne exakt den echten Blocker.\n\nAUFGABE: "
                + str(task.get("goal") or ""),
                skip_clarify=True,
            )
            answer = str(answer or "").strip()

            _BRAIN.transition(task, "VERIFYING")
            verification = _BRAIN.verify_execution(answer, history)
            task["execution_evidence"] = verification.evidence
            task["verification_reason"] = verification.reason

            if verification.ok:
                task["result"] = answer
                _BRAIN.transition(task, "DONE")
            elif (
                verification.fallback_result
                and verification.reason in {"ungrounded_completion", "non_substantive_completion"}
            ):
                # The tool really ran and returned usable evidence, but the small local
                # model produced an unsafe/poor summary. Preserve the strict grounding
                # guard and finish with the raw verified tool result instead of either
                # accepting invented prose or falsely marking real work as failed.
                task["result"] = verification.fallback_result
                task["verification_reason"] = f"{verification.reason}:tool_evidence_fallback"
                task["execution_evidence"]["tool_evidence_fallback"] = True
                _BRAIN.transition(task, "DONE")
            else:
                detail = (
                    "Nicht erledigt: Verifikation fehlgeschlagen "
                    f"({verification.reason}). "
                )
                if verification.fallback_result:
                    detail += "Letztes erfolgreiches Tool-Ergebnis: " + verification.fallback_result[:500]
                elif answer:
                    detail += "Letzte Antwort: " + answer[:500]
                task["result"] = detail
                _BRAIN.transition(task, "FAILED")

        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(f"[tasks] execution error: {err}", flush=True)
            traceback.print_exc()
            if task["attempts"] >= MAX_ATTEMPTS:
                task["result"] = f"Nach {MAX_ATTEMPTS} Versuchen nicht erledigt. Letzter Fehler: {err}"
                _BRAIN.transition(task, "FAILED")
            else:
                task["last_error"] = err
                _BRAIN.transition(task, "QUEUED")
                _save(path, task)
                return 1

        _finish(path, task)

        try:
            from memory.memory_manager import update_memory
            update_memory({
                "notes": {
                    f"aufgabe_{task.get('id')}": {
                        "value": (
                            f"Hintergrund-Aufgabe '{str(task.get('goal') or '')[:80]}' "
                            f"ist {task.get('state')}: {str(task.get('result') or '')[:300]}"
                        )
                    }
                }
            })
        except Exception:
            pass

        if task.get("state") == "DONE":
            try:
                from brain.cognition import resolve_goal
                resolve_goal(str(task.get("goal") or ""), str(task.get("result") or ""))
            except Exception:
                pass

        _notify_completion(task)
        _BRAIN.write_health()
        print(f"[tasks] {task.get('id')} -> {task.get('state')}", flush=True)
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
