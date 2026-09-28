"""Tests für actions/system_monitor.py — Schwellwerte, Cooldown, Streak, Habituation."""
import types

import pytest

from actions import system_monitor as sysmon
from memory import memory_manager as mm


class FakeMem:
    def __init__(self, percent=10.0, used=4 * 1024**3, total=16 * 1024**3):
        self.percent = percent
        self.used = used
        self.total = total


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MEMORY_PATH", tmp_path / "long_term.json")
    yield


@pytest.fixture
def fake_clock(monkeypatch):
    # Start comfortably above _COOLDOWN so a monitor's initial "never alerted"
    # state (_last_alert defaults missing keys to 0) reads as available,
    # matching real time.monotonic() (which is never 0 in practice).
    clock = {"t": 100_000.0}
    monkeypatch.setattr(sysmon.time, "monotonic", lambda: clock["t"])
    return clock


# ── get_system_status ────────────────────────────────────────────────────────
def test_get_system_status_reports_rounded_metrics(monkeypatch):
    monkeypatch.setattr(sysmon.psutil, "cpu_percent", lambda interval=None: 42.34)
    monkeypatch.setattr(sysmon.psutil, "virtual_memory", lambda: FakeMem(percent=55.6))
    monkeypatch.setattr(sysmon.psutil, "boot_time", lambda: 1000.0)
    monkeypatch.setattr(sysmon.psutil, "pids", lambda: list(range(123)))
    monkeypatch.setattr(sysmon.time, "time", lambda: 1000.0 + 3661)  # +1h1m1s
    monkeypatch.setattr(sysmon, "_get_cpu_temp", lambda: 45.6)
    monkeypatch.setattr(sysmon, "_get_gpu_usage", lambda: 12.3)

    status = sysmon.get_system_status()
    assert status["cpu_percent"] == 42.3
    assert status["ram_percent"] == 55.6
    assert status["cpu_temp_c"] == 45.6
    assert status["gpu_percent"] == 12.3
    assert status["uptime"] == "1h 1m"
    assert status["process_count"] == 123


def test_get_system_status_reports_none_for_unavailable_temp_and_gpu(monkeypatch):
    monkeypatch.setattr(sysmon.psutil, "cpu_percent", lambda interval=None: 1.0)
    monkeypatch.setattr(sysmon.psutil, "virtual_memory", lambda: FakeMem())
    monkeypatch.setattr(sysmon.psutil, "boot_time", lambda: 0.0)
    monkeypatch.setattr(sysmon.psutil, "pids", lambda: [])
    monkeypatch.setattr(sysmon.time, "time", lambda: 0.0)
    monkeypatch.setattr(sysmon, "_get_cpu_temp", lambda: -1.0)
    monkeypatch.setattr(sysmon, "_get_gpu_usage", lambda: -1.0)

    status = sysmon.get_system_status()
    assert status["cpu_temp_c"] is None
    assert status["gpu_percent"] is None


# ── SystemMonitor.check(): Schwellwerte + Cooldown + Streak ──────────────────
def _mock_metrics(monkeypatch, cpu=1.0, ram=1.0, temp=-1.0, gpu=-1.0):
    monkeypatch.setattr(sysmon.psutil, "cpu_percent", lambda interval=None: cpu)
    monkeypatch.setattr(sysmon.psutil, "virtual_memory", lambda: FakeMem(percent=ram))
    monkeypatch.setattr(sysmon, "_get_cpu_temp", lambda: temp)
    monkeypatch.setattr(sysmon, "_get_gpu_usage", lambda: gpu)


def test_check_returns_none_when_all_metrics_are_normal(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=10)
    mon = sysmon.SystemMonitor()
    assert mon.check() is None


def test_check_swallows_exceptions_from_psutil(monkeypatch, fake_clock):
    def boom(interval=None):
        raise RuntimeError("psutil kaputt")
    monkeypatch.setattr(sysmon.psutil, "cpu_percent", boom)
    mon = sysmon.SystemMonitor()
    assert mon.check() is None


def test_ram_alert_fires_immediately_without_streak(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=95)
    mon = sysmon.SystemMonitor()
    result = mon.check()
    assert result and "RAM" in result and "SYSTEM_ALERT" in result


def test_cpu_alert_requires_three_consecutive_high_readings(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=95, ram=10)
    mon = sysmon.SystemMonitor()
    assert mon.check() is None
    assert mon.check() is None
    result = mon.check()
    assert result and "CPU" in result


def test_cpu_streak_resets_when_usage_drops_back_down(monkeypatch, fake_clock):
    mon = sysmon.SystemMonitor()
    _mock_metrics(monkeypatch, cpu=95, ram=10)
    mon.check()
    mon.check()
    _mock_metrics(monkeypatch, cpu=10, ram=10)
    mon.check()  # streak resets here
    _mock_metrics(monkeypatch, cpu=95, ram=10)
    assert mon.check() is None  # streak restarts at 1
    assert mon.check() is None  # 2


def test_temp_and_gpu_alerts_fire_when_over_threshold(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=10, temp=90, gpu=99)
    mon = sysmon.SystemMonitor()
    result = mon.check()
    assert "temperature" in result.lower()
    assert "GPU" in result


def test_alert_does_not_refire_within_cooldown(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=95)
    mon = sysmon.SystemMonitor()
    assert mon.check() is not None
    fake_clock["t"] += 60  # still well within the 300s cooldown
    assert mon.check() is None


def test_alert_refires_after_cooldown_expires(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=95)
    mon = sysmon.SystemMonitor()
    assert mon.check() is not None
    fake_clock["t"] += sysmon._COOLDOWN + 1
    assert mon.check() is not None


def test_custom_thresholds_override_defaults(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=50)
    mon = sysmon.SystemMonitor(thresholds={"ram": 40})
    assert mon.check() is not None


def test_repeated_alerts_switch_to_recurring_wording(monkeypatch, fake_clock):
    _mock_metrics(monkeypatch, cpu=10, ram=95)
    mon = sysmon.SystemMonitor()
    for _ in range(sysmon._RECUR_THRESHOLD - 1):
        assert mon.check() is not None
        fake_clock["t"] += sysmon._COOLDOWN + 1
    result = mon.check()
    assert "again" in result and "time in" in result


def test_tool_free_functions_do_not_require_a_tool_declaration():
    # system_monitor exposes get_system_status()/SystemMonitor for other code to
    # call directly; it is not itself an auto-discovered action.
    assert not hasattr(sysmon, "TOOL")
