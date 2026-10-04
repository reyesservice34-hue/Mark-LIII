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

def local_sources(query,scope):
    import json
    from pathlib import Path
    from memory.memory_manager import load_memory,_entry_value
    base=Path(__file__).resolve().parent.parent; rows=[]
    for category,items in load_memory().items():
        if not isinstance(items,dict): continue
        for key,entry in items.items():
            value=_entry_value(entry)
            if scope_of(value)==scope and scope!='mixed': rows.append((f"Mark-LIII/Fakten/{category}/{key}",value))
    path=base/'knowledge/index.json'
    # Explicitly classified reference documents. Logs, access descriptions and
    # unclassified mixed master documents are deliberately not auto recalled.
    general={'ABSOLUTE_RULE_NO_LIES_NO_INVENTIONS.txt','CORE_RULE_NO_LIES_NO_INVENTION.txt','ETHICS_INTEGRITY_CORE.txt','mia_absolute_rule.txt','understanding_module.txt'}
    if path.exists():
        index=json.loads(path.read_text())
        for entry in index.get('entries',[]):
            file=str(entry.get('file','')); text=str(entry.get('text',''))
            item_scope=scope_of(text)
            if item_scope==scope or (scope=='general' and file in general):
                rows.append((f"lokaler Wissensindex/{file}/Abschnitt {entry.get('chunk','?')}",text))
    return choose(query,rows)
