import sys, time, subprocess, json
sys.path.insert(0, ".")
from core.local_brain import chat_stream_and_speak
from core.understanding import clarify
from actions.self_dev import self_dev
from memory.memory_manager import search_memory

results = []

def ask(label, text, must_contain=None, must_not_contain=None, timeout_hint=150):
    t0 = time.time()
    answer = ""
    try:
        for ev in chat_stream_and_speak(text):
            if ev["type"] == "done":
                answer = ev["full_answer"]
    except Exception as e:
        answer = f"EXCEPTION: {e}"
    dt = time.time() - t0
    ok = True
    notes = []
    for s in (must_contain or []):
        if s.lower() not in answer.lower():
            ok = False; notes.append(f"fehlt: '{s}'")
    for s in (must_not_contain or []):
        if s.lower() in answer.lower():
            ok = False; notes.append(f"unerwuenscht: '{s}'")
    results.append((label, ok, dt, answer[:260], notes))

# 1 Identitaet
ask("Identitaet (wer bist du)", "wer bist du?", must_contain=["MIA"], must_not_contain=["JARVIS"])

# 2 Verstehens-Schicht: Fragmente
c = clarify("ähm, kannst du äh, also ich möchte, ich möchte dass du mir, äh, den server neu startest")
results.append(("Verstehens-Schicht Fragmente", "server" in c.lower() and "äh" not in c.lower(), 0, c, []))

# 3 Verstehens-Schicht: Zeitwort bleibt erhalten
c = clarify("erinnere mich äh morgen um 9 uhr an den zahnarzt")
results.append(("Verstehens-Schicht behaelt 'morgen'", "morgen" in c.lower(), 0, c, []))

# 4 Memory-Abruf: was heute gelernt, ohne Erfindung
ask("Memory: was heute gelernt", "was hast du heute gelernt",
    must_contain=["lokalen"], must_not_contain=["Tastatureingabe", "Nachrichten", "理解"])

# 5 Wetter-Tool
ask("Wetter-Tool", "wie ist das wetter in berlin", must_contain=["Berlin"], must_not_contain=["keinen Zugang", "keine direkten Zugang", "Browser"])

# 6 Reminder-Tool + echte Systemverifikation
subprocess.run("systemctl stop $(systemctl list-units --all 'JARVISReminder_*' --plain --no-legend | awk '{print $1}') 2>/dev/null; true", shell=True)
ask("Reminder-Tool (Antwort)", "erinnere mich übermorgen um 14 uhr an das meeting",
    must_contain=["14"], must_not_contain=["nicht möglich", "Vergangenheit", "couldn't", "理解", "nicht verstanden"])
timers = subprocess.run(["systemctl", "list-timers", "--all"], capture_output=True, text=True).stdout
has_timer = "JARVISReminder_" in timers
results.append(("Reminder-Tool (Timer wirklich im System)", has_timer, 0, [l for l in timers.splitlines() if "JARVISReminder" in l][:1] or ["kein Timer gefunden"], []))

# 7b Wissenssuche: Trainingsdateien wirklich abrufbar (nicht erfunden)
from core.knowledge import search_knowledge, stats
ks = stats()
kr = search_knowledge("Wie soll MIA mit Ehrlichkeit und dem Erfinden von Informationen umgehen?", limit=3)
results.append(("Wissensindex vorhanden", "kein Index" not in ks, 0, ks, []))
results.append(("Wissenssuche liefert echte Trainingsinhalte", ("Treffer" in kr) and ("aus " in kr), 0, kr[:260].replace("\n", " | "), []))
ask("Wissen ueber Tool: was weisst du ueber Ehrlichkeit", "was weißt du über ehrlichkeit und integrität",
    must_contain=["ehrlich"], must_not_contain=["理解"])

# 7 Selbst-Edit blockiert
r = self_dev({"action": "write", "path": "verify_block.txt", "content": "x"})
import os
results.append(("Selbst-Edit ohne Bestaetigung blockiert", ("cannot confirm" in r.lower()) and not os.path.exists("verify_block.txt"), 0, r, []))

print("\n" + "="*78)
print("ABSCHLUSSPRUEFUNG - alles, was heute gebaut/repariert wurde")
print("="*78)
passed = 0
for label, ok, dt, answer, notes in results:
    passed += ok
    status = "PASS" if ok else "FAIL"
    t = f" ({dt:.0f}s)" if dt else ""
    print(f"[{status}] {label}{t}")
    print(f"       -> {answer}")
    for n in notes:
        print(f"       !! {n}")
print("="*78)
print(f"ERGEBNIS: {passed}/{len(results)} bestanden")
