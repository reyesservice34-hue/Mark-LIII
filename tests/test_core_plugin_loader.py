"""Tests für core/plugin_loader.py — Discovery, Validierung, Enable/Disable und Dispatch."""
import textwrap
from pathlib import Path

import pytest

from core import plugin_loader as pl
from memory import config_manager as cm


def _write(plugins_dir: Path, filename: str, body: str) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / filename).write_text(textwrap.dedent(body), encoding="utf-8")


def _logger():
    lines = []
    return lines, lines.append


VALID_PLUGIN = """\
    def run(parameters):
        return "erledigt"

    PLUGIN = {
        "name": "good_plugin",
        "description": "Ein gültiges Test-Plugin.",
        "parameters": {"type": "OBJECT", "properties": {}},
    }
"""


@pytest.fixture(autouse=True)
def tmp_plugin_config(tmp_path_factory, monkeypatch):
    """get_plugin_enabled/get_plugin_config in plugin_loader gehen auf echte
    memory.config_manager-Funktionen — deren Speicherort hierhin umbiegen, statt
    das echte config/api_keys.json im Repo zu berühren."""
    cfg_dir = tmp_path_factory.mktemp("plugin-cfg")
    monkeypatch.setattr(cm, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cm, "CONFIG_FILE", cfg_dir / "api_keys.json")
    yield


# ── Discovery: gültige Plugins ────────────────────────────────────────────────
def test_discovers_and_registers_a_valid_plugin(tmp_path):
    _write(tmp_path, "good_plugin.py", VALID_PLUGIN)
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=log)
    assert reg.has("good_plugin")
    assert any("Plugin loaded: good_plugin" in l for l in lines)


def test_creates_plugins_dir_if_missing(tmp_path):
    missing = tmp_path / "does-not-exist-yet"
    pl.discover_plugins(missing, core_tool_names=set(), logger=lambda m: None)
    assert missing.is_dir()


def test_files_starting_with_underscore_are_ignored(tmp_path):
    _write(tmp_path, "_template.py", "PLUGIN = 'not a dict'\n")
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg._all_records == []


def test_get_tool_declarations_includes_enabled_plugin_by_default(tmp_path):
    _write(tmp_path, "good_plugin.py", VALID_PLUGIN)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    decls = reg.get_tool_declarations()
    assert decls == [{"name": "good_plugin", "description": "Ein gültiges Test-Plugin.",
                       "parameters": {"type": "OBJECT", "properties": {}}}]


def test_get_tool_declarations_excludes_a_disabled_plugin(tmp_path):
    _write(tmp_path, "good_plugin.py", VALID_PLUGIN)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    cm.save_plugin_enabled("good_plugin", False)
    assert reg.get_tool_declarations() == []


# ── Validierungsfehler (jede wird geloggt, im Gegensatz zu action_loader) ────
def test_file_without_plugin_dict_is_rejected_and_logged(tmp_path):
    _write(tmp_path, "just_a_helper.py", "def helper():\n    return 1\n")
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=log)
    assert reg.has("just_a_helper") is False
    assert any("Missing PLUGIN dict constant" in l for l in lines)


def test_rejects_plugin_with_invalid_name(tmp_path):
    _write(tmp_path, "bad_name.py", """\
        def run(parameters): return "x"
        PLUGIN = {"name": "123-bad", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg._all_records[0].valid is False


def test_rejects_plugin_with_missing_description(tmp_path):
    _write(tmp_path, "no_desc.py", """\
        def run(parameters): return "x"
        PLUGIN = {"name": "no_desc", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.has("no_desc") is False


def test_rejects_plugin_with_bad_parameters_shape(tmp_path):
    _write(tmp_path, "bad_params.py", """\
        def run(parameters): return "x"
        PLUGIN = {"name": "bad_params", "description": "x", "parameters": {"type": "ARRAY"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.has("bad_params") is False


def test_rejects_plugin_missing_run_function(tmp_path):
    _write(tmp_path, "no_run.py", """\
        PLUGIN = {"name": "no_run", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.has("no_run") is False


def test_module_that_raises_on_import_is_skipped_and_logged(tmp_path):
    _write(tmp_path, "broken.py", "raise RuntimeError('kaputt beim Import')\n")
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=log)
    assert reg._all_records[0].valid is False
    assert any("Failed to load" in l and "kaputt beim Import" in l for l in lines)


# ── Namenskollisionen ────────────────────────────────────────────────────────
def test_rejects_plugin_colliding_with_a_core_tool_name(tmp_path):
    _write(tmp_path, "clashes.py", """\
        def run(parameters): return "x"
        PLUGIN = {"name": "core_tool", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names={"core_tool"}, logger=log)
    assert reg.has("core_tool") is False
    assert any("collides with a core tool" in l for l in lines)


def test_second_plugin_with_duplicate_name_is_rejected_first_file_wins(tmp_path):
    _write(tmp_path, "a_first.py", """\
        def run(parameters): return "erste"
        PLUGIN = {"name": "dup", "description": "erste", "parameters": {"type": "OBJECT"}}
    """)
    _write(tmp_path, "b_second.py", """\
        def run(parameters): return "zweite"
        PLUGIN = {"name": "dup", "description": "zweite", "parameters": {"type": "OBJECT"}}
    """)
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=log)
    assert reg.run("dup", {}) == "erste"
    assert any("already used by plugin" in l and "a_first.py" in l for l in lines)


# ── Dispatch (PluginRegistry.run) ─────────────────────────────────────────────
def test_run_unknown_plugin_returns_not_available_message(tmp_path):
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.run("ghost", {}) == "Plugin 'ghost' is not available."


def test_run_disabled_plugin_refuses(tmp_path):
    _write(tmp_path, "good_plugin.py", VALID_PLUGIN)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    cm.save_plugin_enabled("good_plugin", False)
    assert reg.run("good_plugin", {}) == "The 'good_plugin' plugin is currently disabled."


def test_run_passes_only_declared_kwargs(tmp_path):
    _write(tmp_path, "wants_player.py", """\
        def run(parameters, player):
            return f"player={player}"

        PLUGIN = {"name": "wants_player", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.run("wants_player", {}, player="P", session_memory="M") == "player=P"


def test_run_passes_all_kwargs_when_run_has_var_keyword(tmp_path):
    _write(tmp_path, "wants_all.py", """\
        def run(parameters, **kwargs):
            return ",".join(sorted(kwargs.keys()))

        PLUGIN = {"name": "wants_all", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.run("wants_all", {}, player="P", session_memory="M") == "player,session_memory"


def test_run_returns_done_when_run_fn_returns_falsy(tmp_path):
    _write(tmp_path, "returns_nothing.py", """\
        def run(parameters):
            return None

        PLUGIN = {"name": "returns_nothing", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.run("returns_nothing", {}) == "Done."


def test_run_catches_exceptions_and_logs(tmp_path):
    _write(tmp_path, "boom.py", """\
        def run(parameters):
            raise ValueError("kaputt")

        PLUGIN = {"name": "boom", "description": "x", "parameters": {"type": "OBJECT"}}
    """)
    lines, log = _logger()
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=log)
    result = reg.run("boom", {})
    assert result == "Sir, the 'boom' plugin failed: kaputt"
    assert any("crashed during run()" in l for l in lines)


# ── settings_schemas() ─────────────────────────────────────────────────────────
PLUGIN_WITH_SETTINGS = """\
    def run(parameters): return "x"
    PLUGIN = {"name": "%(name)s", "description": "x", "parameters": {"type": "OBJECT"}}
    PLUGIN_SETTINGS = {"namespace": "%(ns)s", "title": "%(title)s",
                        "fields": [{"key": "ip", "label": "IP"}]}
"""


def test_settings_schemas_includes_plugin_with_settings_and_merges_stored_values(tmp_path):
    _write(tmp_path, "printer.py", PLUGIN_WITH_SETTINGS % {"name": "printer", "ns": "printer", "title": "Drucker"})
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    cm.save_plugin_config("printer", {"ip": "192.168.1.5"})
    schemas = reg.settings_schemas()
    assert len(schemas) == 1
    assert schemas[0]["namespace"] == "printer"
    assert schemas[0]["title"] == "Drucker"
    assert schemas[0]["values"] == {"ip": "192.168.1.5"}


def test_settings_schemas_dedupes_by_shared_namespace(tmp_path):
    _write(tmp_path, "printer_a.py", PLUGIN_WITH_SETTINGS % {"name": "printer_a", "ns": "printer_suite", "title": "Suite"})
    _write(tmp_path, "printer_b.py", PLUGIN_WITH_SETTINGS % {"name": "printer_b", "ns": "printer_suite", "title": "Suite"})
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert len(reg.settings_schemas()) == 1


def test_settings_schemas_excludes_disabled_plugins(tmp_path):
    _write(tmp_path, "printer.py", PLUGIN_WITH_SETTINGS % {"name": "printer", "ns": "printer", "title": "Drucker"})
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    cm.save_plugin_enabled("printer", False)
    assert reg.settings_schemas() == []


def test_settings_schemas_ignores_a_malformed_schema_but_still_loads_the_plugin(tmp_path):
    _write(tmp_path, "half_broken.py", """\
        def run(parameters): return "x"
        PLUGIN = {"name": "half_broken", "description": "x", "parameters": {"type": "OBJECT"}}
        PLUGIN_SETTINGS = {"namespace": "half_broken"}  # kein 'fields' -> ungültig
    """)
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    assert reg.has("half_broken") is True
    assert reg.settings_schemas() == []


# ── list_for_ui() ────────────────────────────────────────────────────────────
def test_list_for_ui_reports_valid_and_invalid_entries(tmp_path):
    _write(tmp_path, "good_plugin.py", VALID_PLUGIN)
    _write(tmp_path, "broken.py", "PLUGIN = 'nicht mal ein dict'\n")
    reg = pl.discover_plugins(tmp_path, core_tool_names=set(), logger=lambda m: None)
    ui = {r["name"]: r for r in reg.list_for_ui()}
    assert ui["good_plugin"]["valid"] is True
    assert ui["good_plugin"]["enabled"] is True
    assert ui["broken"]["valid"] is False
    assert ui["broken"]["enabled"] is False
    assert ui["broken"]["error"]
