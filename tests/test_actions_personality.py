"""Tests für actions/personality.py — Umschalten des Gesprächstons per Sprache."""
import pytest

from actions import personality as p
from memory import config_manager as cm


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "config" / "api_keys.json")
    yield


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


def test_set_personality_with_exact_mode_name_persists_and_confirms():
    result = p.set_personality({"mode": "casual"}, player=None)
    assert cm.get_personality_mode() == "casual"
    assert "casual" in result
    assert "Relaxed" in result


@pytest.mark.parametrize("raw,expected", [
    ("Professional", "professional"),
    ("formal", "professional"),
    ("business", "professional"),
    ("RELAXED", "casual"),
    ("funny", "casual"),
    ("humorous", "casual"),
    ("short", "concise"),
    ("brief", "concise"),
    ("minimal", "concise"),
    ("friendly", "warm"),
    ("caring", "warm"),
])
def test_set_personality_resolves_aliases(raw, expected):
    p.set_personality({"mode": raw})
    assert cm.get_personality_mode() == expected


def test_set_personality_unknown_mode_does_not_change_config_and_lists_known_modes():
    cm.save_personality_mode("warm")
    result = p.set_personality({"mode": "sarcastic"})
    assert cm.get_personality_mode() == "warm"
    assert "Unknown personality mode" in result
    for mode in cm.PERSONALITY_MODES:
        assert mode in result


def test_set_personality_blank_mode_is_rejected():
    result = p.set_personality({"mode": "   "})
    assert "Unknown personality mode" in result


def test_set_personality_logs_to_player_when_provided():
    player = FakePlayer()
    p.set_personality({"mode": "concise"}, player=player)
    assert any("concise" in log for log in player.logs)


def test_set_personality_result_instructs_immediate_tone_adoption():
    result = p.set_personality({"mode": "warm"})
    assert "next reply" in result


def test_tool_declaration_shape():
    assert p.TOOL["name"] == "set_personality"
    assert p.TOOL["handler"] is p.set_personality
    assert "mode" in p.TOOL["parameters"]["properties"]
    assert p.TOOL["parameters"]["required"] == ["mode"]
