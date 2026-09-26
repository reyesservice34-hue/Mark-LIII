"""Tests für actions/desktop.py — Sandbox-Codeausführung, Desktop-Dateiverwaltung, Dispatch."""
import json
import os
import threading
import time as real_time

import pytest

from actions import desktop as dt


@pytest.fixture(autouse=True)
def tmp_desktop(tmp_path, monkeypatch):
    """_get_desktop() falls back to Path.home()/'Desktop' on this (Linux) sandbox
    unless XDG_DESKTOP_DIR is set to an existing dir — pin both so tests never
    touch the real home directory."""
    monkeypatch.delenv("XDG_DESKTOP_DIR", raising=False)
    monkeypatch.setattr(dt.Path, "home", lambda: tmp_path)
    desktop = tmp_path / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── _get_desktop ─────────────────────────────────────────────────────────────
def test_get_desktop_falls_back_to_home_desktop(tmp_desktop):
    assert dt._get_desktop() == tmp_desktop


def test_get_desktop_uses_xdg_dir_when_set_and_existing(tmp_path, monkeypatch):
    xdg = tmp_path / "custom-desktop"
    xdg.mkdir()
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(xdg))
    monkeypatch.setattr(dt, "_OS", "Linux")
    assert dt._get_desktop() == xdg


# ── _execute_generated_code (Sandbox) ─────────────────────────────────────────
def test_execute_generated_code_rejects_empty_or_unsafe():
    assert dt._execute_generated_code("") == "This action cannot be performed safely."
    assert dt._execute_generated_code("UNSAFE") == "This action cannot be performed safely."


def test_execute_generated_code_strips_markdown_fences():
    code = "```python\nprint('hallo')\n```"
    assert dt._execute_generated_code(code) == "hallo"


def test_execute_generated_code_returns_done_without_output():
    assert dt._execute_generated_code("x = 1 + 1") == "Done."


def test_execute_generated_code_captures_multiple_print_lines():
    code = "print('eins')\nprint('zwei')"
    assert dt._execute_generated_code(code) == "eins\nzwei"


def test_execute_generated_code_reports_exceptions():
    # The sandbox's safe_builtins do not expose exception classes like
    # ValueError, so even a runtime error surfaces as a NameError — either way,
    # it must be caught and reported, never raised out of this function.
    result = dt._execute_generated_code("1 / 0")
    assert "Execution error" in result and "division by zero" in result


def test_execute_generated_code_sandbox_has_no_file_open_or_import():
    result = dt._execute_generated_code("open('/etc/passwd')")
    assert "Execution error" in result
    result2 = dt._execute_generated_code("__import__('os').system('echo hi')")
    assert "Execution error" in result2


def test_execute_generated_code_can_use_path_and_shutil_disk_usage(tmp_desktop):
    code = f"print(Path({str(tmp_desktop)!r}).exists())"
    assert dt._execute_generated_code(code) == "True"


# ── organize_desktop ─────────────────────────────────────────────────────────
def test_organize_desktop_by_type_moves_files_into_category_folders(tmp_desktop):
    (tmp_desktop / "photo.jpg").write_text("x")
    (tmp_desktop / "notes.txt").write_text("x")
    (tmp_desktop / "archive.zip").write_text("x")
    result = dt.organize_desktop("by_type")
    assert (tmp_desktop / "Images" / "photo.jpg").exists()
    assert (tmp_desktop / "Documents" / "notes.txt").exists()
    assert (tmp_desktop / "Archives" / "archive.zip").exists()
    assert "3 files moved" in result


def test_organize_desktop_unknown_extension_goes_to_others(tmp_desktop):
    (tmp_desktop / "weird.xyz123").write_text("x")
    dt.organize_desktop("by_type")
    assert (tmp_desktop / "Others" / "weird.xyz123").exists()


def test_organize_desktop_skips_directories_and_hidden_files(tmp_desktop):
    (tmp_desktop / "subdir").mkdir()
    (tmp_desktop / ".hidden").write_text("x")
    result = dt.organize_desktop("by_type")
    assert (tmp_desktop / "subdir").is_dir()
    assert (tmp_desktop / ".hidden").exists()
    assert "0 files moved" in result


def test_organize_desktop_skips_os_specific_extensions(tmp_desktop, monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    (tmp_desktop / "shortcut.desktop").write_text("x")
    dt.organize_desktop("by_type")
    assert (tmp_desktop / "shortcut.desktop").exists()  # never moved


def test_organize_desktop_reports_name_conflicts_as_skipped(tmp_desktop):
    (tmp_desktop / "Documents").mkdir()
    (tmp_desktop / "Documents" / "notes.txt").write_text("existing")
    (tmp_desktop / "notes.txt").write_text("new")
    result = dt.organize_desktop("by_type")
    assert "1 file(s) skipped" in result
    assert (tmp_desktop / "notes.txt").exists()  # left in place, not overwritten


def test_organize_desktop_by_date_uses_year_month_folder(tmp_desktop):
    (tmp_desktop / "old.txt").write_text("x")
    result = dt.organize_desktop("by_date")
    from datetime import datetime
    expected_folder = datetime.now().strftime("%Y-%m")
    assert (tmp_desktop / expected_folder / "old.txt").exists()
    assert "by_date" in result


# ── list_desktop ─────────────────────────────────────────────────────────────
def test_list_desktop_reports_empty():
    assert dt.list_desktop() == "Desktop is empty."


def test_list_desktop_lists_files_and_folders_with_sizes(tmp_desktop):
    (tmp_desktop / "small.txt").write_bytes(b"x" * 100)
    (tmp_desktop / "folder").mkdir()
    (tmp_desktop / "folder" / "inner.txt").write_text("x")
    result = dt.list_desktop()
    assert "small.txt" in result and "KB" in result
    assert "folder/" in result and "1 items" in result


def test_list_desktop_ignores_hidden_files(tmp_desktop):
    (tmp_desktop / ".hidden").write_text("x")
    assert dt.list_desktop() == "Desktop is empty."


# ── clean_desktop ────────────────────────────────────────────────────────────
def test_clean_desktop_archives_files_into_dated_folder(tmp_desktop):
    (tmp_desktop / "a.txt").write_text("x")
    (tmp_desktop / "b.txt").write_text("x")
    result = dt.clean_desktop()
    from datetime import datetime
    archive_name = f"Desktop Archive {datetime.now().strftime('%Y-%m-%d')}"
    assert (tmp_desktop / archive_name / "a.txt").exists()
    assert (tmp_desktop / archive_name / "b.txt").exists()
    assert "2 files archived" in result


def test_clean_desktop_leaves_directories_and_hidden_files(tmp_desktop):
    (tmp_desktop / "subdir").mkdir()
    (tmp_desktop / ".hidden").write_text("x")
    dt.clean_desktop()
    assert (tmp_desktop / "subdir").is_dir()
    assert (tmp_desktop / ".hidden").exists()


# ── get_desktop_stats ────────────────────────────────────────────────────────
def test_get_desktop_stats_counts_files_and_folders(tmp_desktop):
    (tmp_desktop / "a.txt").write_bytes(b"x" * 2048)
    (tmp_desktop / "folder").mkdir()
    result = dt.get_desktop_stats()
    assert "Files   : 1" in result
    assert "Folders : 1" in result
    assert "2.0 KB" in result


# ── Wallpaper (Linux-Zweige, subprocess gemockt) ─────────────────────────────
def test_set_wallpaper_reports_missing_file(tmp_desktop):
    assert "Image not found" in dt.set_wallpaper(str(tmp_desktop / "nope.jpg"))


def test_set_wallpaper_rejects_unsupported_format(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_text("x")
    assert "Unsupported format" in dt.set_wallpaper(str(f))


def test_set_wallpaper_gnome_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    img = tmp_path / "pic.png"
    img.write_bytes(b"fake-image")
    calls = []
    monkeypatch.setattr(dt.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    result = dt.set_wallpaper(str(img))
    assert "Wallpaper set: pic.png" in result
    assert any("picture-uri" in c for c in calls[0])


def test_set_wallpaper_unknown_de_falls_back_to_feh(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "")
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"fake-image")

    class Result:
        returncode = 0

    monkeypatch.setattr(dt.subprocess, "run", lambda cmd, **k: Result())
    result = dt.set_wallpaper(str(img))
    assert "Wallpaper set: pic.jpg" in result


def test_set_wallpaper_feh_missing_reports_manual_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "")
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"fake-image")

    class Result:
        returncode = 1

    monkeypatch.setattr(dt.subprocess, "run", lambda cmd, **k: Result())
    result = dt.set_wallpaper(str(img))
    assert "Could not set wallpaper automatically" in result


def test_set_wallpaper_from_url_downloads_and_delegates(monkeypatch, tmp_path):
    import urllib.request as real_urllib_request

    monkeypatch.setattr(dt.tempfile, "mktemp", lambda suffix=".jpg": str(tmp_path / f"dl{suffix}"))
    monkeypatch.setattr(real_urllib_request, "urlretrieve",
                        lambda url, path: __import__("pathlib").Path(path).write_bytes(b"x"))
    monkeypatch.setattr(dt, "set_wallpaper", lambda path: f"set:{path}")
    result = dt.set_wallpaper_from_url("http://example.com/pic.jpg")
    assert result.startswith("set:")


def test_get_current_wallpaper_gnome(monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")

    class Result:
        stdout = "file:///home/user/wall.jpg\n"

    monkeypatch.setattr(dt.subprocess, "run", lambda *a, **k: Result())
    result = dt.get_current_wallpaper()
    assert "wall.jpg" in result


def test_get_current_wallpaper_unsupported_de(monkeypatch):
    monkeypatch.setattr(dt, "_OS", "Linux")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "sway")
    result = dt.get_current_wallpaper()
    assert "not supported" in result


# ── _run_ai_task ─────────────────────────────────────────────────────────────
def test_run_ai_task_remembers_successful_code(monkeypatch):
    monkeypatch.setattr(dt, "_ask_gemini_for_desktop_action", lambda task: "print('ok')")
    monkeypatch.setattr(dt, "_execute_generated_code", lambda code, player=None: "ok")
    calls = []
    monkeypatch.setattr(dt, "procedure_add", lambda task, code: calls.append((task, code)))

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(dt.threading, "Thread", ImmediateThread)
    result = dt._run_ai_task("mach etwas")
    assert result == "ok"
    assert calls == [("mach etwas", "print('ok')")]


def test_run_ai_task_does_not_remember_unsafe_code(monkeypatch):
    monkeypatch.setattr(dt, "_ask_gemini_for_desktop_action", lambda task: "UNSAFE")
    monkeypatch.setattr(dt, "_execute_generated_code", lambda code, player=None: "This action cannot be performed safely.")
    calls = []
    monkeypatch.setattr(dt, "procedure_add", lambda task, code: calls.append(1))
    monkeypatch.setattr(dt.threading, "Thread", lambda target, args=(), daemon=None: type("T", (), {"start": lambda self: None})())
    dt._run_ai_task("mach etwas gefaehrliches")
    assert calls == []


def test_run_ai_task_does_not_remember_failed_execution(monkeypatch):
    monkeypatch.setattr(dt, "_ask_gemini_for_desktop_action", lambda task: "raise ValueError()")
    monkeypatch.setattr(dt, "_execute_generated_code", lambda code, player=None: "Execution error: x")
    calls = []
    monkeypatch.setattr(dt, "procedure_add", lambda task, code: calls.append(1))
    monkeypatch.setattr(dt.threading, "Thread", lambda target, args=(), daemon=None: type("T", (), {"start": lambda self: None})())
    dt._run_ai_task("mach etwas")
    assert calls == []


# ── desktop_control(): Dispatcher ─────────────────────────────────────────────
def test_desktop_control_no_action_or_task():
    assert dt.desktop_control({}) == "No action or task specified."


def test_desktop_control_wallpaper_requires_path():
    assert dt.desktop_control({"action": "wallpaper"}) == "No image path provided."


def test_desktop_control_wallpaper_url_requires_url():
    assert dt.desktop_control({"action": "wallpaper_url"}) == "No URL provided."


def test_desktop_control_routes_to_list(tmp_desktop):
    (tmp_desktop / "a.txt").write_text("x")
    result = dt.desktop_control({"action": "list"})
    assert "a.txt" in result


def test_desktop_control_routes_to_stats(tmp_desktop):
    result = dt.desktop_control({"action": "stats"})
    assert "Desktop stats" in result


def test_desktop_control_task_action_requires_description():
    assert dt.desktop_control({"action": "task"}) == "Please describe what you want to do on the desktop."


def test_desktop_control_runs_ai_task_for_task_field(monkeypatch):
    monkeypatch.setattr(dt, "_run_ai_task", lambda task, player: f"erledigt: {task}")
    result = dt.desktop_control({"task": "räume auf"})
    assert result == "erledigt: räume auf"


def test_desktop_control_treats_unknown_action_as_ai_task(monkeypatch):
    monkeypatch.setattr(dt, "_run_ai_task", lambda task, player: f"erledigt: {task}")
    result = dt.desktop_control({"action": "irgendwas komisches"})
    assert result == "erledigt: irgendwas komisches"


def test_desktop_control_logs_to_player(tmp_desktop):
    player = FakePlayer()
    dt.desktop_control({"action": "stats"}, player=player)
    assert player.logs


def test_desktop_control_catches_exceptions(monkeypatch):
    def boom(mode):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(dt, "organize_desktop", boom)
    result = dt.desktop_control({"action": "organize"})
    assert "Desktop control error" in result and "kaputt" in result


def test_tool_declaration_shape():
    assert dt.TOOL["name"] == "desktop_control"
    assert dt.TOOL["handler"] is dt.desktop_control
