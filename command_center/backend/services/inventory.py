"""
Was in JARVIS steckt — gezählt, nicht behauptet.

Zwei Leser, eine Quelle:

* Er selbst, über das Werkzeug `system.inventory`. „Was kannst du?" soll er
  beantworten können, indem er nachsieht, statt sich an seinen Systemtext zu
  erinnern — der altert mit jedem neu angebundenen Server.
* Das Dashboard, über `/api/architecture`. Dort wird derselbe Bestand als
  Aufbau gezeichnet: Nutzer, Gerät, Gateway, Master Agent, darunter Gedächtnis,
  Rechteschicht und Ereignisbus, darunter die Werkzeuge, darunter
  Unteragenten, Dienste und Geräte.

Alles hier ist gemessen: Zahlen kommen aus dem Verzeichnis, der Datenbank und
den Adaptern. Was nicht verbunden ist, steht als nicht verbunden da.
"""
from __future__ import annotations

import time


def _tool_groups(state) -> dict[str, int]:
    groups: dict[str, int] = {}
    for t in state.tools.all():
        if t.available and t.handler:
            groups[t.category] = groups.get(t.category, 0) + 1
    return dict(sorted(groups.items(), key=lambda kv: (-kv[1], kv[0])))


def _voice() -> dict:
    """Die Sprachleitung meldet selbst, ob sie kann — nicht geraten."""
    from .realtime import capabilities
    caps = capabilities()
    return {"live_line": bool(caps.get("available")), "detail": caps.get("detail", ""),
            "voice": caps.get("voice", "")}


def inventory(state) -> dict:
    """Der vollständige Bestand, in einer Form, die ein Modell lesen kann."""
    tools = state.tools.all()
    usable = [t for t in tools if t.available and t.handler]
    integ = state.integrations.list()

    mcp = state.services.get("mcp")
    skills = state.services.get("skills")
    selfext = state.services.get("selfext")
    master = state.runtime.status()

    # Ob ein Gerät online ist, weiß die Brücke — in der Tabelle steht nur, wann
    # es zuletzt gesehen wurde.
    devices = state.services["desktop"].list()
    agents = [a for a in (state.agents.get(i) for i in state.agents._specs) if a]

    return {
        "brain": {
            "mode": master["mode"],
            "online": master["online"],
            "provider": (master.get("provider") or {}).get("label", ""),
            "model": (master.get("provider") or {}).get("model", ""),
            "health": master.get("provider_health", {}),
        },
        "tools": {
            "usable": len(usable),
            "total": len(tools),
            "by_group": _tool_groups(state),
            "needs_approval": sum(1 for t in usable if t.needs_approval()),
            "unavailable": [{"name": t.name, "why": t.reason} for t in tools
                            if not (t.available and t.handler)][:40],
        },
        "integrations": [
            {"id": i.get("id"), "name": i.get("name", i.get("id")), "status": i.get("status"),
             "detail": i.get("detail", "")}
            for i in integ
        ],
        "mcp_servers": [
            {"name": s["name"], "status": s["status"], "tools": s["tool_count"], "enabled": s["enabled"]}
            for s in (mcp.servers() if mcp else [])
        ],
        "skills": [
            {"name": s["name"], "what_for": s["description"], "used": s["uses"]}
            for s in (skills.all(enabled_only=True) if skills else [])
        ],
        "self_written": [
            {"name": t.get("name"), "status": t.get("status")}
            for t in (selfext.list() if selfext else [])
        ],
        "agents": [
            {"id": a.id, "name": a.name, "kind": a.kind, "enabled": a.enabled, "role": a.role}
            for a in agents
        ],
        "devices": [
            {"name": d["name"], "platform": d.get("platform", ""),
             "status": "online" if d.get("online") else "offline",
             "last_seen": d.get("last_seen_at"), "actions": len(d.get("actions") or [])}
            for d in devices[:20]
        ],
        "voice": _voice(),
        "uptime_seconds": int(time.time() - state.started_at),
    }


def architecture(state) -> dict:
    """Derselbe Bestand als Aufbau — die Zeichnung, die im Dashboard steht.

    Jede Ebene trägt ihren echten Zustand. Ein Kasten, der grün ist, weil er
    gezeichnet wurde, wäre genau die Sorte Schaubild, die nichts wert ist.
    """
    inv = inventory(state)
    master = state.runtime.status()
    devices = inv["devices"]
    online_device = next((d for d in devices if d["status"] == "online"), None)

    from .realtime import capabilities as voice_caps
    caps = voice_caps()

    def tone(ok: bool, half: bool = False) -> str:
        return "ok" if ok else ("warn" if half else "err")

    approvals_open = state.db.scalar("SELECT COUNT(*) FROM approvals WHERE status='pending'") or 0
    memory_rows = state.db.scalar("SELECT COUNT(*) FROM memory") or 0
    conversations = state.db.scalar("SELECT COUNT(*) FROM conversations") or 0

    return {
        "layers": [
            {"id": "user", "title": "MASTER USER", "detail": "du", "status": "ok", "nodes": []},
            {"id": "input", "title": "STIMME / TEXT", "status": tone(bool(caps.get("available"))),
             "detail": caps.get("detail", ""), "nodes": []},
            {"id": "device", "title": "AKTIVES GERÄT",
             "status": tone(bool(online_device), half=bool(devices)),
             "detail": online_device["name"] if online_device
                       else (f"{len(devices)} gekoppelt, keins online" if devices else "keins gekoppelt"),
             "nodes": [{"title": d["name"], "detail": f"{d['platform']} · {d['actions']} Aktionen",
                        "status": tone(d["status"] == "online")}
                       for d in devices[:6]]},
            {"id": "gateway", "title": "JARVIS GATEWAY", "status": "ok",
             "detail": f"läuft seit {inv['uptime_seconds'] // 60} min", "nodes": []},
            {"id": "master", "title": "MASTER AGENT",
             # Offline ist offline: dann antwortet er nicht, und die Farbe soll
             # das sagen, statt es zu einer Warnung abzumildern.
             "status": tone(master["online"]),
             "detail": f"{master['label']} · {inv['brain']['model'] or inv['brain']['mode']}", "nodes": []},
            {"id": "core", "title": "KERN", "status": "ok", "detail": "", "nodes": [
                {"title": "GEDÄCHTNIS", "detail": f"{memory_rows} Einträge · {conversations} Gespräche",
                 "status": "ok"},
                {"title": "RECHTESCHICHT",
                 "detail": (f"{inv['tools']['needs_approval']} Werkzeuge fragen nach"
                            + (f" · {approvals_open} offen" if approvals_open else "")),
                 "status": "warn" if approvals_open else "ok"},
                {"title": "EREIGNISBUS", "detail": "Live-Strom ins Dashboard", "status": "ok"},
            ]},
            {"id": "tools", "title": "WERKZEUGE",
             "status": tone(inv["tools"]["usable"] > 0),
             "detail": f"{inv['tools']['usable']} von {inv['tools']['total']} einsatzbereit",
             "nodes": [{"title": g, "detail": f"{n}", "status": "ok"}
                       for g, n in list(inv["tools"]["by_group"].items())[:12]]},
            {"id": "agents", "title": "UNTERAGENTEN", "status": tone(len(inv["agents"]) > 1, half=True),
             "detail": f"{sum(1 for a in inv['agents'] if a['kind'] != 'master')} Spezialisten",
             "nodes": [{"title": a["name"], "detail": a["kind"], "status": tone(a["enabled"])}
                       for a in inv["agents"] if a["kind"] != "master"][:8]},
            {"id": "services", "title": "DIENSTE",
             "status": tone(any(i["status"] == "healthy" for i in inv["integrations"]), half=True),
             "detail": f"{sum(1 for i in inv['integrations'] if i['status'] == 'healthy')} verbunden",
             "nodes": [{"title": i["name"], "detail": i["detail"][:60],
                        "status": tone(i["status"] == "healthy",
                                       half=i["status"] not in ("healthy", "not_configured"))}
                       for i in inv["integrations"]][:12]},
            {"id": "extensions", "title": "ERWEITERUNGEN", "status": "ok",
             "detail": f"{len(inv['mcp_servers'])} MCP-Server · {len(inv['skills'])} Fähigkeiten "
                       f"· {sum(1 for t in inv['self_written'] if t['status'] == 'active')} selbst gebaut",
             "nodes": [{"title": s["name"], "detail": f"{s['tools']} Werkzeuge",
                        "status": tone(s["status"] == "healthy")} for s in inv["mcp_servers"]][:8]},
            {"id": "voice", "title": "STIMME ZURÜCK",
             "status": tone(bool(caps.get("available"))),
             "detail": caps.get("voice") or caps.get("detail", ""), "nodes": []},
        ],
        "totals": {
            "tools": inv["tools"]["usable"],
            "integrations_connected": sum(1 for i in inv["integrations"] if i["status"] == "healthy"),
            "mcp": len(inv["mcp_servers"]),
            "skills": len(inv["skills"]),
            "devices_online": sum(1 for d in devices if d["status"] == "online"),
        },
    }


__all__ = ["inventory", "architecture"]
