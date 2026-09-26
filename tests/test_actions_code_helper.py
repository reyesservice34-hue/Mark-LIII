"""Tests für actions/code_helper.py — Codegenerierung, Build-Fix-Loop, Dispatch."""
import subprocess
import threading

import pytest

from actions import code_helper as ch


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


class FakeModel:
    def __init__(self, text):
        self._text = text

    def generate_content(self, prompt):
        return type("R", (), {"text": self._text})()


@pytest.fixture(autouse=True)
def tmp_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "DESKTOP", tmp_path / "Desktop")
    return tmp_path / "Desktop"


@pytest.fixture
def sync_threads(monkeypatch):
    """Run threading.Thread synchronously so background procedure_add calls land
    before the test's own assertions run."""
    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(ch.threading, "Thread", ImmediateThread)


# ── _clean_code ──────────────────────────────────────────────────────────────
def test_clean_code_strips_markdown_fences_with_language():
    assert ch._clean_code("```python\nprint(1)\n```") == "print(1)"


def test_clean_code_strips_bare_fences():
    assert ch._clean_code("```\nx = 1\n```") == "x = 1"


def test_clean_code_leaves_plain_code_untouched():
    assert ch._clean_code("x = 1") == "x = 1"


# ── _resolve_save_path ───────────────────────────────────────────────────────
def test_resolve_save_path_absolute_path_used_as_is(tmp_path):
    abs_path = str(tmp_path / "somewhere" / "a.py")
    assert ch._resolve_save_path(abs_path, "python") == ch.Path(abs_path)


def test_resolve_save_path_relative_path_goes_under_desktop(tmp_desktop):
    assert ch._resolve_save_path("sub/a.js", "javascript") == tmp_desktop / "sub" / "a.js"


def test_resolve_save_path_default_name_uses_language_extension(tmp_desktop):
    assert ch._resolve_save_path("", "javascript") == tmp_desktop / "mia_code.js"


def test_resolve_save_path_unknown_language_defaults_to_py(tmp_desktop):
    assert ch._resolve_save_path("", "cobol") == tmp_desktop / "mia_code.py"


# ── _read_file / _save_file ──────────────────────────────────────────────────
def test_read_file_requires_a_path():
    content, err = ch._read_file("")
    assert content == "" and "No file path" in err


def test_read_file_reports_missing_file():
    content, err = ch._read_file("/does/not/exist.py")
    assert content == "" and "File not found" in err


def test_read_file_returns_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print(1)")
    content, err = ch._read_file(str(f))
    assert content == "print(1)" and err == ""


def test_save_file_creates_parents_and_writes(tmp_path):
    target = tmp_path / "sub" / "a.py"
    result = ch._save_file(target, "print(1)")
    assert "Saved to" in result
    assert target.read_text() == "print(1)"


# ── _preview ─────────────────────────────────────────────────────────────────
def test_preview_short_code_shown_fully():
    assert ch._preview("a\nb\nc", lines=10) == "a\nb\nc"


def test_preview_truncates_long_code_with_count():
    code = "\n".join(f"line{i}" for i in range(20))
    result = ch._preview(code, lines=5)
    assert result.count("\n") == 4 + 1  # 5 lines + suffix line
    assert "15 more lines" in result


# ── _has_error ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("output", ["Traceback (most recent call last)", "SyntaxError: x", "Something FAILED"])
def test_has_error_detects_signals(output):
    assert ch._has_error(output) is True


def test_has_error_false_for_clean_output():
    assert ch._has_error("Hello, world!") is False


# ── _take_screenshot ─────────────────────────────────────────────────────────
def test_take_screenshot_returns_none_when_pyautogui_missing():
    # pyautogui is not installed in this sandbox — real graceful-degradation path.
    assert ch._take_screenshot() is None


# ── _detect_intent ───────────────────────────────────────────────────────────
def test_detect_intent_uses_gemini_classification(monkeypatch):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("build"))
    assert ch._detect_intent("mach ein Skript und teste es", "", "") == "build"


def test_detect_intent_strips_quotes_and_backticks_from_answer(monkeypatch):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("`write`"))
    assert ch._detect_intent("schreib mir etwas", "", "") == "write"


def test_detect_intent_falls_back_when_gemini_returns_invalid_word(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("not_a_real_intent"))
    assert ch._detect_intent("etwas", "", "") == "write"


def test_detect_intent_falls_back_when_gemini_raises(monkeypatch):
    def boom():
        raise RuntimeError("kein Netz")

    monkeypatch.setattr(ch, "_get_gemini", boom)
    assert ch._detect_intent("etwas beschreiben", "", "vorhandener code") == "explain"


def test_detect_intent_structural_fallback_file_with_description(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    assert ch._detect_intent("mach etwas", str(f), "") == "edit"


def test_detect_intent_structural_fallback_file_without_description(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    assert ch._detect_intent("", str(f), "") == "explain"


def test_detect_intent_structural_fallback_code_only():
    assert ch._detect_intent("", "", "x = 1") == "explain"


def test_detect_intent_structural_fallback_nothing_given():
    assert ch._detect_intent("", "", "") == "write"


# ── _write / _fix_code ───────────────────────────────────────────────────────
def test_write_generates_and_saves_code(monkeypatch, tmp_desktop):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("```python\nprint('hi')\n```"))
    code, path = ch._write("say hi", "python", "", None)
    assert code == "print('hi')"
    assert path == tmp_desktop / "mia_code.py"
    assert path.read_text() == "print('hi')"


def test_fix_code_includes_past_procedure_hints(monkeypatch):
    seen = {}

    def fake_generate(prompt):
        seen["prompt"] = prompt
        return type("R", (), {"text": "fixed = True"})()

    monkeypatch.setattr(ch, "_get_gemini", lambda: type("M", (), {"generate_content": staticmethod(fake_generate)})())
    monkeypatch.setattr(ch, "procedure_find", lambda q: [{"problem": "alter Fehler", "solution": "alte Lösung"}])
    result = ch._fix_code("broken = code", "NameError: x", "mach etwas")
    assert result == "fixed = True"
    assert "alte Lösung" in seen["prompt"]


# ── _run_file ────────────────────────────────────────────────────────────────
def test_run_file_reports_unsupported_extension(tmp_path):
    f = tmp_path / "a.xyz"
    f.write_text("x")
    assert "No interpreter" in ch._run_file(f, [], 5)


def test_run_file_reports_stdout_and_stderr(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("print('hi')")
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="hi\n", stderr="warn\n")
    monkeypatch.setattr(ch.subprocess, "run", lambda *a, **k: fake_result)
    result = ch._run_file(f, [], 5)
    assert "Output:\nhi" in result and "Stderr:\nwarn" in result


def test_run_file_reports_no_output(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("pass")
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ch.subprocess, "run", lambda *a, **k: fake_result)
    assert ch._run_file(f, [], 5) == "Executed with no output."


def test_run_file_handles_timeout(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("while True: pass")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=5)

    monkeypatch.setattr(ch.subprocess, "run", boom)
    assert "Timed out after 5s" in ch._run_file(f, [], 5)


def test_run_file_handles_missing_interpreter(tmp_path, monkeypatch):
    f = tmp_path / "a.rb"
    f.write_text("puts 1")

    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(ch.subprocess, "run", boom)
    assert "Interpreter not found: ruby" in ch._run_file(f, [], 5)


# ── _build (Build-Fix-Loop) ───────────────────────────────────────────────────
def test_build_requires_a_description():
    assert "describe what you want" in ch._build("", "python", "", [], 30)


def test_build_succeeds_on_first_attempt(monkeypatch, tmp_desktop):
    monkeypatch.setattr(ch, "_write", lambda desc, lang, out, player: ("print(1)", tmp_desktop / "mia_code.py"))
    monkeypatch.setattr(ch, "_run_file", lambda path, args, timeout: "1")
    result = ch._build("print a number", "python", "", [], 30)
    assert "working after 1 attempt" in result


def test_build_fixes_and_retries_until_success(monkeypatch, tmp_desktop, sync_threads):
    monkeypatch.setattr(ch, "_write", lambda desc, lang, out, player: ("broken", tmp_desktop / "mia_code.py"))
    attempts = {"n": 0}

    def fake_run_file(path, args, timeout):
        attempts["n"] += 1
        return "NameError: x" if attempts["n"] == 1 else "42"

    monkeypatch.setattr(ch, "_run_file", fake_run_file)
    monkeypatch.setattr(ch, "_fix_code", lambda code, output, desc: "fixed_code")
    added = []
    monkeypatch.setattr(ch, "procedure_add", lambda problem, solution: added.append((problem, solution)))

    result = ch._build("mach etwas", "python", "", [], 30)
    assert "working after 2 attempts" in result
    assert added == [("NameError: x", "fixed_code")]


def test_build_gives_up_after_max_attempts(monkeypatch, tmp_desktop):
    monkeypatch.setattr(ch, "_write", lambda desc, lang, out, player: ("broken", tmp_desktop / "mia_code.py"))
    monkeypatch.setattr(ch, "_run_file", lambda path, args, timeout: "SyntaxError: still broken")
    monkeypatch.setattr(ch, "_fix_code", lambda code, output, desc: "still_broken_code")
    result = ch._build("mach etwas kaputtes", "python", "", [], 30)
    assert f"unable to build a working version after {ch.MAX_BUILD_ATTEMPTS}" in result


def test_build_reports_write_failure(monkeypatch):
    def boom(desc, lang, out, player):
        raise RuntimeError("Gemini down")

    monkeypatch.setattr(ch, "_write", boom)
    spoken = []
    result = ch._build("etwas", "python", "", [], 30, speak=spoken.append)
    assert "Could not write initial code" in result
    assert spoken


def test_build_reports_fix_failure(monkeypatch, tmp_desktop):
    monkeypatch.setattr(ch, "_write", lambda desc, lang, out, player: ("broken", tmp_desktop / "mia_code.py"))
    monkeypatch.setattr(ch, "_run_file", lambda path, args, timeout: "Error!")

    def boom(code, output, desc):
        raise RuntimeError("Gemini down")

    monkeypatch.setattr(ch, "_fix_code", boom)
    result = ch._build("etwas", "python", "", [], 30)
    assert "Could not fix code on attempt 1" in result


# ── High-level actions ────────────────────────────────────────────────────────
def test_write_action_requires_description():
    assert "describe what you want me to write" in ch._write_action("", "python", "", None)


def test_write_action_returns_preview(monkeypatch, tmp_desktop):
    monkeypatch.setattr(ch, "_write", lambda desc, lang, out, player: ("print(1)", tmp_desktop / "a.py"))
    result = ch._write_action("say hi", "python", "", None)
    assert "Code written" in result and "print(1)" in result


def test_write_action_reports_generation_failure(monkeypatch):
    def boom(desc, lang, out, player):
        raise RuntimeError("x")

    monkeypatch.setattr(ch, "_write", boom)
    assert "Could not generate code" in ch._write_action("x", "python", "", None)


def test_edit_action_requires_file_path_and_instruction():
    assert "file path" in ch._edit_action("", "do x", None)
    assert "describe what change" in ch._edit_action("a.py", "", None)


def test_edit_action_applies_change_and_saves(monkeypatch, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1")
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("x = 2"))
    result = ch._edit_action(str(f), "change x to 2", None)
    assert "File edited" in result
    assert f.read_text() == "x = 2"


def test_explain_action_requires_code_or_file():
    assert "provide code or a file path" in ch._explain_action("", "", None)


def test_explain_action_returns_explanation(monkeypatch):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("Das macht x."))
    assert ch._explain_action("", "x = 1", None) == "Das macht x."


def test_run_action_requires_file_path():
    assert "provide a file path to run" in ch._run_action("", [], 30, None)


def test_run_action_reports_missing_file():
    assert "File not found" in ch._run_action("/nope.py", [], 30, None)


def test_run_action_delegates_to_run_file(monkeypatch, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print(1)")
    monkeypatch.setattr(ch, "_run_file", lambda path, args, timeout: "1")
    assert ch._run_action(str(f), [], 30, None) == "1"


def test_optimize_action_requires_code_or_file():
    assert "provide code or a file path" in ch._optimize_action("", "", "python", "", None)


def test_optimize_action_reports_line_count_change(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_get_gemini", lambda: FakeModel("x = 1"))
    result = ch._optimize_action("", "x = 1\ny = 2\nz = 3", "python", str(tmp_path / "out.py"), None)
    assert "Lines: 3 → 1" in result


# ── code_helper(): Dispatcher ─────────────────────────────────────────────────
def test_dispatcher_auto_detects_and_routes(monkeypatch):
    monkeypatch.setattr(ch, "_detect_intent", lambda d, f, c: "explain")
    monkeypatch.setattr(ch, "_explain_action", lambda fp, code, player: "erklärt")
    result = ch.code_helper({"action": "auto", "code": "x = 1"})
    assert result == "erklärt"


def test_dispatcher_unknown_action():
    result = ch.code_helper({"action": "levitate"})
    assert "Unknown action" in result


def test_dispatcher_routes_explicit_action(monkeypatch):
    monkeypatch.setattr(ch, "_run_action", lambda fp, args, timeout, player: "lief durch")
    result = ch.code_helper({"action": "run", "file_path": "a.py"})
    assert result == "lief durch"


def test_tool_declaration_shape():
    assert ch.TOOL["name"] == "code_helper"
    assert ch.TOOL["handler"] is ch.code_helper
