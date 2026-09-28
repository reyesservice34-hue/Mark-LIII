"""Tests für core/audio_devices.py — Geräteauswahl, Filterung und Auflösung (sounddevice gemockt)."""
import platform
import sys
import types

import pytest

from core import audio_devices as ad


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    monkeypatch.setattr(ad, "_cache", None)
    monkeypatch.setattr(ad, "_chosen_api", {"input": None, "output": None})
    monkeypatch.setattr(ad, "_probe_results", {})
    monkeypatch.setattr(ad, "_RATES", {"input": 16000, "output": 24000})


def _install_fake_sounddevice(monkeypatch, **overrides):
    fake = types.SimpleNamespace(**overrides)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


# ── _is_pseudo ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["Microsoft Sound Mapper", "sysdefault", "default", "dmix:CARD=0"])
def test_is_pseudo_matches_known_aliases(name):
    assert ad._is_pseudo(name) is True


def test_is_pseudo_false_for_real_device_name():
    assert ad._is_pseudo("Realtek HD Audio") is False


# ── _display_name ─────────────────────────────────────────────────────────────
def test_display_name_unchanged_when_short():
    assert ad._display_name("Mic", []) == "Mic"


def test_display_name_prefers_longer_matching_name():
    truncated = "Realtek HD Audio 2nd output (Re"
    devices = [{"name": "Realtek HD Audio 2nd output (Realtek(R) Audio)"}]
    assert ad._display_name(truncated, devices) == "Realtek HD Audio 2nd output (Realtek(R) Audio)"


def test_display_name_falls_back_when_no_longer_match():
    truncated = "Some Very Long Device Name Here"
    assert ad._display_name(truncated, [{"name": "unrelated"}]) == truncated


# ── configure ────────────────────────────────────────────────────────────────
def test_configure_updates_rates_and_clears_cache():
    ad._cache = {"input": ["x"], "output": ["y"]}
    ad.configure(48000, 44100)
    assert ad._RATES == {"input": 48000, "output": 44100}
    assert ad._cache is None


# ── _transport_works ─────────────────────────────────────────────────────────
def test_transport_works_output_success(monkeypatch):
    class FakeStream:
        def start(self): pass
        def write(self, data): pass
        def stop(self): pass
        def close(self): pass

    clock = {"t": 0.0}
    monkeypatch.setattr(ad.time, "monotonic", lambda: clock["t"])

    def fake_write(data):
        clock["t"] += 1.0  # simulate real-time-limited writing

    stream = FakeStream()
    stream.write = fake_write
    _install_fake_sounddevice(monkeypatch, RawOutputStream=lambda **k: stream)
    assert ad._transport_works(0, "output", ("test", "output")) is True


def test_transport_works_output_fake_sink_is_rejected(monkeypatch):
    class FakeStream:
        def start(self): pass
        def write(self, data): pass  # instant, no time passes
        def stop(self): pass
        def close(self): pass

    monkeypatch.setattr(ad.time, "monotonic", lambda: 0.0)
    _install_fake_sounddevice(monkeypatch, RawOutputStream=lambda **k: FakeStream())
    assert ad._transport_works(0, "output", ("fake", "output")) is False


def test_transport_works_input_counts_delivered_frames(monkeypatch):
    class FakeStream:
        def __init__(self, callback):
            self._cb = callback
        def start(self):
            self._cb(None, 16000, None)  # deliver a full second of frames at once
        def stop(self): pass
        def close(self): pass

    monkeypatch.setattr(ad.time, "sleep", lambda s: None)
    _install_fake_sounddevice(monkeypatch, InputStream=lambda samplerate, channels, dtype, blocksize, device, callback: FakeStream(callback))
    assert ad._transport_works(0, "input", ("test", "input")) is True


def test_transport_works_caches_result(monkeypatch):
    ad._probe_results[("cached", "output")] = True
    calls = []
    _install_fake_sounddevice(monkeypatch, RawOutputStream=lambda **k: calls.append(1))
    assert ad._transport_works(0, "output", ("cached", "output")) is True
    assert calls == []  # never actually probed — served from cache


def test_transport_works_false_on_exception(monkeypatch):
    def boom(**k):
        raise RuntimeError("kein Gerät")

    _install_fake_sounddevice(monkeypatch, RawOutputStream=boom)
    assert ad._transport_works(0, "output", ("boom", "output")) is False


# ── _usable ──────────────────────────────────────────────────────────────────
def test_usable_true_when_stream_opens(monkeypatch):
    closed = []

    class FakeStream:
        def start(self): pass
        def stop(self): closed.append("stop")
        def close(self): closed.append("close")

    _install_fake_sounddevice(monkeypatch, InputStream=lambda **k: FakeStream())
    assert ad._usable(0, "input") is True
    assert closed == ["stop", "close"]


def test_usable_false_when_stream_raises(monkeypatch):
    def boom(**k):
        raise RuntimeError("Format nicht unterstützt")

    _install_fake_sounddevice(monkeypatch, RawOutputStream=boom)
    assert ad._usable(0, "output") is False


# ── _query ───────────────────────────────────────────────────────────────────
def _devices(*entries):
    """entries: (name, hostapi_idx, in_channels, out_channels)"""
    return [
        {"name": n, "hostapi": h, "max_input_channels": ic, "max_output_channels": oc}
        for n, h, ic, oc in entries
    ]


def test_query_filters_pseudo_and_zero_channel_devices_and_dedupes(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    devices = _devices(
        ("default", 0, 2, 2),
        ("Built-in Mic", 0, 2, 0),
        ("Built-in Mic", 0, 2, 0),  # duplicate name
        ("HDMI Out", 0, 0, 2),      # output only, 0 input channels
    )
    _install_fake_sounddevice(monkeypatch, query_devices=lambda: devices, query_hostapis=lambda: [{"name": "pulse"}])
    monkeypatch.setattr(ad, "_usable", lambda idx, kind: True)
    monkeypatch.setattr(ad, "_transport_works", lambda idx, kind, key: True)

    result = ad._query()
    assert result["input"] == ["Built-in Mic"]
    assert result["output"] == ["HDMI Out"]


def test_query_skips_devices_that_are_not_usable_at_configured_rate(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    devices = _devices(("Broken Mic", 0, 2, 0))
    _install_fake_sounddevice(monkeypatch, query_devices=lambda: devices, query_hostapis=lambda: [{"name": "pulse"}])
    monkeypatch.setattr(ad, "_usable", lambda idx, kind: False)
    monkeypatch.setattr(ad, "_transport_works", lambda idx, kind, key: True)
    result = ad._query()
    assert result["input"] == []


def test_query_returns_empty_lists_on_exception(monkeypatch):
    def boom():
        raise RuntimeError("kein audio subsystem")

    _install_fake_sounddevice(monkeypatch, query_devices=boom)
    result = ad._query()
    assert result == {"input": [], "output": []}


# ── list_devices (Cache-Verhalten) ────────────────────────────────────────────
def test_list_devices_queries_once_and_serves_from_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(ad, "_query", lambda: calls.append(1) or {"input": ["Mic"], "output": []})
    assert ad.list_devices("input") == ["Mic"]
    assert ad.list_devices("input") == ["Mic"]
    assert len(calls) == 1


def test_list_devices_refresh_forces_requery(monkeypatch):
    calls = []
    monkeypatch.setattr(ad, "_query", lambda: calls.append(1) or {"input": ["Mic"], "output": []})
    ad.list_devices("input")
    ad.list_devices("input", refresh=True)
    assert len(calls) == 2


# ── resolve ──────────────────────────────────────────────────────────────────
def test_resolve_returns_none_for_default_label_or_empty():
    assert ad.resolve("", "input") is None
    assert ad.resolve(ad.DEFAULT_LABEL, "input") is None


def test_resolve_finds_exact_name_match(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    devices = _devices(("USB Mic", 0, 2, 0), ("Other Mic", 0, 2, 0))
    _install_fake_sounddevice(monkeypatch, query_devices=lambda: devices, query_hostapis=lambda: [{"name": "pulse"}])
    monkeypatch.setattr(ad, "_usable", lambda idx, kind: True)
    monkeypatch.setattr(ad, "list_devices", lambda kind: None)
    assert ad.resolve("USB Mic", "input") == 0


def test_resolve_falls_back_to_prefix_match(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    devices = _devices(("Realtek HD Audio 2nd output (Re", 0, 0, 2),)
    _install_fake_sounddevice(monkeypatch, query_devices=lambda: devices, query_hostapis=lambda: [{"name": "pulse"}])
    monkeypatch.setattr(ad, "_usable", lambda idx, kind: True)
    monkeypatch.setattr(ad, "list_devices", lambda kind: None)
    wanted = "Realtek HD Audio 2nd output (Realtek(R) Audio)"
    assert ad.resolve(wanted, "output") == 0


def test_resolve_returns_none_when_device_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    devices = _devices(("Gone Device", 0, 2, 0),)
    _install_fake_sounddevice(monkeypatch, query_devices=lambda: devices, query_hostapis=lambda: [{"name": "pulse"}])
    monkeypatch.setattr(ad, "_usable", lambda idx, kind: False)
    monkeypatch.setattr(ad, "list_devices", lambda kind: None)
    assert ad.resolve("Gone Device", "input") is None


def test_resolve_returns_none_on_exception(monkeypatch):
    def boom():
        raise RuntimeError("x")

    _install_fake_sounddevice(monkeypatch, query_devices=boom)
    monkeypatch.setattr(ad, "list_devices", lambda kind: None)
    assert ad.resolve("anything", "input") is None
