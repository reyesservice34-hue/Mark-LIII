import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"[Config] Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


# ── Assistant identity ───────────────────────────────────────────────────────
# MIA is the one and only assistant identity. Older installs stored the
# previous name ("JARVIS") in api_keys.json; those values are treated as unset
# so they can never reach the system prompt, the HUD or the dashboard again.
DEFAULT_ASSISTANT_NAME = "MIA"
_LEGACY_ASSISTANT_NAMES = {"jarvis", "j.a.r.v.i.s", "j.a.r.v.i.s."}


def normalize_assistant_name(name) -> str:
    """Return a usable assistant name: empty or legacy names become MIA."""
    n = (name or "").strip() if isinstance(name, str) else ""
    if not n or n.lower() in _LEGACY_ASSISTANT_NAMES:
        return DEFAULT_ASSISTANT_NAME
    return n


def migrate_assistant_identity() -> bool:
    """One-time migration of a legacy assistant name stored in api_keys.json.

    Rewrites only the 'assistant_name' field and keeps every other key (API keys,
    plugin credentials) untouched. Git is the rollback history; no parallel
    backup file is created. Returns True when the file was changed."""
    if not CONFIG_FILE.exists():
        return False
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict) or "assistant_name" not in data:
        return False
    current = data.get("assistant_name")
    fixed = normalize_assistant_name(current)
    if current == fixed:
        return False
    data["assistant_name"] = fixed
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
    print(f"[Config] Assistant identity migrated to {fixed}")
    return True


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'MIA' if not set."""
    return normalize_assistant_name(load_api_keys().get("assistant_name"))


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


# ── How the assistant addresses the user ─────────────────────────────────────
# Stored, not hardcoded: the vocative belongs to the person, not to the code.
# Tool result strings carry NO vocative at all — they are data the model
# rephrases, and a fixed English "sir" inside them was leaking into German
# sentences. The one rule below is the only place the address is decided.
DEFAULT_ADDRESS = "mein Herr"


def get_user_address() -> str:
    """The form of address to use, e.g. 'mein Herr'. Never empty."""
    v = (load_api_keys().get("user_address") or "").strip()
    return v or DEFAULT_ADDRESS


def save_user_address(address: str) -> None:
    """Persist the form of address. An empty value restores the default."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data["user_address"] = (address or "").strip() or DEFAULT_ADDRESS
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data["assistant_name"] = normalize_assistant_name(assistant_name)
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_user_name(user_name: str) -> str:
    """Persist only the user's preferred name/nickname, leaving every other
    field (assistant name, API keys, ...) untouched. Returns the stripped
    value that was stored."""
    name = (user_name or "").strip()
    _patch_config(user_name=name)
    return name


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Kore"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until the wake phrase)."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Personality mode ─────────────────────────────────────────────────────────
# A small set of tone presets the user can switch between by voice. Each entry
# is the fragment injected into the system prompt (see main.py._build_config)
# so the persisted choice survives a restart, not just the current session.
PERSONALITY_MODES = {
    "mia": (
        "Core MIA personality. Competent, calm, direct, personal and intelligent; "
        "natural and fluid, never stiff, preachy or robotic. Think deeply internally "
        "but communicate clearly and efficiently, with the result before the process. "
        "Behave like a discreet cinematic chief-of-staff: anticipate relevant needs, "
        "keep a composed presence, use subtle dry humor, and speak only when useful. "
        "In private conversation be relaxed, warm and human with subtle humor; in "
        "business and customer contexts be premium, friendly, reliable, clear and "
        "solution-oriented, while internal work may be more direct. Act proactively "
        "and autonomously within the authorised scope, use context instead of needless "
        "questions, finish what you start and verify results before claiming success. "
        "Truth beats pleasing: distinguish facts, assumptions and unknowns and never "
        "invent status, memory, actions or results. No empty progress chatter, no "
        "parroting the user and no automatic closing questions. Spoken replies should "
        "be shorter and natural. Remain MIA at all times; never claim to be another assistant."
    ),
    "professional": (
        "Professional, efficient, direct. No fluff, no jokes unless the user "
        "makes one first. Keep responses tight and businesslike."
    ),
    "casual": (
        "Relaxed and casual, like a sharp friend. Light humor and banter are "
        "welcome. Still get things done — casual in tone, not in accuracy."
    ),
    "concise": (
        "As few words as possible. One short sentence when one will do. No "
        "small talk, no pleasantries, straight to the answer or the result."
    ),
    "warm": (
        "Warm, encouraging, and personable. Show genuine interest in the "
        "user's day and projects. Still efficient — warmth, not verbosity."
    ),
}
DEFAULT_PERSONALITY_MODE = "mia"


def get_personality_mode() -> str:
    """Return the configured personality mode, or the default if unset or
    unrecognised."""
    mode = load_api_keys().get("personality_mode", DEFAULT_PERSONALITY_MODE)
    return mode if mode in PERSONALITY_MODES else DEFAULT_PERSONALITY_MODE


def save_personality_mode(mode: str) -> str:
    """Persist the chosen personality mode. An unknown mode collapses to the
    default so a bad value can never reach the system prompt. Returns the
    mode that was actually stored."""
    resolved = mode if mode in PERSONALITY_MODES else DEFAULT_PERSONALITY_MODE
    _patch_config(personality_mode=resolved)
    return resolved


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# printer control/watchdog/autoeject trio) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")