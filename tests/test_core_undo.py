"""Tests für core/undo.py — den gemeinsamen Undo-Stack."""
import threading

import pytest

from core import undo


@pytest.fixture(autouse=True)
def reset_stack():
    undo.clear()
    yield
    undo.clear()


def test_empty_stack_reports_nothing_to_undo():
    assert undo.can_undo() is False
    assert undo.peek() == ""
    assert undo.history() == []
    assert "nothing to undo" in undo.undo_last().lower()


def test_push_makes_it_undoable_and_visible_in_peek():
    undo.push_undo("Lautstärke → 30%", lambda: "war 50%")
    assert undo.can_undo() is True
    assert undo.peek() == "Lautstärke → 30%"


def test_undo_last_runs_the_callable_and_pops_it():
    undo.push_undo("Datei verschoben", lambda: "zurück nach /home")
    msg = undo.undo_last()
    assert msg == "Undone: Datei verschoben. zurück nach /home"
    assert undo.can_undo() is False


def test_undo_last_without_detail_omits_trailing_text():
    undo.push_undo("Stumm geschaltet", lambda: "")
    assert undo.undo_last() == "Undone: Stumm geschaltet."


def test_undo_last_pops_before_running_so_a_failure_is_not_retried():
    undo.push_undo("Kaputte Aktion", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    msg = undo.undo_last()
    assert "Could not undo 'Kaputte Aktion'" in msg
    assert "boom" in msg
    assert undo.can_undo() is False  # entry was popped even though undo failed


def test_undo_pops_most_recently_pushed_first():
    undo.push_undo("erste", lambda: None)
    undo.push_undo("zweite", lambda: None)
    assert undo.undo_last().startswith("Undone: zweite")
    assert undo.undo_last().startswith("Undone: erste")
    assert undo.can_undo() is False


def test_history_lists_most_recent_first():
    undo.push_undo("erste", lambda: None)
    undo.push_undo("zweite", lambda: None)
    undo.push_undo("dritte", lambda: None)
    assert undo.history() == ["dritte", "zweite", "erste"]


def test_stack_drops_oldest_entries_beyond_max_depth():
    for i in range(undo.MAX_DEPTH + 5):
        undo.push_undo(f"aktion-{i}", lambda: None)
    hist = undo.history()
    assert len(hist) == undo.MAX_DEPTH
    assert hist[0] == f"aktion-{undo.MAX_DEPTH + 4}"
    assert hist[-1] == f"aktion-{5}"


def test_push_ignores_non_callable_undo_fn():
    undo.push_undo("kein callable", "das ist kein callable")
    assert undo.can_undo() is False


def test_label_is_truncated_to_120_characters():
    undo.push_undo("x" * 500, lambda: None)
    assert len(undo.peek()) == 120


def test_clear_empties_the_stack():
    undo.push_undo("etwas", lambda: None)
    undo.clear()
    assert undo.can_undo() is False
    assert undo.history() == []


def test_concurrent_pushes_stay_within_max_depth_without_errors():
    def worker(n):
        for i in range(20):
            undo.push_undo(f"t{n}-{i}", lambda: None)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(undo.history()) == undo.MAX_DEPTH
