#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path('/root/Mark-LIII')
ACA=ROOT/'knowledge_src/business_academy'
MATRIX=ACA/'competency_matrix.json'
QUEUE=ACA/'remediation_queue.json'
PLAN=ACA/'next_training_plan.json'

def main():
    matrix=json.loads(MATRIX.read_text()) if MATRIX.exists() else {'domains':{}}
    remediation=json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    candidates=[]
    for name,d in matrix.get('domains',{}).items():
        attempts=int(d.get('attempts',0)); avg=float(d.get('average_score',0)); rate=float(d.get('pass_rate',0))
        weakness_count=sum(int(v) for v in d.get('weaknesses',{}).values())
        # Higher priority = weaker and less proven.
        priority=round((1-avg)*40+(1-rate)*30+max(0,5-attempts)*5+weakness_count*10,2)
        candidates.append({'domain':name,'priority':priority,'attempts':attempts,'average_score':avg,
                           'pass_rate':rate,'weaknesses':d.get('weaknesses',{})})
    candidates.sort(key=lambda x:(-x['priority'],x['attempts'],x['domain']))
    if remediation:
        next_item={'mode':'REMEDIATION','reason':'unresolved_exam_weakness','item':remediation[0]}
    elif candidates:
        next_item={'mode':'WEAKEST_DOMAIN','reason':'lowest_evidence_adjusted_competency','item':candidates[0]}
    else:
        next_item={'mode':'NEW_DOMAIN','reason':'no_assessed_domains_yet','item':None}
    out={'next':next_item,'ranking':candidates}
    PLAN.write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__': main()
