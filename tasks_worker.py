"""
tasks_worker.py — Hintergrund-Worker fuer MIAs persistente Aufgaben.

Laeuft unabhaengig vom Chat (systemd-Timer mia-tasks.timer, jede Minute):
nimmt die aelteste offene Aufgabe aus tasks/queue/, arbeitet sie ueber den
lokalen Denk-Pfad (Ollama + Tools) ab, legt das Ergebnis in tasks/done/ ab
und notiert es im Langzeitgedaechtnis - so kann MIA es beim naechsten Kontakt
von sich aus berichten. Bricht nie hart ab; nach 3 Fehlversuchen wird die
Aufgabe mit dem letzten Fehler als 'failed' abgelegt (ehrlich, nicht still).
"""
import json
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

QUEUE = BASE / "tasks" / "queue"
DONE = BASE / "tasks" / "done"
LOCK = BASE / "tasks" / ".worker.lock"
MAX_ATTEMPTS = 3


def main() -> int:
    QUEUE.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)

    # Einfache Sperre gegen parallele Worker (Timer koennte den vorigen Lauf ueberholen)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 1800:
        return 0
    LOCK.write_text(str(time.time()))

    try:
        files = sorted(QUEUE.glob("*.json"))
        if not files:
            return 0
        path = files[0]
        task = json.loads(path.read_text(encoding="utf-8"))
        task["status"] = "in_progress"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        path.write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[tasks] bearbeite {task['id']} (Versuch {task['attempts']}): {task['goal'][:80]}", flush=True)

        try:
            from core.local_brain import chat
            answer, history = chat(
                "Erledige diese Aufgabe jetzt vollstaendig und eigenstaendig mit deinen Tools. "
                "Berichte am Ende in 2-4 Saetzen, was du konkret getan hast und was das Ergebnis ist. "
                "VERBOTEN: Ankuendigungen wie 'ich werde ... versuchen' oder 'ich mache das jetzt' - "
                "das ist kein Ergebnis. Entweder du lieferst das konkrete Resultat, oder du sagst "
                "exakt, was nicht ging und warum.\n\nAUFGABE: " + task["goal"],
                skip_clarify=True,
                routing_text=task["goal"],
                exclude_tools={"background_task"},
            )
            answer = answer.strip()

            # "done" darf nur mit echter Ausfuehrungsevidenz gesetzt werden.
            tool_results = [
                str(m.get("content") or "").strip()
                for m in history
                if isinstance(m, dict) and m.get("role") == "tool"
            ]

            def _tool_failed(result: str) -> bool:
                low = result.lower().strip()
                return (
                    low.startswith(("fehler", "error", "fehlgeschlagen"))
                    or "unbekanntes tool" in low
                    or "timeout" in low
                    or "nicht erreichbar" in low
                )

            successful_tool_results = [r for r in tool_results if r and not _tool_failed(r)]
            _promise = any(p in answer.lower() for p in (
                "ich werde", "werde ich", "ich versuche es", "versuche ich", "ich mache das jetzt", "gleich"))
            _substance = len(answer) > 40 and not _promise
            _executed = bool(successful_tool_results)

            task["execution_evidence"] = {
                "tool_calls": len(tool_results),
                "successful_tool_calls": len(successful_tool_results),
            }
            task["status"] = "done" if (_substance and _executed) else "failed"

            if task["status"] == "done":
                task["result"] = answer
            elif not _executed:
                task["result"] = (
                    "Nicht erledigt: Es wurde kein erfolgreicher Tool-Aufruf ausgefuehrt. "
                    "MIA darf reine Absichts- oder Ergebnisbehauptungen nicht als erledigt markieren. "
                    "Letzte Antwort: " + answer[:200]
                )
            else:
                task["result"] = (
                    "Nicht erledigt: Die Abschlussantwort war leer oder nur eine Ankuendigung. "
                    "Letzte Antwort: " + answer[:200]
                )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[tasks] Fehler: {err}", flush=True)
            traceback.print_exc()
            if task["attempts"] >= MAX_ATTEMPTS:
                task["status"] = "failed"
                task["result"] = f"Nach {MAX_ATTEMPTS} Versuchen nicht erledigt. Letzter Fehler: {err}"
            else:
                task["status"] = "pending"
                path.write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
                return 1

        task["finished"] = time.time()
        (DONE / path.name).write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        path.unlink(missing_ok=True)

        try:
            from memory.memory_manager import update_memory
            update_memory({"notes": {f"aufgabe_{task['id']}": {
                "value": f"Hintergrund-Aufgabe '{task['goal'][:80]}' ist {task['status']}: {str(task['result'])[:300]}"
            }}})
        except Exception:
            pass
        print(f"[tasks] {task['id']} -> {task['status']}", flush=True)
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
