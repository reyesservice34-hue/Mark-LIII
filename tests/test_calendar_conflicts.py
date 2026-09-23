"""Terminkollisionen — nur, was den Nutzer wirklich betrifft. Offline.

Auslöser: Ein privater Termin und eine Baustelle, auf der der Nutzer nicht eingeteilt ist, wurden als „Zeitüberschneidung“
gemeldet. Das war ein Fehlalarm.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.pop("CALENDAR_OWNER_NAMES", None)

from command_center.backend.services.calendar_service import find_conflicts, involves_owner  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  :: {detail}"))
    if not cond:
        FAILS.append(name)


def ev(title, start, end, category="", notes=""):
    return {"title": title, "start": f"2026-09-24T{start}:00", "end": f"2026-09-24T{end}:00", "category": category, "notes": notes}


site = ev("Baustelle – EG Wohnzimmer Gipskartondecke", "08:00", "17:00", "tour")
site_paul = ev("Baustelle – Bad", "08:00", "17:00", "tour", "Team: Paul, Christoph (von Jarvis erkannt)")
site_title = ev("Baustelle mit Paul und Jürgen", "08:00", "17:00", "tour")
mine = ev("Abholen in Wörstadt", "07:30", "10:00", "ich")
priv = ev("Zahnarzt", "09:00", "10:00", "privat")

check("privat/ich × Baustelle ohne Team: KEIN Alarm (der gemeldete Fehlalarm)", find_conflicts([site, mine]) == [] and find_conflicts([site, priv]) == [])
check("mehrere private Termine neben einer fremden Baustelle: weiterhin kein Alarm mit ihr",
      all(site not in pair for pair in find_conflicts([mine, site, priv])))
check("Baustelle, auf der der Nutzer im Team steht: Alarm", len(find_conflicts([site_paul, mine])) == 1)
check("Name im Titel genügt", len(find_conflicts([site_title, priv])) == 1)
check("Name als Teil eines anderen Wortes zählt nicht (Paulus)", not involves_owner(ev("Baustelle Paulus", "08:00", "09:00", "tour")))
check("zwei persönliche Termine zur selben Zeit: Alarm", len(find_conflicts([ev("A", "09:00", "11:00", "ich"), priv])) == 1)
check("zwei Baustellen zur selben Zeit: Alarm (Personal/Fahrzeug)", len(find_conflicts([site, ev("Andere Baustelle", "09:00", "12:00", "tour")])) == 1)
check("aufeinanderfolgende Termine kollidieren nicht", find_conflicts([ev("A", "09:00", "10:00", "ich"), ev("B", "10:00", "11:00", "privat")]) == [])
allday = {"title": "Leandro Geburtstag", "start": "2026-09-24T00:00:00", "end": "2026-09-24T23:59:00", "category": "privat", "notes": ""}
pairs = find_conflicts([allday, mine, priv, site_paul])
check("ganztägige Termine blockieren keine Zeit (echte Kollisionen bleiben)", pairs != [] and all(allday not in p for p in pairs), pairs)
check("Termin ohne Kategorie gilt als persönlich", len(find_conflicts([ev("A", "09:00", "11:00"), priv])) == 1)
os.environ["CALENDAR_OWNER_NAMES"] = "Yamil, Chef"
check("Namen sind einstellbar (CALENDAR_OWNER_NAMES)", involves_owner(ev("Baustelle mit Yamil", "08:00", "09:00", "tour")) and not involves_owner(site_paul))

print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
