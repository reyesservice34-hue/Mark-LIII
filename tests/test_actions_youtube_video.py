"""Tests für actions/youtube_video.py — URL-Parsing, Scraping (requests gemockt), Dispatch."""
import sys
import types

import pytest

from actions import youtube_video as yt


class FakeResponse:
    def __init__(self, text=""):
        self.text = text


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── _extract_video_id / _is_valid_youtube_url ────────────────────────────────
@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"),
    ("https://youtu.be/abcdefghijk", "abcdefghijk"),
    ("https://www.youtube.com/embed/abcdefghijk", "abcdefghijk"),
    ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
    ("not a youtube url", None),
])
def test_extract_video_id(url, expected):
    assert yt._extract_video_id(url) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=x", True),
    ("https://youtu.be/x", True),
    ("https://example.com", False),
    ("", False),
])
def test_is_valid_youtube_url(url, expected):
    assert yt._is_valid_youtube_url(url) is expected


# ── _scrape_first_video_url ────────────────────────────────────────────────────
def test_scrape_first_video_url_skips_shorts_and_dedupes(monkeypatch):
    html = (
        '"videoId":"shortsvid1"'
        '/shorts/shortsvid1'
        '"videoId":"realvideo11"'
        '"videoId":"realvideo11"'
    )
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: FakeResponse(html))
    result = yt._scrape_first_video_url("katzen")
    assert result == "https://www.youtube.com/watch?v=realvideo11"


def test_scrape_first_video_url_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: FakeResponse(""))
    assert yt._scrape_first_video_url("katzen") is None


def test_scrape_first_video_url_returns_none_on_exception(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert yt._scrape_first_video_url("katzen") is None


# ── _open_url ──────────────────────────────────────────────────────────────────
def test_open_url_uses_xdg_open_on_linux(monkeypatch):
    monkeypatch.setattr(yt, "is_mac", lambda: False)
    monkeypatch.setattr(yt, "is_linux", lambda: True)
    calls = []
    monkeypatch.setattr(yt.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    yt._open_url("http://x")
    assert calls == [["xdg-open", "http://x"]]


def test_open_url_uses_open_on_mac(monkeypatch):
    monkeypatch.setattr(yt, "is_mac", lambda: True)
    monkeypatch.setattr(yt, "is_linux", lambda: False)
    calls = []
    monkeypatch.setattr(yt.subprocess, "Popen", lambda cmd, **k: calls.append(cmd))
    yt._open_url("http://x")
    assert calls == [["open", "http://x"]]


def test_open_url_swallows_exceptions(monkeypatch):
    monkeypatch.setattr(yt, "is_mac", lambda: False)
    monkeypatch.setattr(yt, "is_linux", lambda: True)
    monkeypatch.setattr(yt.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    yt._open_url("http://x")  # must not raise


# ── _scrape_video_info ──────────────────────────────────────────────────────────
def test_scrape_video_info_parses_and_formats_fields(monkeypatch):
    html = (
        '"title":{"runs":[{"text":"Ein Titel"}'
        '"ownerChannelName":"Ein Kanal"'
        '"viewCount":"1234567"'
        '"lengthSeconds":"125"'
        '"label":"9,876 likes"'
    )
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: FakeResponse(html))
    info = yt._scrape_video_info("abcdefghijk")
    assert info["title"] == "Ein Titel"
    assert info["channel"] == "Ein Kanal"
    assert info["views"] == "1,234,567"
    assert info["duration"] == "2:05"
    assert info["likes"] == "9,876 likes"


def test_scrape_video_info_returns_empty_on_exception(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert yt._scrape_video_info("abcdefghijk") == {}


# ── _scrape_trending ─────────────────────────────────────────────────────────
def test_scrape_trending_filters_short_titles_dedupes_and_caps(monkeypatch):
    titles = ["OK", "Titel Eins Lang Genug", "Titel Eins Lang Genug", "Titel Zwei Lang Genug"]
    html = "".join(f'"title":{{"runs":[{{"text":"{t}"}}]}}' for t in titles)
    html += '"ownerText":{"runs":[{"text":"KanalA"}]}"ownerText":{"runs":[{"text":"KanalB"}]}'
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: FakeResponse(html))
    results = yt._scrape_trending(region="TR", max_results=1)
    # "OK" (index 0) is filtered out for being too short; the first accepted
    # title is at its original index 1, which pairs with channels[1] = "KanalB".
    assert results == [{"rank": 1, "title": "Titel Eins Lang Genug", "channel": "KanalB"}]


def test_scrape_trending_returns_empty_list_on_exception(monkeypatch):
    monkeypatch.setattr(yt.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert yt._scrape_trending() == []


# ── _handle_play ───────────────────────────────────────────────────────────────
def test_handle_play_requires_a_query():
    assert "what you'd like to watch" in yt._handle_play({}, None)


def test_handle_play_opens_scraped_video(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_first_video_url", lambda q: "https://www.youtube.com/watch?v=x")
    opened = []
    monkeypatch.setattr(yt, "_open_url", lambda url: opened.append(url))
    player = FakePlayer()
    result = yt._handle_play({"query": "katzen"}, player)
    assert result == "Playing: katzen"
    assert opened == ["https://www.youtube.com/watch?v=x"]
    assert any("katzen" in l for l in player.logs)


def test_handle_play_falls_back_to_search_page_when_scrape_fails(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_first_video_url", lambda q: None)
    opened = []
    monkeypatch.setattr(yt, "_open_url", lambda url: opened.append(url))
    result = yt._handle_play({"query": "katzen"}, None)
    assert "manual selection required" in result
    assert opened and "search_query" in opened[0]


# ── _handle_summarize ──────────────────────────────────────────────────────────
def test_handle_summarize_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", False)
    result = yt._handle_summarize({}, None, None)
    assert "youtube-transcript-api is not installed" in result


def test_handle_summarize_cancelled_without_url(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": None)
    result = yt._handle_summarize({}, None, None)
    assert "cancelled" in result


def test_handle_summarize_rejects_invalid_url(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": "https://example.com")
    result = yt._handle_summarize({}, None, None)
    assert "valid YouTube URL" in result


def test_handle_summarize_reports_missing_transcript(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": "https://youtu.be/abcdefghijk")
    monkeypatch.setattr(yt, "_get_transcript", lambda vid: None)
    result = yt._handle_summarize({}, None, None)
    assert "couldn't retrieve a transcript" in result


def test_handle_summarize_returns_summary_and_speaks(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": "https://youtu.be/abcdefghijk")
    monkeypatch.setattr(yt, "_get_transcript", lambda vid: "ein langes transkript")
    monkeypatch.setattr(yt, "_summarize_with_gemini", lambda transcript, url: "Kurze Zusammenfassung")
    spoken = []
    result = yt._handle_summarize({}, None, spoken.append)
    assert result == "Kurze Zusammenfassung"
    assert any("Zusammenfassung" in s or "Transcript" in s for s in spoken)


def test_handle_summarize_saves_when_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": "https://youtu.be/abcdefghijk")
    monkeypatch.setattr(yt, "_get_transcript", lambda vid: "transkript")
    monkeypatch.setattr(yt, "_summarize_with_gemini", lambda transcript, url: "Zusammenfassung")
    monkeypatch.setattr(yt.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(yt.subprocess, "Popen", lambda *a, **k: None)
    result = yt._handle_summarize({"save": True}, None, None)
    assert "saved to Desktop" in result
    assert list((tmp_path / "Desktop").glob("youtube_summary_*.txt"))


def test_handle_summarize_reports_gemini_failure(monkeypatch):
    monkeypatch.setattr(yt, "_TRANSCRIPT_OK", True)
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": "https://youtu.be/abcdefghijk")
    monkeypatch.setattr(yt, "_get_transcript", lambda vid: "transkript")

    def boom(transcript, url):
        raise RuntimeError("api kaputt")

    monkeypatch.setattr(yt, "_summarize_with_gemini", boom)
    result = yt._handle_summarize({}, None, None)
    assert "Summary generation failed" in result and "api kaputt" in result


# ── _handle_get_info ────────────────────────────────────────────────────────────
def test_handle_get_info_requires_valid_url(monkeypatch):
    monkeypatch.setattr(yt, "_ask_for_url", lambda prompt_text="...": None)
    result = yt._handle_get_info({}, None, None)
    assert "valid YouTube URL" in result


def test_handle_get_info_returns_formatted_info(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_video_info", lambda vid: {"title": "T", "channel": "C", "views": "1,000"})
    spoken = []
    result = yt._handle_get_info({"url": "https://youtu.be/abcdefghijk"}, None, spoken.append)
    assert "Title: T" in result and "Channel: C" in result
    assert spoken


def test_handle_get_info_reports_when_nothing_found(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_video_info", lambda vid: {})
    result = yt._handle_get_info({"url": "https://youtu.be/abcdefghijk"}, None, None)
    assert "Could not retrieve video information" in result


# ── _handle_trending ────────────────────────────────────────────────────────────
def test_handle_trending_reports_results_and_speaks_top3(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_trending", lambda region="TR", max_results=8: [
        {"rank": 1, "title": "A", "channel": "X"}, {"rank": 2, "title": "B", "channel": "Y"}])
    spoken = []
    result = yt._handle_trending({"region": "us"}, None, spoken.append)
    assert "Top trending videos in US" in result
    assert spoken and "A" in spoken[0]


def test_handle_trending_reports_failure(monkeypatch):
    monkeypatch.setattr(yt, "_scrape_trending", lambda region="TR", max_results=8: [])
    result = yt._handle_trending({}, None, None)
    assert "Could not fetch trending videos" in result


# ── youtube_video(): Dispatcher ─────────────────────────────────────────────────
def test_youtube_video_unknown_action():
    result = yt.youtube_video({"action": "flyby"})
    assert "Unknown YouTube action" in result


def test_youtube_video_routes_to_play(monkeypatch):
    # _ACTION_MAP binds the original function objects at module load time, so
    # patching yt._handle_play itself would not affect dispatch — patch the map.
    monkeypatch.setitem(yt._ACTION_MAP, "play", lambda params, player: "gespielt")
    assert yt.youtube_video({"action": "play", "query": "x"}) == "gespielt"


def test_youtube_video_routes_to_summarize_with_speak(monkeypatch):
    monkeypatch.setitem(yt._ACTION_MAP, "summarize", lambda params, player, speak: "zusammengefasst")
    assert yt.youtube_video({"action": "summarize"}) == "zusammengefasst"


def test_youtube_video_catches_handler_exceptions(monkeypatch):
    def boom(params, player):
        raise RuntimeError("kaputt")

    monkeypatch.setitem(yt._ACTION_MAP, "play", boom)
    result = yt.youtube_video({"action": "play"})
    assert "YouTube play failed" in result and "kaputt" in result


def test_youtube_video_defaults_to_play_action(monkeypatch):
    monkeypatch.setitem(yt._ACTION_MAP, "play", lambda params, player: "gespielt")
    assert yt.youtube_video({}) == "gespielt"


def test_tool_declaration_shape():
    assert yt.TOOL["name"] == "youtube_video"
    assert yt.TOOL["handler"] is yt.youtube_video
