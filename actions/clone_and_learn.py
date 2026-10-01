"""
actions/clone_and_learn.py — clone a GitHub (or any git) repo, read what it
actually is (README + top-level structure), and report back in one tool call
— not a chain the model has to orchestrate itself across several calls, which
is exactly where the small local model was unreliable (2026-09-29).

Clones land at BASE_DIR/learned_repos/<name> — INSIDE self_dev's existing
project scope (self_dev._resolve_in_repo checks against the same BASE_DIR),
so "using" a cloned repo (running a script from it, reading more of its
files) is already possible with the existing self_dev tool afterwards:
  self_dev(action="run", command="python learned_repos/<name>/main.py ...")
  self_dev(action="read", path="learned_repos/<name>/some_file.py")
No new "run arbitrary code" surface is introduced here — cloning and reading
a README are safe, reversible (delete the folder), and NOT gated; actually
running anything from the clone still goes through self_dev's existing
confirmation gate, same as self-editing MIA's own code.
"""
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = BASE_DIR / "learned_repos"
CLONE_TIMEOUT_SECONDS = 120
_OUTPUT_LIMIT = 3000

_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _repo_name_from_url(url: str, override: str = "") -> str:
    if override.strip() and _NAME_RE.match(override.strip()):
        return override.strip()
    name = url.rstrip("/").rsplit("/", 1)[-1]
    name = name[:-4] if name.endswith(".git") else name
    return name if _NAME_RE.match(name) else "cloned_repo"


def _list_top_level(target: Path) -> list[str]:
    return sorted(p.name + ("/" if p.is_dir() else "")
                 for p in target.iterdir() if p.name != ".git")


def _read_readme(target: Path) -> str:
    for candidate in ("README.md", "README.rst", "README.txt", "readme.md", "Readme.md"):
        p = target / candidate
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                return text[:_OUTPUT_LIMIT] + (f"\n... [truncated, {len(text)} chars total]"
                                               if len(text) > _OUTPUT_LIMIT else "")
            except Exception as e:
                return f"(README found but could not be read: {e})"
    return "(no README found)"


def clone_and_learn(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    url = str(p.get("repo_url") or "").strip()
    if not url:
        return "I need a repo URL (e.g. https://github.com/owner/name) to clone."
    if not (url.startswith("https://") or url.startswith("git@")):
        return "Refused: only https:// or git@ URLs are cloned, not local paths or anything else."

    name = _repo_name_from_url(url, str(p.get("local_name") or ""))
    target = REPOS_DIR / name
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    if target.exists():
        return (f"'{name}' is already cloned at learned_repos/{name}. Use self_dev(action=\"read\", "
                f"path=\"learned_repos/{name}/...\") to look at specific files, "
                f"or delete the folder first to re-clone.")

    if player:
        try:
            player.write_log(f"MIA: [clone_and_learn] cloning {url} -> learned_repos/{name}")
        except Exception:
            pass

    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            capture_output=True, text=True, timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Clone timed out after {CLONE_TIMEOUT_SECONDS}s."
    except Exception as e:
        return f"Clone failed: {e}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()[:800]
        return f"Clone failed (exit {proc.returncode}): {err}"

    try:
        top_level = _list_top_level(target)
    except Exception as e:
        top_level = [f"(could not list: {e})"]
    readme = _read_readme(target)

    return (
        f"Cloned '{name}' to learned_repos/{name}.\n\n"
        f"Top-level contents:\n" + "\n".join(f"- {f}" for f in top_level) + "\n\n"
        f"README:\n{readme}\n\n"
        f"To actually use or read more of it, call self_dev with path prefixed "
        f"\"learned_repos/{name}/\" — e.g. self_dev(action=\"run\", "
        f"command=\"python learned_repos/{name}/<script>.py\") to run something from it, "
        f"or self_dev(action=\"read\", path=\"learned_repos/{name}/<file>\") to read a specific file. "
        f"Both still ask the user to confirm, same as any self_dev action."
    )


TOOL = {
    "name": "clone_and_learn",
    "description": (
        "Clones a git repo (e.g. from GitHub) into MIA's own project so she can learn from and use "
        "it — returns its README and top-level file structure in one call. Call this when the user "
        "asks to clone/learn/pull in a repository. After cloning, use the self_dev tool with a path "
        "starting 'learned_repos/<name>/' to actually read more files or run something from it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "repo_url": {"type": "STRING", "description": "https:// or git@ URL of the repo to clone"},
            "local_name": {"type": "STRING",
                          "description": "Optional folder name to clone into (default: derived from the URL)"},
        },
        "required": ["repo_url"],
    },
    "handler": clone_and_learn,
}
