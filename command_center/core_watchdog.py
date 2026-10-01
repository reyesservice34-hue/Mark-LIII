"""External MIA core rollback watchdog.

Runs outside the Command Center process. It only acts while a verified proposal
is explicitly in the 'activating' phase. If the app cannot become internally
healthy after activation, it restores the pre-apply backup and restarts only
jarvis-command-center through the Docker socket.
"""
from __future__ import annotations
import json, os, shutil, sqlite3, time
from pathlib import Path
import httpx

MARKER=Path('/data/workspace/jarvis-tools/_evolution/pending.json')
ROOT=Path('/repo').resolve()
DB=Path('/data/jarvis.db')
TARGET_CONTAINER='jarvis-command-center'
HEALTH='http://jarvis-command-center:8080/api/health'

def save(m):
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    tmp=MARKER.with_suffix('.tmp'); tmp.write_text(json.dumps(m,ensure_ascii=False,indent=2)); tmp.replace(MARKER)

def db_stage(pid, stage, field='updated_at'):
    try:
        with sqlite3.connect(DB, timeout=10) as c:
            now=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            if stage=='stable':
                c.execute("UPDATE core_evolution_checks SET stage='stable',stable_at=?,updated_at=? WHERE proposal_id=?",(now,now,pid))
            elif stage=='rolled_back':
                c.execute("UPDATE core_evolution_checks SET stage='rolled_back',rollback_at=?,updated_at=? WHERE proposal_id=?",(now,now,pid))
                c.execute("UPDATE self_tools SET status='disabled',updated_at=? WHERE name=?",(now,'proposal:'+pid))
    except Exception as e:
        print('watchdog db update:',e,flush=True)

def healthy():
    try:
        r=httpx.get(HEALTH,timeout=3.0); r.raise_for_status(); d=r.json(); comps=d.get('components') or {}
        return comps.get('application',{}).get('status')=='healthy' and comps.get('database',{}).get('status')=='healthy'
    except Exception:
        return False

def restart_cc():
    transport=httpx.HTTPTransport(uds='/var/run/docker.sock')
    with httpx.Client(transport=transport,base_url='http://docker',timeout=20.0) as c:
        r=c.post(f'/containers/{TARGET_CONTAINER}/restart',params={'t':10})
        r.raise_for_status()

def rollback(m):
    rel=str(m.get('file') or '').lstrip('/')
    target=(ROOT/rel).resolve()
    if not str(target).startswith(str(ROOT)+os.sep): raise RuntimeError('unsafe target')
    backup=Path(str(m.get('backup') or ''))
    if not backup.is_file(): raise RuntimeError(f'backup missing: {backup}')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(backup,target)
    pid=str(m['proposal_id'])
    db_stage(pid,'rolled_back')
    hist=MARKER.parent/f'rolledback-{pid}-{int(time.time())}.json'
    m['phase']='rolled_back'; m['reason']='health failed after activation'; m['updated_at']=time.time()
    hist.write_text(json.dumps(m,ensure_ascii=False,indent=2))
    try: MARKER.unlink()
    except FileNotFoundError: pass
    restart_cc()
    print(f'ROLLBACK {pid} restored {rel}',flush=True)

def main():
    print('MIA core watchdog online',flush=True)
    while True:
        if not MARKER.exists(): time.sleep(5); continue
        try: m=json.loads(MARKER.read_text())
        except Exception: time.sleep(5); continue
        if m.get('phase')!='activating': time.sleep(5); continue
        if healthy():
            m['healthy']=int(m.get('healthy',0))+1; m['failures']=0; save(m)
            if m['healthy']>=3:
                pid=str(m['proposal_id']); db_stage(pid,'stable')
                hist=MARKER.parent/f'stable-{pid}-{int(time.time())}.json'; m['phase']='stable'; hist.write_text(json.dumps(m,ensure_ascii=False,indent=2))
                try: MARKER.unlink()
                except FileNotFoundError: pass
                print(f'STABLE {pid}',flush=True)
        else:
            m['failures']=int(m.get('failures',0))+1; m['healthy']=0; save(m)
            print(f"health miss {m['failures']} for {m.get('proposal_id')}",flush=True)
            if m['failures']>=6:
                try: rollback(m)
                except Exception as e: print('ROLLBACK FAILED',e,flush=True)
        time.sleep(10)

if __name__=='__main__': main()
