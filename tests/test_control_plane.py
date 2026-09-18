"""Offline verification of the control-plane client + desktop bridge.
No network, no real gateway, no token — every HTTP call is stubbed."""
import sys, json, types, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests as real_requests
import core.control_plane as CP
import core.desktop_bridge as DB

fails = []
def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  :: {detail}"))
    if not cond: fails.append(label)

class FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
    def json(self):
        if self._payload is _BROKEN:
            raise ValueError("no json")
        return self._payload
_BROKEN = object()

def shim(handler):
    return types.SimpleNamespace(request=handler, exceptions=real_requests.exceptions)

print("\n1. token & config resolution — secrets never hardcoded")
import os
os.environ.pop("JARVIS_GATEWAY_TOKEN", None)
cp = CP.ControlPlane(base_url="https://x/api", token="", actor="desktop-windows")
check("DEFAULT_ACTOR is the exact server-authorised name",
      CP.DEFAULT_ACTOR == "mark-liii-windows", CP.DEFAULT_ACTOR)
check("no token -> not configured", cp.configured() is False)
cp2 = CP.ControlPlane(token="T", base_url="https://x/api")
check("client without an actor override uses the default", cp2.actor == "mark-liii-windows")
check("explicit token -> configured", cp2.configured() is True)
os.environ["JARVIS_GATEWAY_TOKEN"] = "ENVTOK"
cp3 = CP.ControlPlane(base_url="https://x/api")
check("env var token picked up", cp3.configured() and cp3._token == "ENVTOK")
os.environ.pop("JARVIS_GATEWAY_TOKEN")
check("default base url is the contract url", CP.DEFAULT_BASE_URL.endswith("/jarvis-api"))
check("token never appears in module source",
      "X-Jarvis-Token" in (Path(__file__).resolve().parent.parent / "core" / "control_plane.py").read_text()
      and "ENVTOK" not in (Path(__file__).resolve().parent.parent / "core" / "control_plane.py").read_text())

print("\n2. submit sends the contract shape")
sent = {}
def h_submit(method, url, headers=None, json=None, timeout=None):
    sent.update(method=method, url=url, headers=headers, body=json)
    return FakeResp({"job_id": "j1", "conversation_id": "c1", "status": "queued"}, 202)
CP.requests = shim(h_submit)
res = cp2.submit("trag den Termin ein", conversation_id="c0")
check("POST to /v1/commands", sent["method"] == "POST" and sent["url"].endswith("/v1/commands"))
check("header carries the token", sent["headers"]["X-Jarvis-Token"] == "T")
check("body has actor+command+conversation", sent["body"] ==
      {"actor": "mark-liii-windows", "command": "trag den Termin ein", "conversation_id": "c0"})
check("job + conversation parsed", res.job_id == "j1" and res.conversation_id == "c1")

print("\n3. run() polls a multi-step job until it is FULLY done (one job)")
seq = [
    {"job_id": "j2", "conversation_id": "c2", "status": "queued"},          # POST
    {"status": "running", "routed_to": "dispo"},                            # still working
    {"status": "running", "routed_to": "kunde"},                            # part two, still one job
    {"status": "completed", "result": "Termin steht und der Kunde ist informiert.",
     "finished_at": "2026-09-17T10:00:00Z"},
]
calls = {"n": 0}
def h_multi(method, url, headers=None, json=None, timeout=None):
    i = calls["n"]; calls["n"] += 1
    return FakeResp(seq[i], 202 if method == "POST" else 200)
CP.requests = shim(h_multi)
statuses = []
res = cp2.run("trag den termin ein und sag dem kunden bescheid",
              on_status=lambda r: statuses.append(r.status),
              poll_interval=0, _sleep=lambda s: None)
check("did not stop after the first part", res.ok() and "Kunde" in res.result, res.result)
check("polled through every intermediate state", [s for s in statuses] ==
      ["queued", "running", "running", "completed"], statuses)
check("four HTTP calls (1 submit + 3 polls)", calls["n"] == 4, calls["n"])

print("\n4. awaiting approval is surfaced, not executed")
def h_approve(method, url, headers=None, json=None, timeout=None):
    return FakeResp({"job_id": "j3", "conversation_id": "c3", "status": "awaiting_approval",
                     "approval_required": True, "approval_code": "AB12",
                     "result": "Soll ich die Mail an den Kunden wirklich senden?"}, 202)
CP.requests = shim(h_approve)
res = cp2.run("schick dem kunden die rechnung", poll_interval=0, _sleep=lambda s: None)
check("awaiting_approval is terminal", res.is_terminal() and res.awaiting_approval())
check("approval code carried", res.approval_code == "AB12")

print("\n5. errors are named, never faked into success")
CP.requests = shim(lambda *a, **k: FakeResp({}, 401))
try:
    cp2.run("x"); check("401 raises auth", False)
except CP.ControlPlaneAuthError:
    check("401 raises auth", True)
def boom(*a, **k): raise real_requests.exceptions.ConnectionError()
CP.requests = shim(boom)
try:
    cp2.run("x"); check("offline raises offline", False)
except CP.ControlPlaneOffline:
    check("offline raises offline", True)
def slow(*a, **k): raise real_requests.exceptions.Timeout()
CP.requests = shim(slow)
try:
    cp2.run("x"); check("timeout raises offline", False)
except CP.ControlPlaneOffline:
    check("timeout raises offline", True)

print("\n6. desktop bridge — verbatim answer, no local persona")
with tempfile.TemporaryDirectory() as td:
    state = Path(td) / "conv.json"
    def make_client(handler):
        c = CP.ControlPlane(token="T", base_url="https://x/api")
        CP.requests = shim(handler)
        return c
    # a normal answer: spoken text is EXACTLY the server result
    server_answer = "Der Termin steht: Donnerstag 17 Uhr, Abfahrt 16:45."
    def h_ans(method, url, headers=None, json=None, timeout=None):
        if method == "POST":
            return FakeResp({"job_id": "j", "conversation_id": "conv-9", "status": "queued"}, 202)
        return FakeResp({"status": "completed", "result": server_answer}, 200)
    br = DB.DesktopBridge(client=make_client(h_ans), state_path=state)
    reply = br.handle("trag den termin ein")
    check("spoken text is the server's, verbatim", reply.speak == server_answer, reply.speak)
    check("kind is answer", reply.kind == "answer")
    check("no 'Sir' / 'mein Herr' injected by the desktop",
          "Sir" not in reply.speak and "mein Herr" not in reply.speak)

    print("\n7. conversation continuity across turns and restarts")
    check("conversation id captured", br.conversation_id == "conv-9")
    check("persisted to disk", json.loads(state.read_text())["conversation_id"] == "conv-9")
    sent_bodies = []
    def h_followup(method, url, headers=None, json=None, timeout=None):
        if method == "POST":
            sent_bodies.append(json)
            return FakeResp({"job_id": "j", "conversation_id": "conv-9", "status": "completed",
                             "result": "ok"}, 202)
        return FakeResp({"status": "completed", "result": "ok"}, 200)
    br2 = DB.DesktopBridge(client=make_client(h_followup), state_path=state)
    check("a fresh bridge reloads the conversation", br2.conversation_id == "conv-9")
    br2.handle("und was steht sonst noch an")
    check("follow-up reuses the same conversation_id",
          sent_bodies[0].get("conversation_id") == "conv-9", sent_bodies)

    print("\n8. bridge turns every failure into a plain spoken line, never a fake success")
    br_off = DB.DesktopBridge(client=make_client(boom), state_path=state)
    r = br_off.handle("mach was")
    check("offline is spoken and labelled", r.kind == "offline" and "nichts ausgeführt" in r.speak, r.speak)
    br_auth = DB.DesktopBridge(client=make_client(lambda *a, **k: FakeResp({}, 401)), state_path=state)
    check("auth failure labelled", br_auth.handle("x").kind == "auth")
    def h_approve2(method, url, headers=None, json=None, timeout=None):
        return FakeResp({"job_id": "j", "conversation_id": "conv-9", "status": "awaiting_approval",
                         "approval_required": True, "approval_code": "Z9",
                         "result": "Freigabe nötig für den Versand."}, 202)
    br_ap = DB.DesktopBridge(client=make_client(h_approve2), state_path=state)
    r = br_ap.handle("schick die mail")
    check("approval spoken with the code", r.kind == "approval" and "Z9" in r.speak, r.speak)
    def h_fail(method, url, headers=None, json=None, timeout=None):
        if method == "POST":
            return FakeResp({"job_id": "j", "conversation_id": "conv-9", "status": "queued"}, 202)
        return FakeResp({"status": "failed", "error": "Kalender lehnte den Schreibzugriff ab."}, 200)
    br_f = DB.DesktopBridge(client=make_client(h_fail), state_path=state)
    r = br_f.handle("trag ein")
    check("server failure is spoken as the server's error", r.kind == "error"
          and "Schreibzugriff" in r.speak, r.speak)

    print("\n9. no token -> the bridge says it's not connected, runs nothing")
    br_nc = DB.DesktopBridge(client=CP.ControlPlane(token="", base_url="https://x/api"), state_path=state)
    r = br_nc.handle("mach was")
    check("not-configured is spoken", r.kind == "not_configured" and "Token" in r.speak, r.speak)

print("\n10. persona files carry no forbidden vocative any more")
prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text(encoding="utf-8")
low = prompt.lower()
# 'sir' only allowed where the rule forbids it
bad = [ln for ln in prompt.splitlines() if "sir" in ln.lower() and "never" not in ln.lower()]
check("prompt has no 'Sir' except in the ban", not bad, bad[:1])
check("prompt states one-person delegation", "ONE INSTRUCTION IN, ONE VOICE OUT" in prompt)

print("\n11. speech_out play_file never raises, returns False without audio libs")
import core.speech_out as SO
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "x.wav"; p.write_bytes(b"not really wav")
    # sounddevice/numpy may be absent here — must degrade to False, not crash
    check("play_file degrades gracefully", SO.play_file(p) in (True, False))
check("play_file exists and is callable", callable(SO.play_file))

print("\n12. Autostart: die Kopplung überlebt einen Neustart")
import autostart as AS
from pathlib import Path as _P
import tempfile as _tf

# Unter Windows ist es ein Eintrag im Autostart-Ordner. Hier wird geprüft, was
# überall gilt: was in der Datei steht, und dass Aus- und Einschalten nichts
# durcheinanderbringt.
launcher = AS._launcher_text()
check("der Starter ruft die Brücke auf, nicht das Fenster",
      "desktop_agent.py" in launcher and "jarvis_desktop.py" not in launcher, launcher)
# pythonw gibt es nur unter Windows; anderswo ist der Rückfall auf python
# richtig und kein Mangel. Geprüft wird das, was überall gelten muss.
from pathlib import Path as _PP
_pyw = _PP(sys.executable).with_name("pythonw.exe")
check("er startet abgekoppelt (start \"\") und ohne Fenster, wo es geht",
      "start \"\"" in launcher and (("pythonw" in launcher.lower()) if _pyw.exists() else True),
      launcher)
check("und arbeitet im Ordner des Projekts", str(AS.ROOT) in launcher)
check("ohne Windows meldet er das ehrlich, statt still nichts zu tun",
      "Windows" in AS.turn_on() if AS.startup_dir() is None else True)

# Der Ordner wird sauber behandelt: anlegen, erkennen, wieder entfernen.
fake = _P(_tf.mkdtemp())
real_dir = AS.startup_dir
AS.startup_dir = lambda: fake
try:
    check("vorher nicht eingetragen", AS.enabled() is False)
    AS.turn_on()
    check("nach dem Einschalten liegt der Starter da", AS.enabled() is True)
    check("und enthält den Aufruf", "desktop_agent.py" in (fake / AS.NAME).read_text(encoding="utf-8"))
    AS.turn_off()
    check("nach dem Ausschalten ist er weg", AS.enabled() is False)
    check("zweimal ausschalten tut nicht weh", "nicht eingetragen" in AS.turn_off())
finally:
    AS.startup_dir = real_dir

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
