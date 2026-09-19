"""
Erweiterungen: MCP-Server, Fähigkeiten, Selbstauskunft.

Der MCP-Teil läuft gegen einen echten kleinen MCP-Server auf 127.0.0.1 — kein
Netz nach außen, aber auch keine Attrappe des eigenen Clients: Es wird wirklich
JSON-RPC gesprochen, initialisiert, aufgelistet und aufgerufen.

Geprüft wird das, worauf es ankommt:
* Ein angebundener Server bringt seine Werkzeuge ins Verzeichnis.
* Diese Werkzeuge gehen durch dasselbe Genehmigungstor wie alles andere.
* Sie tragen Namen, die JEDER Anbieter akzeptiert — das ist die Bedingung
  dafür, dass sie mit Anthropic, OpenAI und Gemini gleichermaßen laufen.
* Fähigkeiten stehen im Systemtext nur mit Namen und Zweck und werden erst auf
  Abruf geöffnet.
* Er kann nachsehen, was in ihm steckt.

Run:  python tests/test_extensions.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="jarvis-ext-test-")
os.environ.update({
    "JARVIS_CC_DATA_DIR": _tmp, "JARVIS_CC_ADMIN_USER": "admin",
    "JARVIS_CC_ADMIN_PASSWORD": "adminpass123", "JARVIS_CC_SECURE_COOKIES": "false",
    "JARVIS_MASTER_AGENT_MODE": "auto",
})
for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LOCAL_LLM_URL",
          "JARVIS_GATEWAY_TOKEN", "N8N_BASE_URL"):
    os.environ.pop(k, None)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from command_center.backend.app import create_app  # noqa: E402
from command_center.backend.config import reset_settings  # noqa: E402

fails: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  :: {detail}"))
    if not cond:
        fails.append(label)


# ── ein echter kleiner MCP-Server ────────────────────────────────────────
# Er kann genau ein Werkzeug. Mehr braucht es nicht, um zu zeigen, dass der
# Weg vom fremden Server bis in den Werkzeugaufruf durchgehend funktioniert.
mcp_app = FastAPI()
seen: list[str] = []


@mcp_app.post("/mcp")
async def mcp_endpoint(request: Request):
    body = await request.json()
    method = body.get("method")
    seen.append(method)
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "test-mcp", "version": "0.1"}}}
    if method == "notifications/initialized":
        return {}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": {"tools": [{
            "name": "echo",
            "description": "Gibt zurück, was man hineinsteckt.",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"]},
        }]}}
    if method == "tools/call":
        params = body.get("params") or {}
        text = (params.get("arguments") or {}).get("text", "")
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "result": {"content": [{"type": "text", "text": f"echo: {text}"}]}}
    return {"jsonrpc": "2.0", "id": body.get("id"),
            "error": {"code": -32601, "message": f"unknown method {method}"}}


def start_mcp() -> str:
    import uvicorn
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(mcp_app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    return f"http://127.0.0.1:{port}/mcp"


MCP_URL = start_mcp()
print(f"\nTest-MCP-Server: {MCP_URL}")

reset_settings()
app = create_app()
state = app.state.jarvis

with TestClient(app) as c:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    H = {"X-CSRF-Token": r.json()["csrf_token"]}

    print("\n1. Fähigkeiten: anlegen, aufschlagen, abschalten")
    skill_md = ("---\nname: angebot-schreiben\ndescription: Wie ein Angebot aufgebaut wird\n---\n\n"
                "# Angebot\n\n1. Anrede\n2. Positionen\n")
    r = c.post("/api/extensions/skills", json={"content": skill_md}, headers=H)
    check("Fähigkeit angelegt", r.status_code == 201, r.text)
    saved = r.json()["skill"]
    check("Name und Zweck kommen aus dem Kopf der Datei",
          saved["name"] == "angebot-schreiben" and "Angebot" in saved["description"], saved)
    check("der Kopf steht nicht mehr im Text", not saved["content"].startswith("---"), saved["content"][:20])

    lib = state.services["skills"]
    cat = lib.catalogue()
    check("der Katalog nennt Name und Zweck", "angebot-schreiben" in cat and "Angebot" in cat, cat)
    check("aber NICHT den vollen Text", "1. Anrede" not in cat, cat)

    opened = lib.open("angebot-schreiben")
    check("aufgeschlagen kommt der volle Text", "1. Anrede" in opened["content"])
    check("die Benutzung wird gezählt", lib.get("angebot-schreiben")["uses"] == 1)

    r = c.patch("/api/extensions/skills/angebot-schreiben?enabled=false", headers=H)
    check("abschalten geht", r.status_code == 200 and not r.json()["skill"]["enabled"])
    check("abgeschaltet steht sie nicht mehr im Katalog", "angebot-schreiben" not in lib.catalogue())
    c.patch("/api/extensions/skills/angebot-schreiben?enabled=true", headers=H)

    r = c.post("/api/extensions/skills", json={"content": "nur text, kein name"}, headers=H)
    check("ohne Namen wird abgelehnt statt geraten", r.status_code == 400, r.text)

    print("\n2. MCP: anbinden, Werkzeuge übernehmen, aufrufen")
    before = len(state.tools.all())
    r = c.post("/api/extensions/servers", headers=H,
               json={"name": "Testserver", "url": MCP_URL, "requires_approval": False})
    check("Server angelegt", r.status_code == 201, r.text)
    server = r.json()["server"]
    check("er meldet sich als erreichbar", server["status"] == "healthy", server)
    check("sein Werkzeug wurde gefunden", server["tool_count"] == 1 and server["tools"] == ["echo"], server)
    check("Handschlag und Auflistung sind wirklich gelaufen",
          "initialize" in seen and "tools/list" in seen, seen)

    tool = state.tools.get("mcp.testserver.echo")
    check("das Werkzeug steht im Verzeichnis", tool is not None)
    check("das Verzeichnis ist gewachsen", len(state.tools.all()) == before + 1)
    check("das Eingabeschema kommt vom Server",
          tool and tool.input_schema.get("properties", {}).get("text") is not None, tool.input_schema if tool else None)

    # Das ist die Bedingung dafür, dass es bei JEDEM Anbieter läuft: Punkte im
    # Namen weist Anthropic ab, OpenAI und Gemini ebenso. Die Umschreibung an
    # der Leitung muss greifen — sonst nützt der schönste Server nichts.
    from command_center.backend.ai.base import ToolNameMap, wire_name
    names = ToolNameMap(t.name for t in state.tools.all())
    wire = names.wire("mcp.testserver.echo")
    check("der Name geht in einer Form über die Leitung, die jeder Anbieter annimmt",
          wire == wire_name(wire) and "." not in wire, wire)
    check("und kommt unverändert zurück", names.real(wire) == "mcp.testserver.echo")

    from command_center.backend.auth import Principal
    from command_center.backend.orchestrator.tool_registry import ToolContext
    principal = Principal(kind="user", id="u", name="admin", role="admin", actor="admin")
    ctx = ToolContext(state=state, principal=principal, agent_id="master")
    out = asyncio.run(tool.handler(ctx, {"text": "hallo"}))
    check("der Aufruf geht wirklich zum Server und zurück",
          out.get("ok") and "echo: hallo" in out.get("text", ""), out)

    print("\n3. MCP läuft durch dasselbe Tor wie alles andere")
    r = c.post("/api/extensions/servers", headers=H,
               json={"name": "Streng", "url": MCP_URL, "requires_approval": True})
    check("zweiter Server angelegt", r.status_code == 201, r.text)
    strict = state.tools.get("mcp.streng.echo")
    check("mit Genehmigungspflicht ist sie auch gesetzt", strict is not None and strict.needs_approval())
    check("ohne Genehmigungspflicht bleibt sie aus", tool is not None and not tool.needs_approval())

    r = c.delete(f"/api/extensions/servers/{r.json()['server']['id']}", headers=H)
    check("entfernen geht", r.status_code == 200)
    check("und nimmt seine Werkzeuge mit", state.tools.get("mcp.streng.echo") is None)

    print("\n4. abgeschalteter Server bringt keine Werkzeuge mit")
    r = c.patch(f"/api/extensions/servers/{server['id']}", json={"enabled": False}, headers=H)
    check("abschalten geht", r.status_code == 200, r.text)
    check("seine Werkzeuge sind weg", state.tools.get("mcp.testserver.echo") is None)
    c.patch(f"/api/extensions/servers/{server['id']}", json={"enabled": True}, headers=H)
    check("und wieder da, wenn man ihn einschaltet", state.tools.get("mcp.testserver.echo") is not None)

    print("\n5. ein Server, der nicht antwortet, wird als solcher benannt")
    r = c.post("/api/extensions/servers", headers=H,
               json={"name": "Tot", "url": "http://127.0.0.1:9/mcp"})
    check("er wird eingetragen, aber nicht schöngeredet",
          r.status_code == 201 and r.json()["server"]["status"] == "offline", r.text)
    check("mit Grund", len(r.json()["server"]["detail"]) > 10, r.json()["server"]["detail"])

    print("\n6. er weiß, was in ihm steckt")
    r = c.get("/api/inventory")
    check("Selbstauskunft antwortet", r.status_code == 200, r.text)
    inv = r.json()
    check("sie zählt Werkzeuge", inv["tools"]["usable"] > 30, inv["tools"]["usable"])
    check("sie kennt die MCP-Server", any(s["name"] == "Testserver" for s in inv["mcp_servers"]))
    check("sie kennt die Fähigkeiten", any(s["name"] == "angebot-schreiben" for s in inv["skills"]))
    check("sie sagt auch, was NICHT geht", isinstance(inv["tools"]["unavailable"], list))

    tool_inv = state.tools.get("system.inventory")
    check("und er kommt selbst dran", tool_inv is not None and tool_inv.handler is not None)
    mine = asyncio.run(tool_inv.handler(ctx, {}))
    check("das Werkzeug liefert denselben Bestand", mine["tools"]["usable"] == inv["tools"]["usable"])

    print("\n7. der Aufbau fürs Dashboard ist gemessen, nicht gemalt")
    r = c.get("/api/architecture")
    check("Aufbau antwortet", r.status_code == 200, r.text)
    arch = r.json()
    ids = [l["id"] for l in arch["layers"]]
    check("die Ebenen stehen in der Reihenfolge des Weges",
          ids[:5] == ["user", "input", "device", "gateway", "master"], ids)
    core = next(l for l in arch["layers"] if l["id"] == "core")
    check("Gedächtnis, Rechteschicht und Ereignisbus sind da",
          [n["title"] for n in core["nodes"]] == ["GEDÄCHTNIS", "RECHTESCHICHT", "EREIGNISBUS"],
          [n["title"] for n in core["nodes"]])
    device = next(l for l in arch["layers"] if l["id"] == "device")
    check("ohne gekoppeltes Gerät ist die Ebene nicht grün", device["status"] != "ok", device)
    tools_layer = next(l for l in arch["layers"] if l["id"] == "tools")
    check("die Werkzeugebene zählt wirklich", str(inv["tools"]["usable"]) in tools_layer["detail"],
          tools_layer["detail"])

    print("\n8. Quelltext holen: was erlaubt ist und was nicht")
    from command_center.backend.services.repos import RepoError, RepoService, check_url, git_available

    for bad, why in [("git@github.com:x/y.git", "SSH"),
                     ("file:///etc/passwd", "lokale Datei"),
                     ("http://127.0.0.1:5678/repo.git", "eigener Rechner"),
                     ("http://192.168.1.10/x.git", "eigenes Netz"),
                     ("https://user:geheim@github.com/x/y.git", "Zugangsdaten in der Adresse")]:
        try:
            check_url(bad)
            check(f"abgelehnt: {why}", False, bad)
        except RepoError:
            check(f"abgelehnt: {why}", True)
        except Exception as e:  # noqa: BLE001
            check(f"abgelehnt: {why}", False, repr(e))

    check("eine öffentliche Adresse geht durch",
          check_url("https://github.com/anthropics/claude-code") .startswith("https://github.com/"))

    clone_tool = state.tools.get("repo.clone")
    check("repo.clone ist da", clone_tool is not None)
    check("und fragt vor dem Klonen nach", clone_tool is not None and clone_tool.needs_approval())
    check("verfügbar genau dann, wenn git da ist",
          clone_tool is not None and clone_tool.available == git_available(),
          f"available={clone_tool.available if clone_tool else None}, git={git_available()}")
    if clone_tool is not None and not clone_tool.available:
        check("und sagt sonst, was fehlt", "git" in clone_tool.reason, clone_tool.reason)

    dl = state.tools.get("web.download")
    check("web.download fragt auch nach", dl is not None and dl.needs_approval())

    svc = RepoService(state.services["files"], state.log)
    try:
        asyncio.run(svc.download("http://127.0.0.1:8080/etwas.json", "etwas.json"))
        check("herunterladen aus dem eigenen Netz wird verweigert", False)
    except RepoError as e:
        check("herunterladen aus dem eigenen Netz wird verweigert", "Netz dieses Servers" in str(e), str(e))

    try:
        asyncio.run(svc.clone("https://github.com/x/y", "../raus"))
        check("Ordnernamen mit .. werden abgelehnt", False)
    except RepoError:
        check("Ordnernamen mit .. werden abgelehnt", True)

    check("nichts davon liegt außerhalb des Arbeitsbereichs",
          str(svc.root).startswith(str(state.services["files"].root)), str(svc.root))

    print("\n9. Anweisungen und Gedächtnis: eintragen, wirken, löschen")
    r = c.get("/api/memory")
    check("Gedächtnis antwortet", r.status_code == 200, r.text)
    check("leer heißt leer", r.json()["instructions"] == "")

    regel = "Sprich mich mit Chef an. Angebote immer mit 14 Tagen Bindefrist."
    r = c.put("/api/memory/instructions", json={"text": regel}, headers=H)
    check("Anweisung gespeichert", r.status_code == 200, r.text)

    # Der Punkt der Übung: Sie muss im Systemtext landen, sonst ist das Feld
    # Dekoration. Und zwar im Chat UND auf der Sprachleitung.
    master = state.agents.get(state.agents.master_id())
    prompt = state.runtime._system_prompt(master, state.tools.available())
    check("die Anweisung steht im Systemtext", regel in prompt)
    check("sie ist als Anweisung des Nutzers gekennzeichnet", "STEHENDE ANWEISUNGEN" in prompt)
    check("sie gilt auch auf der Sprachleitung", regel in state.runtime.live_instructions())

    r = c.post("/api/memory/facts", json={"text": "Der Bauhof macht Mittag von 12 bis 13 Uhr."}, headers=H)
    check("Merksatz angelegt", r.status_code == 201, r.text)
    fact_id = r.json()["fact"]["id"]
    r = c.get("/api/memory")
    check("er steht in der Liste", any(f["id"] == fact_id for f in r.json()["facts"]))

    r = c.delete(f"/api/memory/facts/{fact_id}", headers=H)
    check("einzeln löschen geht", r.status_code == 200, r.text)
    check("und er ist weg", not any(f["id"] == fact_id for f in c.get("/api/memory").json()["facts"]))
    check("ein zweites Mal löschen gibt 404",
          c.delete(f"/api/memory/facts/{fact_id}", headers=H).status_code == 404)

    c.post("/api/memory/facts", json={"text": "eins"}, headers=H)
    c.post("/api/memory/facts", json={"text": "zwei"}, headers=H)
    r = c.delete("/api/memory/facts", headers=H)
    check("alles löschen ohne Bestätigung passiert NICHT", r.status_code == 400, r.text)
    check("und es ist auch wirklich noch da", c.get("/api/memory").json()["total"] >= 2)
    r = c.delete("/api/memory/facts?confirm=ALLES", headers=H)
    check("mit Bestätigung wird geleert", r.status_code == 200 and r.json()["removed"] >= 2, r.text)

    r = c.put("/api/memory/instructions", json={"text": ""}, headers=H)
    check("leeren geht auch", r.status_code == 200)
    prompt = state.runtime._system_prompt(master, state.tools.available())
    check("dann steht die Anweisung auch nicht mehr im Systemtext", "STEHENDE ANWEISUNGEN" not in prompt)

    print("\n10. Aufgaben lassen sich löschen")
    r = c.post("/api/tasks", json={"title": "Wegwerfaufgabe", "start": False}, headers=H)
    check("Aufgabe angelegt", r.status_code == 201, r.text)
    tid = r.json()["task"]["id"]
    check("sie ist da", c.get(f"/api/tasks/{tid}").status_code == 200)
    r = c.delete(f"/api/tasks/{tid}", headers=H)
    check("gelöscht", r.status_code == 200, r.text)
    check("und wirklich weg", c.get(f"/api/tasks/{tid}").status_code == 404)
    check("noch einmal löschen gibt 404", c.delete(f"/api/tasks/{tid}", headers=H).status_code == 404)

    print("\n11. Kalender: anlegen, sehen, verschieben, absagen")
    r = c.get("/api/calendar?days=30")
    check("Kalender antwortet", r.status_code == 200, r.text)
    cal = r.json()
    check("ohne Google läuft er lokal weiter", cal["available"] and cal["backend"] == "local", cal)
    vorher = len(cal["events"])

    # Ein Datum in Reichweite: Die Liste reicht höchstens 90 Tage weit, und ein
    # Test, der an dieser Grenze scheitert, prüft den Kalender nicht mehr.
    from datetime import date, timedelta
    tag = (date.today() + timedelta(days=10)).isoformat()
    spaeter = (date.today() + timedelta(days=13)).isoformat()

    r = c.post("/api/calendar", headers=H, json={
        "title": "Abnahme Bhimber", "when": tag, "at": "10:00", "duration": 90,
        "location": "Baustelle 3"})
    check("Termin angelegt", r.status_code == 201, r.text)
    check("und ehrlich gesagt, wo er liegt", "lokal" in r.json()["note"].lower()
          or r.json()["backend"] == "local", r.json())

    events = c.get("/api/calendar?days=90").json()["events"]
    mine = [e for e in events if e["title"] == "Abnahme Bhimber"]
    check("er steht im Kalender", len(mine) == 1, len(mine))
    check("mit Uhrzeit und Ort", mine and mine[0]["start"].endswith("T10:00:00")
          and mine[0]["location"] == "Baustelle 3", mine[:1])

    r = c.post("/api/calendar/move", headers=H,
               json={"query": "Abnahme Bhimber", "when": spaeter, "at": "08:30"})
    check("verschieben geht", r.status_code == 200, r.text)
    moved = [e for e in c.get("/api/calendar?days=90").json()["events"] if e["title"] == "Abnahme Bhimber"]
    check("und der Termin liegt wirklich neu",
          moved and moved[0]["start"].startswith(f"{spaeter}T08:30"), moved[:1])

    r = c.delete("/api/calendar?query=Abnahme%20Bhimber", headers=H)
    check("absagen geht", r.status_code == 200, r.text)
    check("und er ist weg",
          len(c.get("/api/calendar?days=90").json()["events"]) == vorher)

    r = c.post("/api/calendar", headers=H, json={"title": "Ohne Uhrzeit", "when": tag})
    check("ohne Uhrzeit wird nicht geraten, sondern nachgefragt", r.status_code == 400, r.text)

    print("\n12. Docker: der Grund steht da, nicht nur „unreachable\"")
    import tempfile as _tf
    from pathlib import Path as _P
    metrics = state.services["metrics"]

    hint = metrics._docker_hint("/tmp/gibt-es-sicher-nicht.sock")
    check("fehlender Socket wird als fehlender Socket benannt",
          "nicht vorhanden" in hint and "docker-compose" in hint, hint)

    plain = _P(_tf.mkdtemp()) / "keine.sock"
    plain.write_text("x")
    check("eine gewöhnliche Datei an der Stelle fällt auf",
          "kein Socket" in metrics._docker_hint(str(plain)), metrics._docker_hint(str(plain)))

    # Ein echter Socket, den wir nicht lesen dürfen: Das ist der Fall, der beim
    # Nutzer auftrat, und der einzige, der eine Gruppen-ID nennen muss.
    import socket as _sock
    sdir = _P(_tf.mkdtemp())
    spath = sdir / "docker.sock"
    srv = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
    srv.bind(str(spath))
    try:
        os.chmod(spath, 0o000)
        hint = metrics._docker_hint(str(spath))
        if os.access(spath, os.R_OK | os.W_OK):
            # Als root ist alles lesbar — dann greift der Fall nicht, und das
            # ehrlich zu sagen ist besser, als einen grünen Haken zu setzen.
            print("       (als root nicht prüfbar: Rechte greifen nicht)")
        else:
            check("fehlende Rechte nennen die Gruppen-ID und die Variable",
                  "JARVIS_CC_DOCKER_GID" in hint and "Gruppe" in hint, hint)
    finally:
        srv.close()

    print("\n13. Hauptgedächtnis: gilt immer, ohne dass er etwas aufrufen muss")
    r = c.post("/api/memory/facts", headers=H,
               json={"text": "Der Firmenwagen ist ein Sprinter, Kennzeichen HH-RS 412."})
    fid = r.json()["fact"]["id"]
    master = state.agents.get(state.agents.master_id())

    prompt = state.runtime._system_prompt(master, state.tools.available())
    check("ein gewöhnlicher Merksatz steht NICHT im Systemtext", "Sprinter" not in prompt)

    r = c.post(f"/api/memory/facts/{fid}/pin", headers=H)
    check("anheften geht", r.status_code == 200, r.text)
    prompt = state.runtime._system_prompt(master, state.tools.available())
    check("angeheftet steht er im Systemtext", "Sprinter" in prompt)
    check("und ist als unumgehbar gekennzeichnet", "HAUPTGEDÄCHTNIS" in prompt)
    check("er gilt auch auf der Sprachleitung", "Sprinter" in state.runtime.live_instructions())

    r = c.post(f"/api/memory/facts/{fid}/pin?pinned=false", headers=H)
    check("wieder herausnehmen geht", r.status_code == 200, r.text)
    check("dann ist er auch aus dem Systemtext raus",
          "Sprinter" not in state.runtime._system_prompt(master, state.tools.available()))

    # Die Grenze ist der Punkt: Was hier steht, kostet bei jeder Anfrage.
    from command_center.backend.modules.memory import MAX_PINNED
    check("das Hauptgedächtnis fasst 30 Sätze", MAX_PINNED == 30, MAX_PINNED)
    ids = []
    for i in range(MAX_PINNED):
        rr = c.post("/api/memory/facts", headers=H, json={"text": f"Kernsatz {i}", "pinned": True})
        check(f"Platz {i + 1} belegt", rr.status_code == 201, rr.text) if i in (0, MAX_PINNED - 1) else None
        ids.append(rr.json()["fact"]["id"])
    r = c.post("/api/memory/facts", headers=H, json={"text": "einer zu viel", "pinned": True})
    check("einer über der Grenze wird abgelehnt statt still zu verdrängen", r.status_code == 400, r.text)
    check("mit einem Grund, der zur Lösung führt", "Hauptgedächtnis" in r.json()["detail"], r.json())

    core_text = state.runtime._system_prompt(master, state.tools.available())
    check(f"alle {MAX_PINNED} stehen im Systemtext",
          core_text.count("Kernsatz") == MAX_PINNED, core_text.count("Kernsatz"))

    # Bearbeiten: der Satz behält seinen Platz und wirkt sofort.
    r = c.patch(f"/api/memory/facts/{ids[0]}", headers=H, json={"text": "Kernsatz 0, aber korrigiert"})
    check("bearbeiten geht", r.status_code == 200, r.text)
    check("der Eintrag bleibt im Hauptgedächtnis", r.json()["fact"]["pinned"] == 1, r.json()["fact"])
    neu_text = state.runtime._system_prompt(master, state.tools.available())
    check("und der neue Wortlaut steht sofort im Systemtext", "aber korrigiert" in neu_text)
    r = c.patch(f"/api/memory/facts/{ids[0]}", headers=H, json={"text": "   "})
    check("ein leerer Satz wird abgelehnt", r.status_code == 400, r.text)

    c.delete("/api/memory/facts?confirm=ALLES", headers=H)
    check("nach dem Leeren ist auch das Hauptgedächtnis leer",
          "HAUPTGEDÄCHTNIS" not in state.runtime._system_prompt(master, state.tools.available()))

    print("\n14. Er sieht das Dashboard — und darf sich nicht selbst freigeben")
    from command_center.backend.auth import Principal as _P
    from command_center.backend.orchestrator.tool_registry import ToolContext as _TC
    pr = _P(kind="user", id=state.auth.list_users()[0]["id"], name="admin", role="admin", actor="admin")
    tc = _TC(state=state, principal=pr, agent_id="master")

    for name in ("approval.list", "notification.list", "conversation.search", "dashboard.open"):
        check(f"{name} gibt es", state.tools.get(name) is not None)

    check("er kann Genehmigungen NICHT erteilen",
          not any(t.name.startswith("approval.") and t.name != "approval.list"
                  for t in state.tools.all()),
          [t.name for t in state.tools.all() if t.name.startswith("approval.")])

    out = asyncio.run(state.tools.get("approval.list").handler(tc, {}))
    check("offene Freigaben sind abfragbar", isinstance(out, (list, str)), out)

    seen_ui = []
    unsub = state.bus.subscribe_sync("ui.open", lambda ev: seen_ui.append(ev)) \
        if hasattr(state.bus, "subscribe_sync") else None
    res = asyncio.run(state.tools.get("dashboard.open").handler(tc, {"path": "server", "reason": "Auslastung"}))
    check("dashboard.open normalisiert den Pfad", "/server" in res, res)
    if unsub:
        check("und schickt ein Ereignis", any(e.get("data", {}).get("path") == "/server" for e in seen_ui))

    print("\n15. Browser: nur was wirklich da ist, und nichts ins eigene Netz")
    from command_center.backend.services.browser import available as br_avail
    ok_br, why_br = br_avail()
    for name in ("browser.open", "browser.read", "browser.click", "browser.type",
                 "browser.screenshot", "browser.close"):
        t = state.tools.get(name)
        check(f"{name} gibt es", t is not None)
        if t is not None:
            check(f"{name} meldet sich nur verfügbar, wenn es das ist", t.available == ok_br,
                  f"available={t.available}, playwright={ok_br}")
    if not ok_br:
        check("und sagt, was fehlt", "Playwright" in why_br, why_br)

    klick = state.tools.get("browser.click")
    tippen = state.tools.get("browser.type")
    check("Klicken fragt vorher nach", klick is not None and klick.needs_approval())
    check("Tippen fragt vorher nach", tippen is not None and tippen.needs_approval())
    check("Lesen fragt nicht", state.tools.get("browser.read").needs_approval() is False)

    from command_center.backend.services.browser import BrowserSession
    sess = BrowserSession(state.services["files"].root)
    out = asyncio.run(state.tools.get("browser.open").handler(tc, {"url": "http://127.0.0.1:8080/"}))
    check("eine Adresse ins eigene Netz wird abgelehnt",
          isinstance(out, tuple) and out[1] is False and "Netz dieses Servers" in out[0], out)
    del sess

    print("\n15b. Wissensspeicher: was zu lang fürs Hauptgedächtnis ist")
    # Der Anlass: Das Hauptgedächtnis lag als Datei im Repository — und war damit
    # für ihn unsichtbar. Er liest keine Datei, die niemand in seinen Systemtext
    # stellt. In die 30 Sätze des Hauptgedächtnisses passt sie auch nicht.
    from command_center.backend.services.knowledge import KnowledgeError, KnowledgeBase

    kb = state.services["knowledge"]
    check("beim Start ist das mitgelieferte Hauptgedächtnis schon da",
          kb.get("jarvis-hauptgedaechtnis") is not None)

    lang = "# Aufbau\n\n" + ("Eine Zeile ueber diese Anlage.\n" * 400)
    doc = kb.save(title="Testwissen", content=lang, summary="Wofuer das da ist", actor="test")
    check("ein langes Dokument laesst sich ablegen", doc["lines"] > 400, doc)

    kat = kb.catalogue()
    check("im Systemtext steht der Titel", "Testwissen" in kat, kat[:200])
    check("und der Zweck", "Wofuer das da ist" in kat, kat[:200])
    check("aber NICHT der Inhalt — sonst waere nichts gewonnen",
          "Eine Zeile ueber diese Anlage" not in kat)
    check("der Katalog bleibt kurz, auch bei langen Dokumenten", len(kat) < 1500, len(kat))

    voll = kb.open("testwissen")
    check("aufgeschlagen kommt der ganze Text", "Eine Zeile ueber diese Anlage" in voll["content"])
    check("und das Aufschlagen wird gezaehlt",
          next(d for d in kb.all() if d["slug"] == "testwissen")["uses"] == 1)

    treffer = kb.search("Eine Zeile")
    check("suchen findet die Fundstelle", treffer and treffer[0]["slug"] == "testwissen", treffer)
    check("und gibt nur die Umgebung, nicht das ganze Dokument",
          treffer and len(treffer[0]["excerpt"]) <= 500, len(treffer[0]["excerpt"]) if treffer else 0)

    kb.set_enabled("testwissen", False)
    check("abgeschaltet steht es nicht mehr im Systemtext", "Testwissen" not in kb.catalogue())
    try:
        kb.open("testwissen")
        check("und laesst sich nicht aufschlagen", False)
    except KnowledgeError:
        check("und laesst sich nicht aufschlagen", True)
    kb.set_enabled("testwissen", True)

    try:
        kb.save(title="Zu viel", content="x" * 200_001)
        check("ein uferloser Text wird abgelehnt", False)
    except KnowledgeError as e:
        check("ein uferloser Text wird abgelehnt", "aufteilen" in str(e), str(e)[:80])
    try:
        kb.save(title="", content="egal")
        check("ohne Titel geht es nicht", False)
    except KnowledgeError:
        check("ohne Titel geht es nicht", True)

    ohne = kb.save(title="Ohne Zweck", content="## Die erste sinnvolle Zeile\n\nMehr Text.")
    check("fehlt der Zweck, nimmt er die erste sinnvolle Zeile",
          ohne["summary"] == "Die erste sinnvolle Zeile", ohne)

    for name in ("knowledge.list", "knowledge.open", "knowledge.search"):
        t = state.tools.get(name)
        check(f"{name} gibt es", t is not None)
        if t:
            check(f"{name} fragt nicht nach Freigabe", t.needs_approval() is False)

    out = asyncio.run(state.tools.get("knowledge.open").handler(tc, {"name": "gibtsnicht"}))
    check("ein unbekanntes Dokument wird benannt, nicht erfunden",
          isinstance(out, tuple) and out[1] is False and "gibt es nicht" in out[0], out)

    check("geloescht ist geloescht", kb.remove("testwissen") and kb.get("testwissen") is None)


print("\n16. Ein fremdes Schema legt nicht die ganze Anfrage lahm")
# Der Fehler von der laufenden Instanz, wörtlich:
#   tools.33.custom.input_schema: input_schema does not support oneOf,
#   allOf, or anyOf at the top level
# Ein einziges Werkzeug eines fremden MCP-Servers reichte, und JEDE Antwort
# scheiterte — auch die, für die das Werkzeug gar nicht gebraucht wurde.
from command_center.backend.orchestrator.tool_registry import ToolSpec, sanitize_schema

roh = {
    "type": "object",
    "properties": {"gemeinsam": {"type": "string"}},
    "required": ["gemeinsam"],
    "oneOf": [
        {"properties": {"per_id": {"type": "string"}}, "required": ["per_id", "gemeinsam"]},
        {"properties": {"per_name": {"type": "string"}}, "required": ["per_name", "gemeinsam"]},
    ],
}
sauber = sanitize_schema(roh)
check("oneOf ist oben weg", "oneOf" not in sauber, sauber)
check("allOf/anyOf ebenso", not any(k in sauber for k in ("allOf", "anyOf")), sauber)
check("der Typ bleibt object", sauber.get("type") == "object", sauber)
check("kein Feld geht verloren",
      set(sauber["properties"]) == {"gemeinsam", "per_id", "per_name"}, sauber)
check("Pflicht bleibt nur, was jede Variante verlangt",
      sauber.get("required") == ["gemeinsam"], sauber)

verschachtelt = sanitize_schema(
    {"type": "object", "properties": {"wahl": {"oneOf": [{"type": "string"}, {"type": "number"}]}}})
check("verschachtelt wird nicht angefasst",
      "oneOf" in verschachtelt["properties"]["wahl"], verschachtelt)

check("Unsinn statt Schema ergibt ein leeres Objekt",
      sanitize_schema(None) == {"type": "object", "properties": {}}
      and sanitize_schema("nein") == {"type": "object", "properties": {}})

# Und der Weg, den es im Betrieb wirklich nimmt: Registry → Anbieter.
state.tools.register(ToolSpec(
    name="mcp.fremd.kaputt", description="ein Werkzeug von einem fremden Server",
    input_schema=roh, handler=lambda _c, _a: None), replace=True)
d = state.tools.get("mcp.fremd.kaputt").to_def()
check("to_def() gibt nichts Verbotenes an den Anbieter weiter",
      not any(k in d.input_schema for k in ("oneOf", "allOf", "anyOf")), d.input_schema)
check("und die Liste für den Anbieter ist als Ganze sauber",
      all(not any(k in t.to_def().input_schema for k in ("oneOf", "allOf", "anyOf"))
          for t in state.tools.all()))


print("\n17. Am eigenen Quelltext arbeiten — mit Git als Rückfahrkarte")
# Der Anlass: Die Fehler, die diesen Server lahmlegten (ein Schema, das der
# Anbieter ablehnt; eine Datei, die das Dockerfile nicht kopierte), lagen
# alle im Quelltext. filesystem.* konnte keinen davon beheben — der
# Arbeitsbereich liegt unter /data, der Quelltext nicht.
import subprocess as _sp

from command_center.backend.services.source import (
    SourceError, SourceService, VERBOTEN, source_dir, unavailable_reason)

# Ohne die Variable: vorhanden, aber ehrlich abgeschaltet.
_vorher = os.environ.pop("JARVIS_CC_SOURCE_DIR", None)
check("ohne Variable gibt es kein Quelltextverzeichnis", source_dir() is None)
check("und der Grund nennt die Variable", "JARVIS_CC_SOURCE_DIR" in unavailable_reason())

# Ein echtes kleines Git-Verzeichnis, damit nichts gestellt ist.
_repo = Path(tempfile.mkdtemp()) / "quelle"
(_repo / "command_center" / "backend").mkdir(parents=True)
(_repo / "command_center" / "backend" / "app.py").write_text("print('alt')\n", encoding="utf-8")
(_repo / ".env").write_text("GEHEIM=nichtanfassen\n", encoding="utf-8")
for _c in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=T",
                                           "commit", "-q", "-m", "erster"]):
    _sp.run(["git", *_c], cwd=_repo, check=True, capture_output=True)

os.environ["JARVIS_CC_SOURCE_DIR"] = str(_repo)
_q = SourceService()
check("mit Variable ist es eingeschaltet", _q.available() and unavailable_reason() == "")

_gelesen = asyncio.run(_q.read("command_center/backend/app.py"))
check("eine Quelltextdatei lässt sich lesen", _gelesen["content"] == "print('alt')\n", _gelesen)

# Die Grenzen, auf die man sich verlassen muss.
for _boes in ("../../etc/passwd", "/etc/passwd", ".env", ".git/config", "data/jarvis.db"):
    try:
        asyncio.run(_q.read(_boes))
        check(f"{_boes} wird abgelehnt", False)
    except SourceError as e:
        check(f"{_boes} wird abgelehnt", True)
check("die .env steht auf der Sperrliste", ".env" in VERBOTEN)

try:
    asyncio.run(_q.write(".env", "GEHEIM=gestohlen\n", "test"))
    check("auch Schreiben in die .env wird abgelehnt", False)
except SourceError:
    check("auch Schreiben in die .env wird abgelehnt", True)
check("und die .env ist unverändert",
      (_repo / ".env").read_text(encoding="utf-8") == "GEHEIM=nichtanfassen\n")

# Schreiben: die Datei ändert sich UND es entsteht ein Commit.
_res = asyncio.run(_q.write("command_center/backend/app.py", "print('neu')\n",
                            "Der Anbieter lehnte das alte Verhalten ab"))
check("die Datei ist geändert",
      (_repo / "command_center/backend/app.py").read_text(encoding="utf-8") == "print('neu')\n")
check("es gibt einen Commit dazu", bool(_res["commit"]), _res)
check("die Antwort sagt, dass es erst nach einem Neustart wirkt",
      "Neustart" in _res["note"], _res["note"])
_log = _sp.run(["git", "log", "-1", "--pretty=%an|%s|%b"], cwd=_repo,
               capture_output=True, text=True).stdout
check("JARVIS steht als Autor darin", _log.startswith("JARVIS|"), _log)
check("und die Begründung steht in der Nachricht", "lehnte das alte Verhalten ab" in _log, _log)

# Eine Änderung von Hand wird nicht überfahren.
(_repo / "command_center/backend/app.py").write_text("print('von hand')\n", encoding="utf-8")
try:
    asyncio.run(_q.write("command_center/backend/app.py", "print('egal')\n", "test"))
    check("ungesicherte Handarbeit wird nicht überschrieben", False)
except SourceError as e:
    check("ungesicherte Handarbeit wird nicht überschrieben", "von Hand" in str(e), str(e))
check("und sie ist noch da",
      (_repo / "command_center/backend/app.py").read_text(encoding="utf-8") == "print('von hand')\n")
_sp.run(["git", "checkout", "--", "."], cwd=_repo, check=True, capture_output=True)

# Zurücknehmen ist ein revert, kein Raten.
_zurueck = asyncio.run(_q.revert(_res["commit"]))
check("zurückgenommen wird als eigener Commit", bool(_zurueck["commit"]), _zurueck)
check("und der alte Inhalt ist wieder da",
      (_repo / "command_center/backend/app.py").read_text(encoding="utf-8") == "print('alt')\n")

_hist = asyncio.run(_q.history("command_center/backend/app.py"))
check("die Historie zeigt beide Schritte", len(_hist["commits"]) >= 3, _hist)

# Ohne Begründung nicht — sie ist die Commit-Nachricht.
try:
    asyncio.run(_q.write("neu.py", "x = 1\n", "  "))
    check("ohne Begründung wird nicht geschrieben", False)
except SourceError:
    check("ohne Begründung wird nicht geschrieben", True)

# Und die Einstufung: Schreiben am eigenen Quelltext ist das Heikelste hier.
for _name in ("source.write", "source.delete"):
    _t = state.tools.get(_name)
    check(f"{_name} gibt es", _t is not None)
    if _t:
        check(f"{_name} ist critical", _t.risk == "critical", _t.risk)
        check(f"{_name} fragt immer vorher", _t.needs_approval())
        check(f"{_name} bleibt Administratoren vorbehalten", _t.min_role == "admin", _t.min_role)
check("source.read fragt nicht nach", state.tools.get("source.read").needs_approval() is False)
check("source.revert fragt nach", state.tools.get("source.revert").needs_approval())

if _vorher is None:
    os.environ.pop("JARVIS_CC_SOURCE_DIR", None)
else:
    os.environ["JARVIS_CC_SOURCE_DIR"] = _vorher

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
