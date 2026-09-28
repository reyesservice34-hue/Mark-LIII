"""Tests für actions/proactive.py — ProactiveEngine (Trigger-Gate + Prompt-Builder)."""
import datetime as real_datetime

import pytest

from actions import proactive
from memory import memory_manager as mm


class FrozenDatetime(real_datetime.datetime):
    _frozen = real_datetime.datetime(2026, 1, 15, 9, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(proactive, "datetime", FrozenDatetime)
    return FrozenDatetime


@pytest.fixture(autouse=True)
def fake_memory_prompt(monkeypatch):
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda memory: "Kennt: Christoph")


# ── should_trigger / mark_triggered ───────────────────────────────────────────
def test_should_trigger_false_when_user_spoke_recently(monkeypatch):
    monkeypatch.setattr(proactive.time, "monotonic", lambda: 1000.0)
    engine = proactive.ProactiveEngine(min_silence_secs=900, check_cooldown=1200)
    assert engine.should_trigger(last_user_speech=999.0) is False


def test_should_trigger_true_after_enough_silence(monkeypatch):
    monkeypatch.setattr(proactive.time, "monotonic", lambda: 2000.0)
    engine = proactive.ProactiveEngine(min_silence_secs=900, check_cooldown=1200)
    assert engine.should_trigger(last_user_speech=0.0) is True


def test_should_trigger_false_within_cooldown_even_if_silent(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(proactive.time, "monotonic", lambda: clock["t"])
    engine = proactive.ProactiveEngine(min_silence_secs=900, check_cooldown=1200)
    engine.mark_triggered()
    clock["t"] = 1000.0  # 1000s since trigger, still < 1200s cooldown
    assert engine.should_trigger(last_user_speech=0.0) is False


def test_should_trigger_true_again_once_cooldown_passes(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(proactive.time, "monotonic", lambda: clock["t"])
    engine = proactive.ProactiveEngine(min_silence_secs=900, check_cooldown=1200)
    engine.mark_triggered()
    clock["t"] = 1300.0
    assert engine.should_trigger(last_user_speech=0.0) is True


def test_mark_triggered_advances_rotation():
    engine = proactive.ProactiveEngine()
    assert engine._rotation == 0
    engine.mark_triggered()
    assert engine._rotation == 1


# ── build_prompt(): Tageszeit ─────────────────────────────────────────────────
@pytest.mark.parametrize("hour,expected", [(7, "morning"), (14, "afternoon"), (20, "evening"), (2, "late night")])
def test_build_prompt_labels_time_of_day(monkeypatch, hour, expected):
    FrozenDatetime._frozen = real_datetime.datetime(2026, 1, 15, hour, 0)
    monkeypatch.setattr(proactive, "datetime", FrozenDatetime)
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={})
    assert f"({expected})" in prompt


# ── build_prompt(): Rotation der Fokus-Bereiche ──────────────────────────────
def test_build_prompt_rotates_through_three_focus_areas(frozen_time):
    engine = proactive.ProactiveEngine()
    prompts = []
    for _ in range(3):
        prompts.append(engine.build_prompt(memory={}))
        engine.mark_triggered()
    assert "active projects" in prompts[0]
    assert "wellbeing" in prompts[1]
    assert "interesting or useful" in prompts[2]


# ── build_prompt(): optionaler Kontext ────────────────────────────────────────
def test_build_prompt_includes_memory_block(frozen_time):
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={"identity": {}})
    assert "Kennt: Christoph" in prompt


def test_build_prompt_falls_back_when_memory_block_is_empty(monkeypatch, frozen_time):
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda memory: "")
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={})
    assert "(no stored user data)" in prompt


def test_build_prompt_includes_monitored_topics_capped_at_four(frozen_time):
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={}, monitors=["a", "b", "c", "d", "e"])
    assert "a, b, c, d" in prompt
    assert ", e" not in prompt


def test_build_prompt_omits_monitor_context_when_none_given(frozen_time):
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={})
    assert "tracks these topics" not in prompt


def test_build_prompt_includes_last_six_recent_turns(frozen_time):
    engine = proactive.ProactiveEngine()
    turns = [f"Zeile {i}" for i in range(10)]
    prompt = engine.build_prompt(memory={}, recent_turns=turns)
    assert "Zeile 9" in prompt and "Zeile 4" in prompt
    assert "Zeile 3" not in prompt


def test_build_prompt_always_forbids_english_default_and_tool_calls(frozen_time):
    engine = proactive.ProactiveEngine()
    prompt = engine.build_prompt(memory={})
    assert "Do NOT call any tools." in prompt
    assert "Never default to English" in prompt
