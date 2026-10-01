#!/usr/bin/env python3
import json,sys
from pathlib import Path
ROOT=Path('/root/Mark-LIII'); sys.path.insert(0,str(ROOT))
from core.local_brain import chat
from brain.academy.academy_engine import grade
P=ROOT/'knowledge_src/business_academy/reality_level2/cases_v1.json'
cases=json.loads(P.read_text())['cases']
case=cases[0]
prompt=('REALITY SCHOOL LEVEL 2. Simulation, keine reale externe Aktion. Loese den neuen Transferfall. '
'Trenne Fakten, Annahmen/fehlende Daten, Belege/Pruefung, Prioritaet und konkrete naechste Aktionen. '
'Erfinde nichts und melde keinen Abschluss ohne Evidenz.\n\n'+case['case'])
ans,_=chat(prompt,skip_clarify=True)
score,fb=grade({'id':case['id'],'domain':case['domain']},ans)
print('LEVEL2_CASES',len(cases))
print('CASE',case['id'],case['domain'],'DIFFICULTY',case['difficulty'])
print('SCORE',score,'PASS',score>=0.85,'FEEDBACK',fb)
print('ANSWER',ans[:2500])
