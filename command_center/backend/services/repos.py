"""
Quelltext holen: Git-Repositories klonen und Dateien von einer Adresse laden.

Alles landet im Arbeitsbereich unter `repos/` bzw. `downloads/` — also dort,
wo `filesystem.list`, `filesystem.read` und `filesystem.search` ohnehin schon
hinsehen dürfen. Damit ist ein geklontes Repository sofort lesbar, ohne dass
dafür ein zweiter Satz Werkzeuge nötig wäre.

Was hier bewusst eng gehalten ist:

* Nur `http://` und `https://`. Kein `git@…`, kein `file://`, kein `ssh://` —
  ein Klon über SSH bräuchte einen Schlüssel auf dem Server, und der wäre ein
  Generalschlüssel für fremde Rechner.
* Keine Adressen, die auf das eigene Netz zeigen. Dieser Server steht neben
  n8n, der Datenbank und dem Docker-Socket; eine Adresse wie
  `http://127.0.0.1:5678` oder `http://192.168.1.5` wäre kein Klon, sondern
  ein Griff ins Innere. Das wird vor dem Verbinden geprüft, nicht danach.
* Kein Ausführen. Geklont wird Quelltext, gelesen wird Text. Was damit
  geschieht, entscheidet ein Mensch — `terminal.execute` ist ein eigenes
  Werkzeug mit eigener Genehmigung.
* Größe und Tiefe sind gedeckelt. Ein `--depth 1` ohne Verlaufsdaten reicht,
  um etwas zu lesen, und eine Platte, die vollläuft, nimmt den ganzen Server
  mit.

Für private GitHub-Repositories wird `GITHUB_TOKEN` benutzt, falls gesetzt.
Das Token steht nie in einer Ausgabe: Was Git auf der Fehlerausgabe von sich
gibt, wird gefiltert, bevor es jemand zu sehen bekommt.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
REPOS_DIR = "repos"
DOWNLOAD_DIR = "downloads"
CLONE_TIMEOUT = 300.0
MAX_DOWNLOAD = 64 * 1024 * 1024      # 64 MB — genug für Quelltext, wenig für Unfug


class RepoError(RuntimeError):
    """Grund, der dem Nutzer (und dem Modell) gesagt werden soll."""


def git_available() -> bool:
    return shutil.which("git") is not None


def _is_private(host: str) -> bool:
    """Zeigt diese Adresse ins eigene Netz?

    Geprüft wird jede aufgelöste Adresse, nicht nur die erste: Ein Name, der
    sowohl öffentlich als auch auf 127.0.0.1 zeigt, wäre sonst ein offenes
    Tor.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise RepoError(f"Der Rechnername '{host}' lässt sich nicht auflösen.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def check_url(raw: str) -> str:
    """Adresse prüfen und in der Form zurückgeben, in der sie benutzt wird."""
    url = (raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RepoError("Nur http:// und https:// — für SSH bräuchte der Server einen "
                        "Schlüssel, und den bekommt er nicht.")
    if not parsed.hostname:
        raise RepoError("In der Adresse fehlt der Rechnername.")
    if parsed.username or parsed.password:
        raise RepoError("Zugangsdaten gehören nicht in die Adresse. Für private Repositories "
                        "wird GITHUB_TOKEN benutzt, falls es gesetzt ist.")
    if _is_private(parsed.hostname):
        raise RepoError(f"'{parsed.hostname}' zeigt in das Netz dieses Servers. Von hier aus "
                        "wird nur nach außen geladen.")
    return urlunparse(parsed)


def _name_from_url(url: str) -> str:
    tail = urlparse(url).path.rstrip("/").split("/")[-1]
    return re.sub(r"\.git$", "", tail) or "repo"


def _scrub(text: str) -> str:
    """Alles herausnehmen, was nach Zugangsdaten aussieht."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        text = text.replace(token, "***")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", text)


class RepoService:
    def __init__(self, files, log=None) -> None:
        self.files = files
        self.log = log
        self.root = Path(files.root) / REPOS_DIR
        self.downloads = Path(files.root) / DOWNLOAD_DIR

    # ── Bestand ──────────────────────────────────────────────────────────
    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        out = []
        for d in sorted(self.root.iterdir()):
            if not (d / ".git").exists():
                continue
            files_n = sum(1 for _ in d.rglob("*") if _.is_file())
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            out.append({
                "name": d.name, "path": f"{REPOS_DIR}/{d.name}",
                "origin": _scrub(self._remote(d)), "files": files_n,
                "bytes": size, "head": self._head(d),
            })
        return out

    def _run_sync(self, args: list[str], cwd: Path) -> str:
        import subprocess
        try:
            out = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=15)
        except Exception:  # noqa: BLE001
            return ""
        return out.stdout.strip()

    def _remote(self, d: Path) -> str:
        return self._run_sync(["git", "remote", "get-url", "origin"], d)

    def _head(self, d: Path) -> str:
        return self._run_sync(["git", "log", "-1", "--format=%h %s"], d)

    # ── Klonen ───────────────────────────────────────────────────────────
    async def _git(self, args: list[str], cwd: Path, timeout: float = CLONE_TIMEOUT) -> tuple[int, str]:
        if not git_available():
            raise RepoError("Auf diesem Server ist git nicht installiert. Im Image nachrüsten: "
                            "apt-get install git")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RepoError(f"git brach nach {int(timeout)} Sekunden ab.") from None
        return proc.returncode or 0, _scrub(out.decode("utf-8", "replace"))

    def _auth_url(self, url: str) -> str:
        """Für private GitHub-Repositories das Token einsetzen — nur dort."""
        token = os.environ.get("GITHUB_TOKEN", "")
        host = urlparse(url).hostname or ""
        if token and host == "github.com":
            return url.replace("https://", f"https://x-access-token:{token}@", 1)
        return url

    async def clone(self, url: str, name: str = "", *, branch: str = "", depth: int = 1) -> dict:
        url = check_url(url)
        name = (name or _name_from_url(url)).strip()
        if not NAME_RE.match(name):
            raise RepoError("Der Ordnername darf nur Buchstaben, Ziffern, Punkt, Strich und "
                            "Unterstrich enthalten.")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / name
        if target.exists():
            raise RepoError(f"'{name}' gibt es schon. Mit repo.pull aktualisieren oder mit "
                            "repo.remove entfernen.")

        args = ["clone", "--depth", str(max(1, min(depth, 50))), "--single-branch"]
        if branch:
            args += ["--branch", branch]
        args += [self._auth_url(url), str(target)]
        code, out = await self._git(args, self.root)
        if code != 0:
            # Ein halb angelegter Ordner wäre schlimmer als keiner.
            shutil.rmtree(target, ignore_errors=True)
            raise RepoError(f"git clone schlug fehl:\n{out[-800:]}")

        # Die Anmeldung gehört nicht dauerhaft in .git/config.
        await self._git(["remote", "set-url", "origin", url], target, timeout=20)
        if self.log:
            self.log.info("repos", f"geklont: {url} → {REPOS_DIR}/{name}")
        return {"name": name, "path": f"{REPOS_DIR}/{name}", "origin": url,
                "head": self._head(target), "output": out[-600:]}

    async def pull(self, name: str) -> dict:
        target = self.root / name
        if not (target / ".git").exists():
            raise RepoError(f"'{name}' ist kein geklontes Repository.")
        url = check_url(self._remote(target))
        await self._git(["remote", "set-url", "origin", self._auth_url(url)], target, timeout=20)
        code, out = await self._git(["pull", "--ff-only"], target, timeout=120)
        await self._git(["remote", "set-url", "origin", url], target, timeout=20)
        if code != 0:
            raise RepoError(f"git pull schlug fehl:\n{out[-800:]}")
        return {"name": name, "head": self._head(target), "output": out[-600:]}

    def remove(self, name: str) -> bool:
        target = self.root / name
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise RepoError("Dieser Pfad liegt nicht im Repository-Ordner.") from None
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    # ── Eine Datei von einer Adresse ─────────────────────────────────────
    async def download(self, url: str, name: str = "") -> dict:
        url = check_url(url)
        name = (name or Path(urlparse(url).path).name or "download.bin").strip()
        if not NAME_RE.match(name):
            raise RepoError("Der Dateiname darf nur Buchstaben, Ziffern, Punkt, Strich und "
                            "Unterstrich enthalten.")
        self.downloads.mkdir(parents=True, exist_ok=True)
        target = self.downloads / name

        got = 0
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as res:
                    if res.status_code >= 400:
                        raise RepoError(f"Die Adresse antwortete mit HTTP {res.status_code}.")
                    with target.open("wb") as fh:
                        async for chunk in res.aiter_bytes():
                            got += len(chunk)
                            if got > MAX_DOWNLOAD:
                                fh.close()
                                target.unlink(missing_ok=True)
                                raise RepoError(f"Die Datei ist größer als "
                                                f"{MAX_DOWNLOAD // (1024 * 1024)} MB. Abgebrochen.")
                            fh.write(chunk)
        except httpx.HTTPError as e:
            target.unlink(missing_ok=True)
            raise RepoError(f"Herunterladen ging nicht: {e.__class__.__name__}") from e
        if self.log:
            self.log.info("repos", f"geladen: {url} → {DOWNLOAD_DIR}/{name} ({got} Bytes)")
        return {"name": name, "path": f"{DOWNLOAD_DIR}/{name}", "bytes": got, "url": url}


__all__ = ["RepoService", "RepoError", "check_url", "git_available"]
