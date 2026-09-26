"""Tests für actions/computer_control.py — Zufallsdaten, Sicherheitsprüfungen, Dispatch."""
import json
import re
import sys
import types

import pytest

from actions import computer_control as cc


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_CONFIG_PATH", tmp_path / "api_keys.json")
    monkeypatch.setattr(cc, "_MEMORY_PATH", tmp_path / "long_term.json")
    yield


def _configure(**kv):
    cc._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cc._CONFIG_PATH.write_text(json.dumps(kv), encoding="utf-8")


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


@pytest.fixture(autouse=True)
def pyautogui_available(monkeypatch):
    monkeypatch.setattr(cc, "_PYAUTOGUI", True)
    fake = types.SimpleNamespace(
        typewrite=lambda text, interval=0.03: None,
        click=lambda *a, **k: None,
        hotkey=lambda *a, **k: None,
        press=lambda key: None,
        scroll=lambda clicks: None,
        hscroll=lambda clicks: None,
        moveTo=lambda x, y, duration=0: None,
        dragTo=lambda x, y, duration=0, button="left": None,
        screenshot=lambda: types.SimpleNamespace(save=lambda path, format=None: None),
        size=lambda: (1920, 1080),
    )
    monkeypatch.setattr(cc, "pyautogui", fake, raising=False)
    return fake


# ── _random_data ─────────────────────────────────────────────────────────────
def test_random_data_first_and_last_name():
    assert cc._random_data("first_name") in cc._FIRST_NAMES
    assert cc._random_data("last_name") in cc._LAST_NAMES


def test_random_data_full_name_has_two_parts():
    parts = cc._random_data("name").split(" ")
    assert len(parts) == 2


def test_random_data_email_matches_pattern():
    email = cc._random_data("email")
    assert re.match(r"^[a-z]+\.[a-z]+\d+@[\w.]+$", email)


def test_random_data_username_matches_pattern():
    assert re.match(r"^[a-z]+\d{3,4}$", cc._random_data("username"))


def test_random_data_password_has_required_character_classes():
    pw = cc._random_data("password")
    assert len(pw) == 12
    assert any(c.isupper() for c in pw)
    assert any(c.isdigit() for c in pw)
    assert any(c in "!@#$%" for c in pw)


def test_random_data_phone_format():
    assert re.match(r"^\+1\d{9,10}$", cc._random_data("phone"))


def test_random_data_birthday_format():
    assert re.match(r"^\d{2}/\d{2}/(19|20)\d{2}$", cc._random_data("birthday"))


def test_random_data_zip_code_is_five_digits():
    assert re.match(r"^\d{5}$", cc._random_data("zip_code"))


def test_random_data_unknown_type_has_predictable_shape():
    result = cc._random_data("favorite_color")
    assert result.startswith("random_favorite_color_")


# ── _user_profile ────────────────────────────────────────────────────────────
def test_user_profile_empty_when_memory_file_missing():
    assert cc._user_profile() == {}


def test_user_profile_extracts_identity_values():
    cc._MEMORY_PATH.write_text(json.dumps({
        "identity": {"name": {"value": "Christoph", "updated": "2026-01-01"},
                     "city": {"value": "Berlin", "updated": "2026-01-01"}}
    }), encoding="utf-8")
    profile = cc._user_profile()
    assert profile == {"name": "Christoph", "city": "Berlin"}


def test_user_profile_returns_empty_on_corrupt_json():
    cc._MEMORY_PATH.write_text("{ kaputt", encoding="utf-8")
    assert cc._user_profile() == {}


# ── _safe_screenshot_path ─────────────────────────────────────────────────────
def test_safe_screenshot_path_defaults_to_desktop_fallback():
    path = cc._safe_screenshot_path(None)
    assert path == cc.Path.home() / "Desktop" / "mia_screenshot.png"


def test_safe_screenshot_path_allows_paths_inside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cc, "_SAFE_SCREENSHOT_ROOTS", (tmp_path,))
    requested = str(tmp_path / "shots" / "a.png")
    result = cc._safe_screenshot_path(requested)
    assert result == (tmp_path / "shots" / "a.png").resolve()
    assert (tmp_path / "shots").is_dir()


def test_safe_screenshot_path_rejects_paths_outside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cc, "_SAFE_SCREENSHOT_ROOTS", (tmp_path,))
    result = cc._safe_screenshot_path("/etc/passwd")
    assert result == tmp_path / "Desktop" / "mia_screenshot.png"


# ── _screen_find ─────────────────────────────────────────────────────────────
def test_screen_find_returns_none_without_api_key():
    assert cc._screen_find("the button") is None


def _install_fake_genai(monkeypatch, text):
    class FakePart:
        @staticmethod
        def from_bytes(data, mime_type):
            return {"data": data, "mime_type": mime_type}

    class FakeResponse:
        pass

    resp = FakeResponse()
    resp.text = text

    class FakeModels:
        def generate_content(self, model, contents):
            return resp

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = FakeClient
    genai_types_mod = types.ModuleType("google.genai.types")
    genai_types_mod.Part = FakePart
    genai_mod.types = genai_types_mod
    google_pkg.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types_mod)


def test_screen_find_parses_coordinates(monkeypatch):
    _configure(gemini_api_key="k")
    _install_fake_genai(monkeypatch, "512, 384")
    assert cc._screen_find("the button") == (512, 384)


def test_screen_find_returns_none_when_not_found(monkeypatch):
    _configure(gemini_api_key="k")
    _install_fake_genai(monkeypatch, "NOT_FOUND")
    assert cc._screen_find("the button") is None


# ── computer_control(): Dispatcher ───────────────────────────────────────────
def test_requires_an_action():
    assert cc.computer_control({}) == "No action specified for computer_control."


def test_unknown_action_reports_itself():
    assert cc.computer_control({"action": "levitate"}) == "Unknown action: 'levitate'"


def test_type_action_delegates_to_pyautogui(pyautogui_available):
    result = cc.computer_control({"action": "type", "text": "Hallo Welt"})
    assert "Typed: Hallo Welt" in result


def test_click_uses_given_coordinates():
    result = cc.computer_control({"action": "click", "x": 10, "y": 20})
    assert "Clicked (10, 20)" in result


def test_double_click_reports_double_click():
    result = cc.computer_control({"action": "double_click", "x": 1, "y": 2})
    assert "Double-clicked" in result


def test_hotkey_splits_plus_separated_keys(pyautogui_available):
    seen = []
    pyautogui_available.hotkey = lambda *keys: seen.append(keys)
    result = cc.computer_control({"action": "hotkey", "keys": "ctrl+shift+t"})
    assert seen == [("ctrl", "shift", "t")]
    assert "ctrl+shift+t" in result


def test_press_defaults_to_enter():
    result = cc.computer_control({"action": "press"})
    assert "Pressed: enter" in result


def test_scroll_uses_direction_and_amount():
    result = cc.computer_control({"action": "scroll", "direction": "up", "amount": 5})
    assert "Scrolled up ×5" in result


def test_wait_is_capped_at_30_seconds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: sleeps.append(s))
    result = cc.computer_control({"action": "wait", "seconds": 999})
    assert sleeps == [30.0]
    assert "Waited 30.0s" in result


def test_screen_find_action_reports_not_found(monkeypatch):
    monkeypatch.setattr(cc, "_screen_find", lambda desc: None)
    assert cc.computer_control({"action": "screen_find", "description": "x"}) == "NOT_FOUND"


def test_screen_find_action_reports_coordinates(monkeypatch):
    monkeypatch.setattr(cc, "_screen_find", lambda desc: (100, 200))
    assert cc.computer_control({"action": "screen_find", "description": "x"}) == "100,200"


def test_screen_click_clicks_when_found(monkeypatch):
    monkeypatch.setattr(cc, "_screen_find", lambda desc: (100, 200))
    clicked = []
    monkeypatch.setattr(cc, "_click", lambda x=None, y=None, **k: clicked.append((x, y)))
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    result = cc.computer_control({"action": "screen_click", "description": "Login-Button"})
    assert clicked == [(100, 200)]
    assert "Clicked 'Login-Button'" in result


def test_screen_click_reports_not_found(monkeypatch):
    monkeypatch.setattr(cc, "_screen_find", lambda desc: None)
    result = cc.computer_control({"action": "screen_click", "description": "x"})
    assert "Element not found" in result


def test_random_data_action_returns_generated_value():
    result = cc.computer_control({"action": "random_data", "type": "zip_code"})
    assert re.match(r"^\d{5}$", result)


def test_user_data_action_prefers_stored_memory():
    cc._MEMORY_PATH.write_text(json.dumps({"identity": {"city": {"value": "Hamburg"}}}), encoding="utf-8")
    result = cc.computer_control({"action": "user_data", "field": "city"})
    assert result == "Hamburg"


def test_user_data_action_falls_back_to_random_when_unset():
    result = cc.computer_control({"action": "user_data", "field": "zip_code"})
    assert re.match(r"^\d{5}$", result)


def test_dispatcher_catches_exceptions(monkeypatch):
    def boom(text):
        raise RuntimeError("Absturz")

    monkeypatch.setattr(cc, "_type", boom)
    result = cc.computer_control({"action": "type", "text": "x"})
    assert "computer_control 'type' failed" in result and "Absturz" in result


def test_dispatcher_logs_to_player():
    player = FakePlayer()
    cc.computer_control({"action": "press"}, player=player)
    assert any("press" in l for l in player.logs)


# ── _focus_window (Linux-Zweig auf diesem Sandbox-OS) ────────────────────────
def test_focus_window_linux_uses_wmctrl(monkeypatch):
    monkeypatch.setattr(cc, "_get_os", lambda: "linux")

    class Result:
        returncode = 0

    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: Result())
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    result = cc._focus_window("Firefox")
    assert "Focused window: Firefox" in result


def test_focus_window_linux_falls_back_to_xdotool_when_wmctrl_missing(monkeypatch):
    monkeypatch.setattr(cc, "_get_os", lambda: "linux")
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd[0])
        if cmd[0] == "wmctrl":
            raise FileNotFoundError()
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    monkeypatch.setattr(cc.time, "sleep", lambda s: None)
    result = cc._focus_window("Firefox")
    assert calls == ["wmctrl", "xdotool"]
    assert "Focused window: Firefox" in result


def test_focus_window_linux_reports_missing_tools(monkeypatch):
    monkeypatch.setattr(cc, "_get_os", lambda: "linux")

    def fake_run(cmd, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    result = cc._focus_window("Firefox")
    assert "requires wmctrl or xdotool" in result


def test_tool_declaration_shape():
    assert cc.TOOL["name"] == "computer_control"
    assert cc.TOOL["handler"] is cc.computer_control
