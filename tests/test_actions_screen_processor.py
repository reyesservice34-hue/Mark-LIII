"""Tests für actions/screen_processor.py — Screen-/Kamera-Capture-Helfer."""
import json
import types

import pytest

from actions import screen_processor as sp


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_CONFIG_PATH", tmp_path / "api_keys.json")
    yield


def _configure(**kv):
    sp._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    sp._CONFIG_PATH.write_text(json.dumps(kv), encoding="utf-8")


# ── Konfigurationszugriff ────────────────────────────────────────────────────
def test_load_config_returns_empty_dict_when_missing():
    assert sp._load_config() == {}


def test_load_config_recovers_from_corrupt_json():
    sp._CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    sp._CONFIG_PATH.write_text("{ kaputt", encoding="utf-8")
    assert sp._load_config() == {}


def test_save_config_key_merges_without_clobbering_other_keys():
    _configure(gemini_api_key="k")
    sp._save_config_key("camera_index", 2)
    data = sp._load_config()
    assert data == {"gemini_api_key": "k", "camera_index": 2}


def test_get_os_defaults_to_windows():
    assert sp._get_os() == "windows"


def test_get_os_reads_configured_value_lowercased():
    _configure(os_system="MAC")
    assert sp._get_os() == "mac"


# ── _compress: aktueller Sandbox-Zustand ohne PIL ────────────────────────────
def test_compress_returns_original_bytes_when_pil_missing(monkeypatch):
    monkeypatch.setattr(sp, "_PIL", False)
    data, mime = sp._compress(b"rohe-bytes", "PNG")
    assert data == b"rohe-bytes"
    assert mime == "image/png"


def test_compress_resizes_and_converts_to_jpeg_when_pil_available(monkeypatch):
    fake_img = types.SimpleNamespace(
        thumbnail=lambda size, resample: None,
        save=lambda buf, format, quality=None, optimize=None: buf.write(b"jpeg-bytes"),
    )
    fake_pil_image = types.SimpleNamespace(
        open=lambda buf: types.SimpleNamespace(convert=lambda mode: fake_img),
        BILINEAR=1,
    )
    monkeypatch.setattr(sp, "_PIL", True)
    monkeypatch.setattr(sp, "PIL", types.SimpleNamespace(Image=fake_pil_image), raising=False)
    data, mime = sp._compress(b"irrelevant-input", "PNG")
    assert data == b"jpeg-bytes"
    assert mime == "image/jpeg"


def test_compress_falls_back_to_original_bytes_on_error(monkeypatch):
    def broken_open(buf):
        raise ValueError("kaputtes Bild")

    monkeypatch.setattr(sp, "_PIL", True)
    monkeypatch.setattr(sp, "PIL", types.SimpleNamespace(Image=types.SimpleNamespace(open=broken_open)), raising=False)
    data, mime = sp._compress(b"rohe-bytes", "PNG")
    assert data == b"rohe-bytes" and mime == "image/png"


# ── _capture_screen: aktueller Sandbox-Zustand ohne mss ──────────────────────
def test_capture_screen_raises_when_mss_missing(monkeypatch):
    monkeypatch.setattr(sp, "_MSS", False)
    with pytest.raises(RuntimeError, match="mss is not installed"):
        sp._capture_screen()


def test_capture_screen_grabs_the_second_monitor_when_available(monkeypatch):
    monkeypatch.setattr(sp, "_MSS", True)
    monkeypatch.setattr(sp, "_compress", lambda png, fmt: (png, "image/jpeg"))

    class FakeShot:
        rgb = b"rgbdata"
        size = (10, 10)

    class FakeSct:
        monitors = [{"all": True}, {"mon": 1}, {"mon": 2}]

        def grab(self, target):
            assert target == {"mon": 1}
            return FakeShot()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_mss_module = types.SimpleNamespace(
        mss=lambda: FakeSct(),
        tools=types.SimpleNamespace(to_png=lambda rgb, size: b"png-bytes"),
    )
    monkeypatch.setattr(sp, "mss", fake_mss_module, raising=False)
    data, mime = sp._capture_screen()
    assert data == b"png-bytes" and mime == "image/jpeg"


# ── _cv2_backend / _probe_camera: aktueller Sandbox-Zustand ohne cv2 ─────────
def test_cv2_backend_returns_zero_when_cv2_missing(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", False)
    assert sp._cv2_backend() == 0


def test_probe_camera_false_when_cv2_missing(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", False)
    assert sp._probe_camera(0, 0) is False


def test_capture_camera_raises_when_cv2_missing(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", False)
    with pytest.raises(RuntimeError, match="OpenCV .cv2. is not installed"):
        sp._capture_camera()


# ── _detect_camera_index / _get_camera_index (Discovery-Logik, cv2 gemockt) ──
def test_detect_camera_index_stops_at_first_working_index(monkeypatch):
    monkeypatch.setattr(sp, "_probe_camera", lambda idx, backend, warmup=5: idx == 2)
    idx = sp._detect_camera_index()
    assert idx == 2
    assert sp._load_config()["camera_index"] == 2


def test_detect_camera_index_defaults_to_zero_when_none_found(monkeypatch):
    monkeypatch.setattr(sp, "_probe_camera", lambda idx, backend, warmup=5: False)
    idx = sp._detect_camera_index()
    assert idx == 0
    assert sp._load_config()["camera_index"] == 0


def test_get_camera_index_uses_stored_value_without_redetecting(monkeypatch):
    _configure(camera_index=3)
    called = []
    monkeypatch.setattr(sp, "_detect_camera_index", lambda: called.append(1) or 0)
    assert sp._get_camera_index() == 3
    assert called == []


def test_get_camera_index_detects_when_unset(monkeypatch):
    monkeypatch.setattr(sp, "_detect_camera_index", lambda: 5)
    assert sp._get_camera_index() == 5


# ── _capture_camera: Erfolgspfad mit gefaktem cv2 ────────────────────────────
def test_capture_camera_returns_jpeg_bytes_on_success(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", True)
    monkeypatch.setattr(sp, "_PIL", False)
    monkeypatch.setattr(sp, "_get_camera_index", lambda: 0)
    monkeypatch.setattr(sp, "_cv2_backend", lambda: 0)

    class FakeCap:
        def isOpened(self):
            return True

        def read(self):
            return True, "frame-object"

        def release(self):
            pass

    fake_cv2 = types.SimpleNamespace(
        VideoCapture=lambda index, backend: FakeCap(),
        imencode=lambda ext, frame, params: (True, types.SimpleNamespace(tobytes=lambda: b"jpeg-bytes")),
        IMWRITE_JPEG_QUALITY=1,
    )
    monkeypatch.setattr(sp, "cv2", fake_cv2, raising=False)
    data, mime = sp._capture_camera()
    assert data == b"jpeg-bytes" and mime == "image/jpeg"


def test_capture_camera_raises_when_device_cannot_open(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", True)
    monkeypatch.setattr(sp, "_get_camera_index", lambda: 0)
    monkeypatch.setattr(sp, "_cv2_backend", lambda: 0)

    class FakeCap:
        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(sp, "cv2", types.SimpleNamespace(VideoCapture=lambda index, backend: FakeCap()), raising=False)
    with pytest.raises(RuntimeError, match="could not be opened"):
        sp._capture_camera()


def test_capture_camera_raises_when_no_frame_returned(monkeypatch):
    monkeypatch.setattr(sp, "_CV2", True)
    monkeypatch.setattr(sp, "_get_camera_index", lambda: 0)
    monkeypatch.setattr(sp, "_cv2_backend", lambda: 0)

    class FakeCap:
        def isOpened(self):
            return True

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(sp, "cv2", types.SimpleNamespace(VideoCapture=lambda index, backend: FakeCap()), raising=False)
    with pytest.raises(RuntimeError, match="no frame"):
        sp._capture_camera()
