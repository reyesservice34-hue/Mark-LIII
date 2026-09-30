from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.cognition.autonomous_core import AutonomousBrain

def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        brain = AutonomousBrain(root)

        direct = brain.create_task("Prüfe den internen Systemstatus.", source="user")
        proactive = brain.create_task("Sende eine Kundenmail.", source="proactive:test")
        brain.emit_event(
            "followup_due",
            {"goal": "Prüfe den offenen internen Vorgang und dokumentiere den Status.", "actionable": True},
            source="test",
        )
        trigger = brain.register_time_trigger(
            "Prüfe den internen Trigger-Test und dokumentiere den Status.",
            "2000-01-01T00:00:00+00:00",
            source="test",
        )
        pulse = brain.pulse()
        fired = next(
            (row for row in brain.list_triggers(include_finished=True) if row.get("id") == trigger["id"]),
            {},
        )

        # Simulate a crash/replay after the task was already created.
        event_id = str(fired.get("event_id") or "")
        processed_path = brain.processed / f"{event_id}.json"
        replay_path = brain.inbox / f"{event_id}.json"
        replay_path.write_text(processed_path.read_text(encoding="utf-8"), encoding="utf-8")
        replay = brain.process_events()

        verification = brain.verify_execution(
            "Systemstatus geprüft. Der Dienst ist aktiv und antwortet.",
            [{"role": "tool", "content": "service=active\nhealth=ok"}],
        )
        metrics = brain.verify_execution(
            "CPU-Auslastung: 17 %. RAM-Auslastung: 86 % (13,3/15,6 GB). Disk: 26 %. Laufzeit: 145,7 h. Prozesse: 249.",
            [{"role": "tool", "content": "CPU 17% | RAM 86% (13.3/15.6 GB) | Disk 26% | Laufzeit 145.7 h | Prozesse 249"}],
        )
        hallucinated = brain.verify_execution(
            "Der MIA-Wissensspeicher beschreibt ein Migrantinnen-Programm, das Frauen im Alltag stärkt und ihre Fähigkeiten entwickelt.",
            [{"role": "tool", "content": "MIA knowledge: Docker learned. Bash scripting supports docker restart and df -h."}],
        )

        result = {
            "direct_queued": direct["state"] == "QUEUED",
            "proactive_risky_waits": proactive["state"] == "WAITING_FOR_APPROVAL",
            "event_tasks_created": pulse.get("tasks_created") == 2,
            "time_trigger_fired": pulse.get("time_triggers_fired") == 1 and fired.get("state") == "FIRED",
            "event_replay_reused_task": replay.get("tasks_created") == 0,
            "verification_ok": verification.ok,
            "numeric_grounding_ok": metrics.ok,
            "ungrounded_rejected": (not hallucinated.ok and hallucinated.reason == "ungrounded_completion"),
            "health_file_exists": brain.health_path.exists(),
            "audit_file_exists": brain.audit_path.exists(),
        }
        result["ok"] = all(result.values())
        return result

if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
