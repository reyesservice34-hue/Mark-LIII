"""Tests für core/llm_client.py — Ollama/OpenAI-kompatibel/Anthropic-Routing (requests gemockt)."""
import json

import pytest
import requests

from core import llm_client as lc


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, lines=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self._lines = lines or []
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._json

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "CONFIG_PATH", tmp_path / "api_keys.json")
    yield


def _configure(**kv):
    lc.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lc.CONFIG_PATH.write_text(json.dumps(kv), encoding="utf-8")


# ── Konfiguration ────────────────────────────────────────────────────────────
def test_get_llm_provider_defaults_to_ollama():
    assert lc.get_llm_provider() == "ollama"


@pytest.mark.parametrize("raw", ["openai", "OpenAI", "lmstudio", "localai", "jan", "llamacpp"])
def test_get_llm_provider_recognises_openai_compatible_aliases(raw):
    _configure(llm_provider=raw)
    assert lc.get_llm_provider() == "openai"


def test_get_llm_provider_recognises_anthropic():
    _configure(llm_provider="Anthropic")
    assert lc.get_llm_provider() == "anthropic"


def test_get_llm_provider_falls_back_to_ollama_for_unknown_value():
    _configure(llm_provider="something-else")
    assert lc.get_llm_provider() == "ollama"


def test_get_anthropic_key_defaults_to_empty_and_strips_whitespace():
    assert lc.get_anthropic_key() == ""
    _configure(anthropic_api_key="  sk-ant-123  ")
    assert lc.get_anthropic_key() == "sk-ant-123"


def test_get_llm_settings_defaults():
    assert lc.get_llm_settings() == ("http://localhost:11434", "llama3.2")


def test_get_llm_settings_strips_trailing_slash():
    _configure(llm_url="http://host:1234/")
    assert lc.get_llm_settings()[0] == "http://host:1234"


def test_get_llm_settings_anthropic_defaults_model_when_unset():
    _configure(llm_provider="anthropic")
    assert lc.get_llm_settings()[1] == lc._ANTHROPIC_DEFAULT_MODEL


def test_get_llm_settings_anthropic_respects_explicit_model():
    _configure(llm_provider="anthropic", llm_model="claude-haiku-4-5")
    assert lc.get_llm_settings()[1] == "claude-haiku-4-5"


# ── ensure_ollama_running ──────────────────────────────────────────────────────
def test_ensure_ollama_running_anthropic_without_key_returns_false(monkeypatch):
    _configure(llm_provider="anthropic")
    called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: called.append(1))
    assert lc.ensure_ollama_running() is False
    assert called == []


def test_ensure_ollama_running_anthropic_with_key_pings_models_endpoint(monkeypatch):
    _configure(llm_provider="anthropic", anthropic_api_key="sk-ant-x")
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: FakeResponse(200))
    assert lc.ensure_ollama_running() is True


def test_ensure_ollama_running_anthropic_non_200_is_false(monkeypatch):
    _configure(llm_provider="anthropic", anthropic_api_key="sk-ant-x")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(401))
    assert lc.ensure_ollama_running() is False


def test_ensure_ollama_running_openai_reachable(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200))
    assert lc.ensure_ollama_running() is True


def test_ensure_ollama_running_openai_unreachable(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    assert lc.ensure_ollama_running() is False


def test_ensure_ollama_running_already_up_skips_launch(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200))
    popen_calls = []
    monkeypatch.setattr(lc.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))
    assert lc.ensure_ollama_running() is True
    assert popen_calls == []


def test_ensure_ollama_running_launches_and_waits_until_up(monkeypatch):
    state = {"up_after": 2, "calls": 0}

    def fake_get(url, timeout=None):
        state["calls"] += 1
        return FakeResponse(200 if state["calls"] > state["up_after"] else 500)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(lc.subprocess, "Popen", lambda *a, **k: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(lc.time, "time", lambda: clock["t"])
    monkeypatch.setattr(lc.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    assert lc.ensure_ollama_running(timeout=15) is True


def test_ensure_ollama_running_times_out_if_never_up(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(500))
    monkeypatch.setattr(lc.subprocess, "Popen", lambda *a, **k: None)
    clock = {"t": 0.0}
    monkeypatch.setattr(lc.time, "time", lambda: clock["t"])
    monkeypatch.setattr(lc.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    assert lc.ensure_ollama_running(timeout=3) is False


def test_ensure_ollama_running_missing_binary_returns_false(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(500))

    def raise_fnf(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(lc.subprocess, "Popen", raise_fnf)
    assert lc.ensure_ollama_running() is False


# ── warmup_model ─────────────────────────────────────────────────────────────
def test_warmup_model_anthropic_is_a_noop_true(monkeypatch):
    _configure(llm_provider="anthropic")
    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1))
    assert lc.warmup_model() is True
    assert called == []


def test_warmup_model_openai_success(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200))
    assert lc.warmup_model() is True


def test_warmup_model_openai_failure_is_non_fatal(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(500))
    assert lc.warmup_model() is False


def test_warmup_model_ollama_sends_keep_alive_and_system_prompt(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen.update(url=url, json=json)
        return FakeResponse(200)

    monkeypatch.setattr(requests, "post", fake_post)
    assert lc.warmup_model(system_prompt="Du bist MIA.") is True
    assert seen["json"]["keep_alive"] == -1
    assert seen["json"]["messages"][0] == {"role": "system", "content": "Du bist MIA."}
    assert seen["json"]["messages"][-1] == {"role": "user", "content": "hi"}


# ── check_model_available ─────────────────────────────────────────────────────
def test_check_model_available_true_for_non_ollama_providers():
    _configure(llm_provider="anthropic")
    assert lc.check_model_available() is True


def test_check_model_available_true_when_model_is_pulled(monkeypatch):
    monkeypatch.setattr(requests, "get",
                         lambda *a, **k: FakeResponse(200, json_data={"models": [{"name": "llama3.2:latest"}]}))
    assert lc.check_model_available() is True


def test_check_model_available_false_and_logs_when_missing(monkeypatch):
    monkeypatch.setattr(requests, "get",
                         lambda *a, **k: FakeResponse(200, json_data={"models": [{"name": "mistral:latest"}]}))
    logs = []
    assert lc.check_model_available(log=logs.append) is False
    assert any("not found" in l for l in logs)


def test_check_model_available_true_on_request_exception(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert lc.check_model_available() is True


# ── Anthropic-Hilfsfunktionen ──────────────────────────────────────────────────
def test_split_system_separates_system_messages_from_conversation():
    messages = [{"role": "system", "content": "sei nett"}, {"role": "user", "content": "hi"}]
    system, convo = lc._split_system(messages)
    assert system == "sei nett"
    assert convo == [{"role": "user", "content": "hi"}]


def test_split_system_returns_none_when_no_system_message():
    system, convo = lc._split_system([{"role": "user", "content": "hi"}])
    assert system is None


def test_tools_to_anthropic_converts_openai_style_tools():
    tools = [{"function": {"name": "search", "description": "sucht", "parameters": {"type": "object"}}}]
    converted = lc._tools_to_anthropic(tools)
    assert converted == [{"name": "search", "description": "sucht", "input_schema": {"type": "object"}}]


def test_tools_to_anthropic_returns_none_for_empty_input():
    assert lc._tools_to_anthropic(None) is None
    assert lc._tools_to_anthropic([]) is None


def test_call_anthropic_without_key_raises():
    with pytest.raises(RuntimeError, match="Anthropic-API-Key"):
        lc._call_anthropic([{"role": "user", "content": "hi"}], None, timeout=5)


def test_call_anthropic_parses_text_and_tool_use_blocks(monkeypatch):
    _configure(anthropic_api_key="sk-ant-x")
    resp_json = {"content": [
        {"type": "text", "text": "Klar, "},
        {"type": "text", "text": "mache ich."},
        {"type": "tool_use", "id": "t1", "name": "open_app", "input": {"app": "Notizen"}},
    ]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data=resp_json))
    result = lc._call_anthropic([{"role": "user", "content": "öffne Notizen"}], None, timeout=5)
    assert result["content"] == "Klar, mache ich."
    assert result["tool_calls"] == [{"id": "t1", "function": {"name": "open_app", "arguments": {"app": "Notizen"}}}]


def test_call_anthropic_http_error_is_wrapped(monkeypatch):
    _configure(anthropic_api_key="sk-ant-x")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(429, text="rate limited"))
    with pytest.raises(RuntimeError, match="429"):
        lc._call_anthropic([{"role": "user", "content": "hi"}], None, timeout=5)


# ── call_llm ─────────────────────────────────────────────────────────────────
def test_call_llm_routes_anthropic_to_call_anthropic(monkeypatch):
    _configure(llm_provider="anthropic")
    monkeypatch.setattr(lc, "_call_anthropic", lambda *a, **k: {"content": "ok", "tool_calls": []})
    assert lc.call_llm([{"role": "user", "content": "hi"}]) == {"content": "ok", "tool_calls": []}


def test_call_llm_openai_normalises_tool_call_arguments(monkeypatch):
    _configure(llm_provider="openai")
    resp_json = {"choices": [{"message": {
        "content": "  klar  ",
        "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": '{"x": 1}'}}],
    }}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data=resp_json))
    result = lc.call_llm([{"role": "user", "content": "hi"}], tools=[{"function": {"name": "f"}}])
    assert result["content"] == "klar"
    assert result["tool_calls"] == [{"id": "1", "function": {"name": "f", "arguments": {"x": 1}}}]


def test_call_llm_openai_failure_raises_runtime_error(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(500))
    with pytest.raises(RuntimeError):
        lc.call_llm([{"role": "user", "content": "hi"}])


def test_call_llm_ollama_success(monkeypatch):
    resp_json = {"message": {"content": " klar ", "tool_calls": []}}
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data=resp_json))
    result = lc.call_llm([{"role": "user", "content": "hi"}])
    assert result == {"content": "klar", "tool_calls": []}


def test_call_llm_ollama_reconnects_after_connection_error(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError()
        return FakeResponse(200, json_data={"message": {"content": "ok", "tool_calls": []}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda: True)
    result = lc.call_llm([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert calls["n"] == 2


def test_call_llm_ollama_gives_up_when_restart_fails(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda: False)
    with pytest.raises(RuntimeError, match="Cannot connect to Ollama"):
        lc.call_llm([{"role": "user", "content": "hi"}])


def test_call_llm_ollama_timeout_raises(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.Timeout()))
    with pytest.raises(RuntimeError, match="timed out"):
        lc.call_llm([{"role": "user", "content": "hi"}])


# ── call_llm_text ────────────────────────────────────────────────────────────
def test_call_llm_text_anthropic_returns_content_only(monkeypatch):
    _configure(llm_provider="anthropic")
    monkeypatch.setattr(lc, "_call_anthropic", lambda *a, **k: {"content": "Antworttext", "tool_calls": []})
    assert lc.call_llm_text("Frage") == "Antworttext"


def test_call_llm_text_ollama_success(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, json_data={"message": {"content": " hi "}}))
    assert lc.call_llm_text("Frage") == "hi"


def test_call_llm_text_ollama_reconnects_after_connection_error(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError()
        return FakeResponse(200, json_data={"message": {"content": "wieder da"}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda: True)
    assert lc.call_llm_text("Frage") == "wieder da"


# ── Streaming ────────────────────────────────────────────────────────────────
def _sse_lines(*payloads) -> list:
    return [f"data: {json.dumps(p)}".encode() for p in payloads] + [b"data: [DONE]"]


def test_stream_openai_yields_sentences_then_done_with_tool_calls(monkeypatch):
    _configure(llm_provider="openai")
    payloads = [
        {"choices": [{"delta": {"content": "Hallo. "}}]},
        {"choices": [{"delta": {"content": "Wie geht's?"}, "finish_reason": "stop"}]},
    ]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=_sse_lines(*payloads)))
    events = list(lc._stream_openai([{"role": "user", "content": "hi"}], None, timeout=5))
    assert {"type": "sentence", "text": "Hallo."} in events
    done = events[-1]
    assert done["type"] == "done"
    assert "Wie geht's?" in done["content"]


def test_stream_openai_accumulates_streamed_tool_call_fragments(monkeypatch):
    _configure(llm_provider="openai")
    payloads = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t1",
                                                 "function": {"name": "open_", "arguments": '{"a"'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0,
                                                 "function": {"name": "app", "arguments": ': 1}'}}]},
                      "finish_reason": "tool_calls"}]},
    ]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=_sse_lines(*payloads)))
    events = list(lc._stream_openai([{"role": "user", "content": "hi"}], None, timeout=5))
    done = events[-1]
    assert done["tool_calls"] == [{"id": "t1", "function": {"name": "open_app", "arguments": {"a": 1}}}]


def test_stream_anthropic_without_key_raises():
    with pytest.raises(RuntimeError, match="Anthropic-API-Key"):
        list(lc._stream_anthropic([{"role": "user", "content": "hi"}], None, timeout=5))


def test_stream_anthropic_yields_text_and_tool_use(monkeypatch):
    _configure(anthropic_api_key="sk-ant-x")
    events_in = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hallo. Klar."}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "open_app"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"app": "Notizen"}'}},
        {"type": "message_stop"},
    ]
    lines = [f"data: {json.dumps(e)}".encode() for e in events_in]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=lines))
    events = list(lc._stream_anthropic([{"role": "user", "content": "hi"}], None, timeout=5))
    sentences = [e["text"] for e in events if e["type"] == "sentence"]
    assert "Hallo." in sentences
    done = events[-1]
    assert done["tool_calls"] == [{"id": "t1", "function": {"name": "open_app", "arguments": {"app": "Notizen"}}}]


def test_call_llm_stream_routes_to_anthropic(monkeypatch):
    _configure(llm_provider="anthropic")
    monkeypatch.setattr(lc, "_stream_anthropic", lambda *a, **k: iter([{"type": "done", "content": "x", "tool_calls": []}]))
    assert list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))[0]["content"] == "x"


def test_call_llm_stream_routes_to_openai(monkeypatch):
    _configure(llm_provider="openai")
    monkeypatch.setattr(lc, "_stream_openai", lambda *a, **k: iter([{"type": "done", "content": "y", "tool_calls": []}]))
    assert list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))[0]["content"] == "y"


def test_call_llm_stream_ollama_yields_sentences_and_done(monkeypatch):
    lines = [
        json.dumps({"message": {"content": "Hallo. "}, "done": False}).encode(),
        json.dumps({"message": {"content": "Tschuess."}, "done": True}).encode(),
    ]
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(200, lines=lines))
    events = list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))
    assert {"type": "sentence", "text": "Hallo."} in events
    assert events[-1]["type"] == "done"
    assert "Tschuess." in events[-1]["content"]


def test_call_llm_stream_ollama_reconnects_after_connection_error(monkeypatch):
    calls = {"n": 0}
    good_lines = [json.dumps({"message": {"content": "wieder da"}, "done": True}).encode()]

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError()
        return FakeResponse(200, lines=good_lines)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda: True)
    events = list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))
    assert events[-1]["content"] == "wieder da"
