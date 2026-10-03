"""Bounded topical recall. Existing stores remain authoritative; no new copies."""
import re
STOP=set("was wie ist heißt heisst mein meine meiner einem eine einer der die das den dem und für fuer bitte mir mich du dir wir ich wissen gespeichert welches welche welcher erinnere erinnerst nenne sag über ueber privat geschäftlich geschaeftlich test".split())
STOP.update(' zeig zeige angaben erinnerungen testwerte stehen deinen quellen begriff wert quelle nichts dauerhaft speichern gespeicherten gespeichert gehört gehoert gehört welche nenne lautet lauten finde findest sagen kurze kurz einen frage antwort bitte fakt testangabe künstlicher künstlichen gedächtnistest gedächtnis prüfung wissenstest faktentest'.split())
STOP.update(' zum zur aus mit nach vor von über unter über diese dieser dieses diesem diesen dazu dazugehörigen gespeicherte kannst kann kannte ich dein deine deiner deines einem einen ein eine einer testprojekt bereich allgemein'.split())
STOP.update({"mia", "jarvis"})
def scope_of(text):
    text=str(text or "").lower()
    if "[bereich:allgemein]" in text: return "general"
    private=bool(re.search(r"\bprivat(?:e|en|er|es)?\b|\[bereich:privat\]",text))
    business=bool(re.search(r"\b(?:geschäftlich|geschaeftlich|beruflich)(?:e|en|er|es)?\b|reyes service|\[bereich:geschäftlich\]",text))
    return "mixed" if private and business else "private" if private else "business" if business else "unknown"
def wanted_scope(query,history=None):
    s=scope_of(query)
    if s!='unknown': return s
    for item in reversed(history or []):
        if item.get('role')=='user':
            s=scope_of(item.get('content',''))
            if s!='unknown': return s
    return 'general'
def safe(text):
    return not re.search(r"(?i)\b(?:passwort|kennwort|password|api[_ -]?key|access[_ -]?token|secret|iban|private[_ -]?key|ssh-rsa)\b|-----BEGIN|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",str(text))
def terms(text):
    return {w for w in re.findall(r'[\wäöüß-]+',str(text).lower()) if len(w)>2 and w not in STOP}
def score(query,text):
    return len(terms(query)&terms(text))
def choose(query,rows,limit=3,budget=1600):
    ranked=sorted(((score(query,text),source,text) for source,text in rows if safe(text)),reverse=True)
    selected=[]; used=0; seen=set()
    for rank,source,text in ranked:
        if rank<=0 or text in seen: continue
        seen.add(text); line=f"[Quelle: {source}] {text[:600]}"
        if used+len(line)>budget: continue
        selected.append(line); used+=len(line)+1
        if len(selected)>=limit: break
    return '\n'.join(selected)

def command_sources(db,query,history=None):
    scope=wanted_scope(query,history); rows=[]
    unchecked={'chat-auto':', automatisch gelernt, ungeprüft','mail':', aus E-Mail, ungeprüft'}
    for item in db.fetchall('SELECT id,text,source FROM memory'):
        if scope_of(item['text'])==scope and scope!='mixed': rows.append((f"Command-Center/Fakt/{item['id']}{unchecked.get(item['source'],'')}",item['text']))
    general={'unlazy-skill-zusammenfassung','struktur-des-ecc-repositories','architektur-jarvis-autonomisches-gehirn'}
    for item in db.fetchall('SELECT slug,title,summary,content FROM knowledge WHERE enabled=1'):
        item_scope=scope_of(item['summary'])
        if item_scope!=scope and not (scope=='general' and item['slug'] in general): continue
        lines=item['content'].splitlines()
        for n,line in enumerate(lines):
            if score(query,line)>0:
                text='\n'.join(lines[max(0,n-1):n+2])
                if scope_of(text) not in ('unknown',scope): continue
                rows.append((f"Wissensdokument/{item['slug']}/Zeile {n+1}",text))
    for item in db.fetchall('SELECT name,title,description,content FROM skills WHERE enabled=1'):
        if scope_of(item['description']) != scope or scope == 'mixed': continue
        lines=item['content'].splitlines()
        for n,line in enumerate(lines):
            if score(query,line)>0:
                rows.append((f"Anleitung/{item['name']}/Zeile {n+1}", '\n'.join(lines[max(0,n-1):n+2])))
    if terms(query)&{'aufgabe','aufgaben','aufgabenstatus','projekt','projektstatus','status'}:
        for item in db.fetchall('SELECT id,title,description,status,output FROM tasks'):
            text=item['title']+' '+item['description']
            if scope_of(text)==scope and scope!='mixed':
                rows.append((f"Aufgabe/{item['id']}/aktueller Status",text+'; Status: '+item['status']+'; Ergebnis: '+item['output'][:300]))
    return choose(query,rows)
