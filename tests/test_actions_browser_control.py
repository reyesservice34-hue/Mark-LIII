"""Tests für actions/browser_control.py — URL-Normalisierung, Browser-Erkennung,
Session-Registry und Dispatch (Playwright-Sessions selbst werden gemockt)."""
import subprocess
import threading
import webbrowser

import pytest

from actions import browser_control as bc


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


@pytest.fixture(autouse=True)
def reset_registry(monkeypatch):
    monkeypatch.setattr(bc, "_registry", bc._SessionRegistry())


# ── _normalize_url ───────────────────────────────────────────────────────────
def test_normalize_url_empty_is_about_blank():
    assert bc._normalize_url("") == "about:blank"
    assert bc._normalize_url("   ") == "about:blank"


def test_normalize_url_bare_word_gets_dot_com():
    assert bc._normalize_url("instagram") == "https://instagram.com"


def test_normalize_url_domain_gets_https_prefix():
    assert bc._normalize_url("instagram.com") == "https://instagram.com"


def test_normalize_url_full_url_passes_through():
    assert bc._normalize_url("http://example.com/path") == "http://example.com/path"


# ── _user_agent ──────────────────────────────────────────────────────────────
def test_user_agent_matches_current_os(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    assert "X11; Linux" in bc._user_agent()
    monkeypatch.setattr(bc, "_OS", "Darwin")
    assert "Macintosh" in bc._user_agent()
    monkeypatch.setattr(bc, "_OS", "Windows")
    assert "Windows NT" in bc._user_agent()


# ── _automation_profile ───────────────────────────────────────────────────────
def test_automation_profile_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(bc, "_LEGACY_PROFILES_ROOT", tmp_path / "legacy")
    result = bc._automation_profile("chrome")
    assert result == tmp_path / "profiles" / "chrome"
    assert result.is_dir()


def test_automation_profile_migrates_legacy_root(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "marker.txt").write_text("x")
    monkeypatch.setattr(bc, "_PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(bc, "_LEGACY_PROFILES_ROOT", legacy)
    bc._automation_profile("chrome")
    assert (tmp_path / "profiles" / "marker.txt").exists()
    assert not legacy.exists()


def test_automation_profile_migrates_legacy_subdir_name(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    root.mkdir()
    (root / "firefox_jarvis").mkdir()
    (root / "firefox_jarvis" / "cookies.txt").write_text("x")
    monkeypatch.setattr(bc, "_PROFILES_ROOT", root)
    monkeypatch.setattr(bc, "_LEGACY_PROFILES_ROOT", tmp_path / "does-not-exist")
    result = bc._automation_profile("firefox_mia", legacy_name="firefox_jarvis")
    assert result == root / "firefox_mia"
    assert (result / "cookies.txt").exists()


# ── _real_profile_dir ─────────────────────────────────────────────────────────
def test_real_profile_dir_returns_existing_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    chrome_dir = tmp_path / ".config" / "google-chrome"
    chrome_dir.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    assert bc._real_profile_dir("chrome") == str(chrome_dir)


def test_real_profile_dir_falls_back_to_automation_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(bc, "_PROFILES_ROOT", tmp_path / "mia_profiles")
    monkeypatch.setattr(bc, "_LEGACY_PROFILES_ROOT", tmp_path / "legacy")
    result = bc._real_profile_dir("chrome")
    assert result == str(tmp_path / "mia_profiles" / "chrome")


# ── _firefox_profile_dir ──────────────────────────────────────────────────────
def test_firefox_profile_dir_none_without_ini(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    assert bc._firefox_profile_dir() is None


def test_firefox_profile_dir_parses_default_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    base = tmp_path / ".mozilla" / "firefox"
    profile_dir = base / "abc123.default-release"
    profile_dir.mkdir(parents=True)
    (base / "profiles.ini").write_text(
        "[Profile0]\nName=default\nIsRelative=1\nPath=abc123.default-release\nDefault=1\n",
        encoding="utf-8",
    )
    assert bc._firefox_profile_dir() == str(profile_dir)


def test_firefox_profile_dir_none_when_default_path_missing_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    base = tmp_path / ".mozilla" / "firefox"
    base.mkdir(parents=True)
    (base / "profiles.ini").write_text(
        "[Profile0]\nIsRelative=1\nPath=ghost.default\nDefault=1\n", encoding="utf-8",
    )
    assert bc._firefox_profile_dir() is None


# ── _resolve_browser ─────────────────────────────────────────────────────────
def test_resolve_browser_finds_binary_via_which(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.shutil, "which", lambda b: "/usr/bin/google-chrome" if b == "google-chrome" else None)
    spec = bc._resolve_browser("chrome")
    assert spec["exe"] == "/usr/bin/google-chrome"
    assert spec["engine"] == "chromium"


def test_resolve_browser_resolves_aliases(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.shutil, "which", lambda b: None)
    assert bc._resolve_browser("Google Chrome")["engine"] == "chromium"


def test_resolve_browser_unknown_browser_returns_none(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    assert bc._resolve_browser("netscape") is None


def test_resolve_browser_safari_unsupported_on_linux(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    assert bc._resolve_browser("safari") is None


# ── _detect_default_browser ───────────────────────────────────────────────────
def test_detect_default_browser_linux_reads_xdg_settings(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="firefox.desktop\n"))
    assert bc._detect_default_browser() == "firefox"


def test_detect_default_browser_falls_back_to_chrome_on_error(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(bc.subprocess, "run", boom)
    assert bc._detect_default_browser() == "chrome"


# ── _open_native ─────────────────────────────────────────────────────────────
def test_open_native_launches_resolved_browser_executable(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc, "_resolve_browser", lambda name: {"exe": "/usr/bin/firefox", "engine": "firefox", "channel": None})
    calls = []
    monkeypatch.setattr(bc.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    result = bc._open_native("example.com", "firefox")
    assert "Opened in firefox" in result
    assert calls[0][0] == "/usr/bin/firefox"


def test_open_native_falls_back_to_default_browser_via_xdg_open(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc, "_resolve_browser", lambda name: None)
    monkeypatch.setattr(bc, "_find_exe_windows", lambda name: None)
    calls = []
    monkeypatch.setattr(bc.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    result = bc._open_native("example.com", "unknownbrowser")
    assert "Opened in your default browser" in result
    assert calls[0][0] == "xdg-open"


def test_open_native_with_no_url_and_no_browser_opens_default(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc, "_detect_default_browser", lambda: "chrome")
    monkeypatch.setattr(bc, "_resolve_browser", lambda name: {"exe": "/usr/bin/chrome", "engine": "chromium", "channel": None})
    calls = []
    monkeypatch.setattr(bc.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    result = bc._open_native("", None)
    assert result == "Opened chrome."
    assert calls[0] == ["/usr/bin/chrome"]


def test_open_native_reports_failure_when_nothing_works(monkeypatch):
    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc, "_resolve_browser", lambda name: None)

    def boom_popen(*a, **k):
        raise OSError("kein Browser")

    monkeypatch.setattr(bc.subprocess, "Popen", boom_popen)
    monkeypatch.setattr(bc.webbrowser, "open", lambda url: False)
    result = bc._open_native("example.com", "unknownbrowser")
    assert "Could not open a browser" in result


# ── _SessionRegistry ──────────────────────────────────────────────────────────
class FakeSession:
    instances = []

    def __init__(self, browser_name):
        self.browser_name = browser_name
        self.started = False
        self.closed = False
        FakeSession.instances.append(self)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def run(self, coro, timeout=60):
        return f"ran:{self.browser_name}"

    def __getattr__(self, name):
        # Any session method (go_to, click, type_text, ...) just needs to
        # produce *something* to pass to run() — run() itself ignores it here.
        return lambda *a, **k: None


@pytest.fixture(autouse=True)
def clear_fake_sessions():
    FakeSession.instances.clear()
    yield
    FakeSession.instances.clear()


def test_registry_has_reports_active_sessions(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    assert reg.has() is False
    reg.get("chrome")
    assert reg.has() is True
    assert reg.has("chrome") is True
    assert reg.has("firefox") is False


def test_registry_note_and_pop_native_url_is_consumed_once():
    reg = bc._SessionRegistry()
    reg.note_native_url("https://x.com")
    assert reg.pop_native_url() == "https://x.com"
    assert reg.pop_native_url() == ""


def test_registry_get_reuses_existing_session(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    s1 = reg.get("chrome")
    s2 = reg.get("chrome")
    assert s1 is s2
    assert len(FakeSession.instances) == 1
    assert s1.started is True


def test_registry_get_resolves_aliases_and_sets_active(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    reg.get("Google Chrome")
    assert reg._active_browser == "chrome"


def test_registry_get_defaults_to_detected_browser(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    monkeypatch.setattr(bc, "_detect_default_browser", lambda: "edge")
    reg = bc._SessionRegistry()
    reg.get(None)
    assert reg._active_browser == "edge"


def test_registry_switch_creates_session_and_sets_active(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    result = reg.switch("firefox")
    assert "firefox" in result
    assert reg._active_browser == "firefox"


def test_registry_close_one_closes_and_clears_active(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    reg.get("chrome")
    result = reg.close_one("chrome")
    assert "closed" in result
    assert reg._active_browser == ""
    assert FakeSession.instances[0].closed is True


def test_registry_close_one_reports_missing_session():
    reg = bc._SessionRegistry()
    assert "No active session" in reg.close_one("ghost")


def test_registry_close_all(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    reg.get("chrome")
    reg.get("firefox")
    result = reg.close_all()
    assert "chrome" in result and "firefox" in result
    assert all(s.closed for s in FakeSession.instances)
    assert reg.has() is False


def test_registry_list_sessions_marks_active(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    reg = bc._SessionRegistry()
    reg.get("chrome")
    result = reg.list_sessions()
    assert "chrome" in result and "active" in result


def test_registry_list_sessions_empty():
    reg = bc._SessionRegistry()
    assert reg.list_sessions() == "No active browser sessions."


# ── browser_control(): Dispatcher ─────────────────────────────────────────────
def test_dispatcher_switch_requires_a_target():
    result = bc.browser_control({"action": "switch"})
    assert "Please specify a browser" in result


def test_dispatcher_switch_delegates_to_registry(monkeypatch):
    monkeypatch.setattr(bc._registry, "switch", lambda name: f"switched:{name}")
    assert bc.browser_control({"action": "switch", "browser": "edge"}) == "switched:edge"


def test_dispatcher_list_browsers(monkeypatch):
    monkeypatch.setattr(bc._registry, "list_sessions", lambda: "listing")
    assert bc.browser_control({"action": "list_browsers"}) == "listing"


def test_dispatcher_close_all(monkeypatch):
    monkeypatch.setattr(bc._registry, "close_all", lambda: "all closed")
    assert bc.browser_control({"action": "close_all"}) == "all closed"


def test_dispatcher_close_uses_active_browser_when_none_given(monkeypatch):
    bc._registry._active_browser = "chrome"
    monkeypatch.setattr(bc._registry, "close_one", lambda name: f"closed:{name}")
    assert bc.browser_control({"action": "close"}) == "closed:chrome"


def test_dispatcher_close_without_any_browser_reports_none():
    result = bc.browser_control({"action": "close"})
    assert "No browser specified" in result


def test_dispatcher_go_to_uses_native_open_when_no_session(monkeypatch):
    monkeypatch.setattr(bc, "_open_native", lambda url, browser: f"Opened: {url}")
    result = bc.browser_control({"action": "go_to", "url": "example.com"})
    assert result == "Opened: example.com"
    assert bc._registry.pop_native_url() == "https://example.com"


def test_dispatcher_search_builds_engine_url_natively(monkeypatch):
    seen = {}
    monkeypatch.setattr(bc, "_open_native", lambda url, browser: seen.setdefault("url", url) or f"Opened: {url}")
    bc.browser_control({"action": "search", "query": "katzen bilder", "engine": "bing"})
    assert seen["url"] == "https://www.bing.com/search?q=katzen+bilder"


def test_dispatcher_go_to_continues_in_existing_session(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    bc._registry.get("chrome")  # pre-existing session
    result = bc.browser_control({"action": "go_to", "url": "example.com", "browser": "chrome"})
    assert result == "ran:chrome"


def test_dispatcher_interactive_action_starts_a_session(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    result = bc.browser_control({"action": "click", "text": "Login"})
    assert result == "ran:chrome" or result.startswith("ran:")


def test_dispatcher_interactive_action_resumes_last_native_url(monkeypatch):
    monkeypatch.setattr(bc, "_BrowserSession", FakeSession)
    bc._registry.note_native_url("https://x.com")
    bc.browser_control({"action": "get_text"})
    session = FakeSession.instances[0]
    assert session.started is True


def test_dispatcher_unknown_interactive_action():
    result = bc.browser_control({"action": "levitate"})
    assert "Unknown browser action" in result


def test_dispatcher_session_start_failure_is_reported(monkeypatch):
    def boom(name):
        raise RuntimeError("kein Playwright")

    monkeypatch.setattr(bc._registry, "get", boom)
    result = bc.browser_control({"action": "click"})
    assert "Could not start browser session" in result


def test_dispatcher_logs_to_player(monkeypatch):
    monkeypatch.setattr(bc._registry, "list_sessions", lambda: "listing")
    player = FakePlayer()
    bc.browser_control({"action": "list_browsers"}, player=player)
    assert player.logs


def test_tool_declaration_shape():
    assert bc.TOOL["name"] == "browser_control"
    assert bc.TOOL["handler"] is bc.browser_control
