"""Tests für actions/flight_finder.py — Datumsparsing, Formatierung, Suchablauf (gemockt)."""
import json
import sys
import types
from datetime import datetime, timedelta

import pytest

from actions import flight_finder as ff


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "API_CONFIG_PATH", tmp_path / "api_keys.json")
    yield


def _configure(**kv):
    ff.API_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ff.API_CONFIG_PATH.write_text(json.dumps(kv), encoding="utf-8")


def _install_fake_genai(monkeypatch, text_response):
    class FakeResponse:
        text = text_response

    class FakeModels:
        def generate_content(self, model, contents=None, config=None):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    google_pkg = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = FakeClient
    genai_types_mod = types.ModuleType("google.genai.types")
    genai_types_mod.GenerateContentConfig = lambda **kw: kw
    genai_mod.types = genai_types_mod
    google_pkg.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types_mod)


# ── _parse_date ──────────────────────────────────────────────────────────────
def test_parse_date_passes_through_iso_format():
    assert ff._parse_date("2026-03-15") == "2026-03-15"


@pytest.mark.parametrize("raw,fmt", [
    ("15/03/2026", "%d/%m/%Y"), ("15.03.2026", "%d.%m.%Y"), ("15-03-2026", "%d-%m-%Y"),
])
def test_parse_date_accepts_common_formats(raw, fmt):
    assert ff._parse_date(raw) == datetime.strptime(raw, fmt).strftime("%Y-%m-%d")


def test_parse_date_today():
    assert ff._parse_date("today") == datetime.now().strftime("%Y-%m-%d")


def test_parse_date_tomorrow():
    expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert ff._parse_date("tomorrow") == expected


def test_parse_date_uses_gemini_for_free_text(monkeypatch):
    _configure(gemini_api_key="k")
    _install_fake_genai(monkeypatch, "2026-12-24")
    assert ff._parse_date("Christmas Eve") == "2026-12-24"


def test_parse_date_falls_back_to_month_name_matching_when_gemini_fails(monkeypatch):
    _configure(gemini_api_key="k")
    _install_fake_genai(monkeypatch, "kein datum erkennbar")
    result = ff._parse_date("march 15")
    assert result.endswith("-03-15")


def test_parse_date_falls_back_to_today_as_last_resort(monkeypatch):
    _configure(gemini_api_key="k")
    _install_fake_genai(monkeypatch, "nonsense")
    result = ff._parse_date("gibberish input xyz")
    assert result == datetime.now().strftime("%Y-%m-%d")


# ── _build_google_flights_url ──────────────────────────────────────────────────
def test_build_url_includes_route_and_date():
    url = ff._build_google_flights_url("NYC", "LHR", "2026-03-15")
    assert "NYC" in url and "LHR" in url and "2026-03-15" in url
    assert "cabin=1" in url  # economy default


def test_build_url_includes_return_date_when_round_trip():
    url = ff._build_google_flights_url("NYC", "LHR", "2026-03-15", return_date="2026-03-22")
    assert "returning" in url and "2026-03-22" in url


@pytest.mark.parametrize("cabin,code", [("economy", "1"), ("premium", "2"), ("business", "3"), ("first", "4"), ("unknown", "1")])
def test_build_url_maps_cabin_codes(cabin, code):
    url = ff._build_google_flights_url("A", "B", "2026-01-01", cabin=cabin)
    assert f"cabin={code}" in url


# ── _format_spoken ─────────────────────────────────────────────────────────────
def test_format_spoken_reports_no_flights_found():
    result = ff._format_spoken([], "NYC", "LHR", "2026-03-15")
    assert "couldn't find any flights" in result


def test_format_spoken_lists_options_and_cheapest():
    flights = [
        {"airline": "AirA", "departure": "10:00", "arrival": "14:00", "duration": "4h", "stops": 0, "price": "500", "currency": "USD"},
        {"airline": "AirB", "departure": "11:00", "arrival": "16:00", "stops": 1, "price": "300", "currency": "USD"},
    ]
    result = ff._format_spoken(flights, "NYC", "LHR", "2026-03-15")
    assert "AirA" in result and "AirB" in result
    assert "non-stop" in result and "1 stop" in result
    assert "cheapest option is AirB" in result


# ── _format_text_report ────────────────────────────────────────────────────────
def test_format_text_report_includes_route_and_flights():
    flights = [{"airline": "AirA", "departure": "10:00", "arrival": "14:00", "duration": "4h", "stops": 0, "price": "500", "currency": "USD"}]
    report = ff._format_text_report(flights, "NYC", "LHR", "2026-03-15", None, "http://x")
    assert "NYC → LHR" in report and "AirA" in report and "Non-stop" in report


def test_format_text_report_no_flights():
    report = ff._format_text_report([], "NYC", "LHR", "2026-03-15", None, "http://x")
    assert "No flights found." in report


# ── flight_finder(): Eingabevalidierung ────────────────────────────────────────
def test_flight_finder_requires_origin_and_destination():
    assert "origin and destination" in ff.flight_finder({"date": "today"})


def test_flight_finder_requires_date():
    assert "departure date" in ff.flight_finder({"origin": "NYC", "destination": "LHR"})


def test_flight_finder_normalizes_invalid_cabin_to_economy(monkeypatch):
    seen = {}
    monkeypatch.setattr(ff, "_parse_date", lambda raw: "2026-03-15")
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a, **k: (seen.setdefault("cabin", a[-1]), ("Seiteninhalt", "http://x"))[1])
    monkeypatch.setattr(ff, "_parse_flights_with_gemini", lambda *a, **k: [])
    ff.flight_finder({"origin": "NYC", "destination": "LHR", "date": "today", "cabin": "luxury"})
    assert seen["cabin"] == "economy"


# ── flight_finder(): voller Ablauf (Browser + Gemini gemockt) ─────────────────
def test_flight_finder_returns_spoken_summary(monkeypatch):
    monkeypatch.setattr(ff, "_parse_date", lambda raw: "2026-03-15")
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a, **k: ("Seiteninhalt", "http://x"))
    monkeypatch.setattr(ff, "_parse_flights_with_gemini", lambda *a, **k: [
        {"airline": "AirA", "departure": "10:00", "arrival": "14:00", "stops": 0, "price": "500", "currency": "USD"}])
    spoken = []
    result = ff.flight_finder({"origin": "NYC", "destination": "LHR", "date": "today"}, speak=spoken.append)
    assert "AirA" in result
    assert any("Searching flights" in s for s in spoken)


def test_flight_finder_reports_failure_when_page_has_no_text(monkeypatch):
    monkeypatch.setattr(ff, "_parse_date", lambda raw: "2026-03-15")
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a, **k: ("", "http://x"))
    result = ff.flight_finder({"origin": "NYC", "destination": "LHR", "date": "today"})
    assert "Could not retrieve flight data" in result


def test_flight_finder_saves_report_when_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(ff, "_parse_date", lambda raw: "2026-03-15")
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a, **k: ("Seiteninhalt", "http://x"))
    monkeypatch.setattr(ff, "_parse_flights_with_gemini", lambda *a, **k: [{"airline": "AirA", "price": "500"}])
    monkeypatch.setattr(ff.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(ff.subprocess, "Popen", lambda *a, **k: None)
    result = ff.flight_finder({"origin": "NYC", "destination": "LHR", "date": "today", "save": True})
    assert "Results saved to Desktop" in result
    assert list((tmp_path / "Desktop").glob("flights_*.txt"))


def test_flight_finder_catches_unexpected_exceptions(monkeypatch):
    monkeypatch.setattr(ff, "_parse_date", lambda raw: "2026-03-15")

    def boom(*a, **k):
        raise RuntimeError("Browser abgestürzt")

    monkeypatch.setattr(ff, "_search_flights_browser", boom)
    result = ff.flight_finder({"origin": "NYC", "destination": "LHR", "date": "today"})
    assert "Flight search failed" in result and "Browser abgestürzt" in result


def test_tool_declaration_shape():
    assert ff.TOOL["name"] == "flight_finder"
    assert ff.TOOL["handler"] is ff.flight_finder
