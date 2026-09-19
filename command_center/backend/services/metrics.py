"""
Server metrics — psutil for the host, the Docker Engine API over its socket
for containers, `systemctl` for monitored services. Nothing here is
synthesised: when a source is missing the answer says so.
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import socket
import subprocess
import time
from collections import deque
from pathlib import Path

import httpx
import psutil

from ..db import Database, now_iso
from ..events import EventBus


class MetricsService:
    def __init__(self, db: Database, bus: EventBus, *, docker_socket: str = "/var/run/docker.sock",
                 monitored_services: list[str] | None = None, history_points: int = 720):
        self.db = db
        self.bus = bus
        self.docker_socket = docker_socket
        self.services = monitored_services or []
        self.history: deque[dict] = deque(maxlen=history_points)
        self._last_net = None
        self._last_net_ts = 0.0
        self._boot = psutil.boot_time()
        self._docker_state: dict = {"status": "unknown", "detail": "not checked"}
        psutil.cpu_percent(interval=None)  # prime

    # ── host ─────────────────────────────────────────────────────────────
    def sample(self) -> dict:
        now = time.time()
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        net = psutil.net_io_counters()
        rx = tx = 0.0
        if self._last_net is not None and now > self._last_net_ts:
            dt = now - self._last_net_ts
            rx = (net.bytes_recv - self._last_net.bytes_recv) / dt
            tx = (net.bytes_sent - self._last_net.bytes_sent) / dt
        self._last_net, self._last_net_ts = net, now
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (0.0, 0.0, 0.0)
        disk = self._root_disk()
        point = {
            "ts": now_iso(), "cpu": psutil.cpu_percent(interval=None), "ram": vm.percent,
            "ram_used": vm.used, "ram_total": vm.total, "swap": sw.percent, "swap_used": sw.used,
            "swap_total": sw.total, "disk": disk["percent"], "disk_used": disk["used"],
            "disk_total": disk["total"], "load": load[0], "load5": load[1], "load15": load[2],
            "net_rx": rx, "net_tx": tx, "net_rx_total": net.bytes_recv, "net_tx_total": net.bytes_sent,
            "uptime": now - self._boot, "processes": len(psutil.pids()),
        }
        self.history.append(point)
        return point

    @staticmethod
    def _root_disk() -> dict:
        try:
            u = psutil.disk_usage("/")
            return {"percent": u.percent, "used": u.used, "total": u.total, "free": u.free}
        except OSError:
            return {"percent": 0.0, "used": 0, "total": 0, "free": 0}

    def latest(self) -> dict | None:
        return self.history[-1] if self.history else None

    def overview(self) -> dict:
        point = self.latest() or self.sample()
        return {
            "hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "platform": platform.platform(), "python": platform.python_version(),
            "cpu_count": psutil.cpu_count(logical=True), "cpu_physical": psutil.cpu_count(logical=False),
            "boot_time": self._boot, "in_container": Path("/.dockerenv").exists(),
            "sample": point,
            "docker": self._docker_state,
        }

    def disks(self) -> list[dict]:
        out = []
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            out.append({"device": part.device, "mountpoint": part.mountpoint, "fstype": part.fstype,
                        "total": u.total, "used": u.used, "free": u.free, "percent": u.percent})
        return out

    def network(self) -> list[dict]:
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
        addrs = psutil.net_if_addrs()
        out = []
        for name, st in stats.items():
            if name == "lo":
                continue
            c = counters.get(name)
            out.append({"name": name, "up": st.isup, "speed_mbps": st.speed,
                        "addresses": [a.address for a in addrs.get(name, []) if a.family == socket.AF_INET],
                        "bytes_recv": c.bytes_recv if c else 0, "bytes_sent": c.bytes_sent if c else 0})
        return out

    def processes(self, limit: int = 15) -> list[dict]:
        procs = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_percent", "status"]):
            try:
                info = p.info
                procs.append({"pid": info["pid"], "name": info["name"] or "?", "user": info["username"] or "",
                              "cpu": info["cpu_percent"] or 0.0, "mem": round(info["memory_percent"] or 0.0, 2),
                              "status": info["status"]})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: (x["cpu"], x["mem"]), reverse=True)
        return procs[:limit]

    def history_points(self, limit: int = 720) -> list[dict]:
        pts = list(self.history)[-limit:]
        return [{"ts": p["ts"], "cpu": p["cpu"], "ram": p["ram"], "disk": p["disk"], "load": p["load"],
                 "net_rx": p["net_rx"], "net_tx": p["net_tx"]} for p in pts]

    def persist_point(self) -> None:
        p = self.latest()
        if not p:
            return
        self.db.insert("metrics", {"ts": p["ts"], "cpu": p["cpu"], "ram": p["ram"], "disk": p["disk"],
                                   "load": p["load"], "net_rx": p["net_rx"], "net_tx": p["net_tx"]})
        self.db.execute("DELETE FROM metrics WHERE ts < datetime('now', '-14 days')")

    def stored_history(self, hours: int = 24) -> list[dict]:
        return self.db.fetchall("SELECT * FROM metrics WHERE ts >= datetime('now', ?) ORDER BY ts",
                                (f"-{int(hours)} hours",))

    # ── services (systemd) ───────────────────────────────────────────────
    def service_status(self) -> list[dict]:
        out = []
        if not self.services:
            return out
        systemctl = shutil.which("systemctl")
        for name in self.services:
            if not systemctl:
                out.append({"name": name, "status": "unknown", "detail": "systemctl not available"})
                continue
            try:
                r = subprocess.run([systemctl, "is-active", name], capture_output=True, text=True, timeout=5)
                state = (r.stdout or r.stderr).strip() or "unknown"
                out.append({"name": name, "status": "healthy" if state == "active" else
                            ("offline" if state in ("inactive", "failed", "dead") else "degraded"),
                            "detail": state})
            except Exception as e:  # noqa: BLE001
                out.append({"name": name, "status": "unknown", "detail": str(e)[:100]})
        return out

    # ── docker ───────────────────────────────────────────────────────────
    def _docker_hint(self, sock: str) -> str:
        """Warum der Socket nicht antwortet — in einem Satz, der zur Lösung führt.

        „docker engine unreachable: ConnectError" hat drei völlig verschiedene
        Ursachen, und jede braucht einen anderen Handgriff. Die Datei selbst
        weiß, welche es ist: Gibt es sie nicht, ist sie nicht eingehängt. Gibt
        es sie und wir dürfen nicht lesen, fehlt die Gruppe. Dürfen wir lesen
        und es kommt trotzdem nichts, läuft der Dienst nicht.
        """
        import grp
        import stat as _stat

        path = Path(sock)
        if not path.exists():
            return (f"Der Docker-Socket ist im Container nicht vorhanden ({sock}). In "
                    "docker-compose.command-center.yml muss /var/run/docker.sock eingehängt sein.")
        try:
            st = path.stat()
        except OSError as e:
            return f"Der Docker-Socket ist nicht lesbar ({e.__class__.__name__})."
        if not os.access(sock, os.R_OK | os.W_OK):
            gid = st.st_gid
            try:
                name = grp.getgrgid(gid).gr_name
            except (KeyError, OverflowError):
                name = "?"
            return (f"Der Socket ist da, aber dieser Container darf ihn nicht benutzen. Er gehört "
                    f"der Gruppe {gid} ({name}), der Container läuft in {os.getgroups()}. "
                    f"Setze JARVIS_CC_DOCKER_GID={gid} in command_center/.env und starte neu.")
        if not _stat.S_ISSOCK(st.st_mode):
            return f"{sock} ist kein Socket, sondern eine gewöhnliche Datei."
        return ("Der Socket ist erreichbar, aber der Docker-Dienst antwortet nicht. Läuft er? "
                "systemctl status docker")

    def _docker_client(self) -> httpx.AsyncClient | None:
        host = os.environ.get("DOCKER_HOST", "")
        if host.startswith("tcp://") or host.startswith("http://"):
            return httpx.AsyncClient(base_url=host.replace("tcp://", "http://"), timeout=8.0)
        sock = host.replace("unix://", "") if host.startswith("unix://") else self.docker_socket
        if not Path(sock).exists():
            return None
        transport = httpx.AsyncHTTPTransport(uds=sock)
        return httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=8.0)

    async def docker_containers(self, with_stats: bool = True) -> dict:
        client = self._docker_client()
        if client is None:
            self._docker_state = {"status": "not_configured", "detail": self._docker_hint(self.docker_socket)}
            return {"status": self._docker_state["status"], "detail": self._docker_state["detail"],
                    "containers": []}
        try:
            async with client:
                r = await client.get("/containers/json", params={"all": "true"})
                r.raise_for_status()
                raw = r.json()
                containers = []
                for c in raw:
                    item = {
                        "id": c["Id"][:12], "name": (c.get("Names") or ["?"])[0].lstrip("/"),
                        "image": c.get("Image", ""), "state": c.get("State", ""), "status": c.get("Status", ""),
                        "created": c.get("Created"),
                        "ports": [f"{p.get('PublicPort', '')}:{p.get('PrivatePort', '')}/{p.get('Type', '')}"
                                  .strip(":") for p in c.get("Ports", []) if p.get("PrivatePort")],
                        "cpu": None, "memory": None, "memory_limit": None,
                    }
                    containers.append(item)
                if with_stats:
                    running = [c for c in containers if c["state"] == "running"][:20]
                    stats = await asyncio.gather(*[self._container_stats(client, c["id"]) for c in running],
                                                 return_exceptions=True)
                    for c, s in zip(running, stats):
                        if isinstance(s, dict):
                            c.update(s)
                self._docker_state = {"status": "healthy", "detail": f"{len(containers)} containers"}
                return {"status": "healthy", "detail": self._docker_state["detail"], "containers": containers}
        except Exception as e:  # noqa: BLE001
            host = os.environ.get("DOCKER_HOST", "")
            sock = host.replace("unix://", "") if host.startswith("unix://") else self.docker_socket
            detail = (f"Docker über {host} nicht erreichbar ({e.__class__.__name__})."
                      if host.startswith(("tcp://", "http://")) else self._docker_hint(sock))
            self._docker_state = {"status": "offline", "detail": detail}
            return {"status": "offline", "detail": detail, "containers": []}

    @staticmethod
    async def _container_stats(client: httpx.AsyncClient, cid: str) -> dict:
        r = await client.get(f"/containers/{cid}/stats", params={"stream": "false", "one-shot": "true"})
        r.raise_for_status()
        s = r.json()
        cpu = None
        try:
            cpu_delta = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
            sys_delta = s["cpu_stats"]["system_cpu_usage"] - s["precpu_stats"].get("system_cpu_usage", 0)
            ncpu = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
            if sys_delta > 0 and cpu_delta >= 0:
                cpu = round(cpu_delta / sys_delta * ncpu * 100, 2)
        except (KeyError, TypeError, ZeroDivisionError):
            pass
        mem = s.get("memory_stats", {})
        return {"cpu": cpu, "memory": mem.get("usage"), "memory_limit": mem.get("limit")}

    async def docker_action(self, cid: str, action: str) -> dict:
        if action not in ("restart", "start", "stop"):
            raise ValueError("unsupported action")
        client = self._docker_client()
        if client is None:
            raise RuntimeError("docker socket not available")
        async with client:
            r = await client.post(f"/containers/{cid}/{action}", timeout=60.0)
            if r.status_code >= 300:
                raise RuntimeError(f"docker {action} failed: HTTP {r.status_code} {r.text[:200]}")
        return {"ok": True, "action": action, "container": cid}

    async def docker_logs(self, cid: str, tail: int = 200) -> str:
        client = self._docker_client()
        if client is None:
            raise RuntimeError("docker socket not available")
        async with client:
            r = await client.get(f"/containers/{cid}/logs",
                                 params={"stdout": "true", "stderr": "true", "tail": str(min(tail, 2000))})
            r.raise_for_status()
            raw = r.content
        # strip the 8-byte multiplexed stream headers
        out, i = [], 0
        while i + 8 <= len(raw):
            size = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + size].decode("utf-8", "replace"))
            i += 8 + size
        return "".join(out) if out else raw.decode("utf-8", "replace")

    def restart_service(self, name: str) -> dict:
        if name not in self.services:
            raise RuntimeError(f"service '{name}' is not in the monitored allow-list")
        systemctl = shutil.which("systemctl")
        if not systemctl:
            raise RuntimeError("systemctl not available")
        r = subprocess.run([systemctl, "restart", name], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300] or f"exit {r.returncode}")
        return {"ok": True, "service": name}
