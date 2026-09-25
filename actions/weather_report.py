import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_weather(city: str, when: str) -> str:
    """Grounded search for real, current weather data — same pattern as
    web_search.py's _gemini_search, so MIA can actually speak the answer
    instead of just opening a browser tab nobody may be looking at."""
    from google import genai

    client = genai.Client(api_key=_get_api_key())
    query = (
        f"Current weather forecast for {city}, {when}. "
        "Include temperature, conditions (sunny/rainy/cloudy/etc), and wind "
        "or precipitation chance if relevant. Be concise — 2-3 sentences."
    )
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _browser_fallback(city: str, when: str, player=None, session_memory=None) -> str:
    search_query = f"weather in {city} {when}"
    url          = f"https://www.google.com/search?q={quote_plus(search_query)}"

    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't get the weather for {city}: {e}"
        _log(msg, player)
        return msg

    msg = (
        f"I couldn't fetch the weather directly, sir, so I've opened it in "
        f"your browser for {city}, {when}."
    )
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=msg)
        except Exception:
            pass

    return msg


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    try:
        report = _gemini_weather(city, when)
        _log(f"Weather for {city}: {report[:80]}", player)

        if session_memory:
            try:
                session_memory.set_last_search(
                    query=f"weather in {city} {when}", response=report,
                )
            except Exception:
                pass

        return report

    except Exception as e:
        print(f"[Weather] ⚠️ Gemini weather failed ({e}) — opening browser instead")
        return _browser_fallback(city, when, player, session_memory)


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
    "description": (
        "Gives the current weather report for a city, spoken directly to the "
        "user — falls back to opening a browser search only if that fails."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name"
            },
            "time": {
                "type": "STRING",
                "description": "When: today, tomorrow, this weekend, etc. (default: today)"
            }
        },
        "required": [
            "city"
        ]
    },
    "handler": weather_action,
}
