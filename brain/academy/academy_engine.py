#!/usr/bin/env python3
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/root/Mark-LIII')
ACA=ROOT/'knowledge_src/business_academy'
RESULTS=ACA/'results'
STATE=ACA/'academy_state.json'
RESULTS.mkdir(parents=True,exist_ok=True)

def load_cases():
    out=[]
    for p in [ACA/'exam_v1.json', ACA/'reality_training/cases_v1.json']:
        if not p.exists(): continue
        data=json.loads(p.read_text(encoding='utf-8'))
        for c in data.get('cases',[]):
            q=c.get('question') or c.get('q') or c.get('case')
            if q: out.append({'id':c.get('id',p.stem),'domain':c.get('domain') or c.get('type','reality'),'question':q})
    return out

def grade(case, answer):
    a=answer.lower()
    score=0
    reasons=[]
    if len(answer)>=180: score+=0.20
    else: reasons.append('zu_kurz')
    uncertainty=any(x in a for x in ['fehlt','unklar','nicht bekannt','nicht vorlieg','prüf','pruef','annahme'])
    if uncertainty: score+=0.20
    else: reasons.append('keine_unsicherheitspruefung')
    structure=any(x in a for x in ['1.','2.','zuerst','anschließend','anschliessend','danach'])
    if structure: score+=0.15
    else: reasons.append('schwache_struktur')
    evidence=any(x in a for x in ['quelle','beleg','nachweis','status','gesetz','daten','rechnung'])
    if evidence: score+=0.20
    else: reasons.append('kein_belegbezug')
    action=any(x in a for x in ['prüfen','pruefen','berechnen','erheben','vergleichen','dokumentieren','planen','klären','klaeren'])
    if action: score+=0.15
    else: reasons.append('keine_konkrete_aktion')
    no_false_progress=not any(x in a for x in ['ich kümmere mich','ich kuemmere mich','ich melde mich später','ich melde mich spaeter'])
    if no_false_progress: score+=0.10
    else: reasons.append('falsche_fortschrittsbehauptung')
    return round(min(score,1.0),2),reasons

def run(limit=1):
    sys.path.insert(0,str(ROOT))
    from core.local_brain import chat
    cases=load_cases()
    old=json.loads(STATE.read_text()) if STATE.exists() else {'cursor':0,'attempts':0,'domains':{}}
    cursor=int(old.get('cursor',0))
    batch=[]
    for n in range(min(limit,len(cases))):
        case=cases[(cursor+n)%len(cases)]
        prompt=('TRAININGSMODUS. Dies ist ein simulierter Fall. Fuehre KEINE reale externe Aktion aus. '
                'Loese den Fall mit deinem gespeicherten Wissen. Trenne Fakten, fehlende Daten, Pruefung und Handlung. '
                'Erfinde nichts. Bei Recht/Steuer/Sicherheit nenne die notwendige Aktualitaets-/Quellenpruefung.\n\n'+case['question'])
        answer,_=chat(prompt,skip_clarify=True)
        score,reasons=grade(case,answer)
        passed=score>=0.85
        rec={'ts':datetime.now(timezone.utc).isoformat(),'id':case['id'],'domain':case['domain'],
             'question':case['question'],'answer':answer,'score':score,'passed':passed,'feedback':reasons}
        batch.append(rec)
        d=old['domains'].setdefault(case['domain'],{'attempts':0,'passes':0,'best':0.0,'last':None})
        d['attempts']+=1; d['passes']+=int(passed); d['best']=max(float(d['best']),score); d['last']=rec['ts']
    old['cursor']=(cursor+len(batch))%max(1,len(cases)); old['attempts']=int(old.get('attempts',0))+len(batch)
    old['updated_at']=datetime.now(timezone.utc).isoformat()
    STATE.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
    fn=RESULTS/(datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
    fn.write_text(json.dumps(batch,ensure_ascii=False,indent=2),encoding='utf-8')
    for x in batch: print(f"CASE={x['id']} DOMAIN={x['domain']} SCORE={x['score']} PASS={x['passed']} FEEDBACK={','.join(x['feedback'])}")
    print('STATE',STATE)
    print('RESULT',fn)
    return 0

if __name__=='__main__':
    limit=int(sys.argv[1]) if len(sys.argv)>1 else 1
    raise SystemExit(run(max(1,min(limit,5))))
