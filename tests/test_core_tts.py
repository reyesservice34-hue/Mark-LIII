"""Tests für core/tts.py — Audio-Hilfsfunktionen, Engines, Player und Factory."""
import sys
import types

import numpy as np
import pytest

from core import tts


# ── _to_numpy ────────────────────────────────────────────────────────────────
def test_to_numpy_passes_through_plain_arrays():
    arr = np.array([1.0, 2.0], dtype=np.float32)
    result = tts._to_numpy(arr)
    assert np.array_equal(result, arr)


def test_to_numpy_converts_list_like_input():
    result = tts._to_numpy([1, 2, 3])
    assert result.dtype == np.float32
    assert list(result) == [1.0, 2.0, 3.0]


def test_to_numpy_fast_path_for_tensor_like_object():
    class FakeTensor:
        def detach(self):
            return self
        def cpu(self):
            return self
        def float(self):
            return self
        def numpy(self):
            return np.array([4.0, 5.0], dtype=np.float32)

    result = tts._to_numpy(FakeTensor())
    assert list(result) == [4.0, 5.0]


def test_to_numpy_falls_back_to_tolist_on_version_mismatch():
    class FakeTensor:
        def detach(self):
            return self
        def cpu(self):
            return self
        def float(self):
            return self
        def numpy(self):
            raise RuntimeError("Numpy is not available")
        def tolist(self):
            return [6.0, 7.0]

    result = tts._to_numpy(FakeTensor())
    assert list(result) == [6.0, 7.0]


# ── _compress_silence ────────────────────────────────────────────────────────
def test_compress_silence_keeps_loud_audio_unchanged():
    arr = np.ones(2400, dtype=np.float32)  # well above threshold everywhere
    result = tts._compress_silence(arr, sample_rate=24000)
    assert np.allclose(result, arr)


def test_compress_silence_trims_long_silence_to_the_cap():
    silence = np.zeros(24000, dtype=np.float32)  # 1s of silence at 24kHz
    result = tts._compress_silence(silence, sample_rate=24000, max_silence_ms=500)
    assert len(result) <= int(24000 * 0.5) + 240  # capped, plus one frame's slack


def test_compress_silence_keeps_short_silence_untouched():
    silence = np.zeros(1200, dtype=np.float32)  # 50ms, well under the cap
    result = tts._compress_silence(silence, sample_rate=24000, max_silence_ms=500)
    assert len(result) == len(silence)


# ── _play_np / _play_audio_bytes ─────────────────────────────────────────────
def test_play_np_calls_sounddevice_play_and_wait(monkeypatch):
    calls = {}
    monkeypatch.setattr(tts.sd, "play", lambda arr, rate: calls.update(arr=arr, rate=rate))
    monkeypatch.setattr(tts.sd, "wait", lambda: calls.setdefault("waited", True))
    tts._play_np([1, 2, 3], 24000)
    assert calls["rate"] == 24000 and calls["waited"] is True


def test_play_audio_bytes_decodes_and_plays(monkeypatch):
    class FakeDecoded:
        samples = [0.1, 0.2, 0.3]
        sample_rate = 22050

    fake_miniaudio = types.ModuleType("miniaudio")
    fake_miniaudio.decode = lambda data, output_format=None, nchannels=None: FakeDecoded()
    fake_miniaudio.SampleFormat = types.SimpleNamespace(FLOAT32=1)
    monkeypatch.setitem(sys.modules, "miniaudio", fake_miniaudio)

    calls = {}
    monkeypatch.setattr(tts.sd, "play", lambda arr, rate: calls.update(rate=rate))
    monkeypatch.setattr(tts.sd, "wait", lambda: calls.setdefault("waited", True))
    tts._play_audio_bytes(b"fake-mp3-bytes")
    assert calls["rate"] == 22050 and calls["waited"] is True


# ── EdgeTTSEngine ────────────────────────────────────────────────────────────
def test_edge_tts_engine_plays_synthesised_audio(monkeypatch):
    engine = tts.EdgeTTSEngine(voice="en-US-GuyNeural")

    async def fake_synth(text):
        return b"audio-bytes"

    monkeypatch.setattr(engine, "_synth", fake_synth)
    played = []
    monkeypatch.setattr(tts, "_play_audio_bytes", lambda b: played.append(b))
    engine.speak("hallo")
    assert played == [b"audio-bytes"]


def test_edge_tts_engine_skips_playback_for_empty_audio(monkeypatch):
    engine = tts.EdgeTTSEngine()

    async def fake_synth(text):
        return b""

    monkeypatch.setattr(engine, "_synth", fake_synth)
    played = []
    monkeypatch.setattr(tts, "_play_audio_bytes", lambda b: played.append(b))
    engine.speak("hallo")
    assert played == []


def test_edge_tts_synth_collects_audio_chunks(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice):
            self.text, self.voice = text, voice

        async def stream(self):
            for chunk in [{"type": "audio", "data": b"a"}, {"type": "other"}, {"type": "audio", "data": b"b"}]:
                yield chunk

    fake_edge_tts = types.ModuleType("edge_tts")
    fake_edge_tts.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_tts)

    engine = tts.EdgeTTSEngine()
    import asyncio
    result = asyncio.new_event_loop().run_until_complete(engine._synth("hi"))
    assert result == b"ab"


# ── _import_kokoro_pipeline ──────────────────────────────────────────────────
def _install_fake_kokoro(monkeypatch, pipeline_cls):
    mod = types.ModuleType("kokoro")
    mod.KPipeline = pipeline_cls
    monkeypatch.setitem(sys.modules, "kokoro", mod)


def test_import_kokoro_pipeline_success(monkeypatch):
    _install_fake_kokoro(monkeypatch, object())
    assert tts._import_kokoro_pipeline() is sys.modules["kokoro"].KPipeline


def test_import_kokoro_pipeline_unrelated_error_is_wrapped(monkeypatch):
    monkeypatch.delitem(sys.modules, "kokoro", raising=False)
    with pytest.raises(RuntimeError, match="Kokoro import failed"):
        tts._import_kokoro_pipeline()


def test_import_kokoro_pipeline_upgrades_and_retries_on_version_mismatch(monkeypatch, tmp_path):
    # First `from kokoro import KPipeline` fails with a version-mismatch marker
    # (a stale cached module). The real code then "pip install --upgrade"s and
    # deletes the stale sys.modules entry before retrying the import — so, like
    # a real upgrade would, the retry must find a genuinely importable module on
    # disk once the cache is invalidated. A real file on a tmp sys.path entry
    # does that without fighting the source's own cache-eviction logic.
    class FlakyModule(types.ModuleType):
        def __getattr__(self, name):
            if name == "KPipeline":
                raise ImportError("cannot import name 'AlbertModel' from transformers")
            raise AttributeError(name)

    monkeypatch.setitem(sys.modules, "kokoro", FlakyModule("kokoro"))
    (tmp_path / "kokoro.py").write_text("KPipeline = 'PipelineV2'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run",
                        lambda cmd, **k: real_subprocess.CompletedProcess(args=cmd, returncode=0))

    result = tts._import_kokoro_pipeline()
    assert result == "PipelineV2"


def test_import_kokoro_pipeline_upgrade_failure_is_reported(monkeypatch):
    class FlakyModule(types.ModuleType):
        def __getattr__(self, name):
            raise ImportError("cannot import name 'AutoModel' from transformers")

    monkeypatch.setitem(sys.modules, "kokoro", FlakyModule("kokoro"))
    import subprocess as real_subprocess
    monkeypatch.setattr(real_subprocess, "run",
                        lambda *a, **k: real_subprocess.CompletedProcess(args=[], returncode=1, stderr=b"pip kaputt"))
    with pytest.raises(RuntimeError, match="auto-upgrade failed"):
        tts._import_kokoro_pipeline()


# ── KokoroTTSEngine ──────────────────────────────────────────────────────────
class FakePipeline:
    def __init__(self, lang_code=None, device=None):
        self.lang_code = lang_code
        self.device = device
        self.calls = []

    def __call__(self, text, voice=None, speed=None):
        self.calls.append((text, voice, speed))
        return iter([(None, None, np.ones(240, dtype=np.float32))])


def test_kokoro_lang_code_maps_voice_prefix(monkeypatch):
    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: FakePipeline)
    engine = tts.KokoroTTSEngine(voice="bf_alice")
    assert engine._lang_code == "b"


def test_kokoro_lang_code_defaults_to_a_for_unknown_prefix(monkeypatch):
    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: FakePipeline)
    engine = tts.KokoroTTSEngine(voice="xf_unknown")
    assert engine._lang_code == "a"


def test_kokoro_init_builds_pipeline_and_warms_up(monkeypatch):
    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: FakePipeline)
    engine = tts.KokoroTTSEngine(voice="af_heart")
    assert isinstance(engine._pipeline, FakePipeline)
    assert engine._pipeline.calls  # warmup call happened


def test_kokoro_init_falls_back_when_device_kwarg_unsupported(monkeypatch):
    class NoDeviceKwargPipeline:
        def __init__(self, lang_code=None, device=None):
            if device is not None:
                raise TypeError("unexpected keyword argument 'device'")
            self.calls = []

        def __call__(self, text, voice=None, speed=None):
            self.calls.append(1)
            return iter([])

    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: NoDeviceKwargPipeline)
    engine = tts.KokoroTTSEngine()
    assert isinstance(engine._pipeline, NoDeviceKwargPipeline)


def test_kokoro_speak_streams_chunks_through_playback_queue(monkeypatch):
    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: FakePipeline)
    engine = tts.KokoroTTSEngine()
    played = []
    monkeypatch.setattr(tts, "_play_np", lambda arr, rate: played.append(rate))

    def multi_chunk_call(self, text, voice=None, speed=None):
        return iter([(None, None, np.ones(240, dtype=np.float32)),
                     (None, None, np.ones(240, dtype=np.float32))])

    monkeypatch.setattr(FakePipeline, "__call__", multi_chunk_call)
    engine.speak("Hallo Welt")
    assert played == [24000, 24000]


def test_kokoro_speak_reraises_synthesis_errors(monkeypatch):
    monkeypatch.setattr(tts, "_import_kokoro_pipeline", lambda: FakePipeline)
    engine = tts.KokoroTTSEngine()

    def boom(self, text, voice=None, speed=None):
        raise RuntimeError("Synthese kaputt")
        yield  # pragma: no cover — makes this a generator function

    monkeypatch.setattr(FakePipeline, "__call__", boom)
    monkeypatch.setattr(tts, "_play_np", lambda arr, rate: None)
    with pytest.raises(RuntimeError, match="Synthese kaputt"):
        engine.speak("x")


# ── ElevenLabsTTSEngine ──────────────────────────────────────────────────────
def test_elevenlabs_engine_plays_response_content(monkeypatch):
    import requests

    class FakeResponse:
        content = b"audio-bytes"
        def raise_for_status(self):
            pass

    seen = {}
    monkeypatch.setattr(requests, "post",
                        lambda url, json=None, headers=None, timeout=None: (seen.update(url=url, json=json, headers=headers), FakeResponse())[1])
    played = []
    monkeypatch.setattr(tts, "_play_audio_bytes", lambda b: played.append(b))

    engine = tts.ElevenLabsTTSEngine(api_key="k", voice_id="v1")
    engine.speak("hallo")
    assert played == [b"audio-bytes"]
    assert seen["headers"]["xi-api-key"] == "k"
    assert "v1" in seen["url"]


def test_elevenlabs_engine_raises_on_http_error(monkeypatch):
    import requests

    class FakeResponse:
        def raise_for_status(self):
            raise requests.exceptions.HTTPError("401")

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse())
    engine = tts.ElevenLabsTTSEngine(api_key="bad")
    with pytest.raises(requests.exceptions.HTTPError):
        engine.speak("x")


# ── TTSPlayer ────────────────────────────────────────────────────────────────
class FakeEngine:
    def __init__(self, raise_error=False):
        self.raise_error = raise_error
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)
        if self.raise_error:
            raise RuntimeError("Engine kaputt")


def test_tts_player_calls_callbacks_and_tracks_is_playing():
    engine = FakeEngine()
    player = tts.TTSPlayer(engine)
    events = []
    player.speak("hallo", on_start=lambda: events.append("start"), on_done=lambda: events.append("done"))
    assert events == ["start", "done"]
    assert player.is_playing is False
    assert engine.spoken == ["hallo"]


def test_tts_player_resets_playing_flag_even_on_engine_error():
    engine = FakeEngine(raise_error=True)
    player = tts.TTSPlayer(engine)
    player.speak("hallo")  # must not raise
    assert player.is_playing is False


def test_tts_player_stop_calls_sounddevice_stop(monkeypatch):
    calls = []
    monkeypatch.setattr(tts.sd, "stop", lambda: calls.append(1))
    player = tts.TTSPlayer(FakeEngine())
    player._playing = True
    player.stop()
    assert calls == [1]
    assert player.is_playing is False


# ── create_tts_player (Factory) ──────────────────────────────────────────────
def test_create_tts_player_defaults_to_edgetts(monkeypatch):
    monkeypatch.setattr(tts, "EdgeTTSEngine", lambda voice: types.SimpleNamespace(voice=voice))
    player = tts.create_tts_player({})
    assert player._engine.voice == "en-US-GuyNeural"


def test_create_tts_player_builds_kokoro_with_config_values(monkeypatch):
    seen = {}
    monkeypatch.setattr(tts, "KokoroTTSEngine", lambda voice, speed: seen.update(voice=voice, speed=speed))
    tts.create_tts_player({"tts_engine": "kokoro", "tts_voice": "bf_alice", "tts_speed": "1.5"})
    assert seen == {"voice": "bf_alice", "speed": 1.5}


def test_create_tts_player_builds_elevenlabs_with_api_key(monkeypatch):
    seen = {}
    monkeypatch.setattr(tts, "ElevenLabsTTSEngine", lambda api_key, voice_id: seen.update(api_key=api_key, voice_id=voice_id))
    tts.create_tts_player({"tts_engine": "ElevenLabs", "elevenlabs_api_key": "sk-1", "tts_voice": "v9"})
    assert seen == {"api_key": "sk-1", "voice_id": "v9"}
