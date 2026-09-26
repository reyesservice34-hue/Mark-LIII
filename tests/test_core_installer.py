"""Tests für core/installer.py — Abhängigkeits-Installer (subprocess/pip gemockt)."""
import subprocess

import pytest

from core import installer


def _proc(returncode=0, stderr=b"", stdout=b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ── _available ───────────────────────────────────────────────────────────────
def test_available_true_for_a_real_stdlib_module():
    assert installer._available("os") is True


def test_available_false_for_an_unknown_module():
    assert installer._available("this_module_does_not_exist_xyz") is False


# ── _pip ─────────────────────────────────────────────────────────────────────
def test_pip_success_returns_true_without_logging_an_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(0))
    logs = []
    assert installer._pip("somepkg", log=logs.append) is True
    assert not any(l.startswith("ERR:") for l in logs)


def test_pip_failure_returns_false_and_logs_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(1, stderr=b"kein netz"))
    logs = []
    assert installer._pip("somepkg", log=logs.append) is False
    assert any("somepkg" in l and "kein netz" in l for l in logs)


def test_pip_without_log_callback_does_not_raise(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(1, stderr=b"x"))
    assert installer._pip("somepkg", log=None) is False


# ── install_for_config ────────────────────────────────────────────────────────
def test_install_for_config_skips_everything_when_all_present(monkeypatch):
    monkeypatch.setattr(installer, "_available", lambda mod: True)
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    logs = []
    installer.install_for_config({}, log=logs.append)
    assert calls == []
    assert any("already installed" in l for l in logs)


def test_install_for_config_installs_only_missing_packages(monkeypatch):
    monkeypatch.setattr(installer, "_available", lambda mod: mod not in ("psutil", "PIL"))
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    logs = []
    installer.install_for_config({}, log=logs.append)
    assert set(calls) == {"psutil", "pillow"}
    assert any("Installing 2 package(s)" in l for l in logs)


def test_install_for_config_selects_stt_and_tts_engine_packages(monkeypatch):
    monkeypatch.setattr(installer, "_available", lambda mod: mod != "vosk" and mod != "edge_tts")
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    installer.install_for_config({"stt_engine": "vosk", "tts_engine": "edgetts"}, log=None)
    assert "vosk" in calls
    assert "edge-tts" in calls


def test_install_for_config_dedupes_a_package_needed_by_core_and_tts(monkeypatch):
    # 'soundfile' is in _CORE and again in _TTS['kokoro'] — must only be pip-installed once.
    monkeypatch.setattr(installer, "_available", lambda mod: mod != "soundfile")
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    installer.install_for_config({"tts_engine": "kokoro"}, log=None)
    assert calls.count("soundfile") == 1


def test_install_for_config_adds_windows_only_packages_on_windows(monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer, "_available", lambda mod: mod != "pywinauto")
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    installer.install_for_config({}, log=None)
    assert "pywinauto" in calls


def test_install_for_config_skips_windows_only_packages_elsewhere(monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer, "_available", lambda mod: mod != "pywinauto")
    calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: calls.append(pkg))
    installer.install_for_config({}, log=None)
    assert "pywinauto" not in calls


def test_install_for_config_installs_and_downloads_playwright_when_missing(monkeypatch):
    # Something else must also be missing, otherwise install_for_config returns
    # early (before ever reaching the separate playwright check) once its own
    # `missing` list is empty.
    monkeypatch.setattr(installer, "_available", lambda mod: mod not in ("playwright", "psutil"))
    pip_calls = []
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: pip_calls.append(pkg))
    run_calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: run_calls.append(a) or _proc(0))
    logs = []
    installer.install_for_config({}, log=logs.append)
    assert "playwright" in pip_calls
    assert any("chromium" in str(c) for c in run_calls)
    assert any("Playwright browser ready" in l for l in logs)


def test_install_for_config_does_not_download_playwright_when_already_available(monkeypatch):
    # 'psutil' missing so the missing-package loop actually runs; playwright is
    # already available, so the separate playwright/chromium step must not fire.
    monkeypatch.setattr(installer, "_available", lambda mod: mod != "psutil")
    monkeypatch.setattr(installer, "_pip", lambda pkg, log=None: None)
    run_calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: run_calls.append(a) or _proc(0))
    installer.install_for_config({}, log=None)
    assert run_calls == []
