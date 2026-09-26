"""Tests für actions/game_updater.py — Steam/Epic-Erkennung, Update/Install-Logik, Scheduling."""
import json
import subprocess
import threading

import pytest

from actions import game_updater as gu


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


@pytest.fixture(autouse=True)
def force_linux(monkeypatch):
    """Pin the OS-branching helpers to Linux regardless of config/api_keys.json,
    so tests are hermetic and match this sandbox's real platform."""
    monkeypatch.setattr(gu, "is_windows", lambda: False)
    monkeypatch.setattr(gu, "is_mac", lambda: False)
    monkeypatch.setattr(gu, "is_linux", lambda: True)


@pytest.fixture(autouse=True)
def fake_steam_exe(monkeypatch):
    """_steam_exe(steam_path) is only ever used to build the exe path passed to
    _launch_steam_url — irrelevant to these tests, which pass a bare `None` for
    steam_path and mock _launch_steam_url itself. Stub it out so it doesn't
    choke on `None / "steam.sh"`."""
    monkeypatch.setattr(gu, "_steam_exe", lambda steam_path: gu.Path("/fake/steam.sh"))


@pytest.fixture
def sync_threads(monkeypatch):
    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self._target, self._args, self._kwargs = target, args, kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(gu.threading, "Thread", ImmediateThread)


def _acf(app_id, name, state=4, size=100):
    return (
        f'"AppState"\n{{\n'
        f'\t"appid"\t\t"{app_id}"\n'
        f'\t"name"\t\t"{name}"\n'
        f'\t"StateFlags"\t\t"{state}"\n'
        f'\t"SizeOnDisk"\t\t"{size}"\n'
        f'}}\n'
    )


# ── Steam-Pfaderkennung ───────────────────────────────────────────────────────
def test_find_steam_linux_returns_first_existing_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(gu.Path, "home", lambda: tmp_path)
    steam_dir = tmp_path / ".steam" / "steam"
    steam_dir.mkdir(parents=True)
    assert gu._find_steam_linux() == steam_dir


def test_find_steam_linux_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(gu.Path, "home", lambda: tmp_path)
    assert gu._find_steam_linux() is None


def test_find_steam_path_dispatches_by_os(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_linux", lambda: "linux-path")
    assert gu._find_steam_path() == "linux-path"


# ── _get_steam_libraries / _get_steam_games ──────────────────────────────────
def test_get_steam_libraries_includes_base_and_parses_vdf(tmp_path):
    steam_path = tmp_path / "Steam"
    base_lib = steam_path / "steamapps"
    base_lib.mkdir(parents=True)
    extra_lib_root = tmp_path / "ExtraDrive"
    (extra_lib_root / "steamapps").mkdir(parents=True)
    (base_lib / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"1"\n\t{{\n\t\t"path"\t\t"{extra_lib_root}"\n\t}}\n}}\n',
        encoding="utf-8",
    )
    libs = gu._get_steam_libraries(steam_path)
    assert base_lib in libs
    assert (extra_lib_root / "steamapps") in libs


def test_get_steam_libraries_without_vdf_returns_base_only(tmp_path):
    steam_path = tmp_path / "Steam"
    libs = gu._get_steam_libraries(steam_path)
    assert libs == [steam_path / "steamapps"]


def test_get_steam_games_parses_acf_files(tmp_path):
    steam_path = tmp_path / "Steam"
    lib = steam_path / "steamapps"
    lib.mkdir(parents=True)
    (lib / "appmanifest_730.acf").write_text(_acf("730", "Counter-Strike 2", state=4, size=5000))
    games = gu._get_steam_games(steam_path)
    assert games == [{"id": "730", "name": "Counter-Strike 2", "state": 4, "size": 5000,
                       "lib": str(lib), "acf": str(lib / "appmanifest_730.acf")}]


def test_get_steam_games_skips_malformed_manifest(tmp_path):
    steam_path = tmp_path / "Steam"
    lib = steam_path / "steamapps"
    lib.mkdir(parents=True)
    (lib / "appmanifest_1.acf").write_text('"AppState"\n{\n\t"name"\t\t"No AppID Here"\n}\n')
    assert gu._get_steam_games(steam_path) == []


# ── _is_steam_running / _is_epic_running ─────────────────────────────────────
def test_is_steam_running_true_when_pgrep_finds_it(monkeypatch):
    monkeypatch.setattr(gu.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="1234\n"))
    assert gu._is_steam_running() is True


def test_is_steam_running_false_when_pgrep_finds_nothing(monkeypatch):
    monkeypatch.setattr(gu.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1, stdout=""))
    assert gu._is_steam_running() is False


def test_is_steam_running_false_on_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(gu.subprocess, "run", boom)
    assert gu._is_steam_running() is False


# ── _search_steam_appid ───────────────────────────────────────────────────────
def test_search_steam_appid_finds_installed_game(monkeypatch, tmp_path):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: tmp_path)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"id": "730", "name": "Counter-Strike 2"}])
    assert gu._search_steam_appid("counter-strike") == ("730", "Counter-Strike 2")


def test_search_steam_appid_uses_known_appid_table(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: None)
    assert gu._search_steam_appid("valheim") == ("892970", "Valheim")


def test_search_steam_appid_partial_match_on_known_table(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: None)
    app_id, name = gu._search_steam_appid("some dota 2 mod")
    assert (app_id, name) == ("570", "Dota 2")


def test_search_steam_appid_falls_back_to_store_api(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: None)

    class FakeResp:
        def read(self):
            return json.dumps({"items": [{"id": 999999, "name": "Some Indie Game"}]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=6: FakeResp())
    assert gu._search_steam_appid("totally unknown indie game xyz") == ("999999", "Some Indie Game")


def test_search_steam_appid_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: None)

    import urllib.request

    def boom(req, timeout=6):
        raise RuntimeError("kein netz")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert gu._search_steam_appid("totally unknown indie game xyz") == (None, None)


# ── _update_steam_games ───────────────────────────────────────────────────────
def test_update_steam_games_reports_not_running_failure(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: False)
    assert gu._update_steam_games(None) == "Could not start Steam."


def test_update_steam_games_reports_no_games(monkeypatch, tmp_path):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    assert gu._update_steam_games(tmp_path) == "No Steam games found."


def test_update_steam_games_categorises_by_state(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [
        {"id": "1", "name": "UpToDate", "state": 4},
        {"id": "2", "name": "Downloading", "state": 1026},
        {"id": "3", "name": "NeedsUpdate", "state": 0},
    ])
    launched = []
    monkeypatch.setattr(gu, "_launch_steam_url", lambda exe, url: launched.append(url))
    monkeypatch.setattr(gu.time, "sleep", lambda s: None)
    result = gu._update_steam_games(None)
    assert "Update started for: NeedsUpdate" in result
    assert "Already updating: Downloading" in result
    assert "already up to date" in result
    assert launched == ["steam://update/3"]


def test_update_steam_games_reports_game_not_found(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"id": "1", "name": "Foo", "state": 4}])
    result = gu._update_steam_games(None, game_name="Bar")
    assert "not found" in result


# ── _install_steam_game ───────────────────────────────────────────────────────
def test_install_steam_game_requires_name_or_appid(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    assert gu._install_steam_game(None) == "Please specify a game name or AppID."


def test_install_steam_game_already_up_to_date(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"id": "730", "name": "CS2", "state": 4}])
    result = gu._install_steam_game(None, app_id="730")
    assert "already installed and up to date" in result


def test_install_steam_game_pending_update_triggers_launch(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"id": "730", "name": "CS2", "state": 6}])
    launched = []
    monkeypatch.setattr(gu, "_launch_steam_url", lambda exe, url: launched.append(url))
    result = gu._install_steam_game(None, app_id="730")
    assert "pending update" in result
    assert launched == ["steam://update/730"]


def test_install_steam_game_searches_and_installs_new_game(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    monkeypatch.setattr(gu, "_search_steam_appid", lambda name: ("892970", "Valheim"))
    launched = []
    monkeypatch.setattr(gu, "_launch_steam_url", lambda exe, url: launched.append(url))
    result = gu._install_steam_game(None, game_name="valheim")
    assert "Install started for 'Valheim'" in result
    assert launched == ["steam://install/892970"]


def test_install_steam_game_reports_when_not_found_anywhere(monkeypatch):
    monkeypatch.setattr(gu, "_ensure_steam_running", lambda path: True)
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    monkeypatch.setattr(gu, "_search_steam_appid", lambda name: (None, None))
    result = gu._install_steam_game(None, game_name="totally unknown xyz")
    assert "Could not find" in result


# ── _get_download_status ───────────────────────────────────────────────────────
def test_get_download_status_reports_active_and_pending(monkeypatch):
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [
        {"name": "Downloading Now", "state": 1026},
        {"name": "Pending One", "state": 6},
    ])
    result = gu._get_download_status(None)
    assert "Downloading: Downloading Now" in result
    assert "Pending updates: Pending One" in result


def test_get_download_status_reports_nothing_active(monkeypatch):
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"name": "Idle", "state": 4}])
    assert gu._get_download_status(None) == "No active downloads or pending updates."


# ── Epic ─────────────────────────────────────────────────────────────────────
def test_epic_manifests_path_none_on_linux():
    assert gu._epic_manifests_path() is None


def test_get_epic_games_empty_without_manifests_path(monkeypatch):
    monkeypatch.setattr(gu, "_epic_manifests_path", lambda: None)
    assert gu._get_epic_games() == []


def test_get_epic_games_parses_item_files(tmp_path, monkeypatch):
    monkeypatch.setattr(gu, "_epic_manifests_path", lambda: tmp_path)
    (tmp_path / "game.item").write_text(json.dumps({"DisplayName": "Fortnite", "AppName": "fn123"}))
    assert gu._get_epic_games() == [{"id": "fn123", "name": "Fortnite"}]


def test_update_epic_games_reports_not_found(monkeypatch):
    monkeypatch.setattr(gu, "_get_epic_games", lambda: [{"id": "1", "name": "Foo"}])
    assert gu._update_epic_games(None, game_name="Bar") == "'Bar' not found in Epic."


def test_update_epic_games_launches_specific_game_on_linux(monkeypatch):
    monkeypatch.setattr(gu, "_get_epic_games", lambda: [{"id": "fn123", "name": "Fortnite"}])
    calls = []
    monkeypatch.setattr(gu.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    result = gu._update_epic_games(None, game_name="fortnite")
    assert "Opened Epic for 'Fortnite'" in result
    assert calls[0][0] == "xdg-open"


def test_update_epic_games_no_native_linux_support_without_exe(monkeypatch):
    monkeypatch.setattr(gu, "_get_epic_games", lambda: [])
    result = gu._update_epic_games(None)
    assert "not natively supported on Linux" in result


# ── Scheduling (Linux/cron) ────────────────────────────────────────────────────
def test_schedule_daily_update_dispatches_to_linux(monkeypatch):
    monkeypatch.setattr(gu, "_schedule_linux", lambda h, m: f"scheduled {h}:{m}")
    assert gu._schedule_daily_update(hour=4, minute=30) == "scheduled 4:30"


def test_schedule_linux_writes_crontab(monkeypatch):
    seen = {}

    def fake_run(cmd, **k):
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="")
        seen["input"] = k.get("input", "")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(gu.subprocess, "run", fake_run)
    result = gu._schedule_linux(3, 0)
    assert "scheduled at 03:00" in result
    assert gu._TASK_NAME in seen["input"]


def test_schedule_linux_reports_failure(monkeypatch):
    def fake_run(cmd, **k):
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stderr="crontab kaputt")

    monkeypatch.setattr(gu.subprocess, "run", fake_run)
    assert "Scheduling failed" in gu._schedule_linux(3, 0)


def test_cancel_scheduled_update_removes_matching_cron_lines(monkeypatch):
    existing = f"0 3 * * * old-entry # {gu._TASK_NAME}\n0 4 * * * unrelated-entry\n"
    seen = {}

    def fake_run(cmd, **k):
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=existing)
        seen["input"] = k.get("input", "")
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(gu.subprocess, "run", fake_run)
    result = gu._cancel_scheduled_update()
    assert result == "Scheduled update cancelled."
    assert gu._TASK_NAME not in seen["input"]
    assert "unrelated-entry" in seen["input"]


def test_get_schedule_status_reports_when_scheduled(monkeypatch):
    existing = f"0 3 * * * cmd # {gu._TASK_NAME}\n"
    monkeypatch.setattr(gu.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout=existing))
    result = gu._get_schedule_status()
    assert "Game update is scheduled" in result


def test_get_schedule_status_reports_when_not_scheduled(monkeypatch):
    monkeypatch.setattr(gu.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout=""))
    assert gu._get_schedule_status() == "No scheduled game update found."


# ── game_updater(): Dispatcher ─────────────────────────────────────────────────
def test_dispatcher_schedule_action(monkeypatch):
    monkeypatch.setattr(gu, "_schedule_daily_update", lambda hour, minute: f"scheduled {hour}:{minute}")
    assert gu.game_updater({"action": "schedule", "hour": 5, "minute": 15}) == "scheduled 5:15"


def test_dispatcher_cancel_schedule(monkeypatch):
    monkeypatch.setattr(gu, "_cancel_scheduled_update", lambda: "cancelled")
    assert gu.game_updater({"action": "cancel_schedule"}) == "cancelled"


def test_dispatcher_list_reports_both_platforms(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: "somepath")
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [{"name": "CS2"}])
    result = gu.game_updater({"action": "list", "platform": "steam"})
    assert "Steam (1 games): CS2" in result


def test_dispatcher_list_reports_steam_not_installed(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: None)
    result = gu.game_updater({"action": "list", "platform": "steam"})
    assert "Steam: Not installed." in result


def test_dispatcher_list_reports_epic_unsupported_on_linux():
    # is_linux() forced True by the force_linux fixture
    result = gu.game_updater({"action": "list", "platform": "epic"})
    assert "Not natively supported on Linux" in result


def test_dispatcher_update_requires_game_name_for_install(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: "path")
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    result = gu.game_updater({"action": "install", "platform": "steam"})
    assert "Please specify a game name to install" in result


def test_dispatcher_update_all_steam_games(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: "path")
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    monkeypatch.setattr(gu, "_update_steam_games", lambda path, game_name=None: "all updated")
    result = gu.game_updater({"action": "update", "platform": "steam"})
    assert "Steam: all updated" in result


def test_dispatcher_install_specific_game_not_yet_installed(monkeypatch):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: "path")
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    monkeypatch.setattr(gu, "_install_steam_game", lambda path, game_name=None, app_id=None: f"installing {game_name}")
    result = gu.game_updater({"action": "install", "platform": "steam", "game_name": "Valheim"})
    assert result == "installing Valheim"


def test_dispatcher_enables_auto_shutdown_thread(monkeypatch, sync_threads):
    monkeypatch.setattr(gu, "_find_steam_path", lambda: "path")
    monkeypatch.setattr(gu, "_get_steam_games", lambda path: [])
    monkeypatch.setattr(gu, "_install_steam_game", lambda path, game_name=None, app_id=None: "installing")
    watched = []
    monkeypatch.setattr(gu, "_watch_and_shutdown", lambda steam_path, speak=None: watched.append(steam_path))
    result = gu.game_updater({"action": "install", "platform": "steam", "game_name": "Valheim", "shutdown_when_done": "true"})
    assert "Auto-shutdown enabled" in result
    assert watched == ["path"]


def test_dispatcher_unknown_action():
    assert gu.game_updater({"action": "levitate"}) == "Unknown action: 'levitate'."


def test_tool_declaration_shape():
    assert gu.TOOL["name"] == "game_updater"
    assert gu.TOOL["handler"] is gu.game_updater
