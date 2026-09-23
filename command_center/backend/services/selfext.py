"""
Selbsterweiterung — JARVIS schreibt sich neue Werkzeuge.

Der Ablauf ist absichtlich dreistufig, weil Schreiben harmlos ist und
Ausführen nicht:

  schreiben   eine Datei im eigenen Ordner. Ändert am laufenden System nichts.
  prüfen      in einem eigenen Prozess importieren, die Form validieren und,
              falls vorhanden, den mitgelieferten Selbsttest laufen lassen.
              Stürzt er ab, bleibt er ein Entwurf.
  freigeben   erst jetzt landet er in der Registry. Das ist `critical`, geht
              also durch das Freigabe-Gatter — ein Mensch sieht die Quelle,
              bevor sie Teil des Systems wird.

Wo die Grenze wirklich verläuft — damit sich niemand auf etwas verlässt, das
nicht existiert:

  * Geschrieben wird ausschließlich in EXT_DIRNAME unterhalb des
    Arbeitsbereichs. Jeder Pfad außerhalb wird abgelehnt, auch über `..`.
  * Ein neues Werkzeug darf keinen bestehenden Namen überschreiben. Sonst
    könnte `filesystem.delete` still etwas anderes werden.
  * Der Import läuft in einem getrennten Prozess mit Zeitlimit. Eine
    Endlosschleife oder ein Absturz beim Importieren trifft den Server nicht.
  * Aktiv wird nichts ohne Freigabe.

Was das NICHT ist: eine Sandbox. Ein freigegebenes Werkzeug läuft im selben
Prozess wie der Server und kann alles, was dieser Prozess kann. Die Sicherung
ist die Freigabe davor plus die lesbare Quelle plus die Prüfspur — nicht eine
technische Einsperrung. Das gehört gesagt, statt ein Sicherheitsgefühl zu
erzeugen, das die Umsetzung nicht deckt.
"""
from __future__ import annotations

import ast
import asyncio
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import subprocess
from pathlib import Path

from ..db import Database, new_id, now_iso
from ..orchestrator.tool_registry import ToolContext, ToolSpec

REPO_ROOT = Path(os.environ.get("JARVIS_CC_SOURCE_DIR") or Path(__file__).resolve().parents[3]).resolve()

# Sein eigener Quelltext. Lesen darf er darin alles — wer sich verbessern
# soll, muss sich erst verstehen können. Das Frontend gehört dazu: ohne das
# kann er das Dashboard nicht umbauen, und genau das ist eine der
# naheliegendsten Verbesserungen, die er anbringen könnte.
SELF_TREES = ("command_center/backend", "command_center/frontend/src", "core", "actions", "plugins")

# Dateien, deren Änderung nicht nur einen Neustart braucht, sondern einen
# neuen Build. Das ist kein Detail: wer den Unterschied nicht kennt, ändert
# etwas am Dashboard, startet neu und sieht keine Wirkung.
NEEDS_BUILD_PREFIX = "command_center/frontend/"

# Die Dateien, in denen die Bremsen sitzen. Ändern darf er sie, aber die
# Freigabe sagt dann ausdrücklich, worum es geht. Der Unterschied ist nicht
# technisch, sondern der zwischen „eine Zeile in einem Werkzeug" und „die
# Stelle, die entscheidet, wer was darf".
CRITICAL_FILES = (
    "command_center/backend/auth.py",
    "command_center/backend/deps.py",
    "command_center/backend/services/approvals.py",
    "command_center/backend/services/selfext.py",
    "command_center/backend/orchestrator/runtime.py",
    "command_center/backend/orchestrator/tool_registry.py",
    "command_center/backend/logbook.py",
    "command_center/backend/app.py",
)

EXT_DIRNAME = "jarvis-tools"
NAME_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
CHECK_TIMEOUT = 20.0
MAX_SOURCE_BYTES = 120_000

# Namensräume, die dem Kern gehören. Ein selbstgeschriebenes Werkzeug darf
# dort nicht hinein — nicht weil der Präfix magisch wäre, sondern damit ein
# Aufruf, den ein Mensch für den eingebauten hält, auch der eingebaute ist.
RESERVED_PREFIXES = ("agent", "approval", "task", "memory", "terminal", "filesystem",
                     "desktop", "teach", "procedure", "self")


class SelfExtError(Exception):
    """Etwas, das der Agent hören muss, in klaren Worten."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Der Prüfprozess. Läuft getrennt, gibt eine Zeile JSON zurück.
_PROBE = r'''
import importlib.util, inspect, json, sys, asyncio

path = sys.argv[1]
out = {"ok": False, "error": "", "tool": None, "selftest": ""}
try:
    spec = importlib.util.spec_from_file_location("jarvis_self_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tool = getattr(mod, "TOOL", None)
    if not isinstance(tool, dict):
        raise ValueError("Die Datei muss ein Dict namens TOOL enthalten.")
    for key in ("name", "description", "input_schema"):
        if key not in tool:
            raise ValueError(f"TOOL fehlt der Schlüssel '{key}'.")
    run = getattr(mod, "run", None)
    if run is None or not callable(run):
        raise ValueError("Die Datei muss eine Funktion run(args) enthalten.")
    if not inspect.iscoroutinefunction(run):
        raise ValueError("run(args) muss async def sein.")
    st = getattr(mod, "selftest", None)
    if st is not None:
        res = st()
        if inspect.isawaitable(res):
            res = asyncio.get_event_loop().run_until_complete(res)
        out["selftest"] = str(res)[:400] if res is not None else "ok"
    out["tool"] = {"name": tool["name"], "description": tool["description"],
                   "input_schema": tool["input_schema"], "risk": tool.get("risk", "medium"),
                   "min_role": tool.get("min_role", "operator")}
    out["ok"] = True
except Exception as e:
    out["error"] = f"{e.__class__.__name__}: {e}"
print(json.dumps(out))
'''


class SelfExtension:
    def __init__(self, db: Database, workspace: Path, log, bus) -> None:
        self.db = db
        self.dir = Path(workspace) / EXT_DIRNAME
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.bus = bus

    # ── Pfade ────────────────────────────────────────────────────────────
    def _file(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise SelfExtError("Ein Werkzeugname sieht so aus: bereich.aktion — Kleinbuchstaben, "
                               "ein Punkt, z. B. wetter.heute")
        if name.split(".", 1)[0] in RESERVED_PREFIXES:
            raise SelfExtError(f"'{name.split('.', 1)[0]}' gehört dem Kern. Nimm einen eigenen Bereich.")
        path = (self.dir / f"{name.replace('.', '__')}.py").resolve()
        # Gürtel und Hosenträger: der Name kann nach der Prüfung oben keinen
        # Pfadtrenner mehr enthalten, aber verlassen wird der Ordner trotzdem
        # nicht.
        if not str(path).startswith(str(self.dir.resolve()) + "/"):
            raise SelfExtError("Außerhalb des eigenen Werkzeugordners wird nicht geschrieben.")
        return path

    # ── schreiben ────────────────────────────────────────────────────────
    def write(self, name: str, source: str, *, author: str, existing_tools: set[str]) -> dict:
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise SelfExtError("Die Datei ist zu groß. Zerleg das Werkzeug in kleinere.")
        path = self._file(name)
        row = self.db.fetchone("SELECT name FROM self_tools WHERE name=?", (name,))
        if name in existing_tools and row is None:
            raise SelfExtError(f"'{name}' gibt es bereits als eingebautes Werkzeug. Wähl einen anderen Namen.")
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise SelfExtError(f"Das ist kein gültiges Python: Zeile {e.lineno}, {e.msg}") from e

        path.write_text(source, encoding="utf-8")
        now = now_iso()
        if row is None:
            self.db.insert("self_tools", {"name": name, "file": str(path), "description": "",
                                          "risk": "medium", "status": "draft", "source_sha": _sha(source),
                                          "last_error": "", "created_at": now, "updated_at": now,
                                          "written_by": author, "activated_by": "", "activated_at": None})
        else:
            # Eine geänderte Quelle verliert ihre Freigabe. Sonst wäre die
            # Freigabe eine Freigabe für Code, den niemand gesehen hat.
            self.db.execute("UPDATE self_tools SET source_sha=?, status='draft', last_error='', "
                            "updated_at=?, written_by=?, activated_by='', activated_at=NULL WHERE name=?",
                            (_sha(source), now, author, name))
        self.log.info("selfext", f"{author} schrieb {name} ({len(source)} Zeichen)")
        self.bus.publish("selftool.changed", {"name": name, "status": "draft"})
        return {"name": name, "file": str(path), "status": "draft", "bytes": len(source)}

    def read(self, name: str) -> str:
        path = self._file(name)
        if not path.exists():
            raise SelfExtError(f"'{name}' gibt es noch nicht.")
        return path.read_text(encoding="utf-8")

    # ── prüfen ───────────────────────────────────────────────────────────
    async def check(self, name: str) -> dict:
        path = self._file(name)
        if not path.exists():
            raise SelfExtError(f"'{name}' gibt es noch nicht — erst self.write.")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", _PROBE, str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(self.dir))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            self._fail(name, "Der Import lief in die Zeitüberschreitung — eine Endlosschleife beim Laden?")
            raise SelfExtError("Der Import lief in die Zeitüberschreitung. Beim Importieren darf nichts "
                               "Langes passieren; leg das in run().")
        try:
            result = json.loads((out or b"").decode("utf-8").strip().splitlines()[-1])
        except (ValueError, IndexError):
            detail = (err or b"").decode("utf-8")[-400:] or "keine Ausgabe"
            self._fail(name, detail)
            raise SelfExtError(f"Die Prüfung lieferte kein Ergebnis: {detail}")

        if not result.get("ok"):
            self._fail(name, result.get("error", "unbekannt"))
            raise SelfExtError(result.get("error") or "Die Prüfung schlug fehl.")

        decl = result["tool"]
        if decl["name"] != name:
            self._fail(name, "TOOL['name'] stimmt nicht mit dem Dateinamen überein")
            raise SelfExtError(f"In der Datei steht TOOL['name'] = {decl['name']!r}, geschrieben wurde "
                               f"aber {name!r}. Beides muss gleich sein.")

        self.db.execute("UPDATE self_tools SET status='checked', description=?, risk=?, last_error='', "
                        "updated_at=? WHERE name=?",
                        (str(decl.get("description", ""))[:400], str(decl.get("risk", "medium")),
                         now_iso(), name))
        self.bus.publish("selftool.changed", {"name": name, "status": "checked"})
        return {"name": name, "status": "checked", "declares": decl,
                "selftest": result.get("selftest", "")}

    def _fail(self, name: str, error: str) -> None:
        self.db.execute("UPDATE self_tools SET status='failed', last_error=?, updated_at=? WHERE name=?",
                        (str(error)[:600], now_iso(), name))
        self.bus.publish("selftool.changed", {"name": name, "status": "failed"})

    # ── freigeben und laden ──────────────────────────────────────────────
    def _load(self, name: str, registry) -> ToolSpec:
        """Die Datei in den laufenden Prozess holen und als Werkzeug anmelden."""
        import importlib.util

        path = self._file(name)
        spec = importlib.util.spec_from_file_location(f"jarvis_self_{name.replace('.', '_')}", path)
        if spec is None or spec.loader is None:
            raise SelfExtError(f"'{name}' lässt sich nicht laden.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        decl = getattr(module, "TOOL")
        run = getattr(module, "run")

        async def handler(ctx: ToolContext, args: dict):
            return await run(args)

        tool = ToolSpec(
            name=name,
            description=str(decl.get("description", ""))[:600],
            input_schema=decl.get("input_schema") or {"type": "object", "properties": {}},
            category="self",
            risk=str(decl.get("risk", "medium")),
            min_role=str(decl.get("min_role", "operator")),
            handler=handler,
            available=True,
            source="self",
        )
        registry.register(tool, replace=True)
        return tool

    def activate(self, name: str, registry, *, actor: str) -> dict:
        row = self.db.fetchone("SELECT * FROM self_tools WHERE name=?", (name,))
        if row is None:
            raise SelfExtError(f"'{name}' gibt es noch nicht.")
        if row["status"] not in ("checked", "active"):
            raise SelfExtError(f"'{name}' ist nicht geprüft (Stand: {row['status']}). Erst self.check.")
        source = self.read(name)
        if _sha(source) != row["source_sha"]:
            raise SelfExtError("Die Quelle hat sich seit der Prüfung geändert. Erst self.check, dann freigeben.")
        tool = self._load(name, registry)
        self.db.execute("UPDATE self_tools SET status='active', activated_by=?, activated_at=?, "
                        "updated_at=? WHERE name=?", (actor, now_iso(), now_iso(), name))
        self.log.info("selfext", f"{actor} gab {name} frei — ab jetzt Teil der Registry")
        self.bus.publish("selftool.changed", {"name": name, "status": "active"})
        return {"name": name, "status": "active", "risk": tool.risk}

    def disable(self, name: str, registry, *, actor: str) -> dict:
        row = self.db.fetchone("SELECT status FROM self_tools WHERE name=?", (name,))
        if row is None:
            raise SelfExtError(f"'{name}' gibt es nicht.")
        spec = registry.get(name)
        if spec is not None and spec.source == "self":
            spec.available = False
            spec.handler = None
            spec.reason = f"von {actor} abgeschaltet"
        self.db.execute("UPDATE self_tools SET status='disabled', updated_at=? WHERE name=?",
                        (now_iso(), name))
        self.log.info("selfext", f"{actor} schaltete {name} ab")
        self.bus.publish("selftool.changed", {"name": name, "status": "disabled"})
        return {"name": name, "status": "disabled"}

    # ── Übersicht und Neustart ───────────────────────────────────────────
    def list(self) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM self_tools ORDER BY updated_at DESC") or []
        return [{"name": r["name"], "status": r["status"], "risk": r["risk"],
                 "description": r["description"], "error": r["last_error"],
                 "written_by": r["written_by"], "activated_by": r["activated_by"],
                 "updated_at": r["updated_at"]} for r in rows]

    def restore(self, registry) -> int:
        """Freigegebene Werkzeuge nach einem Neustart wieder anmelden.

        Eine Datei, die inzwischen von der freigegebenen Fassung abweicht,
        wird NICHT geladen: die Freigabe galt der geprüften Quelle.
        """
        loaded = 0
        for row in self.db.fetchall("SELECT * FROM self_tools WHERE status='active'") or []:
            name = row["name"]
            try:
                if _sha(self.read(name)) != row["source_sha"]:
                    self._fail(name, "Die Datei weicht von der freigegebenen Fassung ab — nicht geladen.")
                    continue
                self._load(name, registry)
                loaded += 1
            except Exception as e:  # noqa: BLE001
                self._fail(name, f"Beim Laden: {e}")
        return loaded


    # ── sein eigener Quelltext ───────────────────────────────────────────
    @staticmethod
    def _own_path(rel: str) -> Path:
        """Einen Pfad im eigenen Quellbaum auflösen — und nur dort."""
        rel = (rel or "").strip().lstrip("/")
        path = (REPO_ROOT / rel).resolve()
        if not any(str(path).startswith(str((REPO_ROOT / tree).resolve())) for tree in SELF_TREES):
            raise SelfExtError(f"'{rel}' liegt nicht in seinem eigenen Quelltext. Erreichbar sind: "
                               + ", ".join(SELF_TREES))
        if path.is_dir():
            raise SelfExtError(f"'{rel}' ist ein Verzeichnis.")
        return path

    def source_tree(self, tree: str = "") -> list[dict]:
        """Was es überhaupt gibt. Ohne das rät er sich Dateinamen zusammen."""
        trees = [tree] if tree else list(SELF_TREES)
        out: list[dict] = []
        for t in trees:
            base = (REPO_ROOT / t).resolve()
            if not any(str(base).startswith(str((REPO_ROOT / s).resolve())) for s in SELF_TREES):
                raise SelfExtError(f"'{t}' gehört nicht zu seinem Quelltext.")
            for path in (sorted(base.rglob("*.py")) + sorted(base.rglob("*.txt"))
                         + sorted(base.rglob("*.ts")) + sorted(base.rglob("*.tsx"))
                         + sorted(base.rglob("*.css"))):
                if "__pycache__" in path.parts:
                    continue
                rel = str(path.relative_to(REPO_ROOT))
                out.append({"file": rel, "lines": path.read_text(encoding="utf-8",
                                                                 errors="replace").count("\n") + 1,
                            "critical": rel in CRITICAL_FILES,
                            "needs_build": rel.startswith(NEEDS_BUILD_PREFIX)})
        return out

    def source(self, rel: str) -> dict:
        path = self._own_path(rel)
        if not path.exists():
            raise SelfExtError(f"'{rel}' gibt es nicht. self.tree zeigt, was es gibt.")
        text = path.read_text(encoding="utf-8")
        return {"file": rel, "critical": rel in CRITICAL_FILES, "sha": _sha(text), "source": text}

    # ── Änderungsvorschläge an sich selbst ───────────────────────────────
    def propose(self, rel: str, source: str, reason: str, *, author: str) -> dict:
        """Eine geänderte Fassung hinterlegen. Am laufenden System ändert das nichts."""
        path = self._own_path(rel)
        if not path.exists():
            raise SelfExtError(f"'{rel}' gibt es nicht.")
        if path.suffix == ".py":
            try:
                ast.parse(source)
            except SyntaxError as e:
                raise SelfExtError(f"Das ist kein gültiges Python: Zeile {e.lineno}, {e.msg}") from e
        current = path.read_text(encoding="utf-8")
        if current == source:
            raise SelfExtError("Der Vorschlag ist mit dem aktuellen Stand identisch.")
        diff = list(difflib.unified_diff(current.splitlines(), source.splitlines(),
                                         fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=3))
        pid = new_id("prop")
        draft = self.dir / "_proposals" / f"{pid}.py"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(source, encoding="utf-8")
        self.db.insert("self_tools", {
            "name": f"proposal:{pid}", "file": str(draft), "description": str(reason)[:400],
            "risk": "critical" if rel in CRITICAL_FILES else "high", "status": "checked",
            "source_sha": _sha(source), "last_error": rel, "created_at": now_iso(),
            "updated_at": now_iso(), "written_by": author, "activated_by": "", "activated_at": None})
        self.log.info("selfext", f"{author} schlug eine Änderung an {rel} vor ({pid})")
        self.bus.publish("selftool.changed", {"name": f"proposal:{pid}", "status": "checked"})
        return {"proposal_id": pid, "file": rel, "critical": rel in CRITICAL_FILES,
                "added": sum(1 for d in diff if d.startswith("+") and not d.startswith("+++")),
                "removed": sum(1 for d in diff if d.startswith("-") and not d.startswith("---")),
                "diff": "\n".join(diff[:400]),
                "needs_build": rel.startswith(NEEDS_BUILD_PREFIX),
                "note": ("Das ist ein Vorschlag, mehr nicht. self.apply setzt ihn um — das braucht "
                         "eine Freigabe und wirkt erst nach einem Neustart des Servers."
                         + (" Diese Datei gehört zum Dashboard, also erst nach einem neuen Build "
                            "(docker compose … up -d --build)."
                            if rel.startswith(NEEDS_BUILD_PREFIX) else ""))}


    async def verify_proposal(self, proposal_id: str, *, capability_gain: str = "",
                              verification_plan: str = "") -> dict:
        """Verify a core proposal in isolation and persist machine-generated evidence."""
        row = self.db.fetchone("SELECT * FROM self_tools WHERE name=?", (f"proposal:{proposal_id}",))
        if row is None:
            raise SelfExtError(f"Vorschlag '{proposal_id}' gibt es nicht.")
        rel = row["last_error"]
        target = self._own_path(rel)
        draft = Path(row["file"])
        if not draft.exists():
            raise SelfExtError("Die vorgeschlagene Fassung ist nicht mehr da.")
        candidate = draft.read_text(encoding="utf-8")
        if _sha(candidate) != row["source_sha"]:
            raise SelfExtError("Die vorgeschlagene Fassung hat sich geändert — Prüfung abgebrochen.")
        current = target.read_text(encoding="utf-8")
        checks = []
        ok = True

        def add(name: str, passed: bool, detail: str = "") -> None:
            nonlocal ok
            checks.append({"name": name, "passed": bool(passed), "detail": str(detail)[:2000]})
            ok = ok and bool(passed)

        add("changed", current != candidate, "candidate differs from current source")
        # Critical invariants may never disappear through self-evolution.
        if rel in CRITICAL_FILES:
            invariants = []
            if rel.endswith("builtin_tools.py") or rel.endswith("runtime.py"):
                invariants += ["requires_approval", "approval"]
            if rel.endswith("selfext.py"):
                invariants += ["CRITICAL_FILES", "SelfExtError", "revert"]
            if rel.endswith("approvals.py"):
                invariants += ["approved", "rejected"]
            for token in invariants:
                add(f"invariant:{token}", token in candidate, f"required token '{token}' remains present")

        suffix = target.suffix.lower()
        if suffix == ".py":
            with tempfile.TemporaryDirectory(prefix="mia-core-check-") as td:
                tmp = Path(td) / target.name
                tmp.write_text(candidate, encoding="utf-8")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "py_compile", str(tmp),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=40)
                detail = (out or b"").decode("utf-8", "replace")[-1800:]
                add("python_compile", proc.returncode == 0, detail or "py_compile passed")
        elif rel.startswith(NEEDS_BUILD_PREFIX):
            frontend = self.FRONTEND_DIR
            if not (frontend / "node_modules").is_dir():
                add("frontend_toolchain", False, "node_modules is missing")
            else:
                with tempfile.TemporaryDirectory(prefix="mia-core-frontend-") as td:
                    root = Path(td) / "frontend"
                    shutil.copytree(frontend, root, ignore=shutil.ignore_patterns("node_modules", "dist"))
                    (root / "node_modules").symlink_to(frontend / "node_modules", target_is_directory=True)
                    rel_front = Path(rel).relative_to("command_center/frontend")
                    cand_path = root / rel_front
                    cand_path.parent.mkdir(parents=True, exist_ok=True)
                    cand_path.write_text(candidate, encoding="utf-8")
                    async def run(*args: str, timeout: float = 300):
                        proc = await asyncio.create_subprocess_exec(*args, cwd=str(root),
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                        return proc.returncode or 0, (out or b"").decode("utf-8", "replace")[-2500:]
                    code, out = await run("node", str(root/"node_modules/.bin/tsc"), "--noEmit", "-p", "tsconfig.json", timeout=240)
                    add("typescript", code == 0, out or "tsc passed")
                    if code == 0:
                        code, out = await run("node", str(root/"node_modules/.bin/vite"), "build", "--outDir", str(root/"dist-check"), "--emptyOutDir", timeout=480)
                        add("vite_build", code == 0, out or "vite build passed")
        else:
            add("text_file", bool(candidate.strip()), "non-empty source")

        baseline = {"sha": _sha(current), "bytes": len(current.encode('utf-8'))}
        cand = {"sha": _sha(candidate), "bytes": len(candidate.encode('utf-8')),
                "added_removed": abs(len(candidate.splitlines()) - len(current.splitlines()))}
        status = "passed" if ok else "failed"
        now = now_iso()
        existing = self.db.fetchone("SELECT proposal_id FROM core_evolution_checks WHERE proposal_id=?", (proposal_id,))
        payload=(rel,status,json.dumps(checks,ensure_ascii=False),json.dumps(baseline),json.dumps(cand),
                 capability_gain[:2000],verification_plan[:4000],now)
        if existing:
            self.db.execute("UPDATE core_evolution_checks SET file=?,status=?,checks=?,baseline=?,candidate=?,capability_gain=?,verification_plan=?,updated_at=? WHERE proposal_id=?", payload+(proposal_id,))
        else:
            self.db.insert("core_evolution_checks", {"proposal_id":proposal_id,"file":rel,"status":status,
                "checks":json.dumps(checks,ensure_ascii=False),"baseline":json.dumps(baseline),
                "candidate":json.dumps(cand),"capability_gain":capability_gain[:2000],
                "verification_plan":verification_plan[:4000],"created_at":now,"updated_at":now})
        self.log.info("selfext", f"Core-Prüfung {proposal_id}: {status}")
        self.bus.publish("core.evolution.checked", {"proposal_id":proposal_id,"file":rel,"status":status})
        return {"proposal_id": proposal_id, "file": rel, "status": status, "checks": checks,
                "baseline": baseline, "candidate": cand, "eligible_for_approval": ok}

    def verification(self, proposal_id: str) -> dict | None:
        row = self.db.fetchone("SELECT * FROM core_evolution_checks WHERE proposal_id=?", (proposal_id,))
        if not row:
            return None
        out=dict(row)
        for k in ("checks","baseline","candidate"):
            try: out[k]=json.loads(out[k])
            except Exception: pass
        return out

    def apply(self, proposal_id: str, *, actor: str) -> dict:
        """Einen Vorschlag wirklich in den Quellbaum schreiben. Mit Sicherung."""
        row = self.db.fetchone("SELECT * FROM self_tools WHERE name=?", (f"proposal:{proposal_id}",))
        if row is None:
            raise SelfExtError(f"Vorschlag '{proposal_id}' gibt es nicht.")
        if row["status"] == "active":
            raise SelfExtError("Dieser Vorschlag wurde bereits umgesetzt.")
        verification = self.verification(proposal_id)
        if not verification or verification.get("status") != "passed":
            raise SelfExtError("Core-Änderung nicht freigegeben: self.verify muss vorher serverseitig bestehen.")
        rel = row["last_error"]          # hier steht die Zieldatei
        target = self._own_path(rel)
        draft = Path(row["file"])
        if not draft.exists():
            raise SelfExtError("Die vorgeschlagene Fassung ist nicht mehr da.")
        source = draft.read_text(encoding="utf-8")
        if _sha(source) != row["source_sha"]:
            raise SelfExtError("Die vorgeschlagene Fassung hat sich geändert — nicht umgesetzt.")

        backup = self.dir / "_backups" / f"{proposal_id}__{rel.replace('/', '__')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        target.write_text(source, encoding="utf-8")
        self.db.execute("UPDATE self_tools SET status='active', activated_by=?, activated_at=?, "
                        "updated_at=? WHERE name=?",
                        (actor, now_iso(), now_iso(), f"proposal:{proposal_id}"))
        self.log.audit(actor_type="user", actor_id=actor, agent_id="jarvis", tool="self.apply",
                       action="self.modify", target=rel, status="ok",
                       result=f"Sicherung: {backup.name}")
        self.bus.publish("selftool.changed", {"name": f"proposal:{proposal_id}", "status": "active"})
        needs_build = rel.startswith(NEEDS_BUILD_PREFIX)
        return {"proposal_id": proposal_id, "file": rel, "backup": str(backup),
                "needs_build": needs_build,
                "note": (("Geschrieben. Das ist eine Datei des Dashboards — wirksam erst nach einem "
                          "neuen Build: docker compose --env-file command_center/.env "
                          "-f docker-compose.command-center.yml up -d --build")
                         if needs_build else
                         "Geschrieben. Wirksam wird es beim nächsten Neustart des Servers.")
                        + " Zurückdrehen: self.revert mit derselben Vorschlags-ID."}

    def revert(self, proposal_id: str, *, actor: str) -> dict:
        row = self.db.fetchone("SELECT * FROM self_tools WHERE name=?", (f"proposal:{proposal_id}",))
        if row is None or row["status"] != "active":
            raise SelfExtError(f"'{proposal_id}' wurde nicht umgesetzt — es gibt nichts zurückzudrehen.")
        rel = row["last_error"]
        backup = self.dir / "_backups" / f"{proposal_id}__{rel.replace('/', '__')}"
        if not backup.exists():
            raise SelfExtError("Die Sicherung ist nicht mehr da. Zurückdrehen geht dann nur über git.")
        shutil.copy2(backup, self._own_path(rel))
        self.db.execute("UPDATE self_tools SET status='disabled', updated_at=? WHERE name=?",
                        (now_iso(), f"proposal:{proposal_id}"))
        self.log.audit(actor_type="user", actor_id=actor, agent_id="jarvis", tool="self.revert",
                       action="self.modify", target=rel, status="ok", result="aus Sicherung")
        return {"proposal_id": proposal_id, "file": rel,
                "note": "Zurückgedreht. Wirksam beim nächsten Neustart."}

    # ── wirksam machen ───────────────────────────────────────────────────
    # Eine geschriebene Änderung ist noch keine wirksame. Backend: Neustart.
    # Dashboard: neuer Build. Ohne diese beiden bliebe „er baut sich selbst um"
    # eine Behauptung, die an der ersten Probe zerbricht.
    FRONTEND_DIR = REPO_ROOT / "command_center" / "frontend"

    def can_rebuild(self) -> tuple[bool, str]:
        if not shutil.which("node"):
            return False, ("In diesem Image ist keine Node-Werkzeugkette. Das Dashboard lässt sich "
                           "hier nicht neu bauen — gebaut wurde mit JARVIS_CC_SELF_BUILD=false.")
        if not (self.FRONTEND_DIR / "package.json").exists():
            return False, "Die Frontend-Quellen fehlen in diesem Image."
        if not (self.FRONTEND_DIR / "node_modules").is_dir():
            return False, "node_modules fehlt — ohne die Abhängigkeiten baut vite nicht."
        return True, ""

    async def rebuild_frontend(self, *, actor: str) -> dict:
        """Das Dashboard aus den aktuellen Quellen neu bauen.

        Erst Typprüfung, dann Build. Schlägt die Typprüfung fehl, wird gar
        nicht gebaut: ein kaputtes Dashboard auszuliefern wäre schlimmer als
        eine abgelehnte Änderung.
        """
        ok, why = self.can_rebuild()
        if not ok:
            raise SelfExtError(why)
        static = REPO_ROOT / "command_center" / "backend" / "static"

        async def npx(*args: str, timeout: float) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                "node", str(self.FRONTEND_DIR / "node_modules" / ".bin" / args[0]), *args[1:],
                cwd=str(self.FRONTEND_DIR),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return 124, "Zeitüberschreitung"
            return proc.returncode or 0, (out or b"").decode("utf-8", "replace")[-4000:]

        code, out = await npx("tsc", "--noEmit", "-p", "tsconfig.json", timeout=240)
        if code != 0:
            self.log.warning("selfext", f"{actor}: Typprüfung des Dashboards fehlgeschlagen")
            raise SelfExtError(f"Die Typprüfung schlägt fehl, es wurde nichts gebaut:\n{out[-1500:]}")

        # In ein Nebenverzeichnis bauen und erst danach umschalten: ein
        # abgebrochener Build darf nicht ein halbes Dashboard hinterlassen.
        tmp_out = static.parent / "static.new"
        code, out = await npx("vite", "build", "--outDir", str(tmp_out), "--emptyOutDir", timeout=600)
        if code != 0:
            shutil.rmtree(tmp_out, ignore_errors=True)
            raise SelfExtError(f"Der Build schlug fehl, das laufende Dashboard blieb unberührt:\n{out[-1500:]}")

        backup = static.parent / "static.old"
        shutil.rmtree(backup, ignore_errors=True)
        if static.exists():
            static.rename(backup)
        tmp_out.rename(static)
        shutil.rmtree(backup, ignore_errors=True)
        self.log.audit(actor_type="user", actor_id=actor, agent_id="jarvis", tool="self.rebuild",
                       action="self.modify", target="command_center/frontend", status="ok",
                       result="Dashboard neu gebaut")
        self.bus.publish("selftool.changed", {"name": "frontend", "status": "rebuilt"})
        return {"built": True,
                "note": ("Das Dashboard ist neu gebaut und sofort ausgeliefert. Im Browser einmal "
                         "hart neu laden (Strg+Umschalt+R), sonst zeigt er die alte Fassung aus "
                         "dem Zwischenspeicher.")}

    def restart_server(self, *, actor: str) -> dict:
        """Den eigenen Prozess beenden, damit Docker ihn neu startet.

        Das ist der einzige Weg, mit dem Änderungen am Backend wirksam werden,
        ohne dass jemand an der Konsole sitzt. Voraussetzung ist eine
        Neustart-Regel im Container — ohne die bliebe er unten, und genau das
        wird hier geprüft, statt es zu hoffen.
        """
        import os
        import signal
        import threading

        in_container = Path("/.dockerenv").exists()
        if not in_container:
            raise SelfExtError("Außerhalb eines Containers beende ich mich nicht selbst — dann käme "
                               "niemand zurück. Starte den Server von Hand neu.")
        self.log.audit(actor_type="user", actor_id=actor, agent_id="jarvis", tool="self.restart",
                       action="self.modify", target="server", status="ok", result="Neustart angefordert")

        def bye() -> None:
            os.kill(os.getpid(), signal.SIGTERM)

        # Erst antworten, dann gehen: sonst bekommt niemand mit, dass es klappte.
        threading.Timer(1.5, bye).start()
        return {"restarting": True,
                "note": ("Der Server beendet sich in einer Sekunde und wird vom Container neu "
                         "gestartet (restart: unless-stopped). Nach etwa zehn Sekunden ist er "
                         "wieder da; die Seite verbindet sich von selbst neu.")}

    def proposals(self) -> list[dict]:
        rows = self.db.fetchall("SELECT * FROM self_tools WHERE name LIKE 'proposal:%' "
                                "ORDER BY updated_at DESC LIMIT 50") or []
        return [{"proposal_id": r["name"].split(":", 1)[1], "file": r["last_error"],
                 "reason": r["description"], "status": r["status"], "risk": r["risk"],
                 "critical": r["last_error"] in CRITICAL_FILES,
                 "verification": self.verification(r["name"].split(":", 1)[1]),
                 "by": r["written_by"], "applied_by": r["activated_by"],
                 "updated_at": r["updated_at"]} for r in rows]


__all__ = ["SelfExtension", "SelfExtError", "EXT_DIRNAME", "CRITICAL_FILES", "SELF_TREES",
           "NEEDS_BUILD_PREFIX"]
