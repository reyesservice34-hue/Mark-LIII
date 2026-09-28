"""Tests für actions/file_controller.py — Dateiverwaltung mit Sicherheitsprüfungen und Undo."""
import json
import types

import pytest

from actions import file_controller as fc


@pytest.fixture(autouse=True)
def sandbox_home(tmp_path, monkeypatch):
    """Alle Pfad-Shortcuts und die Sicherheitsprüfung auf ein Test-Home umbiegen,
    damit nichts im echten Home-Verzeichnis berührt wird."""
    monkeypatch.setattr(fc.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(fc, "_SAFE_ROOTS", [tmp_path])
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Downloads").mkdir()
    return tmp_path


@pytest.fixture
def undo_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(fc, "push_undo", lambda label, fn: calls.append((label, fn)))
    return calls


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
def test_resolve_path_shortcuts(sandbox_home):
    assert fc._resolve_path("desktop") == sandbox_home / "Desktop"
    assert fc._resolve_path("DOWNLOADS") == sandbox_home / "Downloads"
    assert fc._resolve_path("home") == sandbox_home


def test_resolve_path_passes_through_arbitrary_paths(sandbox_home):
    assert fc._resolve_path(str(sandbox_home / "x" / "y")) == sandbox_home / "x" / "y"


def test_is_safe_path_true_inside_home(sandbox_home):
    assert fc._is_safe_path(sandbox_home / "Desktop" / "a.txt") is True


def test_is_safe_path_false_outside_home():
    assert fc._is_safe_path(fc.Path("/etc/passwd")) is False


@pytest.mark.parametrize("size,expected", [(500, "500.0 B"), (2048, "2.0 KB"), (5 * 1024**2, "5.0 MB")])
def test_format_size(size, expected):
    assert fc._format_size(size) == expected


# ── list_files ───────────────────────────────────────────────────────────────
def test_list_files_empty_directory(sandbox_home):
    assert "empty" in fc.list_files("desktop")


def test_list_files_reports_items_with_sizes(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_bytes(b"x" * 100)
    (sandbox_home / "Desktop" / "sub").mkdir()
    result = fc.list_files("desktop")
    assert "a.txt" in result and "sub/" in result


def test_list_files_hides_dotfiles_unless_requested(sandbox_home):
    (sandbox_home / "Desktop" / ".secret").write_text("x")
    assert "empty" in fc.list_files("desktop")
    assert ".secret" in fc.list_files("desktop", show_hidden=True)


def test_list_files_rejects_unsafe_path():
    assert "Access denied" in fc.list_files("/etc")


def test_list_files_reports_missing_path(sandbox_home):
    missing = str(sandbox_home / "Desktop" / "does-not-exist")
    assert "not found" in fc.list_files(missing)


# ── create_file / create_folder (+ Undo) ─────────────────────────────────────
def test_create_file_writes_content_and_registers_undo(sandbox_home, undo_calls):
    result = fc.create_file("desktop", name="note.txt", content="hallo")
    assert result == "File created: note.txt"
    assert (sandbox_home / "Desktop" / "note.txt").read_text() == "hallo"
    assert undo_calls


def test_create_file_undo_removes_the_new_file(sandbox_home, undo_calls):
    fc.create_file("desktop", name="note.txt", content="hallo")
    label, undo_fn = undo_calls[0]
    undo_fn()
    assert not (sandbox_home / "Desktop" / "note.txt").exists()


def test_create_file_undo_restores_previous_contents_when_overwriting(sandbox_home, undo_calls):
    (sandbox_home / "Desktop" / "note.txt").write_text("original")
    fc.create_file("desktop", name="note.txt", content="neu")
    _, undo_fn = undo_calls[0]
    undo_fn()
    assert (sandbox_home / "Desktop" / "note.txt").read_text() == "original"


def test_create_file_rejects_unsafe_path():
    assert "Access denied" in fc.create_file("/etc", name="evil.txt")


def test_create_folder_registers_undo_only_when_newly_created(sandbox_home, undo_calls):
    fc.create_folder("desktop", name="NewFolder")
    assert (sandbox_home / "Desktop" / "NewFolder").is_dir()
    assert len(undo_calls) == 1

    undo_calls.clear()
    fc.create_folder("desktop", name="NewFolder")  # already exists
    assert undo_calls == []


def test_create_folder_undo_removes_empty_folder(sandbox_home, undo_calls):
    fc.create_folder("desktop", name="NewFolder")
    _, undo_fn = undo_calls[0]
    undo_fn()
    assert not (sandbox_home / "Desktop" / "NewFolder").exists()


def test_create_folder_undo_refuses_to_delete_a_folder_that_was_filled(sandbox_home, undo_calls):
    fc.create_folder("desktop", name="NewFolder")
    (sandbox_home / "Desktop" / "NewFolder" / "keep.txt").write_text("wichtig")
    _, undo_fn = undo_calls[0]
    result = undo_fn()
    assert "not empty any more" in result
    assert (sandbox_home / "Desktop" / "NewFolder" / "keep.txt").exists()


# ── delete_file ──────────────────────────────────────────────────────────────
def test_delete_file_reports_missing(sandbox_home):
    assert "Not found" in fc.delete_file("desktop", name="ghost.txt")


def test_delete_file_protects_the_desktop_itself(sandbox_home):
    assert "Protected directory" in fc.delete_file("desktop")


def test_delete_file_reports_when_send2trash_unavailable(sandbox_home, monkeypatch):
    monkeypatch.setattr(fc, "_SEND2TRASH", False)
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    result = fc.delete_file("desktop", name="a.txt")
    assert "send2trash is not installed" in result
    assert (sandbox_home / "Desktop" / "a.txt").exists()  # untouched


def test_delete_file_moves_to_trash_and_registers_undo(sandbox_home, monkeypatch, undo_calls):
    trashed = []
    monkeypatch.setattr(fc, "_SEND2TRASH", True)
    fake_send2trash = types.SimpleNamespace(send2trash=lambda p: trashed.append(p))
    monkeypatch.setattr(fc, "send2trash", fake_send2trash, raising=False)
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    result = fc.delete_file("desktop", name="a.txt")
    assert "Moved to Trash" in result
    assert trashed
    assert undo_calls


# ── move_file / copy_file / rename_file (+ Undo) ─────────────────────────────
def test_move_file_moves_and_undo_restores_original_location(sandbox_home, undo_calls):
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    result = fc.move_file("desktop", name="a.txt", destination="downloads")
    assert "Moved: a.txt" in result
    assert (sandbox_home / "Downloads" / "a.txt").exists()
    assert not (sandbox_home / "Desktop" / "a.txt").exists()

    _, undo_fn = undo_calls[0]
    undo_fn()
    assert (sandbox_home / "Desktop" / "a.txt").exists()
    assert not (sandbox_home / "Downloads" / "a.txt").exists()


def test_move_file_requires_existing_source(sandbox_home):
    assert "Source not found" in fc.move_file("desktop", name="ghost.txt", destination="downloads")


def test_move_file_requires_destination(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    assert "No destination specified" in fc.move_file("desktop", name="a.txt")


def test_copy_file_copies_and_undo_removes_only_the_copy(sandbox_home, undo_calls):
    (sandbox_home / "Desktop" / "a.txt").write_text("original")
    result = fc.copy_file("desktop", name="a.txt", destination="downloads")
    assert "Copied: a.txt" in result
    assert (sandbox_home / "Downloads" / "a.txt").read_text() == "original"

    _, undo_fn = undo_calls[0]
    undo_fn()
    assert not (sandbox_home / "Downloads" / "a.txt").exists()
    assert (sandbox_home / "Desktop" / "a.txt").exists()  # original untouched


def test_rename_file_renames_and_undo_restores_old_name(sandbox_home, undo_calls):
    (sandbox_home / "Desktop" / "old.txt").write_text("x")
    result = fc.rename_file("desktop", name="old.txt", new_name="new.txt")
    assert "Renamed: old.txt → new.txt" in result
    assert (sandbox_home / "Desktop" / "new.txt").exists()

    _, undo_fn = undo_calls[0]
    undo_fn()
    assert (sandbox_home / "Desktop" / "old.txt").exists()
    assert not (sandbox_home / "Desktop" / "new.txt").exists()


def test_rename_file_refuses_to_overwrite_existing_name(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    (sandbox_home / "Desktop" / "b.txt").write_text("y")
    result = fc.rename_file("desktop", name="a.txt", new_name="b.txt")
    assert "already exists" in result


# ── read_file / write_file (+ Undo, Größenlimit) ──────────────────────────────
def test_read_file_returns_content(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("Inhalt")
    assert fc.read_file("desktop", name="a.txt") == "Inhalt"


def test_read_file_truncates_long_content(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("x" * 5000)
    result = fc.read_file("desktop", name="a.txt", max_chars=100)
    assert "[Truncated" in result
    assert len(result.split("\n\n[Truncated")[0]) == 100


def test_write_file_creates_new_and_undo_deletes_it(sandbox_home, undo_calls):
    result = fc.write_file("desktop", name="a.txt", content="hallo")
    assert "Written to: a.txt" in result
    assert (sandbox_home / "Desktop" / "a.txt").read_text() == "hallo"
    _, undo_fn = undo_calls[0]
    undo_fn()
    assert not (sandbox_home / "Desktop" / "a.txt").exists()


def test_write_file_append_mode(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("eins-")
    fc.write_file("desktop", name="a.txt", content="zwei", append=True)
    assert (sandbox_home / "Desktop" / "a.txt").read_text() == "eins-zwei"


def test_write_file_skips_undo_when_too_large_to_snapshot(sandbox_home, monkeypatch, undo_calls):
    (sandbox_home / "Desktop" / "big.txt").write_text("x")
    monkeypatch.setattr(fc, "_UNDO_CONTENT_LIMIT", 0)
    result = fc.write_file("desktop", name="big.txt", content="y")
    assert "cannot be undone" in result
    assert undo_calls == []


# ── find_files / get_largest_files / get_disk_usage ──────────────────────────
def test_find_files_matches_by_name_and_extension(sandbox_home):
    (sandbox_home / "Desktop" / "report.pdf").write_text("x")
    (sandbox_home / "Desktop" / "notes.txt").write_text("x")
    result = fc.find_files(extension=".pdf", path="desktop")
    assert "report.pdf" in result and "notes.txt" not in result


def test_find_files_reports_no_matches():
    assert "No ghost found" in fc.find_files(name="ghost", path="desktop")


def test_get_largest_files_sorted_descending(sandbox_home):
    (sandbox_home / "Desktop" / "small.txt").write_bytes(b"x" * 10)
    (sandbox_home / "Desktop" / "big.txt").write_bytes(b"x" * 10000)
    result = fc.get_largest_files(path="desktop", count=5)
    assert result.index("big.txt") < result.index("small.txt")


def test_get_disk_usage_reports_percentages(sandbox_home):
    result = fc.get_disk_usage("home")
    assert "Total" in result and "Used" in result and "Free" in result


# ── organize_desktop (+ Undo aller Verschiebungen) ────────────────────────────
def test_organize_desktop_moves_files_and_undo_restores_all(sandbox_home, undo_calls):
    (sandbox_home / "Desktop" / "photo.jpg").write_text("x")
    (sandbox_home / "Desktop" / "notes.txt").write_text("x")
    result = fc.organize_desktop()
    assert "2 files moved" in result
    assert (sandbox_home / "Desktop" / "Images" / "photo.jpg").exists()
    assert (sandbox_home / "Desktop" / "Documents" / "notes.txt").exists()

    _, undo_fn = undo_calls[0]
    undo_msg = undo_fn()
    assert "2 file(s) put back" in undo_msg
    assert (sandbox_home / "Desktop" / "photo.jpg").exists()
    assert (sandbox_home / "Desktop" / "notes.txt").exists()
    assert not (sandbox_home / "Desktop" / "Images").exists()  # emptied folder removed


def test_organize_desktop_no_undo_pushed_when_nothing_moved(sandbox_home, undo_calls):
    fc.organize_desktop()
    assert undo_calls == []


# ── get_file_info ────────────────────────────────────────────────────────────
def test_get_file_info_reports_expected_fields(sandbox_home):
    (sandbox_home / "Desktop" / "a.txt").write_text("x")
    result = fc.get_file_info("desktop", name="a.txt")
    assert "Name: a.txt" in result and "Type: File" in result and "Extension: .txt" in result


def test_get_file_info_reports_missing():
    assert "Not found" in fc.get_file_info("desktop", name="ghost.txt")


# ── file_controller(): Dispatcher ─────────────────────────────────────────────
def test_dispatcher_routes_to_list(sandbox_home):
    result = fc.file_controller({"action": "list", "path": "desktop"})
    assert "empty" in result


def test_dispatcher_unknown_action():
    assert "Unknown action" in fc.file_controller({"action": "levitate"})


def test_dispatcher_logs_to_player(sandbox_home):
    player = FakePlayer()
    fc.file_controller({"action": "list", "path": "desktop"}, player=player)
    assert player.logs


def test_dispatcher_catches_exceptions(monkeypatch):
    def boom(path):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(fc, "list_files", boom)
    result = fc.file_controller({"action": "list"})
    assert "File controller error" in result and "kaputt" in result


def test_tool_declaration_shape():
    assert fc.TOOL["name"] == "file_controller"
    assert fc.TOOL["handler"] is fc.file_controller
