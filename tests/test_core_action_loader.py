"""Tests für core/action_loader.py — Discovery, Validierung und Dispatch von actions/*.py."""
import textwrap
from pathlib import Path

from core import action_loader as al


def _write(actions_dir: Path, filename: str, body: str) -> None:
    actions_dir.mkdir(parents=True, exist_ok=True)
    (actions_dir / filename).write_text(textwrap.dedent(body), encoding="utf-8")


VALID_ACTION = """\
    def handler(parameters):
        return "erledigt: " + str(parameters.get("x"))

    TOOL = {
        "name": "good_action",
        "description": "Eine gültige Test-Aktion.",
        "parameters": {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}},
        "handler": handler,
    }
"""


def _logger():
    lines = []
    return lines, lines.append


# ── Discovery: gültige Aktionen ─────────────────────────────────────────────
def test_discovers_and_registers_a_valid_action(tmp_path):
    _write(tmp_path, "good_action.py", VALID_ACTION)
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.has("good_action")
    assert reg.names() == {"good_action"}
    assert any("Action loaded: good_action" in l for l in lines)


def test_creates_actions_dir_if_missing(tmp_path):
    missing = tmp_path / "does-not-exist-yet"
    al.discover_actions(missing, logger=lambda m: None)
    assert missing.is_dir()


def test_files_starting_with_underscore_are_ignored(tmp_path):
    _write(tmp_path, "_helper.py", "TOOL = 'not even a dict, but never inspected'\n")
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.names() == set()
    assert lines == ["Action discovery complete: 0 active."]


def test_files_without_tool_dict_are_silently_ignored(tmp_path):
    _write(tmp_path, "just_a_helper.py", "def helper():\n    return 1\n")
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.names() == set()
    assert not any("just_a_helper" in l for l in lines)


def test_get_tool_declarations_only_contains_name_description_parameters(tmp_path):
    _write(tmp_path, "good_action.py", VALID_ACTION)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    decls = reg.get_tool_declarations()
    assert decls == [{
        "name": "good_action",
        "description": "Eine gültige Test-Aktion.",
        "parameters": {"type": "OBJECT", "properties": {"x": {"type": "STRING"}}},
    }]


# ── Validierungsfehler ───────────────────────────────────────────────────────
def test_rejects_tool_with_invalid_name(tmp_path):
    _write(tmp_path, "bad_name.py", """\
        TOOL = {"name": "123-bad", "description": "x", "parameters": {"type": "OBJECT"}, "handler": lambda parameters: "x"}
    """)
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.names() == set()
    assert any("not a valid identifier" in l for l in lines)


def test_rejects_tool_with_missing_description(tmp_path):
    _write(tmp_path, "no_desc.py", """\
        TOOL = {"name": "no_desc", "parameters": {"type": "OBJECT"}, "handler": lambda parameters: "x"}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    assert reg.names() == set()


def test_rejects_tool_with_bad_parameters_shape(tmp_path):
    _write(tmp_path, "bad_params.py", """\
        TOOL = {"name": "bad_params", "description": "x", "parameters": {"type": "ARRAY"}, "handler": lambda parameters: "x"}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    assert reg.names() == set()


def test_rejects_tool_with_non_callable_handler(tmp_path):
    _write(tmp_path, "bad_handler.py", """\
        TOOL = {"name": "bad_handler", "description": "x", "parameters": {"type": "OBJECT"}, "handler": "nicht aufrufbar"}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    assert reg.names() == set()


def test_module_that_raises_on_import_is_skipped_and_logged(tmp_path):
    _write(tmp_path, "broken.py", "raise RuntimeError('kaputt beim Import')\n")
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.names() == set()
    assert any("Failed to load" in l and "kaputt beim Import" in l for l in lines)


# ── Namenskollisionen ────────────────────────────────────────────────────────
def test_rejects_action_colliding_with_a_reserved_name(tmp_path):
    _write(tmp_path, "clashes.py", """\
        TOOL = {"name": "reserved_tool", "description": "x", "parameters": {"type": "OBJECT"}, "handler": lambda parameters: "x"}
    """)
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, reserved_names={"reserved_tool"}, logger=log)
    assert reg.names() == set()
    assert any("collides with a reserved core tool" in l for l in lines)


def test_second_action_with_duplicate_name_is_rejected_first_file_wins(tmp_path):
    _write(tmp_path, "a_first.py", """\
        TOOL = {"name": "dup", "description": "erste", "parameters": {"type": "OBJECT"}, "handler": lambda parameters: "erste"}
    """)
    _write(tmp_path, "b_second.py", """\
        TOOL = {"name": "dup", "description": "zweite", "parameters": {"type": "OBJECT"}, "handler": lambda parameters: "zweite"}
    """)
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    assert reg.run("dup", {}) == "erste"
    assert any("already used by action" in l and "a_first.py" in l for l in lines)


# ── Dispatch (ActionRegistry.run) ────────────────────────────────────────────
def test_run_unknown_action_returns_not_available_message(tmp_path):
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    assert reg.run("ghost", {}) == "Action 'ghost' is not available."


def test_run_passes_only_the_context_kwargs_the_handler_declares(tmp_path):
    _write(tmp_path, "wants_speak.py", """\
        def handler(parameters, speak):
            return f"speak={speak}"

        TOOL = {"name": "wants_speak", "description": "x", "parameters": {"type": "OBJECT"},
                "handler": handler}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    result = reg.run("wants_speak", {}, ctx={"speak": "hallo", "player": "P", "response": "R"})
    assert result == "speak=hallo"


def test_run_passes_all_context_kwargs_when_handler_has_var_keyword(tmp_path):
    _write(tmp_path, "wants_all.py", """\
        def handler(parameters, **kwargs):
            return ",".join(sorted(kwargs.keys()))

        TOOL = {"name": "wants_all", "description": "x", "parameters": {"type": "OBJECT"},
                "handler": handler}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    result = reg.run("wants_all", {}, ctx={"speak": "s", "player": "p", "response": "r", "session_memory": "m"})
    assert result == "player,response,session_memory,speak"


def test_run_returns_done_when_handler_returns_falsy(tmp_path):
    _write(tmp_path, "returns_nothing.py", """\
        def handler(parameters):
            return None

        TOOL = {"name": "returns_nothing", "description": "x", "parameters": {"type": "OBJECT"},
                "handler": handler}
    """)
    reg = al.discover_actions(tmp_path, logger=lambda m: None)
    assert reg.run("returns_nothing", {}) == "Done."


def test_run_catches_handler_exceptions_and_logs(tmp_path):
    _write(tmp_path, "boom.py", """\
        def handler(parameters):
            raise ValueError("kaputt")

        TOOL = {"name": "boom", "description": "x", "parameters": {"type": "OBJECT"}, "handler": handler}
    """)
    lines, log = _logger()
    reg = al.discover_actions(tmp_path, logger=log)
    result = reg.run("boom", {})
    assert result == "Tool 'boom' failed: kaputt"
    assert any("crashed during run()" in l for l in lines)
