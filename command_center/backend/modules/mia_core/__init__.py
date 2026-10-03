"""
MIA Core (Kapitel 43 aus MASTER-PROMPT v2.0) — lesende Übersichts- und
Detailansicht der fünf echten Gedächtnisebenen von Mark-LIII.

Liest ausschließlich vom read-only Bind-Mount MARK_LIII_ROOT (siehe
orchestrator/mark_liii_tools.py, docker-compose.command-center.yml). Dieses
Modul selbst schreibt nirgends hinein — es gibt bewusst keinen PUT/PATCH/DELETE
Endpunkt. Kapitel 43.7: Standard ist Read-only, und das ist hier technisch
erzwungen, nicht nur Konvention.

Startansicht liefert nur Metadaten (Kapitel 43.3) — niemals eine komplette
Datei. Die Detailansicht je Ebene liest serverseitig paginiert nach (Kapitel
43.5, 46): JSONL-Dateien zeilenweise, nie als Ganzes geparst in den Speicher
der Antwort.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import current_principal
from ...orchestrator.mark_liii_tools import MARK_LIII_ROOT
from .. import ModuleSpec

router = APIRouter(prefix="/api/mia-core", tags=["mia_core"])

LAYERS: dict[str, dict[str, Any]] = {
    "episodic": {
        "label": "Episodisches Gedächtnis", "color": "blue",
        "path": MARK_LIII_ROOT / "brain" / "memory" / "episodic" / "sessions.jsonl",
        "kind": "jsonl", "purpose": "Sitzungen, Gespräche, Ereignisse, Abläufe",
    },
    "semantic": {
        "label": "Semantisches Archiv", "color": "violet",
        "path": MARK_LIII_ROOT / "brain" / "memory" / "semantic" / "archive.jsonl",
        "kind": "jsonl", "purpose": "Wissen, Fakten, Regeln, Zusammenfassungen",
    },
    "working": {
        "label": "Arbeitsgedächtnis", "color": "teal",
        "path": MARK_LIII_ROOT / "brain" / "memory" / "working",
        "kind": "dir", "purpose": "Aktueller Kontext, laufende Prozesse, Zwischenstände",
    },
    "long_term": {
        "label": "Langzeitgedächtnis", "color": "amber",
        "path": MARK_LIII_ROOT / "memory" / "long_term.json",
        "kind": "json", "purpose": "Präferenzen, Ziele, Regeln, wichtige Infos",
    },
    "knowledge_index": {
        "label": "Knowledge-Index", "color": "magenta",
        "path": MARK_LIII_ROOT / "knowledge" / "index.json",
        "kind": "json", "purpose": "Indexierter Wissensbestand, Quellen, Verweise",
    },
}


def _layer_or_404(layer: str) -> dict:
    spec = LAYERS.get(layer)
    if not spec:
        raise HTTPException(404, f"Unbekannte Gedächtnisebene '{layer}'")
    return spec


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _count_lines(path: Path) -> int:
    n = 0
    with path.open("rb") as f:
        for _ in f:
            n += 1
    return n


def _meta(layer: str, spec: dict) -> dict:
    path: Path = spec["path"]
    out = {
        "id": layer, "label": spec["label"], "color": spec["color"], "purpose": spec["purpose"],
        "path": str(path).replace(str(MARK_LIII_ROOT), "") or "/",
        "status": "missing", "size": None, "modified_at": None, "entries": None,
    }
    try:
        if spec["kind"] == "dir":
            if not path.is_dir():
                return out
            files = [f for f in path.iterdir() if f.is_file()]
            out["status"] = "active"
            out["entries"] = len(files)
            out["size"] = sum(f.stat().st_size for f in files)
            mtimes = [f.stat().st_mtime for f in files] or [path.stat().st_mtime]
            out["modified_at"] = _iso(max(mtimes))
        else:
            if not path.is_file():
                return out
            st = path.stat()
            out["status"] = "active"
            out["size"] = st.st_size
            out["modified_at"] = _iso(st.st_mtime)
            if spec["kind"] == "jsonl":
                out["entries"] = _count_lines(path)
            else:
                try:
                    data = json.loads(path.read_text(encoding="utf-8") or "null")
                    out["entries"] = len(data) if isinstance(data, (list, dict)) else (0 if data is None else 1)
                except Exception as e:
                    out["status"] = "error"
                    out["detail"] = f"nicht lesbar: {e}"[:200]
    except OSError as e:
        out["status"] = "error"
        out["detail"] = str(e)[:200]
    return out


@router.get("/overview")
async def overview(_: Principal = Depends(current_principal)):
    return {"root_connected": MARK_LIII_ROOT.is_dir(), "layers": [_meta(k, v) for k, v in LAYERS.items()]}


def _tail_jsonl(path: Path, offset: int, limit: int, q: str) -> tuple[list[dict], int]:
    matching: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line and (not q or q.lower() in line.lower()):
                matching.append(line)
    total = len(matching)
    matching.reverse()
    page = matching[offset:offset + limit]
    out = []
    for raw in page:
        try:
            out.append(json.loads(raw))
        except Exception:
            out.append({"_roh": raw})
    return out, total


def _entries_json(path: Path, offset: int, limit: int, q: str) -> tuple[list[Any], int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception as e:
        raise HTTPException(500, f"Datei nicht lesbar: {e}")
    if isinstance(data, list):
        items: list[Any] = data
    elif isinstance(data, dict):
        items = [{"schluessel": k, "wert": v} for k, v in data.items()]
    else:
        items = [] if data is None else [data]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in json.dumps(i, ensure_ascii=False, default=str).lower()]
    total = len(items)
    return items[offset:offset + limit], total


def _entries_dir(path: Path, offset: int, limit: int, q: str) -> tuple[list[dict], int]:
    if not path.is_dir():
        return [], 0
    files = sorted((f for f in path.iterdir() if f.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    if q:
        ql = q.lower()
        files = [f for f in files if ql in f.name.lower()]
    total = len(files)
    page = files[offset:offset + limit]
    out = []
    for f in page:
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "modified_at": _iso(st.st_mtime)})
    return out, total


@router.get("/{layer}/entries")
async def entries(layer: str, offset: int = 0, limit: int = 50, q: str = "",
                  _: Principal = Depends(current_principal)):
    spec = _layer_or_404(layer)
    path: Path = spec["path"]
    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    if spec["kind"] == "dir":
        items, total = _entries_dir(path, offset, limit, q)
    elif spec["kind"] == "jsonl":
        items, total = ([], 0) if not path.is_file() else _tail_jsonl(path, offset, limit, q)
    else:
        items, total = ([], 0) if not path.is_file() else _entries_json(path, offset, limit, q)
    return {"entries": items, "total": total, "offset": offset, "limit": limit}


MODULE = ModuleSpec(
    id="mia_core", title="MIA Core", router=router, icon="cpu", path="/mia-core", order=65,
    description="Die fünf Gedächtnisebenen — nur lesend",
    commands=[{"id": "mia_core.open", "title": "MIA Core öffnen", "path": "/mia-core", "shortcut": "g m"}],
)
