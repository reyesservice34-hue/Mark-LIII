"""
smart_home — lights, heating, sockets and scenes, through Home Assistant.

requirements.txt reserved this slot for `tinytuya`, i.e. talking to Tuya bulbs
directly. That path needs a device id *and* a local key per lamp, and those come
from a Tuya IoT developer project whose free tier expires — a setup that breaks
months later for reasons nobody remembers. Home Assistant is self-hosted, free
for good, issues one long-lived token from its own profile page, and already
speaks to Tuya, Zigbee, HomeKit, Shelly and the rest. So one token reaches every
lamp instead of one key per lamp.

Everything here is reversible — a light switched on is switched off again — so
nothing is gated: core/confirm.py is for what cannot be taken back, and an
assistant that asks before turning on a lamp is one nobody talks to twice.

Names are matched the way a person says them: "Licht Büro" finds
`light.buero_decke`, because nobody remembers entity ids and the ones a German
installation produces are half-transliterated anyway.
"""
from __future__ import annotations

import re
import unicodedata

import requests

from memory.config_manager import get_plugin_setting

NAMESPACE = "smart_home"
TIMEOUT = 15
MAX_LISTED = 25

# Domains worth reaching by voice. Everything else stays addressable by its
# exact entity id but is kept out of fuzzy matching, so "Licht an" can never
# resolve to a door lock.
SAFE_DOMAINS = ("light", "switch", "scene", "climate", "cover", "fan", "media_player")

ON_WORDS  = {"on", "an", "ein", "einschalten", "anmachen", "start"}
OFF_WORDS = {"off", "aus", "ausschalten", "abschalten", "stop"}

# The word for the *kind* of device, mapped to the domain it means. Without this
# the most ordinary sentence in the house fails: the user says "Licht", the
# entity is called `light.buero_decke`, and nothing matches "licht" at all. A
# German installation of Home Assistant is full of English domain names, so the
# domain has to be reachable in the language actually being spoken.
DOMAIN_WORDS = {
    "licht": "light", "lampe": "light", "lampen": "light", "light": "light",
    "leuchte": "light", "beleuchtung": "light",
    "steckdose": "switch", "schalter": "switch", "switch": "switch",
    "strom": "switch",
    "heizung": "climate", "thermostat": "climate", "climate": "climate",
    "rollladen": "cover", "rolladen": "cover", "jalousie": "cover",
    "rollo": "cover", "cover": "cover",
    "szene": "scene", "scene": "scene", "stimmung": "scene",
    "luefter": "fan", "ventilator": "fan", "fan": "fan",
}


class HomeError(Exception):
    """Something the user needs to hear, phrased for speaking aloud."""


def _setting(key: str, default=""):
    value = get_plugin_setting(NAMESPACE, key, default)
    return default if value in (None, "") else value


def _base() -> str:
    url = str(_setting("url", "")).strip().rstrip("/")
    if not url:
        raise HomeError("No Home Assistant address is configured. Put it in plugin "
                        "settings, for example http://homeassistant.local:8123")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _headers() -> dict:
    token = str(_setting("token", "")).strip()
    if not token:
        raise HomeError("No Home Assistant token is configured. Create a long-lived "
                        "access token in your Home Assistant profile and paste it into "
                        "plugin settings.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _call(method: str, path: str, payload: dict | None = None):
    url = f"{_base()}/api/{path.lstrip('/')}"
    try:
        resp = requests.request(method, url, headers=_headers(), json=payload, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        raise HomeError(f"Home Assistant is not answering at {_base()}. Is it running, "
                        f"and is this machine on the same network?")
    except requests.exceptions.Timeout:
        raise HomeError("Home Assistant took too long to answer.")
    except Exception as e:
        raise HomeError(f"Could not reach Home Assistant: {e}")

    if resp.status_code == 401:
        raise HomeError("Home Assistant rejected the token. Create a new long-lived "
                        "access token and paste it into plugin settings.")
    if resp.status_code >= 400:
        raise HomeError(f"Home Assistant refused the request ({resp.status_code}).")
    try:
        return resp.json()
    except Exception:
        return []


# Words a person says around the name that carry no meaning for matching.
# Without this, "Lampe im Büro" fails on "im" and the lamp stays off.
STOPWORDS = {"im", "in", "der", "die", "das", "den", "dem", "des", "am", "beim",
             "vom", "zum", "zur", "the", "at", "of", "my", "mein", "meine"}


def _norm(text: str, *, expand: bool = True) -> str:
    """Fold a spoken name onto an entity id. `expand` writes ü as ue (matching
    a German installation's own transliteration); with it off, ü becomes u — so
    a name typed without umlauts still finds the device."""
    text = (text or "").lower()
    if expand:
        text = (text.replace("ä", "ae").replace("ö", "oe")
                    .replace("ü", "ue").replace("ß", "ss"))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _states() -> list[dict]:
    data = _call("GET", "states")
    return [s for s in data if isinstance(s, dict) and s.get("entity_id")]


def _find(name: str, states: list[dict]) -> list[dict]:
    """Exact entity id wins outright; otherwise every word of the request must
    appear in the name or the id, which keeps 'Licht Büro' from matching the
    first lamp in the house."""
    wanted = _norm(name)
    if not wanted:
        return []

    for s in states:
        if s["entity_id"].lower() == name.lower().strip():
            return [s]

    words = [w for w in wanted.split() if w not in STOPWORDS]
    if not words:
        return []
    hits = []
    for s in states:
        if s["entity_id"].split(".")[0] not in SAFE_DOMAINS:
            continue
        domain = s["entity_id"].split(".")[0]
        friendly = (s.get("attributes") or {}).get("friendly_name", "")
        # Both spellings of every umlaut, so "buero" and "buro" both land.
        haystack = " ".join((
            _norm(s["entity_id"].replace(".", " ")), _norm(friendly),
            _norm(s["entity_id"].replace(".", " "), expand=False),
            _norm(friendly, expand=False),
        ))
        # A word counts as matched when it appears in the name OR when it is the
        # spoken word for this entity's domain.
        if all(w in haystack or _norm(w, expand=False) in haystack
               or DOMAIN_WORDS.get(w) == domain for w in words):
            hits.append(s)
    return hits


def _spoken(state: dict) -> str:
    attrs = state.get("attributes") or {}
    name = attrs.get("friendly_name") or state["entity_id"]
    value = state.get("state", "?")
    if state["entity_id"].startswith("climate."):
        target = attrs.get("temperature")
        current = attrs.get("current_temperature")
        extra = []
        if current is not None:
            extra.append(f"{current}°")
        if target is not None:
            extra.append(f"target {target}°")
        return f"{name}: {value}" + (f" ({', '.join(extra)})" if extra else "")
    return f"{name}: {value}"


def _resolve_one(name: str, states: list[dict]) -> dict:
    hits = _find(name, states)
    if not hits:
        raise HomeError(f"I found nothing called '{name}'.")
    if len(hits) > 1:
        listed = ", ".join((h.get("attributes") or {}).get("friendly_name") or h["entity_id"]
                           for h in hits[:6])
        raise HomeError(f"'{name}' matches {len(hits)} things: {listed}. Which one?")
    return hits[0]


# ── Actions ─────────────────────────────────────────────────────────────────

def _list(p: dict) -> str:
    query = str(p.get("name", "")).strip()
    states = _states()
    items = _find(query, states) if query else [
        s for s in states if s["entity_id"].split(".")[0] in SAFE_DOMAINS]
    if not items:
        return f"Nothing matches '{query}'." if query else "Home Assistant reported nothing."
    shown = items[:MAX_LISTED]
    lines = "\n".join(f"- {_spoken(s)}" for s in shown)
    more = f"\n(and {len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    return lines + more


def _status(p: dict) -> str:
    name = str(p.get("name", "")).strip()
    if not name:
        return _list(p)
    return _spoken(_resolve_one(name, _states()))


def _switch(p: dict, turn_on: bool) -> str:
    name = str(p.get("name", "")).strip()
    if not name:
        return "Which light or device should I switch?"
    state = _resolve_one(name, _states())
    entity = state["entity_id"]
    domain = entity.split(".")[0]

    if domain == "scene":
        if not turn_on:
            return "A scene can only be activated, not switched off."
        _call("POST", "services/scene/turn_on", {"entity_id": entity})
        return f"Scene {(state.get('attributes') or {}).get('friendly_name', entity)} activated."

    service = "turn_on" if turn_on else "turn_off"
    payload: dict = {"entity_id": entity}
    if turn_on and domain == "light":
        if p.get("brightness") is not None:
            try:
                payload["brightness_pct"] = max(1, min(int(p["brightness"]), 100))
            except (TypeError, ValueError):
                pass
    _call("POST", f"services/{domain}/{service}", payload)
    label = (state.get("attributes") or {}).get("friendly_name", entity)
    return f"{label} switched {'on' if turn_on else 'off'}."


def _temperature(p: dict) -> str:
    name = str(p.get("name", "")).strip()
    value = p.get("value")
    if value is None:
        return "What temperature should I set?"
    try:
        target = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return f"'{value}' is not a temperature I can set."
    if not 5 <= target <= 30:
        return f"{target}° is outside what a thermostat will accept (5–30)."

    states = [s for s in _states() if s["entity_id"].startswith("climate.")]
    if not states:
        return "There is no thermostat in Home Assistant."
    state = _resolve_one(name, states) if name else (
        states[0] if len(states) == 1 else None)
    if state is None:
        listed = ", ".join((s.get("attributes") or {}).get("friendly_name") or s["entity_id"]
                           for s in states[:6])
        return f"Which thermostat? There are: {listed}."

    _call("POST", "services/climate/set_temperature",
          {"entity_id": state["entity_id"], "temperature": target})
    label = (state.get("attributes") or {}).get("friendly_name", state["entity_id"])
    return f"{label} set to {target:g}°."


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()

    # "action" is what the model fills in, and it fills it in with whatever the
    # user said — so the German words are accepted rather than corrected.
    if action in ON_WORDS:
        handler = lambda q: _switch(q, True)
    elif action in OFF_WORDS:
        handler = lambda q: _switch(q, False)
    elif action in ("temperature", "temperatur", "heat", "heizung", "set_temperature"):
        handler = _temperature
    elif action in ("status", "state", "zustand", ""):
        handler = _status
    elif action in ("list", "liste", "devices"):
        handler = _list
    else:
        return (f"I do not know the smart home action '{action}'. I can switch things on "
                f"or off, set a temperature, or report status.")

    try:
        result = handler(p)
    except HomeError as e:
        result = str(e)
    except Exception as e:
        result = f"The smart home failed: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result.splitlines()[0]}")
        except Exception:
            pass
    return result


def _test(values: dict) -> tuple[bool, str]:
    global get_plugin_setting
    original = get_plugin_setting
    get_plugin_setting = lambda ns, key, default=None: values.get(key, original(ns, key, default))
    try:
        states = _states()
        usable = [s for s in states if s["entity_id"].split(".")[0] in SAFE_DOMAINS]
        return True, f"Connected. {len(usable)} usable devices of {len(states)} entities."
    except HomeError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Failed: {e}"
    finally:
        get_plugin_setting = original


PLUGIN_SETTINGS = {
    "namespace": NAMESPACE,
    "title": "🏠  SMART HOME (Home Assistant)",
    "fields": [
        {"key": "url", "label": "Home Assistant address", "type": "text",
         "placeholder": "http://homeassistant.local:8123"},
        {"key": "token", "label": "Long-lived access token (HA profile → Security)",
         "type": "password"},
    ],
    "action": {"label": "TEST CONNECTION", "run": _test},
}


PLUGIN = {
    "name": "smart_home",
    "description": (
        "Switches lights, sockets, scenes, blinds and heating through Home Assistant, and "
        "reports what is on — 'Licht im Büro an', 'mach die Heizung auf 21 Grad', 'ist "
        "unten noch Licht?'. Say the device the way the user said it; entity ids are not "
        "needed. Use computer_settings for this computer's own brightness and volume."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "on | off | temperature | status | list"
            },
            "name": {
                "type": "STRING",
                "description": "What the user called the device or room, e.g. 'Licht Büro'"
            },
            "value": {
                "type": "NUMBER",
                "description": "Target temperature in °C for action=temperature"
            },
            "brightness": {
                "type": "INTEGER",
                "description": "Brightness 1-100 when switching a light on"
            }
        },
        "required": []
    },
}
