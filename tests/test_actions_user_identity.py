"""Tests für actions/user_identity.py — Anrede/Spitzname per Sprache setzen."""
import pytest

from actions import user_identity as ui
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


def test_set_user_nickname_persists_and_strips_whitespace():
    result = ui.set_user_nickname({"nickname": "  Chef  "})
    assert cm.get_user_name() == "Chef"
    assert "Chef" in result


def test_set_user_nickname_blank_is_rejected_and_does_not_change_config():
    cm.save_user_name("Chef")
    result = ui.set_user_nickname({"nickname": "   "})
    assert cm.get_user_name() == "Chef"
    assert "empty" in result


def test_set_user_nickname_logs_to_player_when_provided():
    player = FakePlayer()
    ui.set_user_nickname({"nickname": "Boss"}, player=player)
    assert any("Boss" in log for log in player.logs)


def test_set_user_nickname_result_instructs_future_address():
    result = ui.set_user_nickname({"nickname": "Captain"})
    assert "Captain" in result
    assert "address the user" in result


def test_tool_declaration_shape():
    assert ui.TOOL["name"] == "set_user_nickname"
    assert ui.TOOL["handler"] is ui.set_user_nickname
    assert "nickname" in ui.TOOL["parameters"]["properties"]
    assert ui.TOOL["parameters"]["required"] == ["nickname"]
