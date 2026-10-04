"""Strict zero-price OpenRouter transport; existing local tool/memory pipeline stays authoritative."""
import json,time,threading,socket,http.client,urllib.request
from pathlib import Path
from dotenv import dotenv_values
MODEL="qwen/qwen3.8-27b:free"
FREE_FALLBACK="apodex/apodex-1.1-mini:free"
_catalog_checked={}
class FreeRouteError(RuntimeError):pass
def _zero_price(model):
 global _catalog_checked
 if time.monotonic()-_catalog_checked.get(model,0)<300:return
 with urllib.request.urlopen("https://openrouter.ai/api/v1/models",timeout=5) as response:
  models=json.load(response)["data"]
 match=next((m for m in models if m["id"]==model),None)
 if not match or any(float(match["pricing"].get(k,"-1"))!=0 for k in ("prompt","completion")) or "tools" not in match.get("supported_parameters",[]):
  raise FreeRouteError("Kostenlose MIA-Route ist nicht mehr bestätigt. Kein kostenpflichtiger Ersatz wird verwendet.")
 _catalog_checked[model]=time.monotonic()
def _messages(messages):
 out=[];pending=[]
 for item in messages:
  role=item.get("role");m={"role":role,"content":item.get("content") or ""}
  if item.get("tool_calls"):
   calls=[]
   for n,call in enumerate(item["tool_calls"]):
    fn=call["function"];cid=call.get("id") or "mia_call_"+str(len(out))+"_"+str(n);pending.append(cid)
    arguments=fn.get("arguments") or {}
    calls.append({"id":cid,"type":"function","function":{"name":fn["name"],"arguments":arguments if isinstance(arguments,str) else json.dumps(arguments)}})
   m["tool_calls"]=calls
  if role=="tool":
   if not pending:raise FreeRouteError("Werkzeugergebnis ohne zugehörigen Aufruf.")
   m["tool_call_id"]=pending.pop(0)
  out.append(m)
 return out
import os
# 2026-10-04: Erster Weg ist OmniRoute (staerkeres Gratis-Modell); OpenRouter unten bleibt Reserve.
OMNI_MODEL=os.environ.get("MIA_ENGINE_MODEL","ddgw/tinfoil/gpt-oss-120b")
def _flat_tools(messages):
 # Der ddgw-Anbieter lehnt role=tool/tool_calls im Verlauf ab (HTTP 400); als Text versteht er sie.
 out=[]
 for m in messages:
  if m.get("tool_calls"):
   calls="; ".join(f'{c["function"]["name"]} {c["function"]["arguments"]}' for c in m["tool_calls"])
   out.append({"role":"assistant","content":((m.get("content") or "")+f"\n[Werkzeug aufgerufen: {calls}]").strip()})
  elif m.get("role")=="tool":
   out.append({"role":"user","content":f"[Werkzeugergebnis]: {m.get('content') or ''}"})
  else:out.append({"role":m["role"],"content":m.get("content") or ""})
 return out
def _omniroute(payload,cancel_event,resp):
 key=dotenv_values(Path(__file__).resolve().parents[1]/"command_center"/".env").get("OMNIROUTE_API_KEY")
 if not key:return False
 # Reasoning-Modelle verbrauchen Token schon beim Denken: 384 ergaebe leere Antworten.
 # stream=False: im Streaming liefert der ddgw-Anbieter Werkzeugaufrufe als <tool>-Text statt als tool_calls.
 body={"model":OMNI_MODEL,"messages":_flat_tools(_messages(payload["messages"])),"stream":False,"max_tokens":max(payload["options"].get("num_predict",384),2000),"temperature":0.2}
 if payload.get("tools"):body["tools"]=payload["tools"]
 try:
  resp.conn=http.client.HTTPConnection("127.0.0.1",20128,timeout=60);resp.conn.connect()
  resp.conn.request("POST","/v1/chat/completions",body=json.dumps(body).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
  r=resp.conn.getresponse()
 except Exception as e:
  print("[MIA_OMNI_FAILOVER] "+json.dumps({"error":type(e).__name__}),flush=True);resp.conn.close();return False
 if r.status>=400:
  print("[MIA_OMNI_FAILOVER] "+json.dumps({"status":r.status}),flush=True);r.read();r.close();resp.conn.close();return False
 try:data=json.loads(r.read())
 except Exception:data={}
 r.close()
 msg=((data.get("choices") or [{}])[0]).get("message") or {}
 if not msg.get("content") and not msg.get("tool_calls"):
  print("[MIA_OMNI_FAILOVER] "+json.dumps({"empty":True}),flush=True);resp.conn.close();return False
 # Als einzelnes SSE-Ereignis nachbilden, damit iter_lines() unveraendert bleibt.
 delta={"content":msg.get("content") or ""}
 if msg.get("tool_calls"):delta["tool_calls"]=[{"index":i,"id":c.get("id") or "","function":c.get("function") or {}} for i,c in enumerate(msg["tool_calls"])]
 lines=[b"data: "+json.dumps({"model":data.get("model") or OMNI_MODEL,"usage":data.get("usage") or {},"choices":[{"delta":delta}]}).encode()+b"\n",b"data: [DONE]\n"]
 class _Buffered:
  def readline(self):return lines.pop(0) if lines else b""
  def close(self):pass
  def getheader(self,*a):return None
  status=200
 r=_Buffered()
 resp.response=r;resp.done=threading.Event();resp.started=time.monotonic();resp.interrupted=False
 def watch():
  while not resp.done.wait(0.05):
   if (cancel_event is not None and cancel_event.is_set()) or time.monotonic()-resp.started>45:
    resp.interrupted=True
    if resp.conn.sock is not None:
     try:resp.conn.sock.shutdown(socket.SHUT_RDWR)
     except OSError:pass
    resp.conn.close();return
 threading.Thread(target=watch,daemon=True).start()
 return True
class Response:
 raw=None
 def __init__(self,payload,cancel_event):
  if os.environ.get("MIA_ENGINE_OMNIROUTE","1")!="0" and _omniroute(payload,cancel_event,self):return
  _zero_price(MODEL)
  key=dotenv_values(Path(__file__).resolve().parents[1]/".env").get("OPENROUTER_API_KEY")
  if not key:raise FreeRouteError("OpenRouter-Anmeldung fehlt.")
  body={"model":MODEL,"messages":_messages(payload["messages"]),"stream":True,"max_tokens":payload["options"].get("num_predict",384),"temperature":0.2,"reasoning":{"enabled":False},"provider":{"allow_fallbacks":False,"require_parameters":True,"data_collection":"deny","max_price":{"prompt":0,"completion":0}}}
  if payload.get("tools"):body["tools"]=payload["tools"]
  self.conn=http.client.HTTPSConnection("openrouter.ai",timeout=5);self.conn.connect();self.done=threading.Event();self.started=time.monotonic();self.interrupted=False
  def watch():
   while not self.done.wait(0.05):
    if (cancel_event is not None and cancel_event.is_set()) or time.monotonic()-self.started>15:
     self.interrupted=True
     if self.conn.sock is not None:
      try:self.conn.sock.shutdown(socket.SHUT_RDWR)
      except OSError:pass
     self.conn.close();return
  threading.Thread(target=watch,daemon=True).start()
  try:
   self.conn.request("POST","/api/v1/chat/completions",body=json.dumps(body).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
   self.response=self.conn.getresponse()
   if self.response.status in (429,500,502,503,504):
    initial_status=self.response.status
    self.response.read();self.response.close()
    if cancel_event is not None and cancel_event.is_set():raise FreeRouteError("Anfrage abgebrochen.")
    _zero_price(FREE_FALLBACK)
    body["model"]=FREE_FALLBACK
    print("[MIA_FREE_FAILOVER] "+json.dumps({"from":MODEL,"to":FREE_FALLBACK,"status":initial_status}),flush=True)
    self.conn.request("POST","/api/v1/chat/completions",body=json.dumps(body).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    self.response=self.conn.getresponse()
  except Exception:
   self.close();raise FreeRouteError("Die kostenlose Online-Route antwortet nicht rechtzeitig. Kein kostenpflichtiger Ersatz wurde gestartet.")
 def raise_for_status(self):
  if self.response.status>=400:
   status=self.response.status
   try:
    err=json.loads(self.response.read()).get("error",{});meta=err.get("metadata",{})
    raw=meta.get("raw","")
    if isinstance(raw,str):
     try:raw=json.loads(raw)
     except ValueError:raw={}
    print("[MIA_FREE_ERROR] "+json.dumps({"status":status,"provider":meta.get("provider_name"),"code":err.get("code"),"provider_error_code":raw.get("error",{}).get("code") if isinstance(raw,dict) else None,"provider_error_type":raw.get("error",{}).get("type") if isinstance(raw,dict) else None,"retry_after":self.response.getheader("Retry-After")}),flush=True)
   finally:self.close()
   raise FreeRouteError(f"Kostenlose Online-Route derzeit nicht verfügbar (HTTP {status}). Kein kostenpflichtiger Ersatz wurde gestartet.")
 def iter_lines(self):
  content=[];calls={};usage={};model=MODEL
  try:
   while True:
    line=self.response.readline()
    if not line:break
    if not line.startswith(b"data: "):continue
    raw=line[6:].strip()
    if raw==b"[DONE]":break
    event=json.loads(raw)
    if event.get("error"):raise FreeRouteError("Kostenlose Online-Route hat die Antwort abgebrochen.")
    model=event.get("model") or model;usage=event.get("usage") or usage
    for choice in event.get("choices",[]):
     delta=choice.get("delta",{})
     if delta.get("content"):
      piece=delta["content"];content.append(piece)
      yield json.dumps({"message":{"content":piece},"done":False}).encode()
     for call in delta.get("tool_calls") or []:
      n=call.get("index",0);acc=calls.setdefault(n,{"name":"","arguments":"","id":""});fn=call.get("function",{})
      if call.get("id"):acc["id"]=call["id"]
      if fn.get("name"):acc["name"]+=fn["name"]
      if fn.get("arguments"):acc["arguments"]+=fn["arguments"]
   if self.interrupted:raise FreeRouteError("Kostenlose Online-Antwort wurde abgebrochen oder hat das Zeitlimit überschritten.")
   parsed=[{"id":v["id"],"type":"function","function":{"name":v["name"],"arguments":json.loads(v["arguments"] or "{}")}} for _,v in sorted(calls.items())]
   if not content and not parsed:raise FreeRouteError("Kostenlose Online-Route lieferte keine Antwort.")
   if float(usage.get("cost",0) or 0)!=0:raise FreeRouteError("Kostenprüfung fehlgeschlagen; Route wird nicht fortgesetzt.")
   self.final={"message":{"role":"assistant","content":"".join(content),**({"tool_calls":parsed} if parsed else {})},"done":True,"model":model,"prompt_eval_count":usage.get("prompt_tokens"),"eval_count":usage.get("completion_tokens"),"total_duration":int((time.monotonic()-self.started)*1e9)}
   print("[MIA_FREE_ROUTE] "+json.dumps({"model":model,"seconds":round(time.monotonic()-self.started,3),"cost":usage.get("cost"),"tool_calls":len(parsed)}),flush=True)
   yield json.dumps({**self.final,"message":{"content":"",**({"tool_calls":parsed} if parsed else {})}}).encode()
  except (OSError,http.client.HTTPException):raise FreeRouteError("Kostenlose Online-Antwort wurde unterbrochen.")
 def json(self):
  for _ in self.iter_lines():pass
  return self.final
 def close(self):
  self.done.set();self.conn.close()
  if hasattr(self,"response"):self.response.close()
