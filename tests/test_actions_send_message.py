"""Tests für actions/send_message.py — insbesondere das Bestätigungs-Gate (core/confirm)."""
import threading

import pytest

from actions import send_message as sm
from core import confirm


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


@pytest.fixture(autouse=True)
def pyautogui_available(monkeypatch):
    """The sandbox this suite runs in has no real pyautogui installed — assume
    it's present by default so the gate logic can be exercised; the one test
    that checks the "not installed" refusal overrides this explicitly."""
    monkeypatch.setattr(sm, "_PYAUTOGUI", True)


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


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── Eingabevalidierung ────────────────────────────────────────────────────────
def test_missing_receiver_is_rejected():
    result = sm.send_message({"message_text": "hi"})
    assert result == "Please specify a recipient."


def test_missing_message_is_rejected():
    result = sm.send_message({"receiver": "Ayse"})
    assert result == "Please specify the message content."


def test_without_pyautogui_refuses(monkeypatch):
    monkeypatch.setattr(sm, "_PYAUTOGUI", False)
    result = sm.send_message({"receiver": "Ayse", "message_text": "hi"})
    assert "PyAutoGUI is not installed" in result


# ── Das Bestätigungs-Gate ──────────────────────────────────────────────────────
def test_send_message_requests_confirmation_with_title_and_preview(hud):
    result = sm.send_message({"receiver": "Ayse", "message_text": "Hallo!", "platform": "telegram"})
    assert "[CONFIRMATION_PENDING]" in result
    title, detail = hud["show"][0]
    assert title == "Send message via Telegram"
    assert "Ayse" in detail and "Hallo!" in detail


def test_second_request_while_one_pending_is_refused(hud):
    sm.send_message({"receiver": "Ayse", "message_text": "eins"})
    result = sm.send_message({"receiver": "Ayse", "message_text": "zwei"})
    assert "already a confirmation waiting" in result
    assert len(hud["show"]) == 1  # kein zweites Banner


def test_confirmed_send_invokes_resolved_handler_and_logs(hud, worker_done, monkeypatch):
    calls = []

    def fake_handler(receiver, message):
        calls.append((receiver, message))
        return f"Message sent to {receiver} via Fake."

    monkeypatch.setattr(sm, "_resolve_platform", lambda platform: fake_handler)
    player = FakePlayer()
    sm.send_message({"receiver": "Ayse", "message_text": "Hallo!", "platform": "whatsapp"}, player=player)
    confirm.resolve(True)
    assert worker_done.wait(2.0)
    assert calls == [("Ayse", "Hallo!")]
    assert any("sent to Ayse via Fake" in l for l in player.logs)
    assert any("Confirmed" in l for l in hud["log"])


def test_confirmed_send_handles_handler_exception(hud, worker_done, monkeypatch):
    def boom(receiver, message):
        raise RuntimeError("WhatsApp abgestürzt")

    monkeypatch.setattr(sm, "_resolve_platform", lambda platform: boom)
    player = FakePlayer()
    sm.send_message({"receiver": "Ayse", "message_text": "Hallo!"}, player=player)
    confirm.resolve(True)
    assert worker_done.wait(2.0)
    assert any("Could not send message" in l and "WhatsApp abgestürzt" in l for l in player.logs)


def test_rejected_confirmation_never_calls_the_handler(hud, monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "_resolve_platform", lambda platform: (lambda r, m: calls.append((r, m))))
    sm.send_message({"receiver": "Ayse", "message_text": "Hallo!"})
    confirm.resolve(False)
    import time
    time.sleep(0.05)
    assert calls == []


# ── _resolve_platform ────────────────────────────────────────────────────────
@pytest.mark.parametrize("platform,expected", [
    ("whatsapp", "_send_whatsapp"), ("wp", "_send_whatsapp"), ("WhatsApp", "_send_whatsapp"),
    ("telegram", "_send_telegram"), ("tg", "_send_telegram"),
    ("instagram", "_send_instagram"), ("ig", "_send_instagram"), ("insta", "_send_instagram"),
    ("signal", "_send_signal"),
    ("discord", "_send_discord"),
    ("messenger", "_send_messenger"), ("facebook", "_send_messenger"), ("fb", "_send_messenger"),
])
def test_resolve_platform_maps_known_keywords(platform, expected):
    assert sm._resolve_platform(platform) is getattr(sm, expected)


def test_resolve_platform_falls_back_to_generic_desktop_send_for_unknown_platform(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "_desktop_send", lambda app, r, m: calls.append((app, r, m)) or "ok")
    handler = sm._resolve_platform("  slack  ")
    handler("Ayse", "Hallo")
    assert calls == [("Slack", "Ayse", "Hallo")]


def test_tool_declaration_shape():
    assert sm.TOOL["name"] == "send_message"
    assert sm.TOOL["handler"] is sm.send_message
    assert set(sm.TOOL["parameters"]["required"]) == {"receiver", "message_text", "platform"}
