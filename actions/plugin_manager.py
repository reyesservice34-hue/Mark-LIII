"""Install and manage MIA plugins from Git repositories.

Safety model:
- inspect/clone never imports or executes plugin code;
- compatibility is checked with Python AST only;
- install/dependency/reload/remove actions use the existing confirmation gate;
- all paths stay inside /root/Mark-LIII.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from core import confirm as _confirm

BASE_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = BASE_DIR / "plugins"
REPOS_DIR = BASE_DIR / "learned_repos"
DATA_DIR = BASE_DIR / "data" / "plugin_manager"
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FOLDER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name if FOLDER_RE.match(name) else "plugin_repo"


def _clone_if_needed(url: str, repo_name: str = "") -> tuple[Path | None, str]:
    url = (url or "").strip()
    name = (repo_name or _repo_name_from_url(url)).strip()
    if not FOLDER_RE.match(name):
        return None, "Invalid repository folder name."
    target = REPOS_DIR / name
    if target.exists():
        return target, f"Using existing repository learned_repos/{name}."
    if not url:
        return None, f"Repository learned_repos/{name} does not exist."
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None, "Only https Git repository URLs are accepted by the plugin installer."
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", url, str(target)],
            capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
    except Exception as exc:
        return None, f"Clone failed: {exc}"
    if p.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        return None, f"Clone failed: {(p.stderr or p.stdout)[-800:]}"
    return target, f"Cloned to learned_repos/{name}."


def _literal_assignment(tree: ast.AST, variable: str):
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable:
                    try:
                        return ast.literal_eval(node.value)
                    except Exception:
                        return None
    return None


def _inspect_file(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return None
    meta = _literal_assignment(tree, "PLUGIN")
    has_run = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run" for n in tree.body)
    if not isinstance(meta, dict) or not has_run:
        return None
    name = meta.get("name")
    desc = meta.get("description")
    params = meta.get("parameters", {"type": "OBJECT", "properties": {}})
    valid = (
        isinstance(name, str) and NAME_RE.match(name)
        and isinstance(desc, str) and bool(desc.strip())
        and isinstance(params, dict) and params.get("type") == "OBJECT"
    )
    imports = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.extend(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.append(n.module.split(".")[0])
    return {
        "file": str(path),
        "name": str(name or path.stem),
        "description": str(desc or "")[:240],
        "valid": bool(valid),
        "imports": sorted(set(imports)),
    }


def _candidates(repo: Path) -> list[dict]:
    out = []
    for p in repo.rglob("*.py"):
        rel = p.relative_to(repo)
        if any(part in {".git", ".venv", "venv", "node_modules", "__pycache__"} for part in rel.parts):
            continue
        if len(rel.parts) > 5:
            continue
        info = _inspect_file(p)
        if info:
            info["relative_file"] = str(rel)
            out.append(info)
    return out


def _select(repo: Path, plugin_file: str = "") -> tuple[Path | None, dict | None, str]:
    items = _candidates(repo)
    if plugin_file:
        wanted = (repo / plugin_file).resolve()
        try:
            wanted.relative_to(repo.resolve())
        except ValueError:
            return None, None, "Plugin path escapes repository."
        info = _inspect_file(wanted) if wanted.is_file() else None
        if not info:
            return None, None, f"'{plugin_file}' is not a MIA-compatible plugin."
        info["relative_file"] = str(wanted.relative_to(repo.resolve()))
        return wanted, info, ""
    valid = [x for x in items if x["valid"]]
    if len(valid) == 1:
        p = repo / valid[0]["relative_file"]
        return p, valid[0], ""
    if not valid:
        return None, None, "No valid MIA plugin found. It needs a PLUGIN dict and run(parameters, ...)."
    names = ", ".join(f"{x['name']} ({x['relative_file']})" for x in valid)
    return None, None, f"Multiple plugins found. Specify plugin_file. Candidates: {names}"


def _requirements_for(repo: Path, plugin_path: Path) -> list[Path]:
    found = []
    for base in (plugin_path.parent, repo):
        p = base / "requirements.txt"
        if p.is_file() and p not in found:
            found.append(p)
    return found


def _install_now(repo: Path, plugin_path: Path, info: dict) -> str:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    target = PLUGINS_DIR / f"{info['name']}.py"
    if target.exists():
        return f"Plugin '{info['name']}' already exists at plugins/{target.name}."
    shutil.copy2(plugin_path, target)
    return f"Installed '{info['name']}' as plugins/{target.name}. Restart Mark 53 to activate it."


def _install_deps_now(req: Path) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    before = DATA_DIR / f"pip-freeze-before-{stamp}.txt"
    with before.open("w", encoding="utf-8") as fh:
        subprocess.run([str(BASE_DIR / ".venv/bin/python"), "-m", "pip", "freeze"], stdout=fh, text=True)
    p = subprocess.run(
        [str(BASE_DIR / ".venv/bin/python"), "-m", "pip", "install", "-r", str(req)],
        capture_output=True, text=True, timeout=900,
    )
    out = (p.stdout + p.stderr).strip()
    return f"Dependency install exit={p.returncode}.\n{out[-4000:]}\nSnapshot: {before.relative_to(BASE_DIR)}"


def _reload_now() -> str:
    unit = f"mia-plugin-reload-{int(time.time())}"
    p = subprocess.run(
        ["systemd-run", "--unit", unit, "--on-active=2s", "/bin/systemctl", "restart", "mark-liii.service"],
        capture_output=True, text=True, timeout=20,
    )
    if p.returncode != 0:
        return f"Could not schedule reload: {(p.stderr or p.stdout).strip()}"
    return "Plugin reload scheduled. Mark 53 will restart in about 2 seconds."


def _remove_now(name: str) -> str:
    if not NAME_RE.match(name):
        return "Invalid plugin name."
    target = PLUGINS_DIR / f"{name}.py"
    if not target.exists():
        return f"Plugin '{name}' is not installed."
    trash = DATA_DIR / "removed"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{name}-{int(time.time())}.py"
    shutil.move(str(target), str(dest))
    return f"Removed '{name}' from active plugins and preserved a copy at {dest.relative_to(BASE_DIR)}. Reload required."


def plugin_manager(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "list").strip().lower()
    repo_url = str(p.get("repo_url") or "").strip()
    repo_name = str(p.get("repo_name") or "").strip()
    plugin_file = str(p.get("plugin_file") or "").strip()
    plugin_name = str(p.get("plugin_name") or "").strip()

    if action == "list":
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        items = []
        for f in sorted(PLUGINS_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            info = _inspect_file(f)
            items.append((info or {"name": f.stem, "valid": False, "description": ""}))
        if not items:
            return "No installed MIA plugins."
        return "\n".join(f"- {i['name']}: {'valid' if i.get('valid') else 'invalid'} — {i.get('description','')}" for i in items)

    if action == "reload":
        return _confirm.request(
            key="plugin_manager_reload",
            title="MIA will reload Mark 53 to activate plugin changes",
            detail="The Mark 53 service will restart once. Dashboard/voice may disconnect briefly.",
            run=_reload_now,
        )

    if action == "remove":
        if not plugin_name:
            return "plugin_name is required for remove."
        return _confirm.request(
            key=f"plugin_remove_{plugin_name}",
            title=f"MIA will remove plugin: {plugin_name}",
            detail="The plugin file will leave plugins/ but a recovery copy is kept under data/plugin_manager/removed.",
            run=lambda: _remove_now(plugin_name),
        )

    repo, msg = _clone_if_needed(repo_url, repo_name)
    if repo is None:
        return msg

    selected, info, err = _select(repo, plugin_file)
    if action == "inspect":
        items = _candidates(repo)
        if not items:
            return f"{msg}\nNo MIA-compatible plugin candidates found."
        lines = [f"{msg}", f"Found {len(items)} candidate(s):"]
        for x in items:
            lines.append(f"- {x['name']} | valid={x['valid']} | {x['relative_file']} | {x['description']}")
        return "\n".join(lines)

    if err:
        return err
    assert selected is not None and info is not None
    reqs = _requirements_for(repo, selected)

    if action == "dependencies":
        if not reqs:
            return f"Plugin '{info['name']}' has no requirements.txt near it or at repo root."
        req = reqs[0]
        preview = "\n".join(
            line for line in req.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )[:3000]
        return _confirm.request(
            key=f"plugin_deps_{info['name']}",
            title=f"MIA will install dependencies for plugin: {info['name']}",
            detail=f"Requirements file: {req.relative_to(repo)}\n{preview}\nThis changes Mark 53's Python environment; a pip-freeze snapshot is saved first.",
            run=lambda: _install_deps_now(req),
        )

    if action == "install":
        deps_note = ""
        if reqs:
            deps_note = f"\nA requirements.txt exists. Install the plugin first, then use action=dependencies if imports are missing."
        return _confirm.request(
            key=f"plugin_install_{info['name']}",
            title=f"MIA will install plugin: {info['name']}",
            detail=f"Source: {selected.relative_to(repo)}\nTarget: plugins/{info['name']}.py\nDescription: {info['description']}{deps_note}\nThe source was statically inspected but will execute inside Mark 53 after reload.",
            run=lambda: _install_now(repo, selected, info),
        )

    return "Unknown action. Use list, inspect, install, dependencies, reload, or remove."


TOOL = {
    "name": "plugin_manager",
    "description": (
        "Install and manage MIA plugins. Can inspect/clone an HTTPS Git repository, detect MIA-compatible "
        "PLUGIN + run() files without executing them, install the selected plugin after confirmation, "
        "install declared requirements after confirmation, reload Mark 53, list plugins, or remove one. "
        "Use this whenever the user asks MIA to install/add/load a plugin or extension."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | inspect | install | dependencies | reload | remove"},
            "repo_url": {"type": "STRING", "description": "Optional HTTPS Git/GitHub repository URL"},
            "repo_name": {"type": "STRING", "description": "Existing learned_repos folder or local clone name"},
            "plugin_file": {"type": "STRING", "description": "Relative plugin .py path if the repo contains multiple candidates"},
            "plugin_name": {"type": "STRING", "description": "Installed plugin name for remove"},
        },
        "required": ["action"],
    },
    "handler": plugin_manager,
}
