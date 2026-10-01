"""
actions/check_pending_confirmation.py — lets a conversation see whether an
action (e.g. self_dev write/run/restart) is currently waiting on
core/confirm.py's gate. See actions/respond_to_confirmation.py to answer it.

Runs in the same process as main.py, so this reads core.confirm directly.
"""
from core import confirm as _confirm


def check_pending_confirmation(parameters: dict, player=None, session_memory=None) -> str:
    info = _confirm.pending_info()
    if not info:
        return "No confirmation is currently pending."
    return (f"Pending confirmation: {info['title']}. Detail: {info['detail']}. "
            f"Waiting {info['ageSeconds']:.0f}s of {info['timeoutSeconds']:.0f}s. "
            f"Call respond_to_confirmation to accept or reject it.")


TOOL = {
    "name": "check_pending_confirmation",
    "description": (
        "Check whether an action is currently waiting for the user's confirmation "
        "(e.g. after self_dev wants to write/run/restart, or any other irreversible "
        "action). Call this if the user asks 'what are you waiting for' or you are "
        "unsure whether something you just tried is still pending."
    ),
    "parameters": {"type": "OBJECT", "properties": {}},
    "handler": check_pending_confirmation,
}
