from memory.config_manager import PERSONALITY_MODES, save_personality_mode

_ALIASES = {
    "professional": "professional",
    "formal": "professional",
    "business": "professional",
    "casual": "casual",
    "relaxed": "casual",
    "funny": "casual",
    "humorous": "casual",
    "concise": "concise",
    "short": "concise",
    "brief": "concise",
    "minimal": "concise",
    "warm": "warm",
    "friendly": "warm",
    "caring": "warm",
}


def set_personality(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    raw = (parameters.get("mode") or "").strip().lower()
    resolved = _ALIASES.get(raw, raw if raw in PERSONALITY_MODES else "")

    if not resolved:
        known = ", ".join(sorted(PERSONALITY_MODES))
        return f"Unknown personality mode '{raw}'. Known modes: {known}."

    mode = save_personality_mode(resolved)

    if player:
        player.write_log(f"[Personality] ✅ switched to '{mode}'")

    return (
        f"Personality mode set to '{mode}': {PERSONALITY_MODES[mode]} "
        f"Adopt this tone starting with your very next reply, and confirm "
        f"the change to the user in one short sentence, in their own language."
    )


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "set_personality",
    "description": (
        "Changes MIA's conversational tone/personality. Call when the user "
        "asks you to be more casual, more professional, more concise, "
        "friendlier/warmer, or to change how you talk — in ANY language "
        "(e.g. 'sei lockerer', 'be more casual', 'talk less', 'sei wärmer'). "
        "The choice is remembered for future sessions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "description": (
                    "One of: professional, casual, concise, warm. Map the "
                    "user's request to the closest one of these four."
                ),
            }
        },
        "required": ["mode"],
    },
    "handler": set_personality,
}
