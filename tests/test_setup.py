"""Tests für setup.py — einmaliges Setup-Skript (subprocess gemockt)."""
import subprocess

import pytest

import setup as su


def test_run_invokes_subprocess_with_check_true(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda args, check=None: seen.update(args=args, check=check))
    su._run("Doing a thing", ["echo", "hi"])
    assert seen == {"args": ["echo", "hi"], "check": True}
    assert "Doing a thing" in capsys.readouterr().out


def test_main_installs_dependencies_and_playwright_browsers(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(su, "_run", lambda label, args: calls.append(args))
    monkeypatch.setattr(su, "OS", "Linux")
    su.main()
    assert calls[0] == [su.sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    assert calls[1] == [su.sys.executable, "-m", "playwright", "install", "chromium", "firefox"]
    assert "Linux note" in capsys.readouterr().out


def test_main_prints_macos_note(monkeypatch, capsys):
    monkeypatch.setattr(su, "_run", lambda label, args: None)
    monkeypatch.setattr(su, "OS", "Darwin")
    su.main()
    assert "macOS note" in capsys.readouterr().out


def test_main_prints_windows_pywin32_warning_when_missing(monkeypatch, capsys):
    import sys
    import types

    monkeypatch.setattr(su, "_run", lambda label, args: None)
    monkeypatch.setattr(su, "OS", "Windows")
    monkeypatch.delitem(sys.modules, "win32com.client", raising=False)
    monkeypatch.delitem(sys.modules, "win32com", raising=False)

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "win32com.client":
            raise ImportError("no pywin32")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    su.main()
    assert "pywin32 did not register correctly" in capsys.readouterr().out
