"""
actions/respond_to_confirmation.py — lets a conversation (chat or voice)
accept or reject a pending core/confirm.py confirmation — e.g. self_dev
wanting to write/run/restart — instead of only the Qt HUD button or
dashboard/confirm_api.py's own endpoint.

Runs in the same process as main.py (discover_actions() loads every actions/
file into every path: Gemini-Live, core/local_brain.py's Ollama path, and
therefore the Command Center bridge too), so this calls core.confirm.resolve()
directly — no HTTP round trip, no new attack surface.

Without this, a confirmation raised inside a text conversation had no way to
actually be answered from that same conversation: the user was told "confirm
on the HUD", but a chat has no HUD, and the request expired unanswered after
core.confirm.TIMEOUT_SECONDS (90s) — self_dev's write/run/restart actions
were unusable outside the desktop app.
"""
from core import confirm as _confirm


def respond_to_confirmation(parameters: dict, player=None, session_memory=None) -> str:
    accepted = bool(parameters.get("accepted"))
    info = _confirm.pending_info()
    if not info:
        return "There is nothing waiting for confirmation right now."
    title = info["title"]
    _confirm.resolve(accepted)
    if player:
        try:
            player.write_log(f"MIA: [confirm] {'accepted' if accepted else 'rejected'} — {title}")
        except Exception:
            pass
    return (f"Confirmed and executing: {title}." if accepted
            else f"Cancelled, nothing was done: {title}.")


TOOL = {
    "name": "respond_to_confirmation",
    "description": (
        "Accept or reject the currently pending confirmation (from self_dev or any "
        "other irreversible action). Call this as soon as the user says yes/confirm/"
        "go ahead or no/cancel/stop in response to something you told them needs "
        "their confirmation — in ANY language ('ja', 'mach das', 'bestätige', 'nein', "
        "'abbrechen', 'stopp')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {"accepted": {"type": "BOOLEAN",
                                    "description": "true to confirm and run it, false to cancel"}},
        "required": ["accepted"],
    },
    "handler": respond_to_confirmation,
}
