"""Tests für core/wake_word.py — Installations-Checks, Setup und WakeWordDetector."""
import importlib.util
import subprocess
import sys
import types

import pytest

from core import wake_word as ww


# ── is_installed / is_ready ───────────────────────────────────────────────────
# is_installed() does `import importlib.util` locally, so the real stdlib
# module (shared across the process) is what needs patching, not an attribute
# on the wake_word module itself.
def test_is_installed_true_when_find_spec_succeeds(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert ww.is_installed() is True


def test_is_installed_false_when_find_spec_returns_none(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert ww.is_installed() is False


def test_is_installed_false_on_exception(monkeypatch):
    def boom(name):
        raise ImportError("kaputt")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert ww.is_installed() is False


def _install_fake_openwakeword(monkeypatch, models_dir):
    fake = types.ModuleType("openwakeword")
    fake.__file__ = str(models_dir.parent.parent / "__init__.py")
    monkeypatch.setitem(sys.modules, "openwakeword", fake)
    return fake


def _install_fake_openwakeword_utils(monkeypatch, download_models):
    """`import openwakeword.utils` needs the parent package registered too,
    or Python tries (and fails) to import the real, uninstalled package."""
    parent = types.ModuleType("openwakeword")
    fake_utils = types.ModuleType("openwakeword.utils")
    fake_utils.download_models = download_models
    parent.utils = fake_utils
    monkeypatch.setitem(sys.modules, "openwakeword", parent)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", fake_utils)


def test_is_ready_false_when_not_installed(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: False)
    assert ww.is_ready() is False


def test_is_ready_false_when_models_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    _install_fake_openwakeword(monkeypatch, tmp_path / "resources" / "models")
    assert ww.is_ready() is False


def test_is_ready_true_when_all_model_files_present(monkeypatch, tmp_path):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    models_dir = tmp_path / "resources" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / f"{ww.WAKE_MODEL}.onnx").touch()
    (models_dir / "melspectrogram.onnx").touch()
    (models_dir / "embedding_model.onnx").touch()
    _install_fake_openwakeword(monkeypatch, models_dir)
    assert ww.is_ready() is True


def test_is_ready_false_when_one_model_type_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    models_dir = tmp_path / "resources" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / f"{ww.WAKE_MODEL}.onnx").touch()
    (models_dir / "melspectrogram.onnx").touch()
    # embedding_model missing
    _install_fake_openwakeword(monkeypatch, models_dir)
    assert ww.is_ready() is False


def test_is_ready_false_on_exception(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: True)

    class BrokenModule:
        @property
        def __file__(self):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "openwakeword", BrokenModule())
    assert ww.is_ready() is False


# ── install_and_download ──────────────────────────────────────────────────────
def test_install_and_download_succeeds_when_already_installed(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    monkeypatch.setattr(ww, "is_ready", lambda: True)
    _install_fake_openwakeword_utils(monkeypatch, lambda models=None: None)
    ok, msg = ww.install_and_download()
    assert ok is True and "ready" in msg.lower()


def test_install_and_download_pip_failure_is_reported(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: False)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no space left"))
    ok, msg = ww.install_and_download()
    assert ok is False and "pip install failed" in msg


def test_install_and_download_reports_model_download_failure(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: True)

    def boom(models=None):
        raise RuntimeError("kein Netz")

    _install_fake_openwakeword_utils(monkeypatch, boom)
    ok, msg = ww.install_and_download()
    assert ok is False and "model download failed" in msg


def test_install_and_download_falls_back_to_legacy_signature(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    monkeypatch.setattr(ww, "is_ready", lambda: True)
    calls = []

    def download_models(models=None):
        calls.append(models)
        if models is not None:
            raise TypeError("old signature takes no arguments")

    _install_fake_openwakeword_utils(monkeypatch, download_models)
    ok, msg = ww.install_and_download()
    assert ok is True
    assert calls == [[ww.WAKE_MODEL], None]


def test_install_and_download_reports_when_still_not_ready(monkeypatch):
    monkeypatch.setattr(ww, "is_installed", lambda: True)
    monkeypatch.setattr(ww, "is_ready", lambda: False)
    _install_fake_openwakeword_utils(monkeypatch, lambda models=None: None)
    ok, msg = ww.install_and_download()
    assert ok is False and "could not be loaded" in msg


def test_install_and_download_catches_unexpected_errors(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(ww, "is_installed", boom)
    ok, msg = ww.install_and_download()
    assert ok is False and "setup error" in msg


# ── WakeWordDetector ──────────────────────────────────────────────────────────
class FakeModel:
    def __init__(self, wakeword_models=None, inference_framework=None):
        self.wakeword_models = wakeword_models

    def predict(self, frame):
        return {f"{ww.WAKE_MODEL}_v1": 0.9}


def _install_fake_model_module(monkeypatch, model_cls):
    parent = types.ModuleType("openwakeword")
    mod = types.ModuleType("openwakeword.model")
    mod.Model = model_cls
    parent.model = mod
    monkeypatch.setitem(sys.modules, "openwakeword", parent)
    monkeypatch.setitem(sys.modules, "openwakeword.model", mod)


def test_detector_start_success(monkeypatch):
    _install_fake_model_module(monkeypatch, FakeModel)
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    assert detector.start() is True
    assert detector.ready is True
    detector.stop()


def test_detector_start_is_idempotent(monkeypatch):
    calls = []

    class CountingModel(FakeModel):
        def __init__(self, **kw):
            calls.append(1)
            super().__init__(**kw)

    _install_fake_model_module(monkeypatch, CountingModel)
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector.start()
    detector.start()
    assert len(calls) == 1
    detector.stop()


def test_detector_start_failure_reports_and_stays_not_ready(monkeypatch):
    class BrokenModel:
        def __init__(self, **kw):
            raise RuntimeError("kein ONNX runtime")

    _install_fake_model_module(monkeypatch, BrokenModel)
    logs = []
    detector = ww.WakeWordDetector(on_detect=lambda: None, logger=logs.append)
    assert detector.start() is False
    assert detector.ready is False
    assert any("could not load model" in l for l in logs)


def test_detector_stop_clears_state():
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector._running = True
    detector._ready = True
    detector._model = object()
    detector.stop()
    assert detector._running is False
    assert detector._ready is False
    assert detector._model is None


def test_detector_feed_does_nothing_when_not_running():
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector.feed(object())
    assert detector._queue.empty()


def test_detector_feed_queues_a_1d_frame():
    import numpy as np
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector._running = True
    frame = np.array([1, 2, 3], dtype=np.int16)
    detector.feed(frame)
    queued = detector._queue.get_nowait()
    assert list(queued) == [1, 2, 3]


def test_detector_feed_flattens_2d_frame_to_first_column():
    import numpy as np
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector._running = True
    frame = np.array([[1, 9], [2, 9], [3, 9]], dtype=np.int16)
    detector.feed(frame)
    queued = detector._queue.get_nowait()
    assert list(queued) == [1, 2, 3]


def test_detector_feed_drops_frame_when_queue_is_full():
    detector = ww.WakeWordDetector(on_detect=lambda: None)
    detector._running = True
    detector._queue = type(detector._queue)(maxsize=1)
    detector._queue.put_nowait("already-full")
    import numpy as np
    detector.feed(np.array([1], dtype=np.int16))  # must not raise
    assert detector._queue.qsize() == 1


# ── _loop / _drain (aufgerufen im aktuellen Thread, keine echte Nebenläufigkeit) ──
def test_loop_fires_on_detect_above_threshold_and_stops():
    import numpy as np
    detected = []

    def on_detect():
        detected.append(1)
        detector._running = False  # end the loop from inside, no sentinel needed

    detector = ww.WakeWordDetector(on_detect=on_detect, threshold=0.5)
    detector._model = FakeModel()
    detector._running = True
    detector._queue.put(np.array([1, 2, 3], dtype=np.int16))
    detector._loop()
    assert detected == [1]


def test_loop_does_not_fire_below_threshold():
    import numpy as np

    class LowScoreModel(FakeModel):
        def predict(self, frame):
            return {f"{ww.WAKE_MODEL}_v1": 0.1}

    detected = []
    detector = ww.WakeWordDetector(on_detect=lambda: detected.append(1), threshold=0.5)
    detector._model = LowScoreModel()
    detector._running = True
    detector._queue.put(np.array([1, 2, 3], dtype=np.int16))
    detector._queue.put(None)  # ends the loop after the non-detecting frame
    detector._loop()
    assert detected == []


def test_loop_logs_and_continues_on_predict_exception():
    import numpy as np

    class BrokenModel:
        def predict(self, frame):
            raise RuntimeError("Inferenzfehler")

    logs = []
    detector = ww.WakeWordDetector(on_detect=lambda: None, logger=logs.append)
    detector._model = BrokenModel()
    detector._running = True
    detector._queue.put(np.array([1], dtype=np.int16))
    detector._queue.put(None)
    detector._loop()
    assert any("inference error" in l for l in logs)


def test_loop_on_detect_exception_is_logged_not_raised():
    import numpy as np

    def boom():
        raise RuntimeError("UI kaputt")

    detector = ww.WakeWordDetector(on_detect=boom, threshold=0.5)
    detector._model = FakeModel()
    detector._running = True
    detector._queue.put(np.array([1], dtype=np.int16))
    logs = []
    detector._logger = logs.append

    def stop_after_one(*a, **k):
        detector._running = False
        raise RuntimeError("UI kaputt")

    detector._on_detect = stop_after_one
    detector._loop()
    assert any("on_detect error" in l for l in logs)
