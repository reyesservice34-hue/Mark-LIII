"""Tests für memory/config_manager.py (Lese/Schreiben von config/api_keys.json)."""
import json

import pytest

from memory import config_manager as cm


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    yield


def _read_raw() -> dict:
    return json.loads(cm.CONFIG_FILE.read_text(encoding="utf-8"))


# ── Grundlegendes Lesen/Schreiben ─────────────────────────────────────────
def test_config_does_not_exist_until_something_is_saved():
    assert cm.config_exists() is False
    cm.save_api_keys("some-long-gemini-key")
    assert cm.config_exists() is True


def test_save_and_load_api_keys_strips_whitespace():
    cm.save_api_keys("  my-key-1234567890  ")
    assert cm.load_api_keys() == {"gemini_api_key": "my-key-1234567890"}


def test_save_api_keys_preserves_other_existing_fields():
    cm.save_assistant_config("Mia", "Christoph")
    cm.save_api_keys("a-real-looking-key")
    data = cm.load_api_keys()
    assert data["gemini_api_key"] == "a-real-looking-key"
    assert data["assistant_name"] == "Mia"
    assert data["user_name"] == "Christoph"


def test_load_api_keys_returns_empty_dict_when_file_missing():
    assert cm.load_api_keys() == {}


def test_load_api_keys_recovers_from_corrupt_json():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text("{ kaputt", encoding="utf-8")
    assert cm.load_api_keys() == {}


def test_save_api_keys_recovers_from_corrupt_json():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text("{ kaputt", encoding="utf-8")
    cm.save_api_keys("neuer-schluessel")
    assert cm.load_api_keys() == {"gemini_api_key": "neuer-schluessel"}


def test_get_gemini_key_is_none_when_unset():
    assert cm.get_gemini_key() is None


@pytest.mark.parametrize("key,expected", [
    ("", False), ("short", False), ("x" * 15, False), ("x" * 16, True), ("x" * 40, True),
])
def test_is_configured_requires_key_longer_than_15_chars(key, expected):
    cm.save_api_keys(key)
    assert cm.is_configured() is expected


# ── Assistant-Identität ────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", [None, "", "   ", "jarvis", "JARVIS", "J.A.R.V.I.S", "j.a.r.v.i.s."])
def test_normalize_assistant_name_collapses_empty_and_legacy_to_mia(raw):
    assert cm.normalize_assistant_name(raw) == "MIA"


def test_normalize_assistant_name_keeps_a_real_name():
    assert cm.normalize_assistant_name("  Mia  ") == "Mia"


def test_migrate_assistant_identity_false_when_no_config_file():
    assert cm.migrate_assistant_identity() is False


def test_migrate_assistant_identity_false_when_no_assistant_name_field():
    cm.save_api_keys("k" * 20)
    assert cm.migrate_assistant_identity() is False


def test_migrate_assistant_identity_rewrites_legacy_name_and_keeps_other_fields():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text(json.dumps({"assistant_name": "Jarvis", "gemini_api_key": "abc"}), encoding="utf-8")
    changed = cm.migrate_assistant_identity()
    assert changed is True
    data = _read_raw()
    assert data["assistant_name"] == "MIA"
    assert data["gemini_api_key"] == "abc"
    backup = cm.CONFIG_FILE.with_name(cm.CONFIG_FILE.name + ".pre-mia.bak")
    assert json.loads(backup.read_text(encoding="utf-8"))["assistant_name"] == "Jarvis"


def test_migrate_assistant_identity_does_not_overwrite_existing_backup():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text(json.dumps({"assistant_name": "Jarvis"}), encoding="utf-8")
    backup = cm.CONFIG_FILE.with_name(cm.CONFIG_FILE.name + ".pre-mia.bak")
    backup.write_text(json.dumps({"assistant_name": "original-backup"}), encoding="utf-8")
    cm.migrate_assistant_identity()
    assert json.loads(backup.read_text(encoding="utf-8"))["assistant_name"] == "original-backup"


def test_migrate_assistant_identity_false_when_already_fixed():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text(json.dumps({"assistant_name": "MIA"}), encoding="utf-8")
    assert cm.migrate_assistant_identity() is False


def test_migrate_assistant_identity_false_on_corrupt_json():
    cm.ensure_config_dir()
    cm.CONFIG_FILE.write_text("{ kaputt", encoding="utf-8")
    assert cm.migrate_assistant_identity() is False


def test_get_assistant_name_defaults_to_mia():
    assert cm.get_assistant_name() == "MIA"


def test_get_user_name_defaults_to_empty_string():
    assert cm.get_user_name() == ""


def test_save_assistant_config_round_trip_strips_and_normalizes():
    cm.save_assistant_config("  Jarvis  ", "  Christoph  ")
    assert cm.get_assistant_name() == "MIA"
    assert cm.get_user_name() == "Christoph"


# ── Stimme ────────────────────────────────────────────────────────────────
def test_get_voice_defaults_to_charon():
    assert cm.get_voice() == cm.DEFAULT_VOICE == "Charon"


def test_save_and_get_voice_round_trip():
    cm.save_voice("Puck")
    assert cm.get_voice() == "Puck"


def test_save_voice_with_unknown_name_falls_back_to_default():
    cm.save_voice("NichtExistent")
    assert cm.get_voice() == cm.DEFAULT_VOICE


def test_get_voice_falls_back_when_stored_value_is_not_recognised():
    cm._patch_config(voice_name="Nonsense")
    assert cm.get_voice() == cm.DEFAULT_VOICE


# ── Wake word / Morning brief ──────────────────────────────────────────────
def test_wake_word_enabled_defaults_false_and_round_trips():
    assert cm.get_wake_word_enabled() is False
    cm.save_wake_word_enabled(True)
    assert cm.get_wake_word_enabled() is True


# ── Personality mode ─────────────────────────────────────────────────────────
def test_personality_mode_defaults_to_professional():
    assert cm.get_personality_mode() == cm.DEFAULT_PERSONALITY_MODE == "professional"


@pytest.mark.parametrize("mode", ["casual", "concise", "warm", "professional"])
def test_save_and_get_personality_mode_round_trip(mode):
    cm.save_personality_mode(mode)
    assert cm.get_personality_mode() == mode


def test_save_personality_mode_with_unknown_value_falls_back_to_default():
    resolved = cm.save_personality_mode("sarcastic")
    assert resolved == cm.DEFAULT_PERSONALITY_MODE
    assert cm.get_personality_mode() == cm.DEFAULT_PERSONALITY_MODE


def test_get_personality_mode_falls_back_when_stored_value_is_not_recognised():
    cm._patch_config(personality_mode="nonsense")
    assert cm.get_personality_mode() == cm.DEFAULT_PERSONALITY_MODE


def test_all_personality_modes_have_non_empty_descriptions():
    for mode, description in cm.PERSONALITY_MODES.items():
        assert isinstance(mode, str) and mode
        assert isinstance(description, str) and description.strip()


def test_brief_enabled_defaults_true_and_round_trips():
    assert cm.get_brief_enabled() is True
    cm.save_brief_enabled(False)
    assert cm.get_brief_enabled() is False


# ── Audiogeräte ─────────────────────────────────────────────────────────────
def test_input_output_device_default_to_empty_string():
    assert cm.get_input_device() == ""
    assert cm.get_output_device() == ""


def test_save_input_output_device_round_trip_strips_whitespace():
    cm.save_input_device("  USB Mikrofon  ")
    cm.save_output_device("  Lautsprecher  ")
    assert cm.get_input_device() == "USB Mikrofon"
    assert cm.get_output_device() == "Lautsprecher"


# ── Plugins ──────────────────────────────────────────────────────────────────
def test_plugin_enabled_defaults_true_opt_out_model():
    assert cm.get_plugin_enabled("printer_watchdog") is True


def test_save_plugin_enabled_round_trip_and_does_not_affect_others():
    cm.save_plugin_enabled("plugin_a", False)
    cm.save_plugin_enabled("plugin_b", True)
    assert cm.get_plugin_enabled("plugin_a") is False
    assert cm.get_plugin_enabled("plugin_b") is True
    assert cm.get_plugin_enabled("plugin_c") is True


def test_plugin_config_defaults_to_empty_dict():
    assert cm.get_plugin_config("printer") == {}
    assert cm.get_plugin_setting("printer", "ip", default="1.2.3.4") == "1.2.3.4"


def test_save_plugin_config_merges_without_clobbering_other_keys_or_namespaces():
    cm.save_plugin_config("printer", {"ip": "192.168.1.5"})
    cm.save_plugin_config("printer", {"port": 9100})
    cm.save_plugin_config("other_ns", {"token": "xyz"})
    assert cm.get_plugin_config("printer") == {"ip": "192.168.1.5", "port": 9100}
    assert cm.get_plugin_config("other_ns") == {"token": "xyz"}
    assert cm.get_plugin_setting("printer", "ip") == "192.168.1.5"
    assert cm.get_plugin_setting("printer", "missing", default="x") == "x"
