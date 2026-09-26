"""Tests für actions/computer_settings.py — Intent-Auflösung, Dispatch, Confirm-/Undo-Gates."""
import threading

import pytest

from actions import computer_settings as cs
from core import confirm


@pytest.fixture(autouse=True)
def pyautogui_available(monkeypatch):
    monkeypatch.setattr(cs, "_PYAUTOGUI", True)


@pytest.fixture(autouse=True)
def reset_confirm():
    confirm._pending = None
    confirm._show_cb = None
    confirm._hide_cb = None
    confirm._log_cb = None
    yield
    confirm._pending = None
    confirm._show_cb = None
    confirm._hide_cb = None
    confirm._log_cb = None


@pytest.fixture
def hud():
    calls = {"show": [], "hide": 0, "log": []}
    confirm.bind(
        show=lambda title, detail: calls["show"].append((title, detail)),
        hide=lambda: calls.__setitem__("hide", calls["hide"] + 1),
        log=lambda msg: calls["log"].append(msg),
    )
    return calls


@pytest.fixture
def worker_done(monkeypatch):
    done = threading.Event()
    orig_thread = confirm.threading.Thread

    def tracking_thread(*args, **kwargs):
        t = orig_thread(*args, **kwargs)
        orig_run = t.run

        def wrapped():
            orig_run()
            done.set()

        t.run = wrapped
        return t

    monkeypatch.setattr(confirm.threading, "Thread", tracking_thread)
    return done


# ── _normalise ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("Volume Up", "volume_up"), ("volume-up", "volume_up"), ("  VOLUME_UP  ", "volume_up"), (None, ""), ("", ""),
])
def test_normalise(raw, expected):
    assert cs._normalise(raw) == expected


# ── _detect_action ───────────────────────────────────────────────────────────
def test_detect_action_exact_action_name():
    assert cs._detect_action("volume_up") == {"action": "volume_up", "value": None}


def test_detect_action_empty_description():
    assert cs._detect_action("") == {"action": "", "value": None}
    assert cs._detect_action(None) == {"action": "", "value": None}


def test_detect_action_volume_number_phrase():
    result = cs._detect_action("set volume to 30")
    assert result == {"action": "volume_set", "value": 30}


def test_detect_action_volume_number_is_clamped():
    result = cs._detect_action("set sound to 500")
    assert result["value"] == 100


def test_detect_action_alias_phrase_matches():
    assert cs._detect_action("turn it up")["action"] == "volume_up"
    assert cs._detect_action("make it brighter")["action"] == "brightness_up"


def test_detect_action_fuzzy_typo_match():
    result = cs._detect_action("voluume_up")
    assert result["action"] == "volume_up"


def test_detect_action_substring_fallback():
    result = cs._detect_action("computer_shutdown_now")
    assert result["action"] == "shutdown"


def test_detect_action_no_match_returns_empty():
    assert cs._detect_action("qzx totally unrelated gibberish") == {"action": "", "value": None}


# ── _suggest ─────────────────────────────────────────────────────────────────
def test_suggest_names_close_matches():
    msg = cs._suggest("volume")
    assert "volume" in msg.lower()


def test_suggest_falls_back_to_generic_list_when_nothing_close():
    msg = cs._suggest("zzzzzzzzzzz")
    assert "Call computer_settings again" in msg


# ── computer_settings(): Grundvalidierung ────────────────────────────────────
def test_reports_missing_pyautogui(monkeypatch):
    monkeypatch.setattr(cs, "_PYAUTOGUI", False)
    assert "pyautogui is not installed" in cs.computer_settings({"action": "volume_up"})


def test_empty_action_and_description_suggests():
    result = cs.computer_settings({})
    assert "Call computer_settings again" in result


def test_description_is_resolved_locally_without_explicit_action(monkeypatch):
    monkeypatch.setitem(cs.ACTION_MAP, "volume_up", lambda: None)
    result = cs.computer_settings({"description": "turn it up"})
    assert result == "Done: volume_up."


def test_unknown_explicit_action_suggests():
    result = cs.computer_settings({"action": "definitely_not_a_real_action"})
    assert "Call computer_settings again" in result


# ── Irreversible actions: Bestätigungs-Gate ───────────────────────────────────
def test_irreversible_action_requests_confirmation(hud):
    result = cs.computer_settings({"action": "shutdown"})
    assert "[CONFIRMATION_PENDING]" in result
    title, detail = hud["show"][0]
    assert title == "Shut this computer down"


def test_irreversible_action_refuses_second_request_while_pending(hud):
    cs.computer_settings({"action": "shutdown"})
    result = cs.computer_settings({"action": "restart"})
    assert "already a confirmation waiting" in result


def test_irreversible_action_runs_mapped_function_on_confirm(hud, worker_done, monkeypatch):
    calls = []
    monkeypatch.setitem(cs.ACTION_MAP, "toggle_wifi", lambda: calls.append(1))
    cs.computer_settings({"action": "toggle_wifi"})
    confirm.resolve(True)
    assert worker_done.wait(2.0)
    assert calls == [1]
    assert any("Confirmed" in l and "toggle_wifi done." in l for l in hud["log"])


# ── volume_set ───────────────────────────────────────────────────────────────
def test_volume_set_defaults_to_50_and_pushes_undo(monkeypatch):
    monkeypatch.setattr(cs, "volume_get", lambda: 20)
    set_calls = []
    monkeypatch.setattr(cs, "volume_set", lambda v: set_calls.append(v))
    undo_calls = []
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_calls.append((label, fn)))

    result = cs.computer_settings({"action": "volume_set"})
    assert result == "Volume set to 50%."
    assert set_calls == [50]
    assert undo_calls and "20% → 50%" in undo_calls[0][0]


def test_volume_set_skips_undo_when_before_value_unreadable(monkeypatch):
    monkeypatch.setattr(cs, "volume_get", lambda: None)
    monkeypatch.setattr(cs, "volume_set", lambda v: None)
    undo_calls = []
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_calls.append(1))
    cs.computer_settings({"action": "volume_set", "value": "70"})
    assert undo_calls == []


def test_volume_set_undo_restores_previous_value(monkeypatch):
    monkeypatch.setattr(cs, "volume_get", lambda: 20)
    set_calls = []
    monkeypatch.setattr(cs, "volume_set", lambda v: set_calls.append(v))
    undo_fn = {}
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_fn.setdefault("fn", fn))
    cs.computer_settings({"action": "volume_set", "value": "80"})
    assert set_calls == [80]
    result = undo_fn["fn"]()
    assert set_calls == [80, 20]
    assert result == "Back to 20%."


def test_volume_set_handles_exception(monkeypatch):
    monkeypatch.setattr(cs, "volume_get", lambda: 20)

    def boom(v):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(cs, "volume_set", boom)
    result = cs.computer_settings({"action": "volume_set", "value": "10"})
    assert "Could not set volume" in result


# ── type_text / press_key / reload_n / scroll ─────────────────────────────────
def test_type_text_requires_nonempty_text():
    assert cs.computer_settings({"action": "type_text"}) == "No text provided to type."


def test_type_text_types_and_reports(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "type_text", lambda text, press_enter_after=False: calls.append((text, press_enter_after)))
    result = cs.computer_settings({"action": "write", "value": "Hallo Welt", "press_enter": "true"})
    assert result == "Typed: Hallo Welt"
    assert calls == [("Hallo Welt", True)]


def test_press_key_requires_a_key():
    assert cs.computer_settings({"action": "press_key"}) == "No key specified."


def test_press_key_presses_and_reports(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "press_key", lambda key: calls.append(key))
    result = cs.computer_settings({"action": "press_key", "value": "F5"})
    assert result == "Pressed: F5"
    assert calls == ["F5"]


def test_reload_n_defaults_to_one(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "reload_page_n", lambda n: calls.append(n))
    result = cs.computer_settings({"action": "reload_n"})
    assert result == "Reloaded 1 time(s)."
    assert calls == [1]


def test_reload_n_handles_exception(monkeypatch):
    def boom(n):
        raise RuntimeError("x")

    monkeypatch.setattr(cs, "reload_page_n", boom)
    result = cs.computer_settings({"action": "reload_n", "value": "3"})
    assert "Reload failed" in result


def test_scroll_up_uses_default_amount(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "scroll_up", lambda amount: calls.append(amount))
    result = cs.computer_settings({"action": "scroll_up"})
    assert result == "Scrolled up."
    assert calls == [500]


def test_scroll_down_uses_provided_amount(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "scroll_down", lambda amount: calls.append(amount))
    cs.computer_settings({"action": "scroll_down", "value": "200"})
    assert calls == [200]


# ── Generischer ACTION_MAP-Dispatch + Undo ───────────────────────────────────
def test_generic_action_runs_and_reports_done(monkeypatch):
    calls = []
    monkeypatch.setitem(cs.ACTION_MAP, "close_window", lambda: calls.append(1))
    result = cs.computer_settings({"action": "close_window"})
    assert result == "Done: close_window."
    assert calls == [1]


def test_generic_action_failure_is_reported(monkeypatch):
    def boom():
        raise RuntimeError("Absturz")

    monkeypatch.setitem(cs.ACTION_MAP, "close_window", boom)
    result = cs.computer_settings({"action": "close_window"})
    assert "Action failed (close_window)" in result and "Absturz" in result


def test_volume_up_pushes_undo_with_before_value(monkeypatch):
    monkeypatch.setitem(cs.ACTION_MAP, "volume_up", lambda: None)
    monkeypatch.setattr(cs, "volume_get", lambda: 33)
    undo_calls = []
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_calls.append(label))
    cs.computer_settings({"action": "volume_up"})
    assert undo_calls and "volume_up" in undo_calls[0]


def test_brightness_down_pushes_undo_with_before_value(monkeypatch):
    monkeypatch.setitem(cs.ACTION_MAP, "brightness_down", lambda: None)
    monkeypatch.setattr(cs, "brightness_get", lambda: 60)
    undo_calls = []
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_calls.append(label))
    cs.computer_settings({"action": "brightness_down"})
    assert undo_calls and "brightness_down" in undo_calls[0]


def test_dark_mode_pushes_a_toggle_back_undo(monkeypatch):
    monkeypatch.setitem(cs.ACTION_MAP, "dark_mode", lambda: None)
    undo_calls = []
    monkeypatch.setattr(cs, "push_undo", lambda label, fn: undo_calls.append((label, fn)))
    cs.computer_settings({"action": "dark_mode"})
    assert undo_calls and undo_calls[0][0] == "dark mode toggled"


def test_tool_declaration_shape():
    assert cs.TOOL["name"] == "computer_settings"
    assert cs.TOOL["handler"] is cs.computer_settings
