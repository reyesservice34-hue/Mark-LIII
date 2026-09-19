"""
Herzschlag — Jarvis prüft sich selbst und meldet sich ungefragt.

Alle fünf Minuten läuft ein Systemcheck ohne dass jemand etwas anstößt: Speicher, Festplatte,
Container, Verbindungen, Master-Agent und die Sprachdienste. Der Check meldet sich nur, wenn sich
etwas ÄNDERT (etwas fällt aus / ist wieder da). Dauerhaft bekannte Probleme beim Start sind die
Ausgangslage und lösen keinen Alarm aus.

Eine Meldung landet zweimal: als normale Benachrichtigung im Dashboard, und als kurzer Satz in
der „Post" der Live-Konsole. Ist die Live-Konsole offen, spricht Jarvis ihn von sich aus aus.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from ...auth import Principal
from ...deps import AppState, current_principal, get_state, require_role
from .. import ModuleSpec

router = APIRouter(prefix="/api/heartbeat", tags=["heartbeat"])

INTERVAL_SECONDS = 300
RAM_WARN, RAM_ERR = 88.0, 95.0
DISK_WARN, DISK_ERR = 85.0, 93.0
_ORDER = {"ok": 0, "warn": 1, "err": 2}

# Was zusätzlich angepingt wird: "Anzeigename=URL", kommagetrennt.
_DEFAULT_PINGS = ("Live-Stimme=http://jarvis-live-voice:8000/api/health,"
                  "Sprachprogramm=http://jarvis-speaches:8000/health")

_beat: dict[str, Any] = {
    "beats": 0, "last_at": None, "last_duration_ms": 0, "status": "unknown", "checks": [],
    "since": time.time(),
}
_prev: dict[str, str] = {}
_inbox: list[dict] = []          # ungefragte Meldungen, neueste zuletzt


def _pings() -> list[tuple[str, str]]:
    raw = os.environ.get("JARVIS_CC_HEARTBEAT_URLS", _DEFAULT_PINGS)
    out = []
    for part in raw.split(","):
        if "=" in part:
            name, url = part.split("=", 1)
            if name.strip() and url.strip():
                out.append((name.strip(), url.strip()))
    return out


def _level(value: float, warn: float, err: float) -> str:
    return "err" if value >= err else "warn" if value >= warn else "ok"


async def _collect(state: AppState) -> list[dict]:
    checks: list[dict] = []

    def add(cid: str, label: str, level: str, detail: str) -> None:
        checks.append({"id": cid, "label": label, "level": level, "detail": detail})

    # ── Server: Speicher, Festplatte, Rechenlast ────────────────────────
    try:
        s = state.services["metrics"].latest() or state.services["metrics"].sample()
        ram, disk, cpu = float(s.get("ram", 0)), float(s.get("disk", 0)), float(s.get("cpu", 0))
        add("ram", "Arbeitsspeicher", _level(ram, RAM_WARN, RAM_ERR), f"{ram:.0f} % belegt")
        add("disk", "Festplatte", _level(disk, DISK_WARN, DISK_ERR), f"{disk:.0f} % belegt")
        add("cpu", "Rechenlast", "warn" if cpu >= 90 else "ok", f"{cpu:.0f} %")
    except Exception as e:  # noqa: BLE001
        add("metrics", "Serverwerte", "warn", f"nicht lesbar ({e.__class__.__name__})")

    # ── Master-Agent (das Denken) ───────────────────────────────────────
    try:
        m = state.runtime.status()
        ph = (m.get("provider_health") or {}).get("status", "unknown")
        ok = m.get("online") and ph in ("healthy", "unknown")
        detail = str((m.get("provider_health") or {}).get("detail") or m.get("label") or "")[:140]
        add("brain", "Denken (Master-Agent)", "ok" if ok else "err", detail if not ok else str(m.get("label", "bereit")))
    except Exception as e:  # noqa: BLE001
        add("brain", "Denken (Master-Agent)", "err", f"nicht erreichbar ({e.__class__.__name__})")

    # ── Container ────────────────────────────────────────────────────────
    try:
        res = await state.services["metrics"].docker_containers(with_stats=False)
        items = res.get("containers") if isinstance(res, dict) else res
        down = [c.get("name", "?") for c in (items or [])
                if str(c.get("state", c.get("status", ""))).lower() not in ("running", "up")
                and not str(c.get("state", "")).lower().startswith("up")]
        add("containers", "Container", "warn" if down else "ok",
            ("nicht aktiv: " + ", ".join(down[:5])) if down else f"{len(items or [])} laufen")
    except Exception:  # noqa: BLE001
        pass  # ohne Docker-Zugriff gibt es dazu nichts zu sagen

    # ── Verbindungen ─────────────────────────────────────────────────────
    try:
        for i in state.integrations.list():
            if i.get("configured") and i.get("status") in ("degraded", "offline"):
                add(f"int.{i['id']}", i["name"], "warn", str(i.get("detail", ""))[:140])
    except Exception:  # noqa: BLE001
        pass

    # ── Terminkollisionen (Kalender der nächsten 14 Tage) ───────────────
    try:
        cal = state.services["calendar"]
        if cal.available():
            events, _backend = await cal.list(days=14)
            evs = []
            for e in events:
                d = e if isinstance(e, dict) else getattr(e, "__dict__", {})
                if d.get("start") and d.get("end") and "T" in str(d["start"]):
                    evs.append((str(d["start"]), str(d["end"]), str(d.get("title", "Termin"))))
            evs.sort()
            clashes = []
            for i, a in enumerate(evs):
                for b in evs[i + 1:]:
                    if b[0] >= a[1]:
                        break
                    clashes.append(f"„{a[2]}“ und „{b[2]}“ am {a[0][8:10]}.{a[0][5:7]}. um {b[0][11:16]} Uhr")
            add("cal.overlap", "Terminkollision", "warn" if clashes else "ok",
                "; ".join(clashes[:3]) if clashes else "keine Überschneidungen in den nächsten 14 Tagen")
    except Exception:  # noqa: BLE001
        pass

    # ── Sprachdienste ────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=4) as c:
        for name, url in _pings():
            try:
                r = await c.get(url)
                add(f"ping.{name}", name, "ok" if r.status_code < 400 else "err", f"HTTP {r.status_code}")
            except Exception as e:  # noqa: BLE001
                add(f"ping.{name}", name, "err", f"nicht erreichbar ({e.__class__.__name__})")
    return checks


async def push(title: str, text: str, severity: str = "info") -> bool:
    """Nachricht mit Ton aufs Handy (ntfy). Ohne eingerichtetes Thema passiert nichts."""
    topic = os.environ.get("JARVIS_CC_NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = os.environ.get("JARVIS_CC_NTFY_URL", "https://ntfy.sh").rstrip("/")
    click = os.environ.get("JARVIS_CC_PUBLIC_URL", "https://jarvis.jarvis-reyes.de").rstrip("/")
    prio = {"critical": 5, "error": 4, "warning": 3, "success": 2}.get(severity, 3)
    tag = {"critical": "rotating_light", "error": "warning", "warning": "warning", "success": "white_check_mark"}.get(severity, "robot")
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.post(server, json={"topic": topic, "title": title[:120], "message": text[:500],
                                           "priority": prio, "tags": [tag], "click": click})
        return r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def _say(text: str, severity: str) -> None:
    _inbox.append({"id": f"hb{int(time.time() * 1000)}", "ts": time.time(), "text": text, "severity": severity})
    del _inbox[:-20]
    _pending_push.append((text, severity))


_pending_push: list[tuple[str, str]] = []


async def beat(state: AppState) -> dict:
    t0 = time.monotonic()
    checks = await _collect(state)
    first = _beat["beats"] == 0
    for c in checks:
        prev = _prev.get(c["id"])
        _prev[c["id"]] = c["level"]
        if first or prev is None or prev == c["level"]:
            continue                                   # Ausgangslage oder unverändert: kein Alarm
        if _ORDER[c["level"]] > _ORDER[prev]:
            sev = "error" if c["level"] == "err" else "warning"
            state.services["notifications"].notify(
                category="system", severity=sev, title=f"{c['label']}: Problem", body=c["detail"])
            _say((f"Achtung, Master, Terminkollision: {c['detail']}." if c["id"] == "cal.overlap"
                  else f"Kurze Meldung, Master: {c['label']} macht gerade Ärger. {c['detail']}."), sev)
        else:
            state.services["notifications"].notify(
                category="system", severity="success", title=f"{c['label']}: wieder in Ordnung", body=c["detail"])
            _say(f"Gute Nachricht, Master: {c['label']} läuft wieder.", "success")
    while _pending_push:                       # Meldungen dieses Durchlaufs aufs Handy
        text, sev = _pending_push.pop(0)
        await push("Jarvis", text, sev)
    worst = max((_ORDER[c["level"]] for c in checks), default=0)
    _beat.update(beats=_beat["beats"] + 1, last_at=time.time(), checks=checks,
                 last_duration_ms=int((time.monotonic() - t0) * 1000),
                 status=("ok", "warn", "err")[worst])
    return _beat


@router.get("")
async def heartbeat(state: AppState = Depends(get_state), _: Principal = Depends(current_principal)):
    now = time.time()
    return {
        "status": _beat["status"], "beats": _beat["beats"], "interval_seconds": INTERVAL_SECONDS,
        "alive_seconds": int(now - float(state.started_at)),
        "last_at": _beat["last_at"],
        "next_in": max(0, int(INTERVAL_SECONDS - (now - _beat["last_at"]))) if _beat["last_at"] else None,
        "last_duration_ms": _beat["last_duration_ms"], "checks": _beat["checks"],
        "recent": _inbox[-5:][::-1],
    }


@router.get("/inbox")
async def inbox(since: float = 0, _: Principal = Depends(current_principal)):
    """Ungefragte Meldungen seit einem Zeitpunkt — die Live-Konsole holt sie ab und spricht sie aus."""
    return {"now": time.time(), "items": [m for m in _inbox if m["ts"] > since][:3]}


@router.post("/test-push")
async def test_push(_: Principal = Depends(require_role("operator"))):
    ok = await push("Jarvis", "Test: Wenn du das hörst, erreiche ich dich aufs Handy.", "info")
    return {"sent": ok, "configured": bool(os.environ.get("JARVIS_CC_NTFY_TOPIC", "").strip())}


@router.post("/beat")
async def beat_now(state: AppState = Depends(get_state), _: Principal = Depends(require_role("operator"))):
    await beat(state)
    return await heartbeat(state, _)  # type: ignore[arg-type]


def _startup(state: AppState) -> None:
    async def job() -> None:
        await beat(state)

    state.scheduler.add("heartbeat", "Herzschlag", INTERVAL_SECONDS, job, silent=True,
                        description="Systemcheck von selbst: Speicher, Festplatte, Container, Verbindungen, Stimme",
                        run_immediately=True)


MODULE = ModuleSpec(id="heartbeat", title="Herzschlag", router=router, nav=False, order=2,
                    description="Jarvis prüft sich selbst und meldet sich ungefragt", on_startup=_startup)
