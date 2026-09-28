"""Tests für actions/file_processor.py — Typ-Erkennung, Hilfsfunktionen und Dispatch.

Die format-spezifischen Prozessoren (_process_image, _process_pdf, ...) hängen
jeweils von unterschiedlichen optionalen Bibliotheken ab (Pillow, moviepy, ...);
hier wird nur der Dispatcher getestet (mit gemockten Prozessoren) sowie die
reinen Hilfsfunktionen, die unabhängig von jeder optionalen Bibliothek sind.
"""
import pytest

from actions import file_processor as fp


class FakePlayer:
    def __init__(self):
        self.logs = []

    def write_log(self, msg):
        self.logs.append(msg)


# ── _detect_type ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("filename,expected", [
    ("a.jpg", "image"), ("a.PNG", "image"),
    ("a.mp4", "video"), ("a.mp3", "audio"),
    ("a.py", "code"), ("a.zip", "archive"),
    ("a.pdf", "pdf"), ("a.docx", "docx"),
    ("a.txt", "text"), ("a.md", "text"),
    ("a.csv", "csv"), ("a.xlsx", "excel"),
    ("a.json", "json"), ("a.xml", "xml"),
    ("a.pptx", "pptx"), ("a.bin", "unknown"),
])
def test_detect_type(tmp_path, filename, expected):
    path = tmp_path / filename
    assert fp._detect_type(path) == expected


# ── _file_size_str ───────────────────────────────────────────────────────────
def test_file_size_str_bytes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"x" * 500)
    assert fp._file_size_str(f) == "500 B"


def test_file_size_str_kilobytes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"x" * 2048)
    assert fp._file_size_str(f) == "2.0 KB"


def test_file_size_str_megabytes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"x" * (3 * 1024 * 1024))
    assert fp._file_size_str(f) == "3.0 MB"


# ── _output_path ─────────────────────────────────────────────────────────────
def test_output_path_keeps_source_extension_by_default(tmp_path):
    src = tmp_path / "photo.jpg"
    assert fp._output_path(src, "resized") == tmp_path / "photo_resized.jpg"


def test_output_path_uses_new_extension_when_given(tmp_path):
    src = tmp_path / "doc.docx"
    assert fp._output_path(src, "converted", new_ext=".pdf") == tmp_path / "doc_converted.pdf"


# ── file_processor(): Eingabevalidierung ─────────────────────────────────────
def test_requires_a_file_path():
    assert fp.file_processor({}) == "No file path provided."


def test_reports_missing_file():
    result = fp.file_processor({"file_path": "/does/not/exist.txt"})
    assert "File not found" in result


def test_reports_path_that_is_a_directory(tmp_path):
    result = fp.file_processor({"file_path": str(tmp_path)})
    assert "not a file" in result


# ── file_processor(): Dispatch auf bekannte Typen ────────────────────────────
def test_dispatches_image_files_to_process_image(tmp_path, monkeypatch):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")
    monkeypatch.setattr(fp, "_process_image", lambda p, a, pm, s: "beschrieben")
    assert fp.file_processor({"file_path": str(f)}) == "beschrieben"


def test_dispatches_docx_via_text_doc_wrapper(tmp_path, monkeypatch):
    f = tmp_path / "doc.docx"
    f.write_bytes(b"x")
    seen = {}

    def fake_process_text_doc(p, ft, a, pm, s):
        seen["ft"] = ft
        return "ok"

    monkeypatch.setattr(fp, "_process_text_doc", fake_process_text_doc)
    result = fp.file_processor({"file_path": str(f)})
    assert result == "ok" and seen["ft"] == "docx"


def test_dispatches_csv_via_data_wrapper(tmp_path, monkeypatch):
    f = tmp_path / "data.csv"
    f.write_bytes(b"x")
    seen = {}

    def fake_process_data(p, ft, a, pm, s):
        seen["ft"] = ft
        return "ok"

    monkeypatch.setattr(fp, "_process_data", fake_process_data)
    result = fp.file_processor({"file_path": str(f)})
    assert result == "ok" and seen["ft"] == "csv"


def test_xml_routes_through_process_json(tmp_path, monkeypatch):
    f = tmp_path / "data.xml"
    f.write_bytes(b"x")
    monkeypatch.setattr(fp, "_process_json", lambda p, a, pm, s: "xml-verarbeitet")
    assert fp.file_processor({"file_path": str(f)}) == "xml-verarbeitet"


def test_handler_result_falls_back_to_done_on_falsy_return(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_bytes(b"x")
    monkeypatch.setattr(fp, "_process_code", lambda p, a, pm, s: "")
    assert fp.file_processor({"file_path": str(f)}) == "Done."


def test_handler_exception_is_caught_and_reported(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_bytes(b"x")

    def boom(p, a, pm, s):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(fp, "_process_code", boom)
    result = fp.file_processor({"file_path": str(f)})
    assert "Processing failed" in result and "kaputt" in result


def test_logs_to_player(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_bytes(b"x")
    monkeypatch.setattr(fp, "_process_code", lambda p, a, pm, s: "ok")
    player = FakePlayer()
    fp.file_processor({"file_path": str(f)}, player=player)
    assert player.logs


# ── file_processor(): unbekannter Typ → Gemini-Fallback ──────────────────────
def test_unknown_type_falls_back_to_gemini_description(tmp_path, monkeypatch):
    f = tmp_path / "data.bin"
    f.write_text("etwas roher Inhalt")

    class FakeResponse:
        text = "Das ist eine Binärdatei mit rohem Text."

    class FakeModel:
        def generate_content(self, contents):
            return FakeResponse()

    monkeypatch.setattr(fp, "_gemini_client", lambda: FakeModel())
    result = fp.file_processor({"file_path": str(f)})
    assert result == "Das ist eine Binärdatei mit rohem Text."


def test_unknown_type_reports_gemini_failure(tmp_path, monkeypatch):
    f = tmp_path / "data.bin"
    f.write_text("x")

    def boom():
        raise RuntimeError("kein Netz")

    monkeypatch.setattr(fp, "_gemini_client", boom)
    result = fp.file_processor({"file_path": str(f)})
    assert "Could not process" in result and "kein Netz" in result


def test_tool_declaration_shape():
    assert fp.TOOL["name"] == "file_processor"
    assert fp.TOOL["handler"] is fp.file_processor
