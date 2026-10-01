import json
import sys
import requests
from pathlib import Path


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
    """Headless-safe HTTP fallback via wttr.in, no browser and no API key."""
    try:
        r = requests.get(
            f"https://wttr.in/{city}",
            params={"format": "j1"},
            headers={"User-Agent": "MIA/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        current = (data.get("current_condition") or [{}])[0]
        temp_c = current.get("temp_C")
        feels_c = current.get("FeelsLikeC")
        desc = ((current.get("weatherDesc") or [{}])[0].get("value") or "").strip()
        wind_kmh = current.get("windspeedKmph")
        humidity = current.get("humidity")
        if temp_c in (None, "") or not desc:
            raise RuntimeError("wttr.in returned incomplete weather data")

        report = (
            f"{city}: {desc}, {temp_c} °C"
            + (f", gefuehlt {feels_c} °C" if feels_c not in (None, "") else "")
            + (f", Wind {wind_kmh} km/h" if wind_kmh not in (None, "") else "")
            + (f", Luftfeuchte {humidity} %" if humidity not in (None, "") else "")
            + "."
        )

        if session_memory:
            try:
                session_memory.set_last_search(
                    query=f"weather in {city} {when}",
                    response=report,
                )
            except Exception:
                pass

        _log(f"Weather HTTP fallback for {city} succeeded.", player)
        return report
    except Exception as e:
        msg = f"Weather lookup failed for {city}: {e}"
        _log(msg, player)
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

    # Serverbetrieb: direkter HTTP-Abruf ohne Browser und ohne API-Quota.
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
        "user — falls back to a headless web search if the primary provider fails."
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
