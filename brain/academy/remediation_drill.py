#!/usr/bin/env python3
import sys
from pathlib import Path
ROOT=Path('/root/Mark-LIII'); sys.path.insert(0,str(ROOT))
from memory.memory_manager import remember,rebuild_retrieval_index
lesson="""MIA Academy Drill: Jede fachliche Fallantwort muss bei entscheidungsrelevanten Aussagen nach dem Muster BELEG -> BEWERTUNG -> AKTION arbeiten.
BELEG: Welche konkrete Quelle, Zahl, Datei, Vertragsstelle, Gesetzesnorm oder welcher reale Systemstatus stuetzt die Aussage?
BEWERTUNG: Was folgt daraus, und was ist noch unsicher?
AKTION: Welcher konkrete naechste Schritt ist jetzt auszufuehren oder zu veranlassen?
Wenn ein Beleg fehlt, nicht raten. Stattdessen explizit den fehlenden Beleg benennen und dessen Beschaffung als naechsten Schritt festlegen.
Bei Arbeitsrecht niemals nur abstrakt erklaeren. Vor einer konkreten rechtlichen Massnahme aktuelle Primaerquelle und Einzelfalldaten pruefen.
Abschlusskontrolle vor jeder Antwort: Mindestens ein Belegbezug und mindestens ein konkreter Handlungsschritt, sofern der Fall eine Handlung verlangt."""
remember('academy_drill_evidence_evaluation_action',lesson,'knowledge')
idx=rebuild_retrieval_index()
print('DRILL_STORED YES')
print('INDEX',idx)
