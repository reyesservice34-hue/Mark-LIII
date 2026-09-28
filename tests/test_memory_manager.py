"""Tests für memory/memory_manager.py (Langzeitgedächtnis, Recall, Prompt-Block)."""
import json

import pytest

from memory import memory_manager as mm


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "MEMORY_PATH", tmp_path / "long_term.json")
    # Der Remote-Spiegel muss in Tests immer aus sein, unabhängig davon, ob
    # config/api_keys.json im Environment zufällig knowledge_host gesetzt hat.
    monkeypatch.setattr("core.knowledge_client.is_enabled", lambda: False)
    mm.set_trim_notifier(None)
    yield


def _write_raw(data: dict) -> None:
    mm.MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    mm.MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── load_memory / save_memory ──────────────────────────────────────────────
def test_load_memory_returns_empty_categories_when_file_missing():
    mem = mm.load_memory()
    assert mem == {"identity": {}, "preferences": {}, "projects": {}, "relationships": {}, "wishes": {}, "notes": {}}


def test_load_memory_fills_in_missing_categories():
    _write_raw({"identity": {"name": {"value": "Christoph", "updated": "2026-01-01"}}})
    mem = mm.load_memory()
    assert mem["identity"]["name"]["value"] == "Christoph"
    assert mem["preferences"] == {}


def test_load_memory_recovers_from_corrupt_json():
    mm.MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    mm.MEMORY_PATH.write_text("{ kaputt", encoding="utf-8")
    assert mm.load_memory() == mm._empty_memory()


def test_load_memory_recovers_when_json_is_not_an_object():
    mm.MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    mm.MEMORY_PATH.write_text("[1, 2, 3]", encoding="utf-8")
    assert mm.load_memory() == mm._empty_memory()


def test_save_memory_ignores_non_dict_input():
    mm.save_memory("kein dict")
    assert not mm.MEMORY_PATH.exists()


def test_save_memory_round_trips_to_disk():
    mem = mm._empty_memory()
    mem["notes"]["idee"] = {"value": "Garage aufräumen", "updated": "2026-01-01"}
    mm.save_memory(mem)
    assert mm.load_memory()["notes"]["idee"]["value"] == "Garage aufräumen"


def test_save_memory_trims_oldest_entries_when_over_the_char_limit(monkeypatch):
    trims = []
    mm.set_trim_notifier(trims.append)
    monkeypatch.setattr(mm, "MEMORY_MAX_CHARS", 5)
    mem = mm._empty_memory()
    mem["notes"]["alt"] = {"value": "x", "updated": "2020-01-01"}
    mem["notes"]["neu"] = {"value": "y", "updated": "2026-01-01"}
    mm.save_memory(mem)
    on_disk = mm.load_memory()
    assert "alt" not in on_disk["notes"]
    assert trims and "alt" in trims[0]


# ── update_memory / _recursive_update ──────────────────────────────────────
def test_update_memory_with_empty_input_returns_current_memory_unchanged():
    mm.update_memory({"notes": {"a": {"value": "1"}}})
    before = mm.MEMORY_PATH.read_text(encoding="utf-8")
    result = mm.update_memory({})
    assert mm.MEMORY_PATH.read_text(encoding="utf-8") == before
    assert result["notes"]["a"]["value"] == "1"


def test_update_memory_sets_a_new_fact_with_todays_date():
    mm.update_memory({"identity": {"name": "Christoph"}})
    entry = mm.load_memory()["identity"]["name"]
    assert entry["value"] == "Christoph"
    assert entry["updated"]


def test_update_memory_skips_none_and_blank_string_values():
    mm.update_memory({"notes": {"a": None, "b": "   "}})
    mem = mm.load_memory()
    assert mem["notes"] == {}


def test_update_memory_merges_nested_sub_categories():
    mm.update_memory({"relationships": {"schwester": {"name": "Ayse"}}})
    mem = mm.load_memory()
    assert mem["relationships"]["schwester"]["name"]["value"] == "Ayse"


def test_update_memory_truncates_long_values():
    long_val = "x" * 500
    mm.update_memory({"notes": {"lang": long_val}})
    stored = mm.load_memory()["notes"]["lang"]["value"]
    assert len(stored) == mm.MAX_VALUE_LENGTH + 1  # +1 for the trailing ellipsis
    assert stored.endswith("…")


def test_update_memory_does_not_rewrite_when_value_is_unchanged():
    mm.update_memory({"notes": {"a": "gleich"}})
    first_write = mm.MEMORY_PATH.read_text(encoding="utf-8")
    mm.update_memory({"notes": {"a": "gleich"}})
    assert mm.MEMORY_PATH.read_text(encoding="utf-8") == first_write


# ── Legacy-Identität ────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["Jarvis", "JARVIS", "J.A.R.V.I.S", "j a r v i s", "Ich bin Jarvis."])
def test_mentions_legacy_identity_matches_variants(text):
    assert mm.mentions_legacy_identity(text) is True


def test_mentions_legacy_identity_false_for_unrelated_text():
    assert mm.mentions_legacy_identity("Ich bin MIA.") is False


def test_scrub_legacy_identity_replaces_with_mia():
    assert mm.scrub_legacy_identity("Jarvis hat geholfen") == "MIA hat geholfen"


def test_update_memory_never_stores_legacy_assistant_name():
    mm.update_memory({"identity": {"assistant_name": "Jarvis"}})
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "Jarvis" not in prompt


# ── format_memory_for_prompt ────────────────────────────────────────────────
def test_format_memory_for_prompt_empty_memory_returns_empty_string():
    assert mm.format_memory_for_prompt({}) == ""
    assert mm.format_memory_for_prompt(None) == ""


def test_format_memory_for_prompt_includes_identity_fields():
    mm.update_memory({"identity": {"name": "Christoph", "city": "Berlin"}})
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "Name: Christoph" in prompt
    assert "City: Berlin" in prompt


def test_format_memory_for_prompt_phrases_language_as_an_observation():
    mm.update_memory({"identity": {"language": "Deutsch"}})
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "Has spoken to you in: Deutsch" in prompt
    assert "CURRENT message" in prompt


def test_format_memory_for_prompt_lists_other_categories():
    mm.update_memory({"preferences": {"kaffee": "schwarz, kein Zucker"}})
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "Preferences:" in prompt
    assert "Kaffee: schwarz, kein Zucker" in prompt


def test_format_memory_for_prompt_caps_entries_per_category_and_indexes_the_rest(monkeypatch):
    monkeypatch.setattr(mm, "PROMPT_MAX_PER_CATEGORY", 2)
    for i in range(5):
        mm.update_memory({"notes": {f"n{i}": f"wert{i}"}})
    prompt = mm.format_memory_for_prompt(mm.load_memory())
    assert "ALSO REMEMBERED" in prompt


# ── search_memory ────────────────────────────────────────────────────────────
def test_search_memory_on_empty_store_says_nothing_stored_yet():
    assert "have not stored anything" in mm.search_memory("").lower()


def test_search_memory_empty_query_lists_everything():
    mm.update_memory({"notes": {"a": "eins"}, "preferences": {"b": "zwei"}})
    result = mm.search_memory("")
    assert "Everything currently stored:" in result
    assert "notes/a: eins" in result
    assert "preferences/b: zwei" in result


def test_search_memory_finds_by_keyword_in_key_or_value():
    mm.update_memory({"relationships": {"schwester": "Ayse wohnt in Köln"}})
    result = mm.search_memory("Ayse")
    assert "relationships/schwester: Ayse wohnt in Köln" in result


def test_search_memory_no_match_and_no_semantic_service_says_nothing_found():
    mm.update_memory({"notes": {"a": "etwas anderes"}})
    result = mm.search_memory("voellig-unbekannt")
    assert "Nothing stored about 'voellig-unbekannt'." == result


def test_search_memory_reports_overflow_count_when_limit_is_smaller_than_matches():
    mm.update_memory({"notes": {f"a{i}": "gleicher Suchbegriff" for i in range(5)}})
    result = mm.search_memory("gleicher", limit=2)
    assert result.count("gleicher Suchbegriff") == 2
    assert "+3 more" in result


def test_search_memory_uses_semantic_fallback_when_configured(monkeypatch):
    monkeypatch.setattr("core.knowledge_client.is_semantic_enabled", lambda: True)
    monkeypatch.setattr(
        "core.knowledge_client.semantic_memory_search",
        lambda query, limit=5: [{"category": "notes", "key": "auto", "text": "Der Wagen ist in der Werkstatt"}],
    )
    result = mm.search_memory("Fahrzeug")
    assert "seems related" in result
    assert "Der Wagen ist in der Werkstatt" in result


# ── all_entries_for_ui ───────────────────────────────────────────────────────
def test_all_entries_for_ui_sorted_newest_first_and_skips_empty_values():
    _write_raw({
        "notes": {
            "alt": {"value": "alt-wert", "updated": "2020-01-01"},
            "neu": {"value": "neu-wert", "updated": "2026-01-01"},
            "leer": {"value": "", "updated": "2026-01-01"},
        },
    })
    rows = mm.all_entries_for_ui()
    assert [r["key"] for r in rows] == ["neu", "alt"]


# ── remember / forget ────────────────────────────────────────────────────────
def test_remember_stores_under_given_category():
    msg = mm.remember("hobby", "Klettern", category="preferences")
    assert msg == "Remembered: preferences/hobby = Klettern"
    assert mm.load_memory()["preferences"]["hobby"]["value"] == "Klettern"


def test_remember_falls_back_to_notes_for_invalid_category():
    mm.remember("x", "y", category="does-not-exist")
    assert mm.load_memory()["notes"]["x"]["value"] == "y"


def test_forget_removes_existing_key():
    mm.remember("x", "y", category="notes")
    msg = mm.forget("x", category="notes")
    assert msg == "Forgotten: notes/x"
    assert "x" not in mm.load_memory()["notes"]


def test_forget_reports_not_found_for_missing_key():
    assert mm.forget("does-not-exist", category="notes") == "Not found: notes/does-not-exist"


def test_forget_memory_is_an_alias_for_forget():
    assert mm.forget_memory is mm.forget


# ── Sitzungszusammenfassungen ─────────────────────────────────────────────────
def test_save_session_summary_ignores_blank_summary():
    mm.save_session_summary("   ")
    assert not mm.MEMORY_PATH.exists()


def test_save_session_summary_truncates_to_280_chars():
    mm.save_session_summary("x" * 500)
    assert len(mm.recent_sessions_for_ui()[0]["summary"]) == 280


def test_save_session_summary_caps_at_max_and_keeps_most_recent():
    for i in range(mm._SESSION_MAX + 3):
        mm.save_session_summary(f"Sitzung {i}")
    summaries = [s["summary"] for s in mm.recent_sessions_for_ui()]
    assert len(summaries) == mm._SESSION_MAX
    assert summaries[0] == f"Sitzung {mm._SESSION_MAX + 2}"


def test_recent_sessions_for_ui_scrubs_legacy_identity():
    mm.save_session_summary("Jarvis hat beim Umzug geholfen")
    assert "Jarvis" not in mm.recent_sessions_for_ui()[0]["summary"]
    assert "MIA" in mm.recent_sessions_for_ui()[0]["summary"]


def test_pop_last_session_removes_and_returns_the_last_entry():
    mm.save_session_summary("Erste Sitzung")
    mm.save_session_summary("Zweite Sitzung")
    entry = mm.pop_last_session()
    assert entry["summary"] == "Zweite Sitzung"
    remaining = [s["summary"] for s in mm.recent_sessions_for_ui()]
    assert remaining == ["Erste Sitzung"]


def test_pop_last_session_returns_none_when_no_sessions():
    assert mm.pop_last_session() is None


def test_pop_last_session_returns_none_when_file_missing():
    assert mm.pop_last_session() is None
