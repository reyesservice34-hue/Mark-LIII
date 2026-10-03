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

_EN=frozenset("the is was to and of for with has have wanted working on a an in that this as by it be are were stored created asked needs".split())
_WISH=re.compile(r"\b(möchte|möchten|will|wollen|wünscht|wünschen|bevorzugt|soll|sollen|wants|prefers|likes|immer|nie|niemals|ab jetzt|künftig)\b",re.I)
_EVENT=re.compile(r"^(?:der |die )?(?:master|mia|nutzer|benutzer|user)\b[^.]{0,60}?\b(?:hat|haben|hatte|wurde|wurden|was|has|had|stored|asked|created|cloned|installed|checked|versucht|benötigt|bestätigt|bestätigte|erstellt|erstellte|gestartet|fragt|fragte|bat|prüft|geprüft|installiert|geklont|arbeitet|needs|asks|creates)\b",re.I)
_ACTION=re.compile(r"(kann|können|sollte|sollen|muss|darf)[^.]{0,50}(gelöscht|entfernt|deaktiviert|abgeschaltet|überschrieben)|\blöschen\b|\bdeaktivieren\b|\bentfernen\b",re.I)
_STALE=re.compile(r"(nicht aktiv|fehlt\b|fehlend|nicht gefunden|nicht vorhanden|verhindert|rechtehindernis|derzeit|aktuell\b|gerade\b|noch nicht|offline|läuft nicht|ohne verbindung|problematisch|probleme\b|fehlerhaft|funktioniert nicht|einwandfrei|nicht verbinden)",re.I)
# "MIA kann X nicht" als Gedächtnis-Satz nährt falsche Absagen ("dazu habe ich keine Rechte"): nie speichern.
_LIMIT=re.compile(r"\b(kann|können|konnte|konnten)\b[^.]{0,60}\bnicht\b",re.I)
_PROMPTISH=re.compile(r"^(du bist|you are|deine (hauptaufgabe|aufgabe)|dein name)\b",re.I)
def auto_fact_rejection(text):
    """Grund, warum ein automatisch gelernter Satz NICHT ins Gedächtnis gehört; leer, wenn er passt.

    Ein Gedächtnis-Satz soll auch in drei Wochen noch stimmen und nichts auslösen. Gesprächsereignisse
    ("Master hat …"), Zustände ("Schlüssel fehlt"), englische Mitschrift und Handlungsempfehlungen
    ("kann gelöscht werden") erfüllen das nicht. Dauerhafte Wünsche und Regeln bleiben ausdrücklich erlaubt."""
    t=" ".join(str(text or "").split())
    wish=bool(_WISH.search(t))
    if _PROMPTISH.search(t): return "Prompt-Fragment"
    if _ACTION.search(t): return "Handlungsempfehlung"
    if _LIMIT.search(t): return "Einschränkung, nährt falsche Absagen"
    if _EVENT.search(t) and not wish: return "Gesprächsereignis"
    toks=re.findall(r"[a-zA-Zäöüß]+",t.lower())
    if len(toks)>=4 and sum(w in _EN for w in toks)/len(toks)>=0.25 and not wish: return "englische Mitschrift"
    if _STALE.search(t) and not wish: return "Zustand, schnell veraltet"
    return ""

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
