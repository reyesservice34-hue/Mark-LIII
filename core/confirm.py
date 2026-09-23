"""
core/confirm.py — a confirmation the model cannot forge.

THE PROBLEM WITH THE OLD GATE
    computer_settings guarded shutdown and restart like this:

        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return "Please confirm by calling again with confirmed=yes."

    `confirmed` is a tool parameter, which means the *model* writes it. Nothing
    stops it from sending confirmed=yes on the first call, and nothing checks
    that a human was ever involved. It is a convention, not a gate — and its
    coverage was two actions, so deleting files and switching off the WiFi the
    assistant is talking over went through with no gate at all.

THE DESIGN HERE
    The confirmation token is issued by the *interface*, never by the model:

      1. An action calls `request(...)` with a callable that does the real work.
      2. This module hands the desktop HUD a banner with CONFIRM / CANCEL, and
         broadcasts the same request to the phone/dashboard (the "Freigaben"
         centre), then returns IMMEDIATELY with a sentence for the model to
         say out loud.
      3. If — and only if — a human presses CONFIRM/Freigeben (on either
         surface), `resolve()` runs the stored callable off the calling thread.

    Nothing blocks. The model keeps talking while the request is open, so this
    costs no latency at all; in fact it is cheaper than the old gate, which
    burned two tool round trips (reject, then re-call) on every shutdown.

WHAT BELONGS HERE AND WHAT DOES NOT
    Only genuinely irreversible things. Anything that can be reversed should be
    done at once and pushed onto core/undo.py instead — undo is faster than a
    question, and an assistant that asks before every action is one nobody uses.

MULTIPLE CONCURRENT APPROVALS
    The desktop HUD still shows one banner at a time (its layout has no room
    for a stack, and computer_settings.py already refuses a second request
    while one is showing — see `pending_title()`). But the underlying gate
    supports any number of concurrent pending approvals, each with its own
    `id`, so the dashboard can track and resolve several independently. The
    desktop banner always tracks the *last* request that was shown on it;
    `resolve(accepted)` with no `approval_id` resolves that one, which keeps
    ui.py's existing call site (`resolve(bool(accepted))`) working unchanged.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

# A pending confirmation is abandoned after this long. Chosen to outlast a
# normal "hang on, let me look at the screen" pause without leaving a live
# shutdown button sitting on the HUD for the rest of the day.
TIMEOUT_SECONDS = 90.0

# How many resolved approvals to keep for the dashboard's history/archive.
HISTORY_LIMIT = 200


@dataclass
class _Pending:
    id:      str
    key:     str
    title:   str
    detail:  str
    run:     Callable[[], str]
    at:      float
    agent:   str = "MIA"
    action:  str = ""
    target:  str = ""
    meta:    dict = field(default_factory=dict)
    status:  str = "pending"   # pending | executing | rejected | expired | success | failed
    result:  str = ""


_pending: dict[str, _Pending] = {}
_history: list[dict] = []
_desktop_shown_id: Optional[str] = None
_lock = threading.Lock()

# Set once at startup by main.py.
#   show(title, detail) -> None        — desktop HUD banner
#   hide() -> None                     — desktop HUD banner
#   log(msg) -> None                   — desktop text log
#   broadcast(payload: dict) -> None   — dashboard/phone (sync-safe; main.py
#                                         schedules the actual async send)
_show_cb:      Optional[Callable[[str, str], None]] = None
_hide_cb:      Optional[Callable[[], None]] = None
_log_cb:       Optional[Callable[[str], None]] = None
_broadcast_cb: Optional[Callable[[dict], None]] = None


def bind(show, hide, log=None, broadcast=None) -> None:
    """Wire this module to the HUD and the dashboard. Called once from main.py
    at startup."""
    global _show_cb, _hide_cb, _log_cb, _broadcast_cb
    _show_cb, _hide_cb, _log_cb, _broadcast_cb = show, hide, log, broadcast


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception:
            pass


def _emit(payload: dict) -> None:
    if _broadcast_cb:
        try:
            _broadcast_cb(payload)
        except Exception:
            pass


def _public(p: _Pending) -> dict:
    """JSON-safe view of a pending/resolved approval for the dashboard.
    Never includes `run` (the callable) or anything secret-shaped."""
    d = {
        "id":     p.id,
        "key":    p.key,
        "title":  p.title,
        "detail": p.detail,
        "agent":  p.agent,
        "action": p.action or p.key,
        "target": p.target,
        "status": p.status,
        "at":     p.at,
    }
    if p.meta:
        d["meta"] = p.meta
    if p.result:
        d["result"] = p.result
    return d


def request(
    key: str, title: str, detail: str, run: Callable[[], str], *,
    agent: str = "MIA", action: str = "", target: str = "",
    meta: Optional[dict] = None, show_on_hud: bool = True,
) -> str:
    """Park an irreversible action behind the on-screen gate.

    Returns the sentence the tool should hand back to the model — phrased as an
    instruction so the assistant asks the user out loud in their own language,
    rather than reading an English string verbatim."""
    global _desktop_shown_id

    if show_on_hud and _show_cb is None:
        # No interface bound (headless, or a very early call). Refuse rather
        # than silently performing something irreversible.
        return (f"I cannot confirm '{title}' right now because the interface is "
                f"not available, so I have not done it.")

    rid = uuid.uuid4().hex[:12]
    p = _Pending(
        id=rid, key=key, title=title, detail=detail, run=run, at=time.time(),
        agent=agent, action=action or key, target=target, meta=dict(meta or {}),
    )

    with _lock:
        _pending[rid] = p

    if show_on_hud:
        try:
            _show_cb(title, detail)
            _desktop_shown_id = rid
        except Exception as e:
            with _lock:
                _pending.pop(rid, None)
            return f"Could not ask for confirmation: {e}. Nothing was done."

    _log(f"SYS: Awaiting confirmation — {title}")
    _emit({"type": "approval_pending", **_public(p)})
    return (
        f"[CONFIRMATION_PENDING] I have put a confirmation on screen for: {title}. "
        f"Say ONE short sentence in the user's own language telling them you need "
        f"them to confirm it on the HUD or dashboard before you do it. Do not claim it is done."
    )


def _finish(p: _Pending) -> None:
    entry = _public(p)
    with _lock:
        _pending.pop(p.id, None)
        _history.insert(0, entry)
        del _history[HISTORY_LIMIT:]
    _emit({"type": "approval_resolved", **entry})


def resolve(accepted: bool, approval_id: Optional[str] = None) -> bool:
    """Called when a human presses Confirm/Freigeben or Cancel/Ablehnen, on
    either the desktop HUD or the dashboard.

    `approval_id=None` resolves whichever request is currently shown on the
    desktop HUD banner — this is what keeps ui.py's existing call site
    (`resolve(bool(accepted))`) working without any changes there. The
    dashboard always passes an explicit `approval_id`.

    Runs the stored callable on a worker thread — never on the caller's
    thread, since shutting the machine down from inside a button handler
    would freeze whichever interface is waiting on it.

    Returns True if an approval was actually resolved (False if it had
    already expired, or nothing with that id/slot was pending)."""
    global _desktop_shown_id

    with _lock:
        rid = approval_id if approval_id is not None else _desktop_shown_id
        was_desktop_item = rid is not None and rid == _desktop_shown_id
        p = _pending.get(rid) if rid else None
        if was_desktop_item:
            _desktop_shown_id = None
        expired = p is not None and time.time() - p.at > TIMEOUT_SECONDS
        if p is not None and (not accepted or expired):
            # Terminal without an "executing" phase — leaves _pending now.
            # An accepted request stays in _pending (status "executing") until
            # its worker thread finishes, so a REST snapshot taken mid-run
            # still shows it instead of it vanishing between pending/history.
            _pending.pop(rid, None)

    if (approval_id is None or was_desktop_item) and _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass

    if p is None:
        return False

    if expired:
        p.status = "expired"
        _log(f"SYS: Confirmation expired — {p.title}")
        _finish(p)
        return False

    if not accepted:
        p.status = "rejected"
        _log(f"SYS: Cancelled — {p.title}")
        _finish(p)
        return True

    p.status = "executing"
    _emit({"type": "approval_resolved", **_public(p)})

    def _worker():
        try:
            result = p.run() or "Done."
            p.status, p.result = "success", result
            _log(f"SYS: Confirmed — {p.title}. {result}")
        except Exception as e:
            p.status, p.result = "failed", str(e)
            _log(f"ERR: {p.title} failed — {e}")
        _finish(p)

    threading.Thread(target=_worker, daemon=True,
                     name=f"confirm-{p.key}").start()
    return True


def pending_title() -> str:
    """'' when nothing is waiting on the desktop HUD. Lets an action avoid
    stacking two banners there. (Does not reflect dashboard-only approvals —
    those can coexist; this is specifically "is the one HUD banner busy".)"""
    with _lock:
        if _desktop_shown_id and _desktop_shown_id in _pending:
            p = _pending[_desktop_shown_id]
            if time.time() - p.at <= TIMEOUT_SECONDS:
                return p.title
        return ""


def sweep_expired() -> list[dict]:
    """Move any pending approval past TIMEOUT_SECONDS into history as
    'expired', hiding the desktop banner if that was the one showing.
    Returns the list of newly-expired entries (already broadcast)."""
    global _desktop_shown_id
    now = time.time()
    expired: list[_Pending] = []

    with _lock:
        for rid in [k for k, v in _pending.items()
                    if v.status == "pending" and now - v.at > TIMEOUT_SECONDS]:
            expired.append(_pending.pop(rid))
            if rid == _desktop_shown_id:
                _desktop_shown_id = None

    out = []
    for p in expired:
        p.status = "expired"
        _log(f"SYS: Confirmation expired — {p.title}")
        if _hide_cb:
            try:
                _hide_cb()
            except Exception:
                pass
        _finish(p)
        out.append(_public(p))
    return out


def list_state() -> dict:
    """Snapshot for the dashboard: still-open approvals plus recent history.
    Sweeps expired entries first so a client that only ever polls this (no
    WebSocket) still sees accurate status."""
    sweep_expired()
    with _lock:
        pending = [_public(p) for p in _pending.values()]
        history = list(_history[:100])
    pending.sort(key=lambda d: d["at"], reverse=True)
    return {"pending": pending, "history": history}
