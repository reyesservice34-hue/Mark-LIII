#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path('/root/Mark-LIII')
ACA=ROOT/'knowledge_src/business_academy'
MATRIX=ACA/'competency_matrix.json'
PLAN=ACA/'adaptive_training_plan.json'

TARGET_DOMAINS=[
 'arbeitsrecht','finanzen','unternehmensfuehrung','steuern','handwerkskalkulation',
 'baurecht','projektmanagement','hr','vertrieb','einkauf','forderungen',
 'strategie','risiko','qualitaet','arbeitssicherheit','datenschutz',
 'verhandlung','multi_company','agenten','reyes_service'
]

def norm(s):
    s=(s or '').lower()
    repl={'ä':'ae','ö':'oe','ü':'ue','ß':'ss',' ':'_','-':'_','/':'_'}
    for a,b in repl.items(): s=s.replace(a,b)
    return s

def load_matrix():
    if MATRIX.exists():
        try:return json.loads(MATRIX.read_text())
        except:pass
    return {'domains':{}}

m=load_matrix()
known={norm(k):v for k,v in m.get('domains',{}).items()}
rows=[]
for d in TARGET_DOMAINS:
    x=known.get(norm(d),{})
    attempts=int(x.get('attempts',0))
    avg=float(x.get('average_score',0))
    pass_rate=float(x.get('pass_rate',0))
    weakness_count=sum(int(v) for v in (x.get('weaknesses') or {}).values())
    # Higher priority = weaker or untested. Untested deliberately goes first.
    priority=(100 if attempts==0 else 0) + (1-avg)*40 + (1-pass_rate)*30 + weakness_count*10 - min(attempts,5)*2
    rows.append({
      'domain':d,'attempts':attempts,'average_score':avg,'pass_rate':pass_rate,
      'weakness_count':weakness_count,'priority':round(priority,2)
    })
rows.sort(key=lambda r:(r['priority'], -r['attempts']), reverse=True)
plan={
 'updated_at':datetime.now(timezone.utc).isoformat(),
 'next_domain':rows[0]['domain'] if rows else None,
 'queue':rows
}
PLAN.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
print('NEXT_DOMAIN',plan['next_domain'])
for r in rows[:8]:
    print(r)
