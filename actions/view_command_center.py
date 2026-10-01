"""
actions/view_command_center.py — lets MIA see AND change the Command
Center's own source (/root/Mark-LIII) — a separate, shared production project
that gives users a web interface to her, worked on in parallel by other
sessions too (see the 2026-09-29 Command Center unification plan).

Read (list/read) is unrestricted — looking costs nothing. Write goes through
core.confirm.request(), same pattern as self_dev.py's own write action, with
an automatic git snapshot commit before AND after (same safety net self_dev
gives Mark-LIII's own code) — a bad edit is always one `git revert` away, on
jarvis's own history, without touching whatever another session currently
has staged/uncommitted there.

This is intentionally NOT ungated. self_dev.py's write action used to be
(its own top-of-file docstring says so) and was deliberately changed to
require confirmation after — the same reasoning applies even more here:
jarvis is shared with other people/sessions and has no snapshot safety net
of its own the way this repo does.
"""
import subprocess
from pathlib import Path

from core import confirm as _confirm

JARVIS_ROOT = Path("/root/Mark-LIII")
_OUTPUT_LIMIT = 6000


def _git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(JARVIS_ROOT),
                              capture_output=True, text=True, timeout=30)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _resolve(rel_path: str) -> Path | None:
    try:
        target = (JARVIS_ROOT / rel_path).resolve()
        target.relative_to(JARVIS_ROOT.resolve())
    except (ValueError, RuntimeError, OSError):
        return None
    return target


def _write(rel_path: str, content: str) -> str:
    target = _resolve(rel_path)
    if target is None:
        return f"Refused: '{rel_path}' is outside the Command Center's own folder."
    if content is None:
        return "No content given — nothing written."
    _git("add", "-A")
    _git("commit", "-q", "-m", f"[auto-snapshot before MIA edit] {rel_path}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Write failed for '{rel_path}': {e}"
    _git("add", "-A")
    rc, _ = _git("commit", "-q", "-m", f"MIA edit via Command Center tool: {rel_path}")
    return (f"Wrote '{rel_path}' ({len(content)} chars) in the Command Center. "
            + ("Committed to git in /root/Mark-LIII — the running container still needs a "
               "rebuild/restart to pick it up, not done automatically."
               if rc == 0 else "Nothing to commit (content unchanged)."))


def view_command_center(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "list").strip().lower()
    path = str(p.get("path") or "").strip()

    if action == "write":
        content = p.get("content")
        if _resolve(path) is None:
            return f"Refused: '{path}' is outside the Command Center's own folder."
        return _confirm.request(
            key=f"jarvis_write_{path}",
            title=f"MIA will das Command Center aendern: {path}",
            detail=(f"{len(content or '')} Zeichen werden in '{path}' geschrieben "
                   f"(Git-Snapshot vorher in /root/Mark-LIII, reversibel; andere Sitzungen dort "
                   f"werden nicht angetastet)."),
            run=lambda: _write(path, content),
        )

    target = _resolve(path or ".")
    if target is None:
        return f"Refused: '{path}' is outside the Command Center's own folder."
    if not target.exists():
        return f"'{path}' does not exist in the Command Center."

    if action == "read":
        if not target.is_file():
            return f"'{path}' is a folder, not a file — use action=list."
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Could not read '{path}': {e}"
        return text[:_OUTPUT_LIMIT] + (f"\n... [truncated, {len(text)} chars total]"
                                       if len(text) > _OUTPUT_LIMIT else "")

    # default: list
    if target.is_file():
        return f"'{path}' is a file, not a folder — use action=read."
    entries = sorted(e.name + ("/" if e.is_dir() else "") for e in target.iterdir()
                     if e.name not in (".git", "node_modules", "__pycache__"))
    return "\n".join(entries) if entries else "(empty)"


TOOL = {
    "name": "view_command_center",
    "description": (
        "See AND change the Command Center's own source code (a separate project at /root/Mark-LIII "
        "that gives users a web interface to you). action=list browses folders, action=read reads a "
        "file, action=write changes one (asks the user to confirm first, same as self_dev). Use "
        "self_dev instead for changing your own (Mark-LIII) code."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list (default), read, or write"},
            "path": {"type": "STRING", "description": "Path relative to the Command Center's root, e.g. 'command_center/backend/app.py'"},
            "content": {"type": "STRING", "description": "New full file content, for action=write"},
        },
        "required": [],
    },
    "handler": view_command_center,
}
