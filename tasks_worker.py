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
            answer, _ = chat(
                "Erledige diese Aufgabe jetzt vollstaendig und eigenstaendig mit deinen Tools. "
                "Berichte am Ende in 2-4 Saetzen, was du konkret getan hast und was das Ergebnis ist. "
                "VERBOTEN: Ankuendigungen wie 'ich werde ... versuchen' oder 'ich mache das jetzt' - "
                "das ist kein Ergebnis. Entweder du lieferst das konkrete Resultat, oder du sagst "
                "exakt, was nicht ging und warum.\n\nAUFGABE: " + task["goal"]
            )
            answer = answer.strip()
            _promise = any(p in answer.lower() for p in (
                "ich werde", "werde ich", "ich versuche es", "versuche ich", "ich mache das jetzt", "gleich"))
            _substance = len(answer) > 40 and not _promise
            task["status"] = "done" if _substance else "failed"
            task["result"] = answer if _substance else (
                "Kein verwertbares Ergebnis - die Antwort war nur eine Ankuendigung oder leer: " + answer[:200])
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
