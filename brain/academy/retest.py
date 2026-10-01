#!/usr/bin/env python3
import json, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path('/root/Mark-LIII'); ACA=ROOT/'knowledge_src/business_academy'
sys.path.insert(0,str(ROOT))
from brain.academy.academy_engine import load_cases, grade
from core.local_brain import chat
qpath=ACA/'remediation_queue.json'
queue=json.loads(qpath.read_text()) if qpath.exists() else []
if not queue:
    print('NO_REMEDIATION_PENDING'); raise SystemExit(0)
item=queue[0]
case=next((c for c in load_cases() if c['id']==item['case_id']),None)
if not case: print('CASE_NOT_FOUND'); raise SystemExit(2)
old=[]
for p in sorted((ACA/'results').glob('*.json')):
    try: old.extend(json.loads(p.read_text()))
    except: pass
prior=[x for x in old if x.get('id')==case['id']]
before=float(prior[-1]['score']) if prior else 0.0
prompt=f"""TRAININGSMODUS. Keine reale externe Aktion ausfuehren.
Du wirst nach einem erkannten Fehler erneut geprueft.
Nachtrainingshinweis: {item['lesson']}
Loese den Fall neu. Trenne Fakten, fehlende Daten, Pruefung und konkrete Handlungsschritte.
Erfinde nichts. Recht/Steuer/Sicherheit nur mit notwendiger Aktualitaets-/Quellenpruefung.

Fall: {case['question']}"""
answer,_=chat(prompt,skip_clarify=True)
score,reasons=grade(case,answer)
rec={'ts':datetime.now(timezone.utc).isoformat(),'id':case['id'],'domain':case['domain'],'question':case['question'],
     'answer':answer,'score':score,'passed':score>=0.85,'feedback':reasons,'retest':True,'before_score':before,
     'improvement':round(score-before,2)}
fn=ACA/'results'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_retest.json')
fn.write_text(json.dumps([rec],ensure_ascii=False,indent=2))
if item['weakness'] not in reasons:
    queue.pop(0)
qpath.write_text(json.dumps(queue,ensure_ascii=False,indent=2))
print('RETEST_CASE',case['id'])
print('BEFORE',before)
print('AFTER',score)
print('IMPROVEMENT',round(score-before,2))
print('PASS',score>=0.85)
print('FEEDBACK',reasons)
print('WEAKNESS_FIXED',item['weakness'] not in reasons)
print('RESULT',fn)
