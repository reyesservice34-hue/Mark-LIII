"""
Am eigenen Quelltext arbeiten — mit Git als Rückfahrkarte.

Warum es das braucht, und warum der Arbeitsbereich dafür nicht reicht:

Die Fehler, die diesen Server zuletzt lahmgelegt haben, lagen alle im
Quelltext, nicht in den Daten — ein Werkzeugschema, das ein Anbieter ablehnt;
eine Datei, die das Dockerfile nicht ins Abbild kopierte; ein `install.sh`,
das die Container nicht neu erzeugte. Kein `filesystem.write` hätte davon
irgendetwas beheben können: Der Arbeitsbereich liegt unter `/data`, der
Quelltext im Abbild. Eine Änderung dort wäre beim nächsten Bauen weg gewesen.

Deshalb arbeitet dieser Dienst auf dem Git-Arbeitsverzeichnis des Hosts, das
in den Container gereicht wird — dort, wo `git pull` und `docker compose
build` es auch lesen. Und er arbeitet ausschließlich über Git:

  * Jede Änderung wird sofort committet, mit JARVIS als Autor. Was er getan
    hat, steht damit in `git log` — nachlesbar von jemandem, der ihm nicht
    glaubt.
  * Rückgängig ist deshalb immer möglich: `source.revert` ist ein
    `git revert`, kein Rateversuch aus dem Gedächtnis.
  * Ein schmutziges Arbeitsverzeichnis wird nicht überschrieben. Wer von Hand
    etwas geändert hat, verliert es nicht, weil JARVIS dazwischenfunkt.

Was das NICHT ist, damit sich niemand darauf verlässt:

  * Es ist keine Sandbox. Wer den Quelltext dieses Servers ändern darf, darf
    alles, was dieser Server darf. Die Sicherung ist das Freigabe-Gatter
    davor, der lesbare Unterschied und die Git-Historie — nicht eine
    technische Einsperrung.
  * Eine Änderung wirkt NICHT sofort. Python-Dateien wirken nach einem
    Neustart des Containers, das Dockerfile und das Frontend erst nach einem
    neuen Bauen. Jede Antwort sagt das dazu, statt ein „erledigt" zu liefern,
    das im laufenden System nichts bedeutet.
  * Standardmäßig ist alles aus. Ohne JARVIS_CC_SOURCE_DIR gibt es die
    Werkzeuge zwar, aber sie sagen, welche Variable fehlt, und tun nichts.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

GIT_TIMEOUT = 30.0
MAX_BYTES = 400_000          # eine Quelltextdatei, die größer ist, ist keine
MAX_LIST = 400

# Was nie angefasst wird, auch nicht mit Freigabe. Das sind keine Quelltexte,
# sondern Geheimnisse und Zustand — und ein Werkzeug, das die .env schreiben
# darf, kann jeden Schlüssel des Servers ausleiten.
VERBOTEN = (".env", ".git/", "data/", "node_modules/", "__pycache__/",
            "config/api_keys.json", "backend/static/")


class SourceError(Exception):
    pass


def source_dir() -> Path | None:
    """Das Arbeitsverzeichnis, an dem gearbeitet werden darf — oder None."""
    raw = os.environ.get("JARVIS_CC_SOURCE_DIR", "").strip()
    if not raw:
        return None
    p = Path(raw).resolve()
    return p if (p / ".git").is_dir() else None


def unavailable_reason() -> str:
    raw = os.environ.get("JARVIS_CC_SOURCE_DIR", "").strip()
    if not raw:
        return ("Am eigenen Quelltext zu arbeiten ist nicht eingeschaltet. "
                "Dafür muss JARVIS_CC_SOURCE_DIR auf das Git-Arbeitsverzeichnis "
                "zeigen und dieses in den Container gereicht werden "
                "(siehe .env.example).")
    p = Path(raw).resolve()
    if not p.exists():
        return f"JARVIS_CC_SOURCE_DIR zeigt auf {p}, dort ist aber nichts. Ist das Volume gesetzt?"
    if not (p / ".git").is_dir():
        return (f"Unter {p} liegt kein Git-Arbeitsverzeichnis. Ohne Git gäbe es keinen Weg "
                f"zurück, deshalb wird dort nicht geschrieben.")
    return ""


def _pruefe_pfad(rel: str) -> str:
    """Einen Pfad auf das Arbeitsverzeichnis festnageln — vor jedem Zugriff."""
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        raise SourceError("Es fehlt der Pfad der Datei.")
    root = source_dir()
    if root is None:
        raise SourceError(unavailable_reason())
    ziel = (root / rel).resolve()
    try:
        ziel.relative_to(root)
    except ValueError:
        raise SourceError(f"{rel} liegt außerhalb des Quelltexts — abgelehnt.") from None
    posix = ziel.relative_to(root).as_posix()
    for tabu in VERBOTEN:
        if posix == tabu.rstrip("/") or posix.startswith(tabu):
            raise SourceError(
                f"{posix} ist gesperrt. Gesperrt sind Geheimnisse und erzeugte Dateien "
                f"({', '.join(VERBOTEN)}) — dort steht kein Quelltext, den es zu "
                f"reparieren gäbe.")
    return posix


def _wirkung(pfad: str) -> str:
    """Wann eine Änderung an dieser Datei tatsächlich etwas tut."""
    if pfad.endswith(("Dockerfile", ".dockerfile")) or "Dockerfile" in pfad:
        return "Wirkt erst nach einem neuen Bauen: bash command_center/install.sh"
    if pfad.startswith("command_center/frontend/"):
        return "Wirkt erst nach einem neuen Bauen des Frontends: bash command_center/install.sh"
    if pfad.endswith((".sh", ".yml", ".yaml")):
        return "Wirkt beim nächsten Aufruf dieser Datei bzw. nach einem neuen Bauen."
    if pfad.endswith(".py"):
        return ("Wirkt nach einem Neustart des Containers: docker compose "
                "-f docker-compose.command-center.yml restart")
    return "Ob und wann das wirkt, hängt davon ab, wer die Datei liest."


class SourceService:
    """Lesen, ändern und zurücknehmen — jeder Schreibvorgang ein Commit."""

    def available(self) -> bool:
        return source_dir() is not None

    # ── git ──────────────────────────────────────────────────────────────
    async def _git(self, *args: str, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
        root = source_dir()
        if root is None:
            raise SourceError(unavailable_reason())
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(root),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
                 "GIT_AUTHOR_NAME": "JARVIS", "GIT_AUTHOR_EMAIL": "jarvis@localhost",
                 "GIT_COMMITTER_NAME": "JARVIS", "GIT_COMMITTER_EMAIL": "jarvis@localhost"},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise SourceError(f"git antwortete {int(timeout)} Sekunden lang nicht.") from None
        return proc.returncode or 0, out.decode("utf-8", "replace").strip()

    async def _sauber(self, pfad: str) -> None:
        """Nichts überschreiben, woran jemand anderes gerade arbeitet."""
        code, out = await self._git("status", "--porcelain", "--", pfad)
        if code == 0 and out.strip():
            raise SourceError(
                f"An {pfad} gibt es bereits ungesicherte Änderungen von Hand. "
                f"Die würden verlorengehen — erst committen oder verwerfen, dann noch einmal.")

    # ── lesen ────────────────────────────────────────────────────────────
    async def read(self, pfad: str) -> dict:
        posix = _pruefe_pfad(pfad)
        datei = source_dir() / posix                     # type: ignore[operator]
        if not datei.is_file():
            raise SourceError(f"{posix} gibt es nicht.")
        roh = datei.read_bytes()
        if len(roh) > MAX_BYTES:
            raise SourceError(f"{posix} ist {len(roh)} Bytes groß — zu viel für eine Quelltextdatei.")
        try:
            text = roh.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError(f"{posix} ist keine Textdatei.") from None
        return {"path": posix, "bytes": len(roh), "lines": text.count("\n") + 1, "content": text}

    async def list(self, unterordner: str = "") -> dict:
        root = source_dir()
        if root is None:
            raise SourceError(unavailable_reason())
        code, out = await self._git("ls-files", "--", unterordner or ".")
        if code != 0:
            raise SourceError(f"git ls-files schlug fehl: {out[:200]}")
        dateien = [z for z in out.splitlines() if z]
        return {"path": unterordner or ".", "count": len(dateien),
                "files": dateien[:MAX_LIST],
                "truncated": len(dateien) > MAX_LIST}

    async def history(self, pfad: str = "", limit: int = 15) -> dict:
        args = ["log", f"-{max(1, min(limit, 50))}", "--pretty=%h|%an|%ar|%s"]
        if pfad:
            args += ["--", _pruefe_pfad(pfad)]
        code, out = await self._git(*args)
        if code != 0:
            raise SourceError(f"git log schlug fehl: {out[:200]}")
        eintraege = []
        for zeile in out.splitlines():
            teile = zeile.split("|", 3)
            if len(teile) == 4:
                eintraege.append({"commit": teile[0], "author": teile[1],
                                  "when": teile[2], "subject": teile[3]})
        return {"path": pfad or ".", "commits": eintraege}

    async def diff(self, commit: str = "") -> dict:
        """Was hat sich geändert — im Arbeitsverzeichnis oder in einem Commit."""
        if commit:
            if not commit.replace("~", "").replace("^", "").isalnum():
                raise SourceError("Das sieht nicht wie eine Commit-Kennung aus.")
            code, out = await self._git("show", "--stat", "--patch", commit)
        else:
            code, out = await self._git("diff", "HEAD")
        if code != 0:
            raise SourceError(f"git diff schlug fehl: {out[:200]}")
        return {"commit": commit or "Arbeitsverzeichnis",
                "diff": out[:MAX_BYTES], "empty": not out.strip()}

    # ── ändern ───────────────────────────────────────────────────────────
    async def write(self, pfad: str, inhalt: str, grund: str) -> dict:
        posix = _pruefe_pfad(pfad)
        if not (grund or "").strip():
            raise SourceError("Ohne Begründung nicht — sie steht später in der Commit-Nachricht.")
        if len(inhalt.encode("utf-8")) > MAX_BYTES:
            raise SourceError("Der Inhalt ist zu groß für eine Quelltextdatei.")
        await self._sauber(posix)

        datei = source_dir() / posix                     # type: ignore[operator]
        neu = not datei.exists()
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(inhalt, encoding="utf-8")

        code, out = await self._git("add", "--", posix)
        if code != 0:
            raise SourceError(f"git add schlug fehl: {out[:200]}")
        verb = "legt an" if neu else "ändert"
        code, out = await self._git("commit", "-m", f"JARVIS {verb} {posix}\n\n{grund.strip()}")
        if code != 0 and "nothing to commit" not in out:
            raise SourceError(f"git commit schlug fehl: {out[:300]}")
        _, sha = await self._git("rev-parse", "--short", "HEAD")
        return {"path": posix, "created": neu, "commit": sha,
                "note": _wirkung(posix)}

    async def delete(self, pfad: str, grund: str) -> dict:
        posix = _pruefe_pfad(pfad)
        if not (grund or "").strip():
            raise SourceError("Ohne Begründung nicht — sie steht später in der Commit-Nachricht.")
        datei = source_dir() / posix                     # type: ignore[operator]
        if not datei.exists():
            raise SourceError(f"{posix} gibt es nicht.")
        await self._sauber(posix)

        code, out = await self._git("rm", "-r", "--", posix)
        if code != 0:
            raise SourceError(f"git rm schlug fehl: {out[:200]}")
        code, out = await self._git("commit", "-m", f"JARVIS entfernt {posix}\n\n{grund.strip()}")
        if code != 0:
            raise SourceError(f"git commit schlug fehl: {out[:300]}")
        _, sha = await self._git("rev-parse", "--short", "HEAD")
        return {"path": posix, "commit": sha, "note": _wirkung(posix)}

    async def revert(self, commit: str) -> dict:
        """Eine Änderung zurücknehmen — als eigener Commit, nicht durch Löschen."""
        if not commit.strip().isalnum():
            raise SourceError("Das sieht nicht wie eine Commit-Kennung aus.")
        code, out = await self._git("revert", "--no-edit", commit.strip())
        if code != 0:
            raise SourceError(f"Zurücknehmen ging nicht: {out[:300]}")
        _, sha = await self._git("rev-parse", "--short", "HEAD")
        return {"reverted": commit.strip(), "commit": sha,
                "note": "Wirkt wie jede Änderung erst nach Neustart bzw. neuem Bauen."}


__all__ = ["SourceService", "SourceError", "source_dir", "unavailable_reason", "VERBOTEN"]
