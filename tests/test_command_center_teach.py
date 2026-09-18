"""
Offline verification of desktop remote control and teach mode.

No real desktop, no network, no AI key: the PC is simulated by calling the same
HTTP endpoints the runner calls, and the distillation runs against a scripted
provider.

Run:  python tests/test_command_center_teach.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="jarvis-cc-teach-")
os.environ.update({
    "JARVIS_CC_DATA_DIR": _tmp, "JARVIS_CC_ADMIN_USER": "admin", "JARVIS_CC_ADMIN_PASSWORD": "adminpass123",
    "JARVIS_CC_SECURE_COOKIES": "false", "JARVIS_CC_APPROVAL_TIMEOUT_MIN": "1",
})
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LOCAL_LLM_URL",
          "JARVIS_GATEWAY_TOKEN", "GITHUB_TOKEN", "EMAIL_USER", "EMAIL_PASSWORD"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from command_center.backend.ai.base import ProviderInfo  # noqa: E402
from command_center.backend.app import create_app  # noqa: E402
from command_center.backend.config import reset_settings  # noqa: E402
from command_center.backend.services.desktop_bridge import DesktopError  # noqa: E402

fails: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  :: {detail}"))
    if not cond:
        fails.append(label)


class ScriptedProvider:
    def __init__(self):
        self.info = ProviderInfo(id="fake", model="scripted-1", label="Scripted · test")
        self.turns: list[list[dict]] = []
        self.seen: list[list[dict]] = []

    async def stream(self, *, system, messages, tools, max_tokens=16000):
        self.seen.append(messages)
        turn = self.turns.pop(0) if self.turns else text_turn("Done.")
        for ev in turn:
            yield ev

    async def health(self):
        return {"status": "healthy", "detail": "scripted"}


def text_turn(text: str) -> list[dict]:
    return [{"type": "text_delta", "text": text},
            {"type": "message_end", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
             "usage": {}}]


def tool_turn(name: str, args: dict, call_id: str = "c1") -> list[dict]:
    return [{"type": "tool_use", "id": call_id, "name": name, "input": args},
            {"type": "message_end", "stop_reason": "tool_use",
             "content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}], "usage": {}}]


def read_sse(resp) -> list[dict]:
    out = []
    for line in resp.iter_lines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:]))
            except ValueError:
                pass
    return out


reset_settings()
app = create_app()
state = app.state.jarvis
bridge = state.services["desktop"]
teaching = state.services["teaching"]

with TestClient(app) as c:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    H = {"X-CSRF-Token": r.json()["csrf_token"]}

    print("\n1. the bridge: a command is a request, not a promise")

    async def bridge_roundtrip():
        device = bridge.register(name="Buero-PC", actor="mark-liii-windows", platform="Windows 11",
                                 actions=[{"name": "open_app", "description": "opens an app"},
                                          {"name": "computer_control", "description": "types and clicks"}])
        # No runner is polling, so an unknown action must be refused before anything is queued.
        try:
            await bridge.dispatch(device_id=device["id"], action="format_disk", params={},
                                  requested_by="admin", timeout=1)
            return device, "unknown action was accepted", None
        except DesktopError as e:
            if "has no action" not in str(e):
                return device, f"wrong refusal: {e}", None

        # Now simulate the PC: dispatch in one task, poll+answer in another.
        async def fake_runner():
            for _ in range(50):
                cmd = bridge.next_for(device["id"])
                if cmd:
                    bridge.complete(cmd["id"], ok=True, result=f"opened {cmd['params'].get('app_name')}")
                    return cmd
                await asyncio.sleep(0.02)
            return None

        dispatched, polled = await asyncio.gather(
            bridge.dispatch(device_id=device["id"], action="open_app", params={"app_name": "Excel"},
                            requested_by="admin", agent_id="master", timeout=5),
            fake_runner())
        return device, "", (dispatched, polled)

    device, problem, roundtrip = asyncio.run(bridge_roundtrip())
    check("unknown action refused with the device's real capability list", not problem, problem)
    check("command reached the PC and the result came back",
          roundtrip and roundtrip[0]["status"] == "done" and "Excel" in roundtrip[0]["result"],
          roundtrip[0] if roundtrip else None)
    check("the PC saw the parameters it was given",
          roundtrip and roundtrip[1]["params"] == {"app_name": "Excel"})

    print("\n2. an offline desktop is said to be offline, not pretended away")
    bridge._last_poll.clear()
    offline = bridge.register(name="Schlafender-PC", actor="mark-liii-windows",
                              actions=[{"name": "open_app"}])
    state.db.update("desktop_devices", offline["id"], {"last_seen_at": "2020-01-01T00:00:00Z"})
    try:
        asyncio.run(bridge.dispatch(device_id=offline["id"], action="open_app", params={"app_name": "X"},
                                    requested_by="admin", timeout=1))
        check("offline device reported honestly", False)
    except DesktopError as e:
        check("offline device reported honestly", "not connected" in str(e).lower(), str(e))
    check("the command is not left hanging as queued",
          state.db.fetchone("SELECT status FROM desktop_commands WHERE device_id=? ORDER BY created_at DESC",
                            (offline["id"],))["status"] == "timeout")

    print("\n3. the runner's own endpoints (the wire the PC speaks)")
    tok = c.post("/api/auth/tokens", json={"name": "pc", "actor": "mark-liii-windows", "role": "operator"},
                 headers=H).json()["secret"]
    T = {"X-Jarvis-Token": tok}
    reg = c.post("/v1/desktop/register", headers=T, json={
        "name": "Werkstatt-PC", "platform": "Windows 11",
        "actions": [{"name": "open_app", "description": "opens an app"}]})
    check("register returns a device id and a poll hint", reg.status_code == 201 and reg.json()["device_id"],
          reg.text)
    dev_id = reg.json()["device_id"]
    check("registering again keeps the same device",
          c.post("/v1/desktop/register", headers=T,
                 json={"name": "Werkstatt-PC", "device_id": dev_id, "actions": []}).json()["device_id"] == dev_id)
    check("a poll with nothing queued returns no command",
          c.get(f"/v1/desktop/poll?device_id={dev_id}&wait=1", headers=T).json()["command"] is None)
    check("an unknown device is told to register again",
          c.get("/v1/desktop/poll?device_id=nope&wait=1", headers=T).status_code == 404)
    anon = TestClient(app)
    check("without any credentials the wire is closed",
          anon.get(f"/v1/desktop/poll?device_id={dev_id}&wait=1").status_code == 401)
    check("and a wrong token too",
          anon.get(f"/v1/desktop/poll?device_id={dev_id}&wait=1",
                   headers={"X-Jarvis-Token": "jcc_wrong"}).status_code == 401)

    # queue one, then let the "PC" pick it up over HTTP and answer over HTTP
    state.db.insert("desktop_commands", {
        "id": "dcmd_test1", "device_id": dev_id, "action": "open_app",
        "params": json.dumps({"app_name": "Chrome"}), "status": "queued",
        "created_at": "2026-09-18T06:00:00Z", "dispatched_at": None, "finished_at": None,
        "result": "", "error": "", "requested_by": "admin", "agent_id": "master",
        "task_id": None, "run_id": None})
    polled = c.get(f"/v1/desktop/poll?device_id={dev_id}&wait=2", headers=T).json()["command"]
    check("the queued command is handed over", polled and polled["action"] == "open_app"
          and polled["params"] == {"app_name": "Chrome"}, polled)
    check("result posts back", c.post("/v1/desktop/result", headers=T,
                                      json={"command_id": "dcmd_test1", "ok": True,
                                            "result": "Chrome opened"}).status_code == 200)
    check("and is recorded as done", bridge.command("dcmd_test1")["status"] == "done")
    check("the dashboard sees the devices",
          len(c.get("/api/desktop/devices").json()["devices"]) >= 3)

    print("\n4. JARVIS drives the PC through tools, and the risky one is gated")
    fake = ScriptedProvider()
    state.runtime.provider = fake
    state.runtime.mode = "local"
    conv = c.post("/api/chat/conversations", json={"title": "PC"}, headers=H).json()["conversation"]["id"]

    def answer_as_pc(device_id: str, reply: str = "done", tries: int = 200) -> dict | None:
        for _ in range(tries):
            cmd = bridge.next_for(device_id)
            if cmd:
                bridge.complete(cmd["id"], ok=True, result=reply)
                return cmd
            time.sleep(0.02)
        return None

    holder: dict = {}
    t = threading.Thread(target=lambda: holder.update(cmd=answer_as_pc(device["id"], "Excel is open")),
                         daemon=True)
    t.start()
    fake.turns = [tool_turn("desktop.open_app", {"app": "Excel", "device": "Buero-PC"}),
                  text_turn("Excel ist offen.")]
    with c.stream("POST", f"/api/chat/conversations/{conv}/messages",
                  json={"content": "mach mir Excel auf"}, headers=H) as r:
        evs = read_sse(r)
    t.join(timeout=5)
    check("the agent's command reached the PC", holder.get("cmd") is not None
          and holder["cmd"]["params"] == {"app_name": "Excel"}, holder.get("cmd"))
    results = [e["data"] for e in evs if e.get("type") == "chat.tool_result"]
    check("the tool reported success back into the chat",
          results and results[0]["ok"] and "Excel" in results[0]["output"], results[:1])

    tools = {t["name"]: t for t in c.get("/api/tools").json()["tools"]}
    check("desktop.run asks for approval by default", tools["desktop.run"]["requires_approval"])
    check("opening an app does not", not tools["desktop.open_app"]["requires_approval"])
    check("both are only for operators and above",
          tools["desktop.run"]["permissions"] == ["operator"])

    print("\n5. teach mode records what really happened")
    rec = c.post("/api/teach/recordings", json={"title": "Angebot erstellen", "goal": "Angebot als PDF",
                                                "conversation_id": conv}, headers=H)
    check("recording starts", rec.status_code == 201, rec.text)
    rec_id = rec.json()["recording"]["id"]
    check("only one at a time", c.post("/api/teach/recordings", json={"title": "zweite"},
                                       headers=H).status_code == 409)

    fake.turns = [tool_turn("document.create", {"title": "Angebot Meier", "content": "Position 1: Trockenbau"}),
                  text_turn("Angebot liegt im Workspace.")]
    with c.stream("POST", f"/api/chat/conversations/{conv}/messages",
                  json={"content": "Schreib ein Angebot für Herrn Meier über Trockenbau"}, headers=H) as r:
        read_sse(r)
    c.post(f"/api/teach/recordings/{rec_id}/note", json={"text": "Immer die Anfahrt mit einrechnen."},
           headers=H)
    events = c.get(f"/api/teach/recordings/{rec_id}").json()["recording"]["events"]
    kinds = [e["kind"] for e in events]
    check("what the user said was recorded", "said" in kinds, kinds)
    check("the tool that ran was recorded with its arguments",
          any(e["kind"] == "tool" and e["tool"] == "document.create"
              and e["params"].get("title") == "Angebot Meier" for e in events), events)
    check("the note was recorded", any(e["kind"] == "note" for e in events))
    transcript = c.get(f"/api/teach/recordings/{rec_id}").json()["transcript"]
    check("the transcript reads as a trace", "document.create" in transcript and "Anfahrt" in transcript,
          transcript[:200])

    print("\n6. distilling it into a procedure and a specialist")
    c.post(f"/api/teach/recordings/{rec_id}/stop", headers=H)
    fake.turns = [text_turn(json.dumps({
        "name": "Angebot erstellen",
        "description": "Erstellt ein Angebot als Dokument im Workspace.",
        "goal": "Ein fertiges Angebot für einen Kunden liegt im Workspace.",
        "inputs": [{"name": "kunde", "description": "Name des Kunden"}],
        "steps": [{"text": "Leistungen mit dem Kunden {kunde} klären", "tool": ""},
                  {"text": "Angebot als Dokument schreiben", "tool": "document.create"},
                  {"text": "Anfahrt einrechnen", "tool": ""},
                  {"text": "Per Mail schicken", "tool": "email.send"},
                  {"text": "Nicht existierendes Tool", "tool": "erfundenes.tool"}],
        "tools": ["document.create", "erfundenes.tool", "memory.remember"],
        "confidence": "medium", "notes": "Anfahrt nicht vergessen.",
        "agent": {"name": "Angebots-Agent", "role": "Erstellt Angebote für Reyes Service.",
                  "instructions": "Arbeite die Schritte der Reihe nach ab.",
                  "capabilities": ["Angebote", "Kalkulation"]}}, ensure_ascii=False))]
    out = c.post(f"/api/teach/recordings/{rec_id}/distill", json={"create_agent": True}, headers=H)
    check("distillation succeeds", out.status_code == 201, out.text)
    proc = out.json()["procedure"]
    agent = out.json()["agent"]
    check("procedure has the steps", len(proc["steps"]) == 5 and proc["name"] == "Angebot erstellen", proc)
    check("invented tools are dropped, not stored as a promise",
          "erfundenes.tool" not in proc["tools"] and "erfundenes.tool" in out.json()["dropped_tools"], proc["tools"])
    check("a step referencing an invented tool loses the reference",
          all(s["tool"] != "erfundenes.tool" for s in proc["steps"]), proc["steps"])
    check("a real tool that merely lacks credentials is kept, not dropped",
          any(s["tool"] == "email.send" for s in proc["steps"]), proc["steps"])
    check("and the procedure says it still needs setting up",
          "email.send" in proc["meta"]["needs_setup"], proc["meta"])
    check("the briefing warns about it",
          "not set up yet" in c.get(f"/api/teach/procedures/{proc['id']}").json()["briefing"])
    check("placeholders survive", proc["meta"]["inputs"][0]["name"] == "kunde", proc["meta"])
    check("a specialist was created, named after itself", agent and agent["id"] == "angebots-agent", agent)
    check("the specialist got the procedure's tools plus the basics",
          "document.create" in agent["tools"] and "task.*" in agent["tools"], agent["tools"])
    check("it shows up in the roster", any(a["id"] == agent["id"] and a["source"] == "learned"
                                           for a in c.get("/api/agents").json()["agents"]))
    briefing = c.get(f"/api/teach/procedures/{proc['id']}").json()["briefing"]
    check("the briefing lists the steps in order and names the placeholder",
          "1. Leistungen" in briefing and "{kunde}" in briefing, briefing[:200])
    check("the lesson is in memory too",
          "Angebot erstellen" in json.dumps(c.get("/v1/memory/search?q=Angebot", headers=T).json()))

    print("\n7. running what was learned")
    fake.turns = [text_turn("Angebot ist fertig.")]
    run = c.post(f"/api/teach/procedures/{proc['id']}/run", json={"inputs": {"kunde": "Schmidt"}}, headers=H)
    check("running creates a task for the specialist", run.status_code == 202
          and run.json()["task"]["assigned_agent"] == agent["id"], run.text)
    check("the run values are handed to it", "Schmidt" in run.json()["task"]["description"])
    check("the procedure counts its runs",
          c.get(f"/api/teach/procedures/{proc['id']}").json()["procedure"]["runs"] == 1)

    print("\n8. a schedule makes it proactive")
    c.patch(f"/api/teach/procedures/{proc['id']}",
            json={"trigger": {"type": "schedule", "every_seconds": 3600}}, headers=H)
    jobs = {j["id"]: j for j in c.get("/api/automations/jobs").json()["jobs"]}
    check("a scheduler job appears for it", f"procedure:{proc['id']}" in jobs, list(jobs)[-5:])
    check("with the requested interval", jobs[f"procedure:{proc['id']}"]["interval_seconds"] == 3600)
    c.patch(f"/api/teach/procedures/{proc['id']}", json={"trigger": {"type": "manual"}}, headers=H)
    check("removing the schedule removes the job",
          f"procedure:{proc['id']}" not in {j["id"] for j in c.get("/api/automations/jobs").json()["jobs"]})

    print("\n9. thin recordings are refused rather than invented")
    thin = c.post("/api/teach/recordings", json={"title": "fast nichts"}, headers=H).json()["recording"]["id"]
    c.post(f"/api/teach/recordings/{thin}/stop", headers=H)
    r = c.post(f"/api/teach/recordings/{thin}/distill", json={}, headers=H)
    check("too little to learn from is said plainly", r.status_code == 409 and "recording" in r.text.lower(),
          r.text)

    print("\n10. viewers cannot drive the PC or teach")
    c.post("/api/auth/users", json={"username": "gast", "password": "gastpass1", "role": "viewer"}, headers=H)
    v = TestClient(app)
    vh = {"X-CSRF-Token": v.post("/api/auth/login",
                                 json={"username": "gast", "password": "gastpass1"}).json()["csrf_token"]}
    check("a viewer cannot send a desktop command",
          v.post(f"/api/desktop/devices/{device['id']}/command",
                 json={"action": "open_app", "params": {}}, headers=vh).status_code == 403)
    check("a viewer cannot start a recording",
          v.post("/api/teach/recordings", json={"title": "x"}, headers=vh).status_code == 403)
    check("but may look at what was learned", v.get("/api/teach/procedures").status_code == 200)

    print("\n8. Selbsterweiterung — schreiben ist harmlos, freigeben nicht")
    # Der Wert steckt in den Ablehnungen, nicht im Erfolgsfall: ein Agent, der
    # seine eigenen Bremsen überschreiben kann, hat keine Bremsen mehr.
    import asyncio as _aio  # noqa: E402
    from command_center.backend.services.selfext import (  # noqa: E402
        CRITICAL_FILES, SelfExtError, SelfExtension,
    )

    sx = state.services["selfext"]
    existing = {t.name for t in state.tools.all()}

    GOOD = textwrap.dedent("""
        TOOL = {"name": "wetter.heute", "description": "Sagt das Wetter.",
                "input_schema": {"type": "object", "properties": {"ort": {"type": "string"}}},
                "risk": "low"}


        async def run(args):
            return f"In {args.get('ort', 'hier')} ist es sonnig."


        def selftest():
            return "ok"
        """)

    res = sx.write("wetter.heute", GOOD, author="admin", existing_tools=existing)
    check("ein neues Werkzeug lässt sich schreiben", res["status"] == "draft", res)
    check("aber es ist noch nicht in der Registry", state.tools.get("wetter.heute") is None)

    for bad_name, why in (("filesystem.delete", "Kernbereich"), ("../../etc/passwd", "Pfad"),
                          ("Wetter.Heute", "Großbuchstaben"), ("self.write", "Kernbereich")):
        try:
            sx.write(bad_name, GOOD, author="admin", existing_tools=existing)
            check(f"Name abgelehnt: {why}", False, bad_name)
        except SelfExtError as e:
            check(f"Name abgelehnt: {why}", True, str(e)[:60])

    try:
        sx.write("kaputt.test", "def run(  :", author="admin", existing_tools=existing)
        check("ungültiges Python wird gar nicht erst geschrieben", False)
    except SelfExtError as e:
        check("ungültiges Python wird gar nicht erst geschrieben", "Python" in str(e), str(e)[:60])

    checked = _aio.run(sx.check("wetter.heute"))
    check("die Prüfung läuft und findet den Selbsttest", checked["selftest"] == "ok", checked)
    check("und merkt sich, was das Werkzeug deklariert", checked["declares"]["risk"] == "low", checked)

    try:
        sx.activate("wetter.heute", state.tools, actor="admin")
        check("erst nach der Prüfung freigebbar", True)
    except SelfExtError as e:
        check("erst nach der Prüfung freigebbar", False, str(e))
    spec = state.tools.get("wetter.heute")
    check("jetzt ist es ein echtes Werkzeug", spec is not None and spec.available and spec.source == "self")
    out = _aio.run(spec.handler(None, {"ort": "Köln"}))
    check("und es tut wirklich etwas", "Köln" in str(out) and "sonnig" in str(out), out)

    # Eine geänderte Quelle verliert ihre Freigabe — sonst wäre sie eine Freigabe
    # für Code, den niemand gesehen hat.
    sx.write("wetter.heute", GOOD.replace("sonnig", "regnerisch"), author="admin", existing_tools=existing)
    try:
        sx.activate("wetter.heute", state.tools, actor="admin")
        check("geänderte Quelle verliert die Freigabe", False, "wurde trotzdem aktiviert")
    except SelfExtError as e:
        check("geänderte Quelle verliert die Freigabe", "geprüft" in str(e) or "geändert" in str(e), str(e)[:70])

    print("\n9. Zugriff auf den eigenen Quelltext — lesen frei, ändern nur als Vorschlag")
    tree = sx.source_tree("core")
    check("er findet seinen eigenen Quelltext", any(f["file"].endswith("prompt.txt") for f in tree), len(tree))
    check("und weiß, welche Dateien heikel sind",
          any(f["critical"] for f in sx.source_tree("command_center/backend")))
    src = sx.source("command_center/backend/auth.py")
    check("er darf auch die heiklen lesen", src["critical"] is True and "class AuthService" in src["source"])

    # Das Frontend gehört seit der Erweiterung dazu — er soll das Dashboard
    # umbauen können. Draußen bleibt, was nicht sein Quelltext ist:
    # Betriebssystem, Compose-Datei, Einstiegsskripte im Wurzelverzeichnis.
    for outside in ("../../etc/passwd", "/etc/passwd", "docker-compose.command-center.yml",
                    "main.py", ".gitignore"):
        try:
            sx.source(outside)
            check(f"außerhalb des Quelltexts abgelehnt: {outside}", False)
        except SelfExtError:
            check(f"außerhalb des Quelltexts abgelehnt: {outside}", True)

    before = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
    prop = sx.propose("core/prompt.txt", before + "\n# von JARVIS vorgeschlagen\n",
                      "Eine Zeile zum Ausprobieren", author="admin")
    check("ein Vorschlag entsteht mit Diff", prop["added"] >= 1 and "prompt.txt" in prop["diff"], prop["added"])
    check("und ändert am laufenden Stand nichts",
          (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8") == before)
    check("ein Vorschlag an einer heiklen Datei ist als solcher markiert",
          sx.propose(CRITICAL_FILES[0], sx.source(CRITICAL_FILES[0])["source"] + "\n# probe\n",
                     "Probe", author="admin")["critical"] is True)

    applied = sx.apply(prop["proposal_id"], actor="admin")
    check("erst self.apply schreibt wirklich",
          (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8").endswith("vorgeschlagen\n"), applied["file"])
    check("und legt vorher eine Sicherung an", Path(applied["backup"]).exists())
    sx.revert(prop["proposal_id"], actor="admin")
    check("und es lässt sich zurückdrehen",
          (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8") == before)


    print("\n9b. jeder Schritt am eigenen Code fragt um Genehmigung")
    # Ausdrücklicher Wunsch des Nutzers. Nur Lesen und Prüfen laufen frei —
    # ein Gatter vor einem Lesevorgang wäre Lärm, der die echten Freigaben
    # entwertet.
    gate = {t.name: t.needs_approval(state.tools.approval_threshold)
            for t in state.tools.all() if t.name.startswith("self.")}
    must_ask = ["self.write", "self.propose", "self.activate", "self.apply",
                "self.disable", "self.revert"]
    may_pass = ["self.tools", "self.read", "self.tree", "self.source",
                "self.check", "self.review", "self.proposals", "self.improve"]
    for name in must_ask:
        check(f"{name} fragt", gate.get(name) is True, gate.get(name))
    for name in may_pass:
        check(f"{name} liest nur und fragt nicht", gate.get(name) is False, gate.get(name))

    # Eine geschriebene Änderung ist noch keine wirksame. Die zwei Werkzeuge,
    # die das ändern, müssen fragen — und ehrlich sagen, wenn dieses Image sie
    # gar nicht ausführen kann.
    for name in ("self.rebuild", "self.restart"):
        check(f"{name} fragt", gate.get(name) is True, gate.get(name))
    check("self.can_rebuild liest nur und fragt nicht", gate.get("self.can_rebuild") is False)
    can, why = sx.can_rebuild()
    check("und sagt beim Namen, was fehlt, wenn es nicht geht", can or bool(why), (can, why))

    # Sich außerhalb eines Containers selbst zu beenden wäre ein Weg ohne
    # Rückweg — dort muss es sich weigern.
    if not Path("/.dockerenv").exists():
        try:
            sx.restart_server(actor="test")
            check("außerhalb eines Containers kein Selbstmord", False, "hat sich beendet")
        except SelfExtError as e:
            check("außerhalb eines Containers kein Selbstmord",
                  "Container" in str(e), str(e)[:60])

    fe = sx.source_tree("command_center/frontend/src")
    check("das Dashboard gehört zu seinem Quelltext", len(fe) > 40, len(fe))
    check("und er weiß, dass es einen Build braucht", all(f["needs_build"] for f in fe))
    check("der Server dagegen nicht",
          not any(f["needs_build"] for f in sx.source_tree("core")))

print("\n11. learned specialists come back after a restart")
state.db.close()
reset_settings()
app2 = create_app()
state2 = app2.state.jarvis
restored = state2.agents.get("angebots-agent")
check("the agent is in the roster again", restored is not None and restored.source == "learned")
check("with its instructions and tools", restored and "Schritte" in restored.instructions
      and "document.create" in restored.tools)
check("and the procedure is still there", any(p["name"] == "Angebot erstellen"
                                              for p in state2.services["teaching"].procedures()))
state2.db.close()

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
