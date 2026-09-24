"""Deterministic, allowlisted self-healing for MIA infrastructure."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from ..db import now_iso

SAFE_CONTAINERS = {"jarvis-edge-tts", "jarvis-live-voice", "jarvis-speaches"}

# Retries are bounded: a container that keeps dying is a fault to report, not
# something to restart forever.
MAX_ACTIONS_PER_HOUR = 3
# A PLANNING/RUNNING task with no live run and no recent activity is orphaned
# (typically left behind by a restart).
ORPHAN_AFTER_MINUTES = 30

# (pattern, kind, cause, next step, may the service repair it on its own?)
_FAULTS = (
    (r"model_not_found|model .{0,40}(does not exist|not found)", "model_config",
     "Das konfigurierte Modell existiert beim Anbieter nicht.", "Modellname in der Konfiguration prüfen.", False),
    (r"modulenotfounderror|no module named|importerror", "missing_dependency",
     "Eine Python-Abhängigkeit fehlt im Abbild.", "Abhängigkeit in requirements.txt ergänzen und Abbild neu bauen.", False),
    (r"unicodedecodeerror|unicodeencodeerror|codec can't", "encoding",
     "Text wurde mit falscher Zeichenkodierung gelesen oder geschrieben.", "Datei/Quelle explizit als utf-8 behandeln.", False),
    (r"address already in use|port is already allocated", "port_conflict",
     "Der Port ist bereits belegt.", "Belegenden Prozess suchen (ss -tlnp) oder den Port umlegen.", False),
    (r"timeout|timed out", "timeout",
     "Die Gegenstelle hat nicht rechtzeitig geantwortet.", "Erreichbarkeit und Last der Gegenstelle prüfen; begrenzt wiederholen.", False),
    (r"connection refused|connecterror|name or service not known|no route to host|max retries exceeded",
     "service_unreachable", "Dienst oder Port ist nicht erreichbar.",
     "Container-Status prüfen; erlaubte Container werden neu gestartet.", True),
)


def classify_fault(text: str) -> dict:
    """Name a fault from an error text. Unknown stays unknown — nothing is guessed."""
    low = (text or "").lower()
    for pattern, kind, cause, step, auto in _FAULTS:
        if re.search(pattern, low):
            return {"kind": kind, "cause": cause, "next_step": step, "auto_repair": auto}
    return {"kind": "unknown", "cause": "", "next_step": "", "auto_repair": False}


class SelfHealingService:
    def __init__(self, state) -> None:
        self.state = state
        self._bad_counts: dict[str, int] = {}
        self._last_notice: dict[str, str] = {}
        self._action_times: dict[str, list[float]] = {}
        self._recorded: set[str] = set()

    def _may_act(self, component: str) -> bool:
        """At most MAX_ACTIONS_PER_HOUR repairs per component."""
        cutoff = time.time() - 3600
        recent = [t for t in self._action_times.get(component, []) if t >= cutoff]
        self._action_times[component] = recent
        return len(recent) < MAX_ACTIONS_PER_HOUR

    def _note_action(self, component: str) -> None:
        self._action_times.setdefault(component, []).append(time.time())

    def _reap_orphans(self) -> list[dict]:
        """Close PLANNING/RUNNING tasks that nothing is working on any more."""
        tasks = self.state.services.get("tasks")
        if tasks is None:
            return []
        live = {r.get("task_id") for r in self.state.runtime.active_runs() if r.get("task_id")}
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=ORPHAN_AFTER_MINUTES)
                  ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        rows = self.state.db.fetchall(
            "SELECT id, title FROM tasks WHERE status IN ('PLANNING','RUNNING') AND updated_at < ?", (cutoff,)) or []
        reaped = []
        for r in rows:
            if r["id"] in live:
                continue
            # a parent whose sub-tasks are still moving is not orphaned
            busy = self.state.db.scalar(
                "SELECT COUNT(*) FROM tasks WHERE parent_id=? AND status IN ('PLANNING','RUNNING','WAITING_FOR_APPROVAL') "
                "AND updated_at >= ?", (r["id"], cutoff)) or 0
            if busy:
                continue
            tasks.set_status(r["id"], "FAILED", error="orphaned: no active run for this task (self-healing)",
                             note=f"Task orphaned, closed by self-healing: {r['title']}")
            reaped.append({"component": "task", "action": "close_orphan", "ok": True, "task_id": r["id"]})
        return reaped

    async def run_once(self) -> dict:
        metrics = self.state.services["metrics"]
        actions: list[dict] = []
        issues: list[dict] = []

        # Database is essential. A failed DB check is reported, never "repaired" blindly.
        try:
            self.state.db.scalar("SELECT 1")
        except Exception as e:  # noqa: BLE001
            issues.append({"component": "database", "status": "offline", "detail": str(e)[:300]})

        # Provider: refresh health, but do not silently switch providers or credentials.
        try:
            ph = await self.state.runtime.check_provider()
            if ph.get("status") not in ("healthy", "unknown"):
                issues.append({"component": "provider", "status": ph.get("status"),
                               "detail": str(ph.get("detail", ""))[:300]})
        except Exception as e:  # noqa: BLE001
            issues.append({"component": "provider", "status": "error", "detail": str(e)[:300]})

        docker = await metrics.docker_containers(with_stats=False)
        if docker.get("status") != "healthy":
            issues.append({"component": "docker", "status": docker.get("status"),
                           "detail": str(docker.get("detail", ""))[:300]})
        else:
            for c in docker.get("containers", []):
                name = c.get("name", "")
                if name not in SAFE_CONTAINERS:
                    continue
                state = str(c.get("state", "")).lower()
                status = str(c.get("status", "")).lower()
                bad = state in ("exited", "dead", "created") or "unhealthy" in status
                if not bad:
                    self._bad_counts[name] = 0
                    continue
                self._bad_counts[name] = self._bad_counts.get(name, 0) + 1
                issue = {"component": name, "status": state or status,
                         "detail": status, "consecutive": self._bad_counts[name]}
                issues.append(issue)
                # Stopped/dead can be started immediately. Running-unhealthy gets 3 checks grace.
                action = None
                if state in ("exited", "dead", "created"):
                    action = "start"
                elif "unhealthy" in status and self._bad_counts[name] >= 3:
                    action = "restart"
                if action and not self._may_act(name):
                    issue["limit_reached"] = (f"{MAX_ACTIONS_PER_HOUR} Reparaturen in der letzten Stunde — "
                                              "nicht weiter automatisch, Ursache prüfen.")
                    action = None
                if action:
                    self._note_action(name)
                    try:
                        res = await metrics.docker_action(c["id"], action)
                        actions.append({"component": name, "action": action, "ok": True, "result": res})
                        self._bad_counts[name] = 0
                        learning = self.state.services.get("learning")
                        if learning is not None:
                            learning.record(kind="solution", title=f"Self-Healing: {name} {action}",
                                            problem=status, lesson=f"Container automatisch {action} ausgeführt.",
                                            verification="Docker API bestätigte die Aktion; Folgelauf prüft Zustand erneut.",
                                            source="self-healing", confidence=0.95,
                                            tags=["self-healing", "container", name], actor="mia:self-healing")
                    except Exception as e:  # noqa: BLE001
                        actions.append({"component": name, "action": action, "ok": False, "error": str(e)[:300]})

        # Orphaned tasks: report-and-close, so nothing lingers without an end state.
        try:
            actions.extend(self._reap_orphans())
        except Exception as e:  # noqa: BLE001
            self.state.log.warn("self-healing", f"Orphan-Prüfung fehlgeschlagen: {e}")

        # Name each fault and keep one durable error record per (component, kind).
        learning = self.state.services.get("learning")
        for issue in issues:
            fault = classify_fault(f"{issue.get('status', '')} {issue.get('detail', '')}")
            if fault["kind"] == "unknown":
                continue
            issue["fault"] = fault["kind"]
            issue["next_step"] = fault["next_step"]
            key = f"{issue['component']}:{fault['kind']}"
            if learning is not None and key not in self._recorded:
                self._recorded.add(key)
                learning.record(kind="error", title=f"Fehlerbild {fault['kind']}: {issue['component']}",
                                problem=str(issue.get("detail", ""))[:600], lesson=f"{fault['cause']} {fault['next_step']}",
                                verification="Von Self-Healing beobachtet, Ursache anhand des Fehlertextes zugeordnet.",
                                source="self-healing", confidence=0.7,
                                tags=["self-healing", fault["kind"], issue["component"]], actor="mia:self-healing")

        # Notify only on changed issue summaries to avoid alert storms.
        summary = " | ".join(f"{i['component']}:{i['status']}" for i in issues)
        if summary and self._last_notice.get("issues") != summary:
            self.state.services["notifications"].notify(
                category="server", severity="warning", title="MIA Self-Healing beobachtet ein Problem",
                body=summary[:700], link="/server")
            self._last_notice["issues"] = summary
        elif not summary:
            self._last_notice.pop("issues", None)

        if actions:
            self.state.log.info("self-healing", f"{len(actions)} Reparaturaktion(en)", data={"actions": actions})
            self.state.bus.publish("self.healing", {"ts": now_iso(), "actions": actions, "issues": issues})
        return {"ok": not issues, "issues": issues, "actions": actions,
                "safe_targets": sorted(SAFE_CONTAINERS)}
