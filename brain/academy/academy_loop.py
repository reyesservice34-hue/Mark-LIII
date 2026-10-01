#!/usr/bin/env python3
import json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('/root/Mark-LIII')
ACA=ROOT/'knowledge_src/business_academy'
RESULTS=ACA/'results'
STATE=ACA/'academy_state.json'
MATRIX=ACA/'competency_matrix.json'
QUEUE=ACA/'remediation_queue.json'

FEEDBACK_LESSONS={
 'zu_kurz':'Antwort war zu knapp. Naechstes Mal Fall vollstaendig, aber kompakt loesen.',
 'keine_unsicherheitspruefung':'Fehlende Daten und Unsicherheiten explizit benennen, bevor Schlussfolgerungen gezogen werden.',
 'schwache_struktur':'Antwort in klare Pruef- und Handlungsschritte gliedern.',
 'kein_belegbezug':'Belege, Daten, Quellen oder realen Systemstatus nennen und Behauptungen daran binden.',
 'keine_konkrete_aktion':'Nicht bei Erklaerung stehen bleiben: konkrete naechste Handlungsschritte nennen.',
 'falsche_fortschrittsbehauptung':'Nie Aktivitaet oder Abschluss behaupten ohne realen Task-/Tool-Beleg.'
}

def load_all_results():
    rows=[]
    for p in sorted(RESULTS.glob('*.json')):
        try:
            data=json.loads(p.read_text(encoding='utf-8'))
            if isinstance(data,list): rows.extend(data)
        except Exception: pass
    return rows

def build():
    rows=load_all_results()
    domains={}
    queue=[]
    for r in rows:
        d=domains.setdefault(r['domain'],{'attempts':0,'passes':0,'scores':[],'best':0.0,'weaknesses':{},'last_attempt':None})
        d['attempts']+=1
        d['passes']+=int(bool(r.get('passed')))
        sc=float(r.get('score',0))
        d['scores'].append(sc); d['best']=max(d['best'],sc); d['last_attempt']=r.get('ts')
        for f in r.get('feedback',[]):
            d['weaknesses'][f]=d['weaknesses'].get(f,0)+1
            queue.append({'case_id':r['id'],'domain':r['domain'],'weakness':f,'lesson':FEEDBACK_LESSONS.get(f,f),'source_ts':r.get('ts')})
    matrix={'updated_at':datetime.now(timezone.utc).isoformat(),'domains':{}}
    for name,d in domains.items():
        avg=round(sum(d['scores'])/len(d['scores']),2)
        confidence='UNPROVEN'
        if d['attempts']>=5 and avg>=0.9 and d['passes']/d['attempts']>=0.8: confidence='PROFICIENT'
        elif d['attempts']>=3 and avg>=0.85: confidence='DEVELOPING'
        elif d['attempts']>=1: confidence='ASSESSED'
        matrix['domains'][name]={
            'attempts':d['attempts'],'passes':d['passes'],'average_score':avg,'best_score':round(d['best'],2),
            'pass_rate':round(d['passes']/d['attempts'],2),'status':confidence,
            'weaknesses':d['weaknesses'],'last_attempt':d['last_attempt']
        }
    MATRIX.write_text(json.dumps(matrix,ensure_ascii=False,indent=2),encoding='utf-8')
    seen=set(); unique=[]
    for q in reversed(queue):
        key=(q['case_id'],q['weakness'])
        if key in seen: continue
        seen.add(key); unique.append(q)
    QUEUE.write_text(json.dumps(list(reversed(unique)),ensure_ascii=False,indent=2),encoding='utf-8')
    return matrix,unique

def install_lessons():
    sys.path.insert(0,str(ROOT))
    from memory.memory_manager import remember,rebuild_retrieval_index
    q=json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    for i,item in enumerate(q,1):
        key=f"academy_remediation_{item['domain']}_{item['weakness']}"
        val=(f"Academy-Nachtraining {item['domain']}: {item['lesson']} "
             f"Ausgeloest durch Pruefungsfall {item['case_id']}. Diese Regel bei kuenftigen Faellen aktiv anwenden.")
        remember(key,val,'knowledge')
    idx=rebuild_retrieval_index()
    return len(q),idx

if __name__=='__main__':
    m,q=build()
    n,idx=install_lessons()
    print('DOMAINS',len(m['domains']))
    for name,d in m['domains'].items():
        print(f"MATRIX {name} attempts={d['attempts']} avg={d['average_score']} pass_rate={d['pass_rate']} status={d['status']} weaknesses={d['weaknesses']}")
    print('REMEDIATION_ITEMS',n)
    print('INDEX',idx)
