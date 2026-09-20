"""
actions/self_dev.py — lets JARVIS act as its own programmer for its own
project: edit the dashboard, the phone companion app, its own action files,
and write entirely new tools/plugins for itself — never anything outside
this project folder.

Explicit, informed request from the owner (Reyes Service): no confirmation
gate on this one, unlike core/confirm.py's irreversible-action gate. The
trade made in exchange for that:

  * Scoped to this repository only (BASE_DIR) — every path is resolved and
    checked to still be inside it before any read/write/run happens.
  * Every write is preceded by an automatic git snapshot commit, so a bad
    edit is never unrecoverable — `git log` / `git revert` always gets it
    back. This is the safety net that replaces the confirmation dialog.
  * `run` executes inside this repo's directory only. It does NOT drop
    privileges — this process still runs as whatever user started it — so
    it is a strong convention, not a hard OS sandbox. If main.py is ever
    run as root (e.g. the systemd unit in scripts/mark-liii.service), this
    tool can still reach the rest of that machine via an absolute path or
    a command that targets something else. Run main.py as a dedicated
    non-root user with filesystem permissions limited to this directory if
    a real, enforced boundary is required — this module cannot provide one
    by itself.
  * Deliberately NOT wired to also control other services on the host
    (n8n, docker, other systemd units) — this is for JARVIS's own project,
    not the whole server.
"""

import subprocess
import sys
from pathlib import Path

RUN_TIMEOUT_SECONDS = 120
_OUTPUT_LIMIT = 4000


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()


def _resolve_in_repo(rel_path: str) -> Path | None:
    """Resolve a path relative to the repo root; None if it escapes it."""
    try:
        target = (BASE_DIR / rel_path).resolve()
        target.relative_to(BASE_DIR.resolve())
    except (ValueError, RuntimeError, OSError):
        return None
    return target


def _git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=30,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _snapshot(message: str) -> None:
    """Auto-commit whatever is currently on disk before we change it, so an
    edit that turns out to be wrong is always one `git revert` away."""
    _git("add", "-A")
    _git("commit", "-q", "-m", f"[auto-snapshot] {message}")


def _list(rel_path: str) -> str:
    target = _resolve_in_repo(rel_path or ".")
    if target is None:
        return f"Refused: '{rel_path}' is outside the project folder."
    if not target.exists():
        return f"'{rel_path}' does not exist."
    if target.is_file():
        return f"'{rel_path}' is a file, not a folder."
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(entries) if entries else "(empty)"


def _read(rel_path: str) -> str:
    target = _resolve_in_repo(rel_path)
    if target is None:
        return f"Refused: '{rel_path}' is outside the project folder."
    if not target.is_file():
        return f"'{rel_path}' does not exist."
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read '{rel_path}': {e}"
    if len(text) > _OUTPUT_LIMIT:
        return text[:_OUTPUT_LIMIT] + f"\n... [truncated, {len(text)} chars total]"
    return text


def _write(rel_path: str, content: str) -> str:
    target = _resolve_in_repo(rel_path)
    if target is None:
        return f"Refused: '{rel_path}' is outside the project folder — not touching it."
    if content is None:
        return "No content given — nothing written."

    _snapshot(f"before editing {rel_path}")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Write failed for '{rel_path}': {e}"

    rc, out = _git("add", "-A")
    rc, out = _git("commit", "-q", "-m", f"JARVIS self-edit: {rel_path}")
    committed = rc == 0

    return (
        f"Wrote '{rel_path}' ({len(content)} chars). "
        + ("Committed to git — call restart to apply it."
           if committed else
           "Nothing to commit (content unchanged).")
    )


def _run(command: str) -> str:
    if not command or not command.strip():
        return "No command given."
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(BASE_DIR),
            capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS,
        )
        out = (proc.stdout + proc.stderr).strip()
        if len(out) > _OUTPUT_LIMIT:
            out = out[:_OUTPUT_LIMIT] + f"\n... [truncated]"
        return f"Exit {proc.returncode}.\n{out}" if out else f"Exit {proc.returncode}. No output."
    except subprocess.TimeoutExpired:
        return f"Command timed out after {RUN_TIMEOUT_SECONDS}s."
    except Exception as e:
        return f"Command failed: {e}"


def _restart() -> str:
    # Prefer the systemd unit from scripts/mark-liii.service when present —
    # it's what actually keeps the dashboard reachable on the phone.
    check = subprocess.run(
        ["systemctl", "is-enabled", "mark-liii"],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        result = subprocess.run(
            ["systemctl", "restart", "mark-liii"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Restarting via systemd — back in a few seconds."
        return f"systemctl restart failed: {result.stderr.strip()}"
    return (
        "Not running under the mark-liii systemd service, so I can't restart "
        "myself safely from here — restart main.py by hand to apply the change."
    )


def self_dev(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    path    = parameters.get("path", "")
    content = parameters.get("content")
    command = parameters.get("command", "")

    if action == "list":
        result = _list(path)
    elif action == "read":
        result = _read(path)
    elif action == "write":
        result = _write(path, content)
    elif action == "run":
        result = _run(command)
    elif action == "restart":
        result = _restart()
    else:
        result = "Unknown self_dev action. Use list, read, write, run, or restart."

    if player:
        try:
            player.write_log(f"JARVIS: [self_dev {action}] {result[:200]}")
        except Exception:
            pass
    return result


TOOL = {
    "name": "self_dev",
    "description": (
        "Your own developer toolbox — full, unconfirmed read/write/run access "
        "to this JARVIS project's own folder (this repository), and nowhere "
        "else. Use it to: (1) modify your own dashboard, your phone companion "
        "app (dashboard/static/*.html, dashboard/server.py), or any of your "
        "existing actions/plugins; (2) write BRAND NEW tools for yourself — a "
        "new capability the user asks for that doesn't exist yet — as a new "
        "file under actions/ or plugins/; (3) inspect your own code first with "
        "list/read before changing it, so edits are informed, not guessed.\n\n"
        "HARD BOUNDARY, no exceptions: every path is checked to be inside this "
        "project folder before anything happens, and `run` always executes "
        "with this folder as its working directory. Never construct a path "
        "that leaves it (no '..', no absolute paths to /etc, /root, other "
        "projects, or other services on the machine like n8n, Docker, or "
        "system config) — that is explicitly out of bounds even though the "
        "tool itself won't stop every possible way to try. If a task needs "
        "something outside this project, say so instead of attempting it.\n\n"
        "WRITING A NEW TOOL — follow this shape exactly or JARVIS won't load "
        "it. Read plugins/_template.py first (action='read', "
        "path='plugins/_template.py') to see a live example, then write a new "
        "file to plugins/<snake_case_name>.py containing:\n"
        "  PLUGIN = {'name': '<snake_case_name>', 'description': '<when to "
        "call this, be explicit about trigger phrases>', 'parameters': "
        "{'type': 'OBJECT', 'properties': {...}, 'required': [...]}}\n"
        "  def run(parameters: dict, player=None, session_memory=None) -> str: "
        "...  # return a short spoken sentence, never raise\n"
        "(Prefer plugins/ for a brand-new capability; only add to actions/ "
        "when asked to change something that already lives there — actions/ "
        "uses TOOL + a handler= entry instead of PLUGIN, see any existing "
        "actions/*.py file for that shape.)\n\n"
        "NEEDING A NEW PYTHON PACKAGE for a plugin/tool you're writing: use "
        "action='run' with command='.venv/bin/pip install <package>' — this "
        "installs into JARVIS's own virtual environment only, never system-wide "
        "and never on the rest of the machine. Also add the package to "
        "requirements.txt (action='read' it first, then 'write' it back with "
        "the new line appended) so a fresh setup reinstalls it too.\n\n"
        "APPLYING CHANGES: a write only takes effect after action='restart'. "
        "Every write auto-commits to git first — nothing is ever unrecoverable, "
        "`git log`/`git revert` always gets a bad change back. Do the smallest "
        "correct change, verify it (e.g. run='python -m py_compile <path>') "
        "before restarting, and tell the user in one sentence what you changed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "list | read | write | run | restart",
            },
            "path": {
                "type": "STRING",
                "description": "Path relative to the project root, for list/read/write "
                                "(e.g. 'dashboard/static/app.html', 'plugins/', "
                                "'plugins/reminder_plus.py').",
            },
            "content": {
                "type": "STRING",
                "description": "Full new file content, for write.",
            },
            "command": {
                "type": "STRING",
                "description": "Shell command to run inside the project folder, for run "
                                "(e.g. 'python -m py_compile dashboard/server.py').",
            },
        },
        "required": ["action"],
    },
    "handler": self_dev,
}
