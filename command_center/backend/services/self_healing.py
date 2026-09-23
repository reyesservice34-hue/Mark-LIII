"""Deterministic, allowlisted self-healing for MIA infrastructure."""
from __future__ import annotations

from ..db import now_iso

SAFE_CONTAINERS = {"jarvis-edge-tts", "jarvis-live-voice", "jarvis-speaches"}


class SelfHealingService:
    def __init__(self, state) -> None:
        self.state = state
        self._bad_counts: dict[str, int] = {}
        self._last_notice: dict[str, str] = {}

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
                issues.append({"component": name, "status": state or status,
                               "detail": status, "consecutive": self._bad_counts[name]})
                # Stopped/dead can be started immediately. Running-unhealthy gets 3 checks grace.
                action = None
                if state in ("exited", "dead", "created"):
                    action = "start"
                elif "unhealthy" in status and self._bad_counts[name] >= 3:
                    action = "restart"
                if action:
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
