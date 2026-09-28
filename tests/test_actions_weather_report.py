"""Tests für actions/weather_report.py — Gemini-Wetterbericht mit Browser-Fallback."""
import json

import pytest

from actions import weather_report as wr


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "API_CONFIG_PATH", tmp_path / "api_keys.json")
    yield


def _configure(**kv):
    wr.API_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    wr.API_CONFIG_PATH.write_text(json.dumps(kv), encoding="utf-8")


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


class FakeSessionMemory:
    def __init__(self):
        self.calls = []

    def set_last_search(self, query, response):
        self.calls.append((query, response))


# ── _get_api_key ─────────────────────────────────────────────────────────────
def test_get_api_key_reads_gemini_key_from_config():
    _configure(gemini_api_key="sk-test-123")
    assert wr._get_api_key() == "sk-test-123"


def test_get_api_key_raises_when_config_missing():
    with pytest.raises(FileNotFoundError):
        wr._get_api_key()


# ── weather_action: Eingabevalidierung ────────────────────────────────────────
def test_missing_city_is_rejected():
    player = FakePlayer()
    result = wr.weather_action({}, player=player)
    assert "city is missing" in result
    assert player.logs


def test_blank_city_is_rejected():
    result = wr.weather_action({"city": "   "})
    assert "city is missing" in result


# ── weather_action: erfolgreicher Gemini-Pfad ─────────────────────────────────
def test_successful_gemini_report_is_returned_and_logged(monkeypatch):
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: f"Sonnig in {city}, {when}.")
    session_memory = FakeSessionMemory()
    player = FakePlayer()
    result = wr.weather_action({"city": " Berlin ", "time": "morgen"}, player=player, session_memory=session_memory)
    assert result == "Sonnig in Berlin, morgen."
    assert session_memory.calls == [("weather in Berlin morgen", "Sonnig in Berlin, morgen.")]
    assert any("Berlin" in l for l in player.logs)


def test_default_time_is_today_when_unset(monkeypatch):
    seen = {}
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: seen.setdefault("when", when) or "ok")
    wr.weather_action({"city": "Berlin"})
    assert seen["when"] == "today"


def test_session_memory_failure_does_not_break_the_response(monkeypatch):
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: "Bericht")

    class BrokenMemory:
        def set_last_search(self, *a, **k):
            raise RuntimeError("boom")

    result = wr.weather_action({"city": "Berlin"}, session_memory=BrokenMemory())
    assert result == "Bericht"


# ── weather_action: Browser-Fallback ───────────────────────────────────────────
def test_gemini_failure_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(wr.webbrowser, "open", lambda url: True)
    session_memory = FakeSessionMemory()
    result = wr.weather_action({"city": "Berlin"}, session_memory=session_memory)
    assert "opened it in" in result
    assert "Berlin" in result
    assert session_memory.calls


def test_browser_fallback_reports_error_when_browser_cannot_open(monkeypatch):
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(wr.webbrowser, "open", lambda url: False)
    result = wr.weather_action({"city": "Berlin"})
    assert "couldn't get the weather" in result


def test_browser_fallback_handles_webbrowser_exception(monkeypatch):
    monkeypatch.setattr(wr, "_gemini_weather", lambda city, when: (_ for _ in ()).throw(RuntimeError("api down")))

    def raise_open(url):
        raise OSError("kein Browser")

    monkeypatch.setattr(wr.webbrowser, "open", raise_open)
    result = wr.weather_action({"city": "Berlin"})
    assert "couldn't get the weather" in result
    assert "kein Browser" in result


def test_tool_declaration_shape():
    assert wr.TOOL["name"] == "weather_report"
    assert wr.TOOL["handler"] is wr.weather_action
    assert wr.TOOL["parameters"]["required"] == ["city"]
