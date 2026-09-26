"""Tests für actions/open_app.py — App-Alias-Auflösung + plattformspezifische Launcher."""
import subprocess
import sys
import types

import pytest

from actions import open_app as oa


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)


def _proc(returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def _install_fake_pyautogui(monkeypatch, raise_on_call=False):
    fake = types.ModuleType("pyautogui")
    calls = []
    if raise_on_call:
        def boom(*a, **k):
            raise RuntimeError("kein Display")
        fake.press = boom
        fake.write = boom
        fake.hotkey = boom
    else:
        fake.press = lambda *a, **k: calls.append(("press", a))
        fake.write = lambda *a, **k: calls.append(("write", a))
        fake.hotkey = lambda *a, **k: calls.append(("hotkey", a))
    fake.PAUSE = 0.0
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return calls


# ── _normalize ───────────────────────────────────────────────────────────────
def test_normalize_exact_alias_match_uses_current_os_mapping():
    assert oa._normalize("chrome") == oa._APP_ALIASES["chrome"][oa._SYSTEM]


def test_normalize_is_case_and_whitespace_insensitive():
    assert oa._normalize("  ChRoMe  ") == oa._APP_ALIASES["chrome"][oa._SYSTEM]


def test_normalize_matches_via_substring_fallback():
    assert oa._normalize("chrome browser") == oa._APP_ALIASES["chrome"][oa._SYSTEM]


def test_normalize_returns_raw_unchanged_when_nothing_matches():
    assert oa._normalize("TotallyUnknownApp123") == "TotallyUnknownApp123"


# ── _launch_windows ────────────────────────────────────────────────────────────
def test_launch_windows_uses_popen_when_binary_is_on_path(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: "/usr/bin/x")
    calls = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: calls.append(a))
    assert oa._launch_windows("chrome") is True
    assert calls


def test_launch_windows_uses_start_command_for_uri_schemes(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    seen = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda cmd, **k: seen.append(cmd))
    assert oa._launch_windows("ms-settings:") is True
    assert seen == ["start ms-settings:"]


def test_launch_windows_falls_back_to_start_menu_search(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    _install_fake_pyautogui(monkeypatch)
    assert oa._launch_windows("SomeApp") is True


def test_launch_windows_returns_false_when_nothing_works(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    monkeypatch.delitem(sys.modules, "pyautogui", raising=False)
    assert oa._launch_windows("SomeApp") is False


# ── _launch_macos ──────────────────────────────────────────────────────────────
def test_launch_macos_succeeds_via_open_dash_a(monkeypatch):
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: _proc(0))
    assert oa._launch_macos("Safari") is True


def test_launch_macos_tries_dot_app_suffix_when_first_attempt_fails(monkeypatch):
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        return _proc(0 if cmd[-1].endswith(".app") else 1)

    monkeypatch.setattr(oa.subprocess, "run", fake_run)
    assert oa._launch_macos("Safari") is True
    assert calls[-1][-1] == "Safari.app"


def test_launch_macos_falls_back_to_binary_on_path(monkeypatch):
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: _proc(1))
    monkeypatch.setattr(oa.shutil, "which", lambda name: "/usr/local/bin/safari")
    popen_calls = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    assert oa._launch_macos("Safari") is True
    assert popen_calls


def test_launch_macos_falls_back_to_spotlight(monkeypatch):
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: _proc(1))
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    _install_fake_pyautogui(monkeypatch)
    assert oa._launch_macos("Safari") is True


def test_launch_macos_returns_false_when_everything_fails(monkeypatch):
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: _proc(1))
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    monkeypatch.delitem(sys.modules, "pyautogui", raising=False)
    assert oa._launch_macos("Safari") is False


# ── _launch_linux ──────────────────────────────────────────────────────────────
def test_launch_linux_tries_known_terminal_emulators(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: "/usr/bin/xterm" if name == "xterm" else None)
    popen_calls = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    assert oa._launch_linux("x-terminal-emulator") is True
    assert popen_calls


def test_launch_linux_uses_binary_on_path(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: "/usr/bin/blender" if name == "blender" else None)
    popen_calls = []
    monkeypatch.setattr(oa.subprocess, "Popen", lambda *a, **k: popen_calls.append(a))
    assert oa._launch_linux("blender") is True
    assert popen_calls


def test_launch_linux_falls_back_to_xdg_open(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)
    monkeypatch.setattr(oa.subprocess, "run", lambda *a, **k: _proc(0))
    assert oa._launch_linux("SomeApp") is True


def test_launch_linux_falls_back_to_gtk_launch_when_xdg_open_raises(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)

    def fake_run(cmd, **k):
        if cmd[0] == "xdg-open":
            raise RuntimeError("kein xdg-open")
        return _proc(0)

    monkeypatch.setattr(oa.subprocess, "run", fake_run)
    assert oa._launch_linux("SomeApp") is True


def test_launch_linux_returns_false_when_everything_fails(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda name: None)

    def fake_run(cmd, **k):
        raise RuntimeError("nichts installiert")

    monkeypatch.setattr(oa.subprocess, "run", fake_run)
    assert oa._launch_linux("SomeApp") is False


# ── open_app(): Dispatcher ─────────────────────────────────────────────────────
class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


def test_open_app_requires_app_name():
    assert oa.open_app({}) == "No application name provided."


def test_open_app_reports_unsupported_os(monkeypatch):
    monkeypatch.setattr(oa, "_SYSTEM", "BeOS")
    assert "Unsupported operating system: BeOS" in oa.open_app({"app_name": "chrome"})


def test_open_app_success_logs_and_confirms(monkeypatch):
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa._SYSTEM: lambda name: True})
    player = FakePlayer()
    result = oa.open_app({"app_name": "Chrome"}, player=player)
    assert result == "Opened Chrome."
    assert any("Chrome" in l for l in player.logs)


def test_open_app_retries_with_original_name_if_normalized_fails(monkeypatch):
    calls = []

    def fake_launcher(name):
        calls.append(name)
        return name == "MyWeirdApp"

    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa._SYSTEM: fake_launcher})
    result = oa.open_app({"app_name": "MyWeirdApp"})
    assert result == "Opened MyWeirdApp."


def test_open_app_reports_uncertain_result_when_launcher_fails_both_times(monkeypatch):
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa._SYSTEM: lambda name: False})
    result = oa.open_app({"app_name": "Chrome"})
    assert "Could not confirm" in result


def test_open_app_handles_launcher_exception(monkeypatch):
    def boom(name):
        raise RuntimeError("Absturz")

    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa._SYSTEM: boom})
    result = oa.open_app({"app_name": "Chrome"})
    assert "Failed to open Chrome" in result and "Absturz" in result


def test_tool_declaration_shape():
    assert oa.TOOL["name"] == "open_app"
    assert oa.TOOL["handler"] is oa.open_app
