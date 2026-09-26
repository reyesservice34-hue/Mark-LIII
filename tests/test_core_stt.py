"""Tests für core/stt.py — Whisper/Vosk STT-Engines (Bibliotheken gestubbt)."""
import sys
import types

import pytest

from core import stt


class FakeSegment:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    """Installiert ein Fake faster_whisper-Modul und gibt die WhisperModel-Fabrik zurück."""
    created = []

    class FakeWhisperModel:
        def __init__(self, model_name, device=None, compute_type=None):
            created.append((model_name, device, compute_type))
            self.segments = [FakeSegment("hallo"), FakeSegment("welt")]

        def transcribe(self, audio, **kwargs):
            return iter(self.segments), None

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    return created


def test_whisper_stt_initializes_and_sets_language(fake_faster_whisper):
    engine = stt.WhisperSTT(model_name="base", language="DE")
    assert engine._language == "de"
    assert fake_faster_whisper[0][0] == "base"


@pytest.mark.parametrize("lang", [None, "auto", "AUTO", ""])
def test_whisper_stt_normalises_auto_language_to_none(fake_faster_whisper, lang):
    engine = stt.WhisperSTT(language=lang)
    assert engine._language is None


def test_whisper_stt_transcribe_joins_segment_texts(fake_faster_whisper):
    engine = stt.WhisperSTT()
    result = engine.transcribe(None)
    assert result == "hallo welt"


def test_whisper_stt_transcribe_reraises_on_error(fake_faster_whisper, monkeypatch):
    engine = stt.WhisperSTT()

    def boom(*a, **k):
        raise RuntimeError("Modellfehler")

    monkeypatch.setattr(engine._model, "transcribe", boom)
    with pytest.raises(RuntimeError, match="Modellfehler"):
        engine.transcribe(None)


def test_whisper_stt_retries_after_clearing_offline_env_vars(monkeypatch):
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    calls = []

    class RetryingWhisperModel:
        def __init__(self, model_name, device=None, compute_type=None):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("model not found in cache — localEntryNotFoundError")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = RetryingWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    engine = stt.WhisperSTT()
    assert len(calls) == 2
    assert "HF_HUB_OFFLINE" not in os.environ


def test_whisper_stt_raises_helpful_error_when_download_also_fails(monkeypatch):
    class AlwaysFailsWhisperModel:
        def __init__(self, model_name, device=None, compute_type=None):
            raise RuntimeError("does not exist locally")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = AlwaysFailsWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    with pytest.raises(RuntimeError, match="download failed"):
        stt.WhisperSTT()


def test_whisper_stt_reraises_unrelated_errors_without_retry(monkeypatch):
    calls = []

    class UnrelatedErrorWhisperModel:
        def __init__(self, model_name, device=None, compute_type=None):
            calls.append(1)
            raise ValueError("totally unrelated failure")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = UnrelatedErrorWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    with pytest.raises(ValueError, match="totally unrelated failure"):
        stt.WhisperSTT()
    assert len(calls) == 1  # no retry attempted


# ── VoskSTT ──────────────────────────────────────────────────────────────────
@pytest.fixture
def fake_vosk(monkeypatch):
    created = {}

    class FakeModel:
        def __init__(self, model_path=None, lang=None):
            created["model_path"] = model_path
            created["lang"] = lang

    class FakeRecognizer:
        def __init__(self, model, rate):
            self.model = model
            self.rate = rate
            self._accept = True
            self._result = {"text": "final text"}
            self._partial = {"partial": "partial text"}

        def AcceptWaveform(self, data):
            return self._accept

        def Result(self):
            import json
            return json.dumps(self._result)

        def PartialResult(self):
            import json
            return json.dumps(self._partial)

    mod = types.ModuleType("vosk")
    mod.Model = FakeModel
    mod.KaldiRecognizer = FakeRecognizer
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return created, FakeRecognizer


def test_vosk_stt_uses_model_path_when_given(fake_vosk):
    created, _ = fake_vosk
    stt.VoskSTT(model_path="/models/de")
    assert created["model_path"] == "/models/de"


def test_vosk_stt_uses_language_when_no_path(fake_vosk):
    created, _ = fake_vosk
    stt.VoskSTT(language="DE-de")
    assert created["lang"] == "de-de"


@pytest.mark.parametrize("lang", [None, "", "auto", "AUTO"])
def test_vosk_stt_defaults_language_to_en_us(fake_vosk, lang):
    created, _ = fake_vosk
    stt.VoskSTT(language=lang)
    assert created["lang"] == "en-us"


def test_vosk_stt_process_chunk_returns_final_result(fake_vosk):
    engine = stt.VoskSTT()
    text, is_final = engine.process_chunk(b"\x00\x01")
    assert (text, is_final) == ("final text", True)


def test_vosk_stt_process_chunk_returns_partial_result(fake_vosk):
    engine = stt.VoskSTT()
    engine._rec._accept = False
    text, is_final = engine.process_chunk(b"\x00\x01")
    assert (text, is_final) == ("partial text", False)
