"""Tests für actions/background_monitor.py — Themen-Überwachung via DDG-News."""
import pytest

from actions import background_monitor as bm
from actions import web_search
from memory import memory_manager as mm


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MEMORY_PATH", tmp_path / "long_term.json")
    yield


def _news(title="Neue Entwicklung", snippet="Kurzbeschreibung", source="dpa"):
    return [{"title": title, "snippet": snippet, "source": source}]


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("topic", ["Bitcoin", "ETHEREUM kurs", "krypto markt", "NFT drops"])
def test_is_blocked_matches_crypto_topics_case_insensitively(topic):
    assert bm._is_blocked(topic) is True


def test_is_blocked_false_for_normal_topic():
    assert bm._is_blocked("Formel 1") is False


def test_slug_normalises_and_truncates():
    assert bm._slug("  Formel 1 News!!  ") == "formel_1_news"
    assert len(bm._slug("x" * 100)) == 40


def test_title_hash_is_deterministic_and_short():
    h1 = bm._title_hash("Gleiche Überschrift")
    h2 = bm._title_hash("Gleiche Überschrift")
    assert h1 == h2 and len(h1) == 12


# ── add_monitor / remove_monitor / list_monitors ─────────────────────────────
def test_add_monitor_rejects_empty_topic():
    assert bm.add_monitor("   ") == "Please specify a topic to monitor."


def test_add_monitor_rejects_blocked_topic():
    assert bm.add_monitor("Bitcoin") == "I don't monitor crypto or financial topics."


def test_add_monitor_stores_and_confirms():
    result = bm.add_monitor("Formel 1")
    assert result == "Now monitoring: Formel 1"
    assert "Formel 1" in bm.list_monitors()


def test_add_monitor_reports_already_monitoring_for_duplicate_slug():
    bm.add_monitor("Formel 1")
    result = bm.add_monitor("formel 1")  # same slug, different casing
    assert result == "Already monitoring: Formel 1"


def test_remove_monitor_by_exact_match():
    bm.add_monitor("Formel 1")
    assert bm.remove_monitor("Formel 1") == "Stopped monitoring: Formel 1"
    assert bm.list_monitors() == []


def test_remove_monitor_by_partial_match():
    bm.add_monitor("Formel 1 News")
    assert bm.remove_monitor("formel 1") == "Stopped monitoring: Formel 1 News"


def test_remove_monitor_not_found():
    assert bm.remove_monitor("gibt es nicht") == "Not found in monitored topics: gibt es nicht"


def test_list_monitors_empty_initially():
    assert bm.list_monitors() == []


# ── check_all ────────────────────────────────────────────────────────────────
def test_check_all_returns_empty_when_no_monitors():
    assert bm.check_all() == []


def test_check_all_skips_topic_already_checked_today(monkeypatch):
    bm.add_monitor("Formel 1")
    monitors = bm._load()
    slug = next(iter(monitors))
    monitors[slug]["last_check"] = bm.datetime.now().strftime("%Y-%m-%d")
    bm._save(monitors)

    called = []
    monkeypatch.setattr(web_search, "_ddg_news", lambda *a, **k: called.append(1))
    assert bm.check_all() == []
    assert called == []


def test_check_all_updates_last_check_without_alert_when_no_results(monkeypatch):
    bm.add_monitor("Formel 1")
    monkeypatch.setattr(web_search, "_ddg_news", lambda *a, **k: [])
    assert bm.check_all() == []
    slug = next(iter(bm._load()))
    assert bm._load()[slug]["last_check"] == bm.datetime.now().strftime("%Y-%m-%d")


def test_check_all_alerts_on_a_new_headline(monkeypatch):
    bm.add_monitor("Formel 1")
    monkeypatch.setattr(web_search, "_ddg_news", lambda *a, **k: _news(title="Neuer Sieger"))
    alerts = bm.check_all()
    assert len(alerts) == 1
    assert "[MONITOR_ALERT] Formel 1" in alerts[0]
    assert "Neuer Sieger" in alerts[0]
    assert "dpa" in alerts[0]


def test_check_all_does_not_alert_twice_for_the_same_headline(monkeypatch):
    bm.add_monitor("Formel 1")
    monkeypatch.setattr(web_search, "_ddg_news", lambda *a, **k: _news(title="Gleiche Nachricht"))
    first = bm.check_all()
    assert len(first) == 1

    # Reset last_check so the topic is eligible again ("next day"), headline unchanged.
    monitors = bm._load()
    slug = next(iter(monitors))
    monitors[slug]["last_check"] = ""
    bm._save(monitors)

    second = bm.check_all()
    assert second == []


def test_check_all_handles_ddg_exception_without_crashing_and_retries_later(monkeypatch):
    bm.add_monitor("Formel 1")

    def boom(*a, **k):
        raise RuntimeError("DDG nicht erreichbar")

    monkeypatch.setattr(web_search, "_ddg_news", boom)
    assert bm.check_all() == []
    slug = next(iter(bm._load()))
    assert bm._load()[slug]["last_check"] == ""  # untouched, so it's retried
