"""Tests für actions/dev_agent.py — Projektplanung, Datei-Generierung, Run-Fix-Loop."""
import json
import subprocess

import pytest

from actions import dev_agent as da


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
def tmp_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(da, "PROJECTS_DIR", tmp_path / "MiaProjects")
    return tmp_path / "MiaProjects"


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(da.time, "sleep", lambda s: None)


@pytest.fixture
def sync_threads(monkeypatch):
    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(da.threading, "Thread", ImmediateThread)


# ── _strip_fences ────────────────────────────────────────────────────────────
def test_strip_fences_removes_markdown_wrapper():
    assert da._strip_fences("```python\nx = 1\n```") == "x = 1"


def test_strip_fences_leaves_plain_code():
    assert da._strip_fences("x = 1") == "x = 1"


# ── _is_rate_limit ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", ["Error 429", "Quota exceeded", "RESOURCE_EXHAUSTED"])
def test_is_rate_limit_true(msg):
    assert da._is_rate_limit(Exception(msg)) is True


def test_is_rate_limit_false_for_unrelated_error():
    assert da._is_rate_limit(Exception("connection refused")) is False


# ── _parse_traceback ─────────────────────────────────────────────────────────
def test_parse_traceback_finds_matching_project_file():
    output = 'Traceback...\n  File "main.py", line 12, in <module>\nNameError: x'
    file, line = da._parse_traceback(output, ["main.py", "utils/helpers.py"])
    assert (file, line) == ("main.py", 12)


def test_parse_traceback_prefers_the_last_matching_frame():
    output = (
        'File "main.py", line 3, in <module>\n'
        'File "utils/helpers.py", line 9, in foo\n'
    )
    file, line = da._parse_traceback(output, ["main.py", "utils/helpers.py"])
    assert (file, line) == ("utils/helpers.py", 9)


def test_parse_traceback_returns_none_when_no_match():
    assert da._parse_traceback("no traceback here", ["main.py"]) == (None, None)


# ── _classify_error ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("output,expected", [
    ("ModuleNotFoundError: No module named 'requests'", "dependency_error"),
    ("SyntaxError: invalid syntax", "syntax_error"),
    # A literal "ImportError" class name hits the dependency_error check first
    # (it matches on the substring "importerror"), so only a message that says
    # "cannot import" without spelling out that class name reaches this branch.
    ("cannot import name 'helper' from 'utils'", "import_error"),
    ("NameError: name 'x' is not defined", "runtime_error"),
    ("all good, no problems", "none"),
])
def test_classify_error(output, expected):
    assert da._classify_error(output) == expected


def test_classify_error_import_error_class_name_is_classified_as_dependency_error():
    # Documents the actual precedence: "ImportError" also matches the
    # dependency_error check, so it wins over the import_error branch below it.
    assert da._classify_error("ImportError: cannot import name 'x'") == "dependency_error"


# ── _has_error ───────────────────────────────────────────────────────────────
def test_has_error_false_for_timeout_message():
    assert da._has_error("Timed out after 30s", "python main.py") is False


def test_has_error_false_for_empty_output():
    assert da._has_error("   ", "python main.py") is False


def test_has_error_true_for_traceback():
    assert da._has_error("Traceback (most recent call last)", "python main.py") is True


# ── _plan_project ────────────────────────────────────────────────────────────
def test_plan_project_parses_json_plan(monkeypatch):
    plan = {"project_name": "demo", "entry_point": "main.py", "files": [], "run_command": "python main.py", "dependencies": []}
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel(json.dumps(plan)))
    assert da._plan_project("build a thing", "python") == plan


def test_plan_project_strips_fences_before_parsing(monkeypatch):
    plan = {"project_name": "demo"}
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel(f"```json\n{json.dumps(plan)}\n```"))
    assert da._plan_project("x", "python") == plan


def test_plan_project_raises_valueerror_on_invalid_json(monkeypatch):
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel("not json at all"))
    with pytest.raises(ValueError, match="invalid JSON"):
        da._plan_project("x", "python")


def test_plan_project_raises_rate_limit_error(monkeypatch):
    def boom(contents):
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(da, "_get_model", lambda name: type("M", (), {"generate_content": staticmethod(boom)})())
    with pytest.raises(da.RateLimitError):
        da._plan_project("x", "python")


# ── _write_file ──────────────────────────────────────────────────────────────
def test_write_file_saves_code_to_project_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel("```python\nprint(1)\n```"))
    file_info = {"path": "main.py", "description": "entry", "imports": []}
    code = da._write_file(file_info, "a project", [file_info], "python", tmp_path, {})
    assert code == "print(1)"
    assert (tmp_path / "main.py").read_text() == "print(1)"


def test_write_file_includes_dependency_context_for_already_written_imports(monkeypatch, tmp_path):
    seen = {}

    def fake_generate(prompt):
        seen["prompt"] = prompt
        return type("R", (), {"text": "code"})()

    monkeypatch.setattr(da, "_get_model", lambda name: type("M", (), {"generate_content": staticmethod(fake_generate)})())
    file_info = {"path": "main.py", "description": "entry", "imports": ["utils.helpers"]}
    da._write_file(file_info, "x", [file_info], "python", tmp_path,
                    already_written={"utils/helpers.py": "def foo(): pass"})
    assert "def foo(): pass" in seen["prompt"]


def test_write_file_raises_rate_limit_error(monkeypatch, tmp_path):
    def boom(contents):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(da, "_get_model", lambda name: type("M", (), {"generate_content": staticmethod(boom)})())
    file_info = {"path": "main.py"}
    with pytest.raises(da.RateLimitError):
        da._write_file(file_info, "x", [file_info], "python", tmp_path, {})


# ── _install_dependencies ─────────────────────────────────────────────────────
def test_install_dependencies_no_deps():
    assert da._install_dependencies([], None) == "No external dependencies."


def test_install_dependencies_all_already_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(da.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0))
    result = da._install_dependencies(["requests"], tmp_path)
    assert "already installed" in result


def test_install_dependencies_installs_missing_packages(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        if "show" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stderr="")

    monkeypatch.setattr(da.subprocess, "run", fake_run)
    result = da._install_dependencies(["requests>=2.0"], tmp_path)
    assert "Installed: requests>=2.0" in result


def test_install_dependencies_reports_timeout(monkeypatch, tmp_path):
    def fake_run(cmd, **k):
        if "show" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1)
        raise subprocess.TimeoutExpired(cmd="pip", timeout=120)

    monkeypatch.setattr(da.subprocess, "run", fake_run)
    result = da._install_dependencies(["slowpkg"], tmp_path)
    assert "timed out" in result


# ── _open_vscode ─────────────────────────────────────────────────────────────
def test_open_vscode_succeeds_on_first_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(da.subprocess, "Popen", lambda *a, **k: None)
    assert da._open_vscode(tmp_path) is True


def test_open_vscode_returns_false_when_nothing_works(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(da.subprocess, "Popen", boom)
    assert da._open_vscode(tmp_path) is False


# ── _run_project ─────────────────────────────────────────────────────────────
def test_run_project_substitutes_python_executable(monkeypatch, tmp_path):
    seen = {}

    def fake_run(parts, **k):
        seen["parts"] = parts
        return subprocess.CompletedProcess(args=parts, returncode=0, stdout="hi", stderr="")

    monkeypatch.setattr(da.subprocess, "run", fake_run)
    result = da._run_project("python main.py", tmp_path)
    assert seen["parts"][0] == da.sys.executable
    assert "STDOUT:\nhi" in result


def test_run_project_reports_no_output(monkeypatch, tmp_path):
    monkeypatch.setattr(da.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""))
    assert da._run_project("python main.py", tmp_path) == "Ran with no output."


def test_run_project_reports_timeout_as_likely_long_running(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=5)

    monkeypatch.setattr(da.subprocess, "run", boom)
    result = da._run_project("python server.py", tmp_path, timeout=5)
    assert "Timed out after 5s" in result and "likely working" in result


def test_run_project_reports_command_not_found(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("node")

    monkeypatch.setattr(da.subprocess, "run", boom)
    assert "Command not found" in da._run_project("node app.js", tmp_path)


# ── _try_auto_install ─────────────────────────────────────────────────────────
def test_try_auto_install_installs_missing_module(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(da.subprocess, "run", fake_run)
    ok = da._try_auto_install("ModuleNotFoundError: No module named 'requests'", tmp_path)
    assert ok is True
    assert "requests" in seen["cmd"]


def test_try_auto_install_returns_false_without_module_error(tmp_path):
    assert da._try_auto_install("some unrelated error", tmp_path) is False


def test_try_auto_install_returns_false_on_pip_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(da.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1))
    assert da._try_auto_install("No module named 'foo'", tmp_path) is False


# ── _fix_files ───────────────────────────────────────────────────────────────
def test_fix_files_targets_the_file_from_the_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel("fixed code"))
    monkeypatch.setattr(da, "procedure_find", lambda q: [])
    all_files = [{"path": "main.py", "description": "entry", "imports": []}]
    updated = da._fix_files(
        error_output='File "main.py", line 5, in <module>\nNameError: x',
        project_description="x", all_files=all_files,
        file_codes={"main.py": "broken"}, language="python",
        project_dir=tmp_path, entry_point="main.py",
    )
    assert updated == {"main.py": "fixed code"}
    assert (tmp_path / "main.py").read_text() == "fixed code"


def test_fix_files_falls_back_to_entry_point_without_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(da, "_get_model", lambda name: FakeModel("fixed"))
    monkeypatch.setattr(da, "procedure_find", lambda q: [])
    all_files = [{"path": "main.py", "imports": []}]
    updated = da._fix_files(
        error_output="something failed, no file reference",
        project_description="x", all_files=all_files,
        file_codes={"main.py": "broken"}, language="python",
        project_dir=tmp_path, entry_point="main.py",
    )
    assert "main.py" in updated


def test_fix_files_raises_rate_limit_error(monkeypatch, tmp_path):
    def boom(contents):
        raise RuntimeError("429")

    monkeypatch.setattr(da, "_get_model", lambda name: type("M", (), {"generate_content": staticmethod(boom)})())
    monkeypatch.setattr(da, "procedure_find", lambda q: [])
    all_files = [{"path": "main.py", "imports": []}]
    with pytest.raises(da.RateLimitError):
        da._fix_files("NameError", "x", all_files, {"main.py": "broken"}, "python", tmp_path, "main.py")


def test_fix_files_continues_after_a_non_rate_limit_failure(monkeypatch, tmp_path):
    def boom(contents):
        raise RuntimeError("some other failure")

    monkeypatch.setattr(da, "_get_model", lambda name: type("M", (), {"generate_content": staticmethod(boom)})())
    monkeypatch.setattr(da, "procedure_find", lambda q: [])
    all_files = [{"path": "main.py", "imports": []}]
    updated = da._fix_files("NameError", "x", all_files, {"main.py": "broken"}, "python", tmp_path, "main.py")
    assert updated == {}  # failed silently, no exception raised out


# ── _build_project ───────────────────────────────────────────────────────────
def _simple_plan(files=None):
    return {
        "project_name": "demo_project",
        "entry_point": "main.py",
        "files": files if files is not None else [{"path": "main.py", "description": "entry", "imports": []}],
        "run_command": "python main.py",
        "dependencies": [],
    }


def test_build_project_reports_rate_limit_during_planning(monkeypatch):
    def boom(desc, lang):
        raise da.RateLimitError("429")

    monkeypatch.setattr(da, "_plan_project", boom)
    spoken = []
    result = da._build_project("a thing", "python", "", 30, speak=spoken.append)
    assert "Rate limit reached" in result
    assert spoken


def test_build_project_reports_planning_value_error(monkeypatch):
    def boom(desc, lang):
        raise ValueError("bad json")

    monkeypatch.setattr(da, "_plan_project", boom)
    assert "Planning failed" in da._build_project("a thing", "python", "", 30)


def test_build_project_succeeds_on_first_run(monkeypatch, tmp_projects_dir):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())
    monkeypatch.setattr(da, "_write_file", lambda **kw: "print(1)")
    monkeypatch.setattr(da, "_open_vscode", lambda project_dir: True)
    monkeypatch.setattr(da, "_run_project", lambda cmd, project_dir, timeout: "1")
    result = da._build_project("say hi", "python", "", 30)
    assert "is working, sir" in result
    assert "Built in 1 attempt" in result


def test_build_project_reports_when_no_files_could_be_written(monkeypatch, tmp_projects_dir):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())

    def boom(**kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(da, "_write_file", boom)
    result = da._build_project("say hi", "python", "", 30)
    assert "could not write any project files" in result


def test_build_project_auto_installs_missing_dependency_and_retries(monkeypatch, tmp_projects_dir):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())
    monkeypatch.setattr(da, "_write_file", lambda **kw: "print(1)")
    monkeypatch.setattr(da, "_open_vscode", lambda project_dir: True)

    calls = {"n": 0}

    def fake_run_project(cmd, project_dir, timeout):
        calls["n"] += 1
        return "ModuleNotFoundError: No module named 'requests'" if calls["n"] == 1 else "1"

    monkeypatch.setattr(da, "_run_project", fake_run_project)
    monkeypatch.setattr(da, "_try_auto_install", lambda output, project_dir: True)
    result = da._build_project("say hi", "python", "", 30)
    assert "is working, sir" in result
    assert calls["n"] == 2


def test_build_project_fixes_and_retries_until_success(monkeypatch, tmp_projects_dir, sync_threads):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())
    monkeypatch.setattr(da, "_write_file", lambda **kw: "broken")
    monkeypatch.setattr(da, "_open_vscode", lambda project_dir: True)

    calls = {"n": 0}

    def fake_run_project(cmd, project_dir, timeout):
        calls["n"] += 1
        return "NameError: x" if calls["n"] == 1 else "42"

    monkeypatch.setattr(da, "_run_project", fake_run_project)
    monkeypatch.setattr(da, "_fix_files", lambda **kw: {"main.py": "fixed"})
    added = []
    monkeypatch.setattr(da, "procedure_add", lambda problem, solution: added.append((problem, solution)))

    result = da._build_project("say hi", "python", "", 30)
    assert "Built in 2 attempts" in result
    assert added and added[0][1] == "fixed"


def test_build_project_gives_up_after_max_attempts(monkeypatch, tmp_projects_dir):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())
    monkeypatch.setattr(da, "_write_file", lambda **kw: "broken")
    monkeypatch.setattr(da, "_open_vscode", lambda project_dir: True)
    monkeypatch.setattr(da, "_run_project", lambda cmd, project_dir, timeout: "SyntaxError: still broken")
    monkeypatch.setattr(da, "_fix_files", lambda **kw: {"main.py": "still broken"})
    result = da._build_project("say hi", "python", "", 30)
    assert f"after {da.MAX_FIX_ATTEMPTS} attempts" in result


def test_build_project_reports_rate_limit_during_fix(monkeypatch, tmp_projects_dir):
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: _simple_plan())
    monkeypatch.setattr(da, "_write_file", lambda **kw: "broken")
    monkeypatch.setattr(da, "_open_vscode", lambda project_dir: True)
    monkeypatch.setattr(da, "_run_project", lambda cmd, project_dir, timeout: "NameError: x")

    def boom(**kw):
        raise da.RateLimitError("429")

    monkeypatch.setattr(da, "_fix_files", boom)
    result = da._build_project("say hi", "python", "", 30)
    assert "Rate limit reached during fix" in result


# ── dev_agent(): Dispatcher ───────────────────────────────────────────────────
def test_dev_agent_requires_description():
    assert "describe the project" in da.dev_agent({})


def test_dev_agent_delegates_to_build_project(monkeypatch):
    seen = {}
    monkeypatch.setattr(da, "_build_project", lambda **kw: seen.update(kw) or "ok")
    result = da.dev_agent({"description": "a game", "language": "python", "timeout": "10"})
    assert result == "ok"
    assert seen["description"] == "a game"
    assert seen["timeout"] == 10


def test_tool_declaration_shape():
    assert da.TOOL["name"] == "dev_agent"
    assert da.TOOL["handler"] is da.dev_agent
