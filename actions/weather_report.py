import requests


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Die Stadt fehlt für den Wetterbericht."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    try:
        # wttr.in: kostenlos, kein API-Key, liefert echten Text-Wetterbericht -
        # laeuft auch headless auf einem Server, kein Browser noetig.
        resp = requests.get(
            f"https://wttr.in/{requests.utils.quote(city)}",
            params={"format": "3", "lang": "de"},
            timeout=8,
            headers={"User-Agent": "curl"},
        )
        resp.raise_for_status()
        weather_text = resp.text.strip()
        msg = f"Wetter für {city}: {weather_text}"
    except Exception as e:
        msg = f"Ich konnte das Wetter für {city} gerade nicht abrufen: {e}"
        _log(msg, player)
        return msg

    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city} {when}", response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"MIA: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "weather_report",
    "description": "Gives the weather report to user",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name"
            }
        },
        "required": [
            "city"
        ]
    },
    "handler": weather_action,
}
