"""
File center — a sandboxed workspace, never the whole filesystem.

Every path is resolved relative to the configured workspace and rejected if
it escapes it (`..`, absolute paths, symlinks pointing outside). Deletes go to
a `.trash` folder inside the workspace so an agent mistake is recoverable.
"""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
from pathlib import Path

from ..db import Database, dumps, loads, new_id, now_iso
from ..events import EventBus
from . import file_security

TRASH = ".trash"
VERSIONS = ".versions"
_SAFE_NAME = re.compile(r'[<>:"|?*\x00-\x1f\\]')


class WorkspaceError(Exception):
    pass


class FileService:
    def __init__(self, root: Path, db: Database, bus: EventBus, max_upload_mb: int = 200):
        self.root = Path(root).resolve()
        self.db = db
        self.bus = bus
        self.max_upload = max_upload_mb * 1024 * 1024
        for sub in ("uploads", "documents", "agents", TRASH, VERSIONS):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ── path safety ──────────────────────────────────────────────────────
    def resolve(self, rel: str, *, must_exist: bool = False) -> Path:
        rel = (rel or "").replace("\\", "/").strip()
        # The host project is mounted read-only at workspace/Mark-LIII. Users
        # naturally quote its host path, so translate that one explicit prefix
        # before applying the ordinary workspace confinement below.
        if rel in ("/root/Mark-LIII", "root/Mark-LIII"):
            rel = "Mark-LIII"
        elif rel.startswith(("/root/Mark-LIII/", "root/Mark-LIII/")):
            rel = "Mark-LIII/" + rel.split("root/Mark-LIII/", 1)[1]
        else:
            rel = rel.lstrip("/")
        candidate = (self.root / rel).resolve() if rel else self.root
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise WorkspaceError("path escapes the workspace")
        if must_exist and not candidate.exists():
            raise WorkspaceError("not found")
        return candidate

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def safe_name(name: str) -> str:
        name = Path(name or "file").name
        name = _SAFE_NAME.sub("_", name).strip(". ")
        return name[:180] or "file"

    def unique(self, directory: Path, name: str) -> Path:
        dest = directory / name
        stem, suffix = Path(name).stem, Path(name).suffix
        i = 1
        while dest.exists():
            dest = directory / f"{stem}_{i}{suffix}"
            i += 1
        return dest

    # ── browse ───────────────────────────────────────────────────────────
    def entry(self, path: Path) -> dict:
        st = path.stat()
        rel = self.rel(path)
        return {
            "path": rel, "name": path.name, "is_dir": path.is_dir(),
            "size": 0 if path.is_dir() else st.st_size, "modified_at": _iso(st.st_mtime),
            "mime": "" if path.is_dir() else (mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
        }

    def list(self, rel: str = "") -> dict:
        directory = self.resolve(rel, must_exist=True)
        if not directory.is_dir():
            raise WorkspaceError("not a directory")
        entries = []
        for p in sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name in (TRASH, VERSIONS) and directory == self.root:
                continue
            try:
                entries.append(self.entry(p))
            except OSError:
                continue
        return {"path": self.rel(directory), "entries": entries,
                "parent": self.rel(directory.parent) if directory != self.root else None}

    def search(self, q: str, limit: int = 100) -> list[dict]:
        q = q.lower().strip()
        out: list[dict] = []
        if not q:
            return out
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in (TRASH, VERSIONS) and not d.startswith(".")]
            for f in filenames:
                if q in f.lower():
                    try:
                        out.append(self.entry(Path(dirpath) / f))
                    except OSError:
                        pass
                    if len(out) >= limit:
                        return out
        return out

    def read_text(self, rel: str, max_bytes: int = 512 * 1024) -> dict:
        path = self.resolve(rel, must_exist=True)
        if path.is_dir():
            raise WorkspaceError("is a directory")
        raw = path.read_bytes()[: max_bytes + 1]
        truncated = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        return {"path": self.rel(path), "content": text, "truncated": truncated,
                "size": path.stat().st_size, "mime": mimetypes.guess_type(path.name)[0] or ""}

    # ── mutate ───────────────────────────────────────────────────────────
    def write_text(self, rel: str, content: str, *, source: str = "agent", owner: str = "",
                   task_id: str | None = None, overwrite: bool = True) -> dict:
        path = self.resolve(rel)
        if path == self.root or path.is_dir():
            raise WorkspaceError("target is a directory")
        if path.exists() and not overwrite:
            raise WorkspaceError("file exists")
        raw = content.encode("utf-8")
        if len(raw) > self.max_upload:
            raise WorkspaceError("content too large")
        check = file_security.classify(path.name, data=raw)
        if check["risk"] == "high":
            raise WorkspaceError("Dateisicherheit: " + "; ".join(check["flags"]))
        if path.exists():
            # Original -> Bearbeitung -> neue Version: die bisherige Fassung geht nie ersatzlos verloren.
            self._snapshot(path, source=source, owner=owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self._register(path, source=source, owner=owner, task_id=task_id, security=check)

    def write_bytes(self, rel: str, data: bytes, *, source: str = "agent", owner: str = "",
                    task_id: str | None = None, overwrite: bool = True) -> dict:
        """Wie write_text, aber für erzeugte Binärdateien (PDF, XLSX, PNG, ...)."""
        path = self.resolve(rel)
        if path == self.root or path.is_dir():
            raise WorkspaceError("target is a directory")
        if path.exists() and not overwrite:
            raise WorkspaceError("file exists")
        if len(data) > self.max_upload:
            raise WorkspaceError("content too large")
        check = file_security.classify(path.name, data=data)
        if check["risk"] == "high":
            raise WorkspaceError("Dateisicherheit: " + "; ".join(check["flags"]))
        if path.exists():
            self._snapshot(path, source=source, owner=owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._register(path, source=source, owner=owner, task_id=task_id, security=check)

    # ── versioning ───────────────────────────────────────────────────────
    def _snapshot(self, path: Path, *, source: str = "", owner: str = "") -> dict | None:
        """Die aktuelle Fassung von `path` unter .versions/ sichern, bevor sie überschrieben wird.

        Ein Eintrag je gesicherter alter Fassung. Leere Dateien werden trotzdem gesichert — eine Datei,
        die gerade geleert wurde, ist genauso ein Verlust wie eine, die geändert wurde.
        """
        try:
            if not path.is_file():
                return None
            rel = self.rel(path)
            vdir = self.root / VERSIONS / rel
            vdir.mkdir(parents=True, exist_ok=True)
            stamp = now_iso().replace(":", "").replace("-", "")[:15]   # sortierbar, dateinamentauglich
            vpath = self.unique(vdir, f"{stamp}_{path.name}")
            shutil.copy2(path, vpath)
            row = {"id": new_id("filever"), "path": rel, "version_path": self.rel(vpath),
                  "size": vpath.stat().st_size, "created_at": now_iso(),
                  "source": source, "owner": owner}
            self.db.insert("file_versions", row)
            return row
        except OSError:
            return None   # eine fehlgeschlagene Sicherung darf das Schreiben selbst nie verhindern

    def versions(self, rel: str) -> list[dict]:
        path = self.resolve(rel)
        rows = self.db.fetchall(
            "SELECT * FROM file_versions WHERE path=? ORDER BY created_at DESC", (self.rel(path),))
        for r in rows:
            r["exists"] = (self.root / r["version_path"]).exists()
        return rows

    def restore_version(self, version_id: str, *, source: str = "restore", owner: str = "") -> dict:
        row = self.db.fetchone("SELECT * FROM file_versions WHERE id=?", (version_id,))
        if not row:
            raise WorkspaceError("version not found")
        vpath = self.root / row["version_path"]
        if not vpath.is_file():
            raise WorkspaceError("this version's file is gone")
        target = self.root / row["path"]
        if target.exists():
            # Auch der aktuelle Stand geht nicht verloren — eine Wiederherstellung ist selbst
            # wieder umkehrbar, statt die Version davor zu überschreiben.
            self._snapshot(target, source=source, owner=owner)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vpath, target)
        return self._register(target, source=source, owner=owner, task_id=None)

    def save_upload(self, name: str, data_iter, *, subdir: str = "uploads", owner: str = "",
                    task_id: str | None = None) -> dict:
        directory = self.resolve(subdir)
        directory.mkdir(parents=True, exist_ok=True)
        dest = self.unique(directory, self.safe_name(name))
        size = 0
        with open(dest, "wb") as out:
            for chunk in data_iter:
                size += len(chunk)
                if size > self.max_upload:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise WorkspaceError(f"file exceeds {self.max_upload // (1024 * 1024)} MB")
                out.write(chunk)
        # Erst prüfen, dann behalten: ein hochgeladener getarnter Programmcode (egal welcher
        # Name) oder ein Archiv mit Pfad-Flucht wird gar nicht erst Teil des Arbeitsbereichs.
        check = file_security.classify(dest.name, path=dest)
        if check["risk"] == "high":
            dest.unlink(missing_ok=True)
            raise WorkspaceError("Dateisicherheit: " + "; ".join(check["flags"]))
        return self._register(dest, source="upload", owner=owner, task_id=task_id, security=check)

    def mkdir(self, rel: str) -> dict:
        path = self.resolve(rel)
        path.mkdir(parents=True, exist_ok=True)
        return self.entry(path)

    def rename(self, rel: str, new_name: str) -> dict:
        path = self.resolve(rel, must_exist=True)
        if path == self.root:
            raise WorkspaceError("cannot rename the workspace root")
        dest = path.parent / self.safe_name(new_name)
        if dest.exists():
            raise WorkspaceError("target exists")
        path.rename(dest)
        self._move_record(self.rel(path), self.rel(dest))
        return self.entry(dest)

    def move(self, rel: str, dest_dir: str) -> dict:
        path = self.resolve(rel, must_exist=True)
        target_dir = self.resolve(dest_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = self.unique(target_dir, path.name)
        shutil.move(str(path), str(dest))
        self._move_record(self.rel(path), self.rel(dest))
        return self.entry(dest)

    def delete(self, rel: str) -> dict:
        path = self.resolve(rel, must_exist=True)
        if path == self.root:
            raise WorkspaceError("cannot delete the workspace root")
        if self.rel(path).startswith(TRASH):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            self.db.execute("DELETE FROM files WHERE path=?", (self.rel(path),))
            return {"deleted": True, "permanent": True}
        trash_dir = self.root / TRASH
        dest = self.unique(trash_dir, f"{now_iso()[:19].replace(':', '')}_{path.name}")
        shutil.move(str(path), str(dest))
        self._move_record(self.rel(path), self.rel(dest))
        return {"deleted": True, "permanent": False, "trash_path": self.rel(dest)}

    # ── metadata ─────────────────────────────────────────────────────────
    def _register(self, path: Path, *, source: str, owner: str, task_id: str | None,
                  security: dict | None = None) -> dict:
        info = self.entry(path)
        existing = self.db.fetchone("SELECT id, created_at, meta FROM files WHERE path=?", (info["path"],))
        meta = loads(existing["meta"], {}) if existing else {}
        if security is not None:
            meta["security"] = security
        row = {
            "id": existing["id"] if existing else new_id("file"), "path": info["path"], "name": info["name"],
            "size": info["size"], "mime": info["mime"],
            "created_at": existing["created_at"] if existing else now_iso(), "updated_at": now_iso(),
            "owner": owner, "source": source, "task_id": task_id, "meta": dumps(meta),
        }
        self.db.upsert("files", row)
        info.update({"id": row["id"], "source": source, "owner": owner, "task_id": task_id, "meta": meta})
        self.bus.publish("file.changed", info)
        return info

    def _move_record(self, old: str, new: str) -> None:
        self.db.execute("UPDATE files SET path=?, name=?, updated_at=? WHERE path=?",
                        (new, Path(new).name, now_iso(), old))

    def recent(self, limit: int = 30, source: str = "") -> list[dict]:
        if source:
            rows = self.db.fetchall("SELECT * FROM files WHERE source=? ORDER BY updated_at DESC LIMIT ?",
                                    (source, limit))
        else:
            rows = self.db.fetchall("SELECT * FROM files ORDER BY updated_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = loads(r["meta"], {})
            r["exists"] = (self.root / r["path"]).exists()
        return rows

    def usage(self) -> dict:
        total, count = 0, 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in (TRASH, VERSIONS)]
            for f in filenames:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                    count += 1
                except OSError:
                    pass
        return {"bytes": total, "files": count, "root": str(self.root)}


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
