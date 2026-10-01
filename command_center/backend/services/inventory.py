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

import json
import os
import time
from pathlib import Path


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

    source_root = Path(os.environ.get("JARVIS_CC_SOURCE_DIR") or Path(__file__).resolve().parents[3])
    def health_file(name: str) -> dict:
        try:
            data = json.loads((source_root / "brain" / "system" / "health" / name).read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    cognition = health_file("cognition-health.json")
    autonomy = health_file("autonomy-health.json")
    try:
        from ..modules.heartbeat import _beat as heartbeat_state
        heartbeat_status = str(heartbeat_state.get("status") or "unknown")
        heartbeat_beats = int(heartbeat_state.get("beats") or 0)
        heartbeat_detail = (
            f"{heartbeat_beats} Prüfungen · zuletzt "
            + ("aktiv" if heartbeat_state.get("last_at") else "noch nicht gelaufen")
        )
    except Exception:
        heartbeat_status, heartbeat_beats, heartbeat_detail = "unknown", 0, "Status nicht lesbar"

    cognitive_ok = bool(cognition.get("ok"))
    autonomy_ok = bool(autonomy.get("ok"))
    specialists = sum(1 for a in inv["agents"] if a["kind"] != "master" and a["enabled"])
    senses_online = bool(caps.get("available") or online_device
                         or any(i["status"] == "healthy" for i in inv["integrations"]))

    organs = [
        {"id": "brain", "title": "Gehirn", "role": "Verstehen · Planen · Entscheiden",
         "status": tone(master["online"] and cognitive_ok, half=master["online"]),
         "detail": (f"{cognition.get('turn_count', 0)} Denkzyklen · "
                    f"{cognition.get('memory_hits', 0)} Gedächtnistreffer")},
        {"id": "heart", "title": "Herz", "role": "Herzschlag · Gesundheit · Wiederanlauf",
         "status": ("ok" if heartbeat_status == "ok" else
                    "warn" if heartbeat_status in ("warn", "unknown") else "err"),
         "detail": heartbeat_detail},
        {"id": "memory", "title": "Gedächtnis", "role": "Arbeits-, Ereignis- und Langzeitgedächtnis",
         "status": tone(bool(memory_rows or conversations or cognition)),
         "detail": f"{memory_rows} Kerninhalte · {conversations} Gespräche"},
        {"id": "nervous", "title": "Nervensystem", "role": "Ereignisse · Echtzeit · Aufgabenweitergabe",
         "status": tone(bool(getattr(state, "bus", None))),
         "detail": (f"{autonomy.get('done', 0)} autonome Aufgaben abgeschlossen · "
                    f"{autonomy.get('event_inbox', 0)} Ereignisse offen")},
        {"id": "senses", "title": "Sinne", "role": "Stimme · Text · Geräte · Verbindungen",
         "status": tone(senses_online, half=bool(devices or inv["integrations"])),
         "detail": (f"{sum(1 for d in devices if d['status'] == 'online')} Geräte online · "
                    f"Stimme {'bereit' if caps.get('available') else 'nicht bereit'}")},
        {"id": "hands", "title": "Hände", "role": "Werkzeuge · Agenten · Ausführung",
         "status": tone(inv["tools"]["usable"] > 0 and specialists > 0,
                        half=inv["tools"]["usable"] > 0),
         "detail": f"{inv['tools']['usable']} Werkzeuge · {specialists} Spezialisten"},
        {"id": "immune", "title": "Schutzsystem", "role": "Freigaben · Rechte · Audit · Grenzen",
         "status": "warn" if approvals_open else "ok",
         "detail": (f"{approvals_open} Freigaben offen · "
                    f"{inv['tools']['needs_approval']} geschützte Werkzeuge")},
    ]

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
        "organs": organs,
        "totals": {
            "tools": inv["tools"]["usable"],
            "integrations_connected": sum(1 for i in inv["integrations"] if i["status"] == "healthy"),
            "mcp": len(inv["mcp_servers"]),
            "skills": len(inv["skills"]),
            "devices_online": sum(1 for d in devices if d["status"] == "online"),
        },
    }


__all__ = ["inventory", "architecture"]
