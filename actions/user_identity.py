from memory.config_manager import save_user_name


def set_user_nickname(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    nickname = (parameters.get("nickname") or "").strip()

    if not nickname:
        return "I need an actual name or title to call you — that one was empty."

    saved = save_user_name(nickname)

    if player:
        player.write_log(f"[Identity] ✅ user address set to '{saved}'")

    return (
        f"From now on, address the user as '{saved}' instead of any previous "
        f"form of address. Confirm this briefly to the user in their own language."
    )


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "set_user_nickname",
    "description": (
        "Changes how MIA addresses the user — a name, nickname, or title. "
        "Call when the user says (in ANY language) 'call me X', 'nenn mich X', "
        "'du kannst mich X nennen', etc. Remembered for future sessions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "nickname": {
                "type": "STRING",
                "description": "The name or title the user wants to be addressed by.",
            }
        },
        "required": ["nickname"],
    },
    "handler": set_user_nickname,
}
