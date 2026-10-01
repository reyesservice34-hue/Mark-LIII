import re
_PROMISE=re.compile(r'(?i)\b(ich werde|werde ich|ich melde mich|du hörst bald|nicht mehr lange|gleich fertig|bald fertig|in kürze.*ergebnis)\b')
_CLAIM=re.compile(r'(?i)(?:^|[.!?]\s*)(?:erledigt|abgeschlossen|fertig[.!]|(?:ich habe|ich hab|wurde|ist jetzt|alles ist).*?(?:gespeichert|installiert|geändert|verbunden|aktiviert|abgeschlossen|erledigt|ausgeführt))')
def execution_evidence(messages):
 out=[]
 for m in messages:
  if m.get('role')!='tool':continue
  value=str(m.get('content') or '')
  failed=bool(re.search(r'(?i)(fehler|failed|not available|unbekanntes tool|nicht erledigt|nothing written|cannot confirm|not done|interface.*not available)',value))
  out.append({'tool':str(m.get('name') or 'unknown'),'success':bool(value.strip()) and not failed})
 return out
def validate_answer(text,evidence=()):
 if not str(text or '').strip():return 'Leere Antwort; kein Ergebnis vorhanden.'
 if _PROMISE.search(text):return 'Nur eine Ankündigung oder Zeitversprechen; kein abgeschlossenes Ergebnis.'
 if _CLAIM.search(text) and not any(e.get('success') for e in evidence):
  return 'Erfolgsbehauptung ohne Beleg einer Werkzeugausführung.'
 if text.startswith('Nicht abgeschlossen:'):return text
 return ''
