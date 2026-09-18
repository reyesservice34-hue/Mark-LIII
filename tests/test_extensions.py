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

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
