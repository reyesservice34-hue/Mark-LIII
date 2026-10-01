"""Verified Core Evolution gate for MIA.

Proposals may be prepared autonomously, but live core activation requires:
1) a persisted proposal, 2) a real sandbox verification record, 3) the normal
approval-gated apply/restart tools. A separate watchdog owns catastrophic
rollback if the new backend cannot become healthy after activation.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..db import dumps, loads, now_iso


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class CoreEvolutionError(RuntimeError):
    pass


class CoreEvolutionService:
    def __init__(self, state) -> None:
        self.state = state
        self.db = state.db
        self.root = Path(os.environ.get("JARVIS_CC_SOURCE_DIR") or "/repo").resolve()
        self.evolution_dir = Path(state.settings.workspace_dir) / "jarvis-tools" / "_evolution"
        self.evolution_dir.mkdir(parents=True, exist_ok=True)
        self.pending_path = self.evolution_dir / "pending.json"

    def _proposal(self, proposal_id: str) -> tuple[dict, str, Path, str]:
        row = self.db.fetchone("SELECT * FROM self_tools WHERE name=?", (f"proposal:{proposal_id}",))
        if not row:
            raise CoreEvolutionError(f"Vorschlag '{proposal_id}' gibt es nicht.")
        rel = str(row["last_error"] or "").strip().lstrip("/")
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root) + os.sep):
            raise CoreEvolutionError("Ungültiger Zielpfad.")
        draft = Path(row["file"])
        if not draft.exists():
            raise CoreEvolutionError("Der Entwurf ist nicht mehr vorhanden.")
        candidate = draft.read_text(encoding="utf-8")
        if _sha(candidate) != row["source_sha"]:
            raise CoreEvolutionError("Der Entwurf hat sich seit dem Vorschlag verändert.")
        if not target.exists():
            raise CoreEvolutionError(f"Zieldatei fehlt im persistenten Quellbaum: {rel}")
        return dict(row), rel, target, candidate

    def activation_mode(self, rel: str) -> str:
        if rel.startswith("command_center/frontend/"):
            return "frontend_rebuild"
        compose = self.root / "docker-compose.command-center.yml"
        text = compose.read_text(encoding="utf-8", errors="replace") if compose.exists() else ""
        needle = f"./{rel}:/app/{rel}:"
        if needle in text:
            return "restart"
        return "image_rebuild_required"

    async def verify(self, proposal_id: str) -> dict:
        row, rel, target, candidate = self._proposal(proposal_id)
        baseline = target.read_text(encoding="utf-8", errors="replace")
        checks: list[dict] = []
        reason = str(row.get("description") or "")
        for marker in ("CAPABILITY_GAIN:", "EVIDENCE:", "VERIFICATION_PLAN:"):
            ok = marker in reason
            checks.append({"name": marker.rstrip(":"), "ok": ok})
        suffix = target.suffix.lower()
        if suffix == ".py":
            try:
                ast.parse(candidate, filename=rel)
                compile(candidate, rel, "exec")
                checks.append({"name": "python_compile", "ok": True})
            except Exception as e:
                checks.append({"name": "python_compile", "ok": False, "detail": str(e)[:500]})
        elif rel.startswith("command_center/frontend/"):
            result = self._verify_frontend(rel, candidate)
            checks.extend(result)
        else:
            # Text/config-like source: structural check only. Activation is still
            # blocked unless the file has a safe deployment mode.
            checks.append({"name": "nonempty_source", "ok": bool(candidate.strip())})

        changed = _sha(baseline) != _sha(candidate)
        checks.append({"name": "changes_existing_source", "ok": changed})
        if suffix == ".py":
            checks.extend(self._compare_python(baseline, candidate))
        mode = self.activation_mode(rel)
        deployable = mode != "image_rebuild_required"
        checks.append({"name": "safe_activation_path", "ok": deployable, "detail": mode})
        passed = all(bool(c.get("ok")) for c in checks)
        status = "passed" if passed else "failed"
        now = now_iso()
        self.db.execute(
            "INSERT INTO core_evolution_checks(proposal_id,file,baseline_sha,candidate_sha,status,stage,activation_mode,checks,verified_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'verified',?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET "
            "file=excluded.file,baseline_sha=excluded.baseline_sha,candidate_sha=excluded.candidate_sha,status=excluded.status,"
            "stage='verified',activation_mode=excluded.activation_mode,checks=excluded.checks,verified_at=excluded.verified_at,updated_at=excluded.updated_at",
            (proposal_id, rel, _sha(baseline), _sha(candidate), status, mode, dumps(checks), now, now, now))
        self.state.bus.publish("core.evolution.verified", {"proposal_id": proposal_id, "status": status,
                                                          "file": rel, "activation_mode": mode})
        return {"proposal_id": proposal_id, "file": rel, "status": status,
                "activation_mode": mode, "checks": checks,
                "eligible_for_apply": passed}

    @staticmethod
    def _public_surface(source: str) -> tuple[set[str], dict]:
        """Public top-level functions/classes and public methods, plus size metrics."""
        tree = ast.parse(source)
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                names.add(node.name)
                if isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                            names.add(f"{node.name}.{sub.name}")
        metrics = {"lines": len(source.splitlines()), "bytes": len(source.encode("utf-8")),
                   "public_names": len(names), "ast_nodes": sum(1 for _ in ast.walk(tree))}
        return names, metrics

    def _compare_python(self, baseline: str, candidate: str) -> list[dict]:
        """Static old-vs-new comparison. It does not execute the candidate.

        `public_api_preserved` is a gate: dropping a public function or method
        breaks callers, so it must be a deliberate, reviewed decision — not
        something an automated self-improvement slips in. `benchmark` records
        the before/after numbers so the reviewer sees the size of the change.
        """
        try:
            base_names, base_m = self._public_surface(baseline)
            cand_names, cand_m = self._public_surface(candidate)
        except SyntaxError as e:
            return [{"name": "public_api_preserved", "ok": False, "detail": f"unparseable: {e}"[:300]}]
        removed = sorted(base_names - cand_names)
        return [
            {"name": "public_api_preserved", "ok": not removed,
             "detail": ("removed: " + ", ".join(removed[:20])) if removed else "no public name removed"},
            {"name": "benchmark", "ok": True,
             "detail": dumps({"baseline": base_m, "candidate": cand_m,
                              "added": sorted(cand_names - base_names)[:20]})},
        ]

    def _verify_frontend(self, rel: str, candidate: str) -> list[dict]:
        frontend = self.root / "command_center" / "frontend"
        if not frontend.is_dir():
            return [{"name": "frontend_source", "ok": False, "detail": "frontend source missing"}]
        node = shutil.which("node")
        deps = frontend / "node_modules"
        if not deps.is_dir():
            fallback = Path("/app/command_center/frontend/node_modules")
            deps = fallback if fallback.is_dir() else deps
        if not node or not deps.is_dir():
            return [{"name": "frontend_toolchain", "ok": False, "detail": "node/node_modules missing"}]
        tmp = Path(tempfile.mkdtemp(prefix="mia-core-evolve-"))
        try:
            work = tmp / "frontend"
            shutil.copytree(frontend, work, ignore=shutil.ignore_patterns("node_modules", "dist"))
            (work / "node_modules").symlink_to(deps, target_is_directory=True)
            rel_inside = Path(rel).relative_to("command_center/frontend")
            cand_path = work / rel_inside
            cand_path.parent.mkdir(parents=True, exist_ok=True)
            cand_path.write_text(candidate, encoding="utf-8")
            tsc = deps / ".bin" / "tsc"
            p = subprocess.run([node, str(tsc), "--noEmit", "-p", "tsconfig.json"], cwd=work,
                               capture_output=True, text=True, timeout=240)
            checks = [{"name": "typescript", "ok": p.returncode == 0,
                       "detail": (p.stdout + p.stderr)[-1200:] if p.returncode else "ok"}]
            if p.returncode == 0:
                vite = deps / ".bin" / "vite"
                outdir = tmp / "dist"
                p2 = subprocess.run([node, str(vite), "build", "--outDir", str(outdir), "--emptyOutDir"],
                                    cwd=work, capture_output=True, text=True, timeout=600)
                checks.append({"name": "vite_build", "ok": p2.returncode == 0,
                               "detail": (p2.stdout + p2.stderr)[-1200:] if p2.returncode else "ok"})
            return checks
        except Exception as e:
            return [{"name": "frontend_sandbox", "ok": False, "detail": str(e)[:700]}]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def ensure_verified(self, proposal_id: str) -> dict:
        row = self.db.fetchone("SELECT * FROM core_evolution_checks WHERE proposal_id=?", (proposal_id,))
        if not row or row["status"] != "passed":
            raise CoreEvolutionError("Core-Änderung blockiert: zuerst self.verify erfolgreich ausführen.")
        prop, rel, _target, candidate = self._proposal(proposal_id)
        if row["candidate_sha"] != _sha(candidate):
            raise CoreEvolutionError("Core-Änderung blockiert: der Entwurf änderte sich nach der Verifikation.")
        if row["activation_mode"] == "image_rebuild_required":
            raise CoreEvolutionError("Diese Datei hat noch keinen sicheren Aktivierungspfad. Erst Deployment-Pfad/Mount herstellen.")
        return {"proposal_id": proposal_id, "file": rel, "candidate_sha": row["candidate_sha"],
                "activation_mode": row["activation_mode"], "checks": loads(row["checks"], [])}

    def arm_after_apply(self, applied: dict, verified: dict) -> dict:
        proposal_id = str(applied["proposal_id"])
        marker = {"proposal_id": proposal_id, "file": applied["file"], "backup": applied["backup"],
                  "candidate_sha": verified["candidate_sha"], "activation_mode": verified["activation_mode"],
                  "phase": "applied_pending_activation", "failures": 0, "healthy": 0,
                  "updated_at": now_iso()}
        self.pending_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.execute("UPDATE core_evolution_checks SET stage='applied_pending_activation',applied_at=?,updated_at=? WHERE proposal_id=?",
                        (now_iso(), now_iso(), proposal_id))
        return marker

    def begin_activation(self) -> dict | None:
        if not self.pending_path.exists():
            return None
        try:
            marker = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if marker.get("phase") != "applied_pending_activation":
            return marker
        marker["phase"] = "activating"
        marker["failures"] = 0
        marker["healthy"] = 0
        marker["updated_at"] = now_iso()
        self.pending_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.execute("UPDATE core_evolution_checks SET stage='activating',activated_at=?,updated_at=? WHERE proposal_id=?",
                        (now_iso(), now_iso(), marker["proposal_id"]))
        return marker

    def status(self) -> dict:
        rows = self.db.fetchall("SELECT * FROM core_evolution_checks ORDER BY updated_at DESC LIMIT 20") or []
        out=[]
        for r in rows:
            x=dict(r); x["checks"]=loads(x.get("checks"), []); out.append(x)
        pending=None
        if self.pending_path.exists():
            try: pending=json.loads(self.pending_path.read_text(encoding="utf-8"))
            except Exception: pending={"error":"marker unreadable"}
        return {"pending": pending, "history": out}
