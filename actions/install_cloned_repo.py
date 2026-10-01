"""Install a repository previously cloned into learned_repos.

The tool detects common project manifests and uses the matching package/build
manager. Install actions are confirmation-gated because package installation
can execute third-party code. Python dependencies go into a repo-local venv.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from core import confirm as _confirm

BASE_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = BASE_DIR / "learned_repos"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OUTPUT_LIMIT = 5000
INSTALL_TIMEOUT = 900


def _repo(name: str) -> Path | None:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        return None
    target = (REPOS_DIR / name).resolve()
    try:
        target.relative_to(REPOS_DIR.resolve())
    except ValueError:
        return None
    return target if (target / ".git").is_dir() else None


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _detect(target: Path) -> tuple[str, list[list[str]], str]:
    python = shutil.which("python3") or sys.executable

    if (target / "requirements.txt").is_file():
        venv = target / ".mia-venv"
        return (
            "python-requirements",
            [[python, "-m", "venv", str(venv)],
             [str(venv / "bin" / "python"), "-m", "pip", "install", "-r", "requirements.txt"]],
            "Python requirements.txt -> repo-local .mia-venv",
        )

    if any((target / f).is_file() for f in ("pyproject.toml", "setup.py", "setup.cfg")):
        venv = target / ".mia-venv"
        return (
            "python-package",
            [[python, "-m", "venv", str(venv)],
             [str(venv / "bin" / "python"), "-m", "pip", "install", "-e", "."]],
            "Python package -> editable install in repo-local .mia-venv",
        )

    if (target / "package.json").is_file():
        if (target / "pnpm-lock.yaml").is_file() and _which("pnpm"):
            return "node-pnpm", [["pnpm", "install"]], "Node.js -> pnpm install"
        if (target / "yarn.lock").is_file() and _which("yarn"):
            return "node-yarn", [["yarn", "install"]], "Node.js -> yarn install"
        if _which("npm"):
            command = ["npm", "ci"] if (target / "package-lock.json").is_file() else ["npm", "install"]
            return "node-npm", [command], "Node.js -> npm dependency install"
        return "node", [], "Node.js project detected, but npm/pnpm/yarn is unavailable"

    if any((target / f).is_file() for f in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")):
        if _which("docker"):
            return "docker-compose", [["docker", "compose", "build"]], "Docker Compose -> build images"
        return "docker-compose", [], "Docker Compose detected, but docker is unavailable"

    if (target / "Dockerfile").is_file():
        if _which("docker"):
            return "docker", [["docker", "build", "-t", f"mia-{target.name.lower()}", "."]], "Dockerfile -> build image"
        return "docker", [], "Dockerfile detected, but docker is unavailable"

    if (target / "go.mod").is_file():
        return ("go", [["go", "mod", "download"]], "Go -> download modules") if _which("go") else ("go", [], "Go project detected, but go is unavailable")

    if (target / "Cargo.toml").is_file():
        return ("rust", [["cargo", "build"]], "Rust -> cargo build") if _which("cargo") else ("rust", [], "Rust project detected, but cargo is unavailable")

    if (target / "composer.json").is_file():
        return ("php", [["composer", "install"]], "PHP -> composer install") if _which("composer") else ("php", [], "PHP project detected, but composer is unavailable")

    if (target / "gradlew").is_file():
        return "gradle-wrapper", [["bash", "./gradlew", "build", "-x", "test"]], "Java/Gradle -> wrapper build"
    if (target / "pom.xml").is_file():
        return ("maven", [["mvn", "-DskipTests", "package"]], "Java/Maven -> package") if _which("mvn") else ("maven", [], "Maven project detected, but mvn is unavailable")

    return "unknown", [], "No supported project manifest found"


def _run_plan(target: Path, plan: list[list[str]]) -> str:
    chunks: list[str] = []
    for cmd in plan:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT,
                env=None,
            )
        except subprocess.TimeoutExpired:
            return f"Installation timed out after {INSTALL_TIMEOUT}s while running: {' '.join(cmd)}"
        except Exception as exc:
            return f"Installation failed to start: {exc}"

        out = (proc.stdout + proc.stderr).strip()
        chunks.append(f"$ {' '.join(cmd)}\nexit={proc.returncode}\n{out[-1800:]}")
        if proc.returncode != 0:
            break

    result = "\n\n".join(chunks)
    if len(result) > OUTPUT_LIMIT:
        result = result[-OUTPUT_LIMIT:]
    return result or "Installation finished with no output."


def install_cloned_repo(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    name = str(p.get("repo_name") or "").strip()
    action = str(p.get("action") or "install").strip().lower()
    target = _repo(name)
    if target is None:
        return f"Repository '{name}' was not found under learned_repos or is not a git repository."

    kind, plan, detail = _detect(target)
    if action == "detect":
        commands = [" ".join(c) for c in plan]
        return f"Detected {kind}: {detail}. Planned commands: {commands or ['none']}"

    if action != "install":
        return "Unknown action. Use detect or install."
    if not plan:
        return f"Cannot install automatically. Detected {kind}: {detail}."

    commands = "\n".join(" ".join(c) for c in plan)
    return _confirm.request(
        key=f"install_cloned_repo_{name}",
        title=f"MIA will install cloned repository: {name}",
        detail=f"{detail}\nCommands:\n{commands}\nThird-party install steps may execute repository code.",
        run=lambda: _run_plan(target, plan),
    )


TOOL = {
    "name": "install_cloned_repo",
    "description": (
        "Detect and install a repository already cloned under learned_repos. "
        "Use after clone_and_learn when the user asks to install/setup the GitHub repo. "
        "Supports Python, Node.js, Docker, Go, Rust, PHP, Gradle and Maven. "
        "Use action=detect to inspect without changing anything; action=install requests confirmation "
        "before executing third-party installation code."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "repo_name": {"type": "STRING", "description": "Folder name under learned_repos"},
            "action": {"type": "STRING", "description": "detect | install"},
        },
        "required": ["repo_name"],
    },
    "handler": install_cloned_repo,
}
