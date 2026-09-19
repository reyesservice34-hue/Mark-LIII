"""
call_device.py — lets JARVIS itself ring the user's paired phone for
something urgent, instead of waiting for them to be looking at a screen.

Delivery is a Web Push notification (dashboard/push.py) that makes the phone
show a full-screen "incoming call" UI and read the message aloud — see the
push handler in dashboard/static/app.html. It needs the phone to have been
paired once via Geräte → Remote Control and to have push notifications
enabled there; both fail gracefully into a spoken explanation rather than an
error, since the model calling this can't see the phone's pairing state.
"""
from __future__ import annotations


def call_phone(parameters: dict, player=None) -> str:
    message = (parameters.get("message") or "").strip()
    title   = (parameters.get("title") or "JARVIS").strip()

    if not message:
        return "I need a message to deliver before I can call the phone."

    dashboard = getattr(player, "dashboard", None) if player else None
    if dashboard is None:
        return "Sir, the remote dashboard isn't running, so I can't call your phone."

    result = dashboard.send_call(title, message)
    if result.get("total_devices", 0) == 0:
        return "No phone is paired yet — open Geräte in JARVIS and pair one first."
    if result.get("pushable", 0) == 0:
        return "Your phone is paired, but push notifications aren't enabled there yet."
    if result.get("sent", 0) == 0:
        return "I tried to call your phone, but the push notification failed to send."

    if player:
        try:
            player.write_log(f"[Call] 📞 {title}: {message[:60]}")
        except Exception:
            pass
    return f"Calling your phone now: {message}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "call_phone",
    "description": (
        "Rings the user's paired phone like an incoming call: it shows a full-screen alert, "
        "vibrates/rings, and reads the given message aloud when opened. Use ONLY for genuinely "
        "important or time-sensitive information the user should not miss right now — a critical "
        "reminder, something urgent you noticed, an important request from someone else — never for "
        "routine answers or anything the user can just as well read later. Do not use this to answer "
        "a question the user just asked in conversation; speak that normally instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "message": {"type": "STRING", "description": "What JARVIS should say/show on the call"},
            "title": {"type": "STRING", "description": "Short caller label, defaults to 'JARVIS'"},
        },
        "required": ["message"],
    },
    "handler": call_phone,
}
