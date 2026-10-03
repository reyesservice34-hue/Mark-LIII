"""
Training Hub (Kapitel 45 aus MASTER-PROMPT v2.0) — Übersicht über den
Trainings-, Codex-, Lektionen- und Ratschläge-Bestand, sowie Status der
selbstgeschriebenen Skills.

Liest ausschließlich aus der Trainings-Hub-Verzeichnisstruktur (Kapitel 31.1).
Nur lesend: keine Änderungen, Versioning oder Löschungen von hier aus.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...auth import Principal
from ...deps import AppState, current_principal, get_state
from ...orchestrator.mark_liii_tools import MARK_LIII_ROOT
from .. import ModuleSpec

router = APIRouter(prefix="/api/training", tags=["training"])

TRAINING_ROOT = MARK_LIII_ROOT / "command-center" / "training"

CATEGORIES = {
    "claude-code": {
        "label": "Claude Code", "icon": "code",
        "purpose": "Codebeispiele und praktische Code-Schnipsel",
    },
    "codex": {
        "label": "Codex", "icon": "book-open",
        "purpose": "Wiederverwendbare Wissenbausteine und Referenzen",
    },
    "lektionen": {
        "label": "Lektionen", "icon": "graduation-cap",
        "purpose": "Strukturierte Lerneinheiten zu Prozessen und Fachbereichen",
    },
    "ratschlaege": {
        "label": "Ratschläge", "icon": "lightbulb",
        "purpose": "Best Practices, Tipps, bewährte Vorgehensweisen",
    },
    "uebungen": {
        "label": "Übungen", "icon": "list-checks",
        "purpose": "Trainingsinhalte und Übungsaufgaben",
    },
    "skills-neu": {
        "label": "Self-Written Skills", "icon": "puzzle",
        "purpose": "Skills, die während Trainings selbst geschrieben wurden",
    },
}


def _cat_meta(cat: str, spec: dict) -> dict:
    path = TRAINING_ROOT / cat
    out = {
        "id": cat, "label": spec["label"], "icon": spec["icon"], "purpose": spec["purpose"],
        "status": "missing", "file_count": 0, "size_bytes": 0,
    }
    if not path.is_dir():
        return out
    try:
        files = [f for f in path.iterdir() if f.is_file()]
        out["status"] = "active"
        out["file_count"] = len(files)
        out["size_bytes"] = sum(f.stat().st_size for f in files)
    except OSError:
        out["status"] = "error"
    return out


@router.get("/overview")
async def overview(_: Principal = Depends(current_principal)):
    root_exists = TRAINING_ROOT.is_dir()
    categories = [_cat_meta(cat, spec) for cat, spec in CATEGORIES.items()]
    log_path = TRAINING_ROOT / "protokoll.jsonl"
    recent_logs: list[dict] = []
    if log_path.is_file():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-10:]
                for line in lines:
                    try:
                        recent_logs.append(json.loads(line))
                    except Exception:
                        pass
        except OSError:
            pass
    return {
        "root_connected": root_exists, "training_root": str(TRAINING_ROOT),
        "categories": categories, "recent_log_entries": recent_logs,
    }


@router.get("/{category}/items")
async def category_items(category: str, offset: int = 0, limit: int = 50,
                         _: Principal = Depends(current_principal)):
    if category not in CATEGORIES:
        raise HTTPException(404, f"Unbekannte Trainings-Kategorie '{category}'")
    path = TRAINING_ROOT / category
    if not path.is_dir():
        return {"items": [], "total": 0, "offset": offset}
    try:
        files = sorted((f for f in path.iterdir() if f.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        total = len(files)
        page = files[offset:offset + limit]
        items = []
        for f in page:
            st = f.stat()
            items.append({"name": f.name, "path": f.relative_to(TRAINING_ROOT).as_posix(),
                          "size": st.st_size, "modified_at": st.st_mtime})
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    except OSError as e:
        raise HTTPException(500, f"Fehler beim Lesen der Kategorie: {e}")


@router.get("/log/entries")
async def log_entries(offset: int = 0, limit: int = 100,
                      _: Principal = Depends(current_principal)):
    log_path = TRAINING_ROOT / "protokoll.jsonl"
    if not log_path.is_file():
        return {"entries": [], "total": 0}
    try:
        entries = []
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
        total = len(entries)
        entries.reverse()
        page = entries[offset:offset + limit]
        return {"entries": page, "total": total, "offset": offset}
    except OSError as e:
        raise HTTPException(500, f"Protokoll nicht lesbar: {e}")


MODULE = ModuleSpec(
    id="training", title="Training", router=router, icon="graduation-cap", path="/training",
    order=48, min_role="operator", description="Trainings-Hub: Claude Code, Codex, Lektionen, Ratschläge",
    commands=[{"id": "training.open", "title": "Training öffnen", "path": "/training", "shortcut": "g h"}],
)
