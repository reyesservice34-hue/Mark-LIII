"""Tests für actions/web_search.py — Gemini-Suche mit DDG-Fallback über mehrere Modi."""
import sys
import types

import pytest

from actions import web_search as ws


def _install_fake_genai(monkeypatch, generate_content):
    """Stubs `from google import genai` (the package isn't installed in this
    sandbox) with a fake Client whose models.generate_content is `generate_content`."""

    class FakeModels:
        def generate_content(self, model, contents, config):
            return generate_content(model, contents, config)

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = FakeClient
    google_pkg.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── Formatierung ─────────────────────────────────────────────────────────────
def test_format_ddg_empty_results():
    assert ws._format_ddg("katzen", []) == "No results found for: katzen"


def test_format_ddg_includes_title_snippet_and_url():
    results = [{"title": "T1", "snippet": "S1", "url": "http://x"}]
    out = ws._format_ddg("q", results)
    assert "T1" in out and "S1" in out and "http://x" in out


def test_format_news_empty_results():
    assert ws._format_news("formel1", []) == "No news found for: formel1"


def test_format_news_skips_entries_without_title():
    results = [{"title": "", "snippet": "irrelevant"}, {"title": "Echt", "source": "dpa", "snippet": "x" * 200}]
    out = ws._format_news("q", results)
    assert "irrelevant" not in out
    assert "Echt" in out and "[dpa]" in out
    # snippet truncated to 140 chars
    assert "x" * 141 not in out


# ── _gemini_headlines: Parsing-Logik ──────────────────────────────────────────
def _fake_genai_response(text: str):
    class Part:
        def __init__(self, t):
            self.text = t

    class Content:
        def __init__(self, t):
            self.parts = [Part(t)]

    class Candidate:
        def __init__(self, t):
            self.content = Content(t)

    class Response:
        def __init__(self, t):
            self.candidates = [Candidate(t)]

    return Response(text)


def test_gemini_headlines_parses_numbered_lines_only(monkeypatch):
    raw = (
        "Here are today's headlines:\n"
        "1. Erste wichtige Meldung heute\n"
        "2) Zweite Meldung mit genug Zeichen\n"
        "3- Dritte Meldung ist auch lang genug\n"
        "Some closing remark that is not numbered\n"
    )

    _install_fake_genai(monkeypatch, lambda model, contents, config: _fake_genai_response(raw))
    monkeypatch.setattr(ws, "_get_api_key", lambda: "k")

    headlines, text = ws._gemini_headlines(n=5)
    assert headlines == [
        "Erste wichtige Meldung heute",
        "Zweite Meldung mit genug Zeichen",
        "Dritte Meldung ist auch lang genug",
    ]
    assert "closing remark" in text


def test_gemini_headlines_respects_n_limit(monkeypatch):
    raw = "\n".join(f"{i}. Meldung Nummer {i} mit Text" for i in range(1, 10))

    _install_fake_genai(monkeypatch, lambda model, contents, config: _fake_genai_response(raw))
    monkeypatch.setattr(ws, "_get_api_key", lambda: "k")

    headlines, _ = ws._gemini_headlines(n=3)
    assert len(headlines) == 3


# ── _search ──────────────────────────────────────────────────────────────────
def test_search_uses_gemini_when_available(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: "Gemini-Antwort")
    assert ws._search("katzen") == "Gemini-Antwort"


def test_search_falls_back_to_ddg_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=6: [{"title": "T", "snippet": "S", "url": "u"}])
    result = ws._search("katzen")
    assert "T" in result


# ── _news ────────────────────────────────────────────────────────────────────
def test_news_returns_first_valid_result(monkeypatch):
    # _store() only accepts results longer than 60 chars, so the fake needs real length.
    long_result = "Ausreichend lange Gemini-Nachricht über das Thema heute, mit genug Zeichen."
    monkeypatch.setattr(ws, "_gemini_search", lambda q: long_result)
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: [])
    result = ws._news("Formel 1")
    assert "Gemini-Nachricht" in result


def test_news_falls_back_to_ddg_when_gemini_result_too_short(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: "kurz")  # <= 60 chars: rejected
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: [
        {"title": "DDG Nachricht", "source": "dpa", "snippet": "Ausführliche Zusammenfassung der Meldung mit genug Zeichen."},
    ])
    result = ws._news("Formel 1")
    assert "DDG Nachricht" in result


def test_news_returns_no_news_found_when_both_fail(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: (_ for _ in ()).throw(RuntimeError("y")))
    result = ws._news("Formel 1")
    assert result == "No news found for: Formel 1"


# ── _research / _price / _compare ─────────────────────────────────────────────
def test_research_uses_gemini_with_context_prompt(monkeypatch):
    seen = {}

    def fake_gemini(q):
        seen["q"] = q
        return "Antwort"

    monkeypatch.setattr(ws, "_gemini_search", fake_gemini)
    result = ws._research("Quantencomputer")
    assert result == "Antwort"
    assert "Comprehensive" in seen["q"] and "Quantencomputer" in seen["q"]


def test_research_falls_back_to_ddg(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=10: [{"title": "T"}])
    result = ws._research("Quantencomputer")
    assert "T" in result


def test_price_builds_price_specific_query(monkeypatch):
    seen = {}

    def fake_gemini(q):
        seen["q"] = q
        return "42 EUR"

    monkeypatch.setattr(ws, "_gemini_search", fake_gemini)
    result = ws._price("iPhone 17")
    assert result == "42 EUR"
    assert "current price of iPhone 17" in seen["q"]


def test_compare_uses_gemini_first(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: "Vergleichsergebnis")
    assert ws._compare(["A", "B"], "Preis") == "Vergleichsergebnis"


def test_compare_falls_back_to_per_item_ddg_search(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("x")))

    def fake_ddg(q, max_results=3):
        return [{"snippet": f"Info über {q}", "url": "http://x"}]

    monkeypatch.setattr(ws, "_ddg_search", fake_ddg)
    result = ws._compare(["Handy A", "Handy B"], "Kamera")
    assert "Handy A" in result and "Handy B" in result
    assert "KAMERA" in result


def test_compare_handles_ddg_failure_per_item(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=3: (_ for _ in ()).throw(RuntimeError("y")))
    result = ws._compare(["A"], "Preis")
    assert "▸ A" in result


# ── web_search(): Dispatcher ───────────────────────────────────────────────────
def test_web_search_requires_query_or_items():
    assert ws.web_search({}) == "Please provide a search query."


def test_web_search_default_mode_calls_search(monkeypatch):
    monkeypatch.setattr(ws, "_search", lambda q: f"search:{q}")
    assert ws.web_search({"query": "katzen"}) == "search:katzen"


@pytest.mark.parametrize("mode,fn", [("news", "_news"), ("research", "_research"), ("price", "_price")])
def test_web_search_routes_to_correct_mode(monkeypatch, mode, fn):
    monkeypatch.setattr(ws, fn, lambda q: f"{fn}:{q}")
    assert ws.web_search({"query": "x", "mode": mode}) == f"{fn}:x"


def test_web_search_items_forces_compare_mode(monkeypatch):
    monkeypatch.setattr(ws, "_compare", lambda items, aspect: f"{items}:{aspect}")
    result = ws.web_search({"items": ["A", "B"], "aspect": "preis"})
    assert result == "['A', 'B']:preis"


def test_web_search_logs_to_player(monkeypatch):
    monkeypatch.setattr(ws, "_search", lambda q: "ok")
    player = FakePlayer()
    ws.web_search({"query": "katzen"}, player=player)
    assert any("katzen" in l for l in player.logs)


def test_web_search_catches_exceptions_from_all_backends(monkeypatch):
    monkeypatch.setattr(ws, "_search", lambda q: (_ for _ in ()).throw(RuntimeError("alles kaputt")))
    result = ws.web_search({"query": "katzen"})
    assert "Search failed" in result and "alles kaputt" in result


def test_tool_declaration_shape():
    assert ws.TOOL["name"] == "web_search"
    assert ws.TOOL["handler"] is ws.web_search
