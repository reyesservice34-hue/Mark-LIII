"""Tests für core/confirm.py — das nicht vom Modell fälschbare Bestätigungs-Gate."""
import threading
import time

import pytest

from core import confirm


class FakeClock:
    """Steuerbare Ersatzuhr für time.monotonic(), um Timeouts deterministisch zu testen."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture(autouse=True)
def reset_state():
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
    """Ein Fake-HUD, das show/hide/log-Aufrufe protokolliert."""
    calls = {"show": [], "hide": 0, "log": []}
    confirm.bind(
        show=lambda title, detail: calls["show"].append((title, detail)),
        hide=lambda: calls.__setitem__("hide", calls["hide"] + 1),
        log=lambda msg: calls["log"].append(msg),
    )
    return calls


@pytest.fixture
def worker_done(monkeypatch):
    """Signalisiert, sobald resolve()'s daemon-Worker-Thread durchgelaufen ist."""
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


# ── request() ohne gebundenes Interface ───────────────────────────────────
def test_request_without_bound_interface_refuses_and_does_not_run():
    ran = []
    msg = confirm.request("k", "Herunterfahren", "detail", lambda: ran.append(1))
    assert "interface" in msg.lower() and "not" in msg.lower()
    assert ran == []
    assert confirm.pending_title() == ""


# ── request() mit gebundenem Interface ────────────────────────────────────
def test_request_shows_banner_and_returns_pending_message(hud):
    msg = confirm.request("shutdown", "PC herunterfahren", "Details hier", lambda: "ok")
    assert hud["show"] == [("PC herunterfahren", "Details hier")]
    assert "[CONFIRMATION_PENDING]" in msg
    assert "PC herunterfahren" in msg
    assert confirm.pending_title() == "PC herunterfahren"
    assert any("Awaiting confirmation" in m for m in hud["log"])


def test_request_show_callback_failure_clears_pending_and_reports_error():
    def broken_show(title, detail):
        raise RuntimeError("HUD kaputt")

    confirm.bind(show=broken_show, hide=lambda: None, log=None)
    msg = confirm.request("k", "Titel", "Detail", lambda: "ok")
    assert "Could not ask for confirmation" in msg
    assert "HUD kaputt" in msg
    assert confirm.pending_title() == ""


def test_second_request_replaces_a_still_pending_one(hud):
    confirm.request("a", "Erste Aktion", "d1", lambda: "1")
    confirm.request("b", "Zweite Aktion", "d2", lambda: "2")
    assert confirm.pending_title() == "Zweite Aktion"


# ── resolve() ──────────────────────────────────────────────────────────────
def test_resolve_accepted_runs_callable_and_hides_banner(hud, worker_done):
    ran = []

    def run():
        ran.append(1)
        return "erledigt"

    confirm.request("k", "Titel", "Detail", run)
    confirm.resolve(True)
    assert worker_done.wait(2.0)
    assert ran == [1]
    assert hud["hide"] == 1
    assert any("Confirmed" in m and "erledigt" in m for m in hud["log"])
    assert confirm.pending_title() == ""


def test_resolve_rejected_does_not_run_callable(hud):
    ran = []
    confirm.request("k", "Titel", "Detail", lambda: ran.append(1))
    confirm.resolve(False)
    time.sleep(0.05)
    assert ran == []
    assert hud["hide"] == 1
    assert any("Cancelled" in m for m in hud["log"])


def test_resolve_with_nothing_pending_still_hides_and_is_a_noop(hud):
    confirm.resolve(True)
    assert hud["hide"] == 1
    assert hud["log"] == []


def test_resolve_after_timeout_does_not_run(hud, monkeypatch):
    clock = FakeClock(0.0)
    monkeypatch.setattr(confirm.time, "monotonic", clock)
    ran = []
    confirm.request("k", "Titel", "Detail", lambda: ran.append(1))
    clock.advance(confirm.TIMEOUT_SECONDS + 1)
    confirm.resolve(True)
    time.sleep(0.05)
    assert ran == []
    assert any("expired" in m.lower() for m in hud["log"])


def test_resolve_failure_in_run_is_logged_not_raised(hud, worker_done):
    def boom():
        raise ValueError("kaputt")

    confirm.request("k", "Titel", "Detail", boom)
    confirm.resolve(True)
    assert worker_done.wait(2.0)
    assert any("ERR" in m and "kaputt" in m for m in hud["log"])


def test_log_callback_exception_is_swallowed(hud):
    def broken_log(msg):
        raise RuntimeError("log kaputt")

    confirm.bind(show=lambda t, d: None, hide=lambda: None, log=broken_log)
    confirm.request("k", "Titel", "Detail", lambda: "ok")  # darf nicht werfen
    confirm.resolve(False)  # darf nicht werfen


# ── pending_title() ────────────────────────────────────────────────────────
def test_pending_title_empty_when_nothing_pending():
    assert confirm.pending_title() == ""


def test_pending_title_empty_after_timeout_without_resolving(hud, monkeypatch):
    clock = FakeClock(0.0)
    monkeypatch.setattr(confirm.time, "monotonic", clock)
    confirm.request("k", "Titel", "Detail", lambda: "ok")
    assert confirm.pending_title() == "Titel"
    clock.advance(confirm.TIMEOUT_SECONDS + 1)
    assert confirm.pending_title() == ""
