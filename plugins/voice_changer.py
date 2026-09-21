
PLUGIN = {
    "name": "voice_changer",
    "description": "Adjusts the pitch of the voice output.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "pitch_multiplier": {"type": "NUMBER", "description": "Factor to multiply the pitch, e.g. 1.2 for higher, 0.8 for lower"},
        },
        "required": ["pitch_multiplier"],
    },
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    pitch = parameters.get("pitch_multiplier", 1.0)
    # Placeholder for actual implementation. Integrating with system TTS or external tools requires OS-spesific commands not directly available here.
    return f"Pitch set to {pitch}. Applying this change system-wide is not implemented yet."
