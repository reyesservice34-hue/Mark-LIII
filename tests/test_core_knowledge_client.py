"""Tests für core/knowledge_client.py — HTTP-Client zu den optionalen MIA-KNOWLEDGE-01-Diensten.

Alles hier ist per Design aus (nichts ruft je nach draußen) bis "knowledge_host" und
"knowledge_service_token" in api_keys.json stehen. Diese Tests setzen die Konfiguration
über eine Testdatei und mocken requests.request, damit nichts wirklich über das Netz geht.
"""
import json

import pytest
import requests

from core import knowledge_client as kc


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", content=b"x"):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_body
        self.text = text
        self.content = content

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "CONFIG_PATH", tmp_path / "api_keys.json")
    yield


def _configure(host="http://mia.example.com", token="secret-token", **extra):
    kc.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"knowledge_host": host, "knowledge_service_token": token, **extra}
    kc.CONFIG_PATH.write_text(json.dumps(data), encoding="utf-8")


# ── Konfiguration / An-Aus-Schalter ─────────────────────────────────────────
def test_disabled_by_default_when_config_file_is_missing():
    assert kc.is_enabled() is False
    assert kc.is_semantic_enabled() is False
    assert kc.get_host() == ""
    assert kc.get_token() == ""


def test_disabled_when_only_host_is_set():
    _configure(host="http://x", token="")
    assert kc.is_enabled() is False


def test_enabled_once_host_and_token_are_both_set():
    _configure()
    assert kc.is_enabled() is True
    assert kc.is_semantic_enabled() is True


def test_get_host_strips_trailing_slash():
    _configure(host="http://mia.example.com/")
    assert kc.get_host() == "http://mia.example.com"


def test_load_config_recovers_from_corrupt_json():
    kc.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    kc.CONFIG_PATH.write_text("{ kaputt", encoding="utf-8")
    assert kc.get_host() == ""
    assert kc.is_enabled() is False


def test_get_vector_api_key_defaults_to_empty_string():
    _configure()
    assert kc.get_vector_api_key() == ""
    _configure(qdrant_api_key="  qkey  ")
    assert kc.get_vector_api_key() == "qkey"


# ── _request(): das gemeinsame Fundament aller Flask-Dienst-Aufrufe ─────────
def test_request_returns_none_without_calling_requests_when_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: called.append(1))
    assert kc._request("GET", "memory", "/memory/count") is None
    assert called == []


def test_request_builds_url_with_correct_port_and_auth_header(monkeypatch):
    _configure()
    seen = {}

    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        seen.update(method=method, url=url, headers=headers, timeout=timeout)
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(requests, "request", fake_request)
    result = kc._request("GET", "memory", "/memory/count")
    assert result == {"ok": True}
    assert seen["url"] == "http://mia.example.com:8001/memory/count"
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["timeout"] == kc._TIMEOUT


def test_request_returns_none_on_non_ok_status(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(500, text="boom"))
    assert kc._request("GET", "memory", "/memory/count") is None


def test_request_returns_empty_dict_for_empty_body(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(204, content=b""))
    assert kc._request("POST", "memory", "/memory/bulk") == {}


def test_request_returns_none_on_connection_error(monkeypatch):
    _configure()

    def raise_conn_err(*a, **k):
        raise requests.exceptions.ConnectionError("kein Netz")

    monkeypatch.setattr(requests, "request", raise_conn_err)
    assert kc._request("GET", "memory", "/memory/count") is None


def test_request_returns_none_on_unexpected_exception(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    assert kc._request("GET", "memory", "/memory/count") is None


# ── Memory-Dienst ────────────────────────────────────────────────────────────
def test_memory_upsert_sends_expected_body(monkeypatch):
    _configure()
    seen = {}
    monkeypatch.setattr(requests, "request",
                         lambda method, url, headers=None, timeout=None, **k: (seen.update(k), FakeResponse(200, {}))[1])
    ok = kc.memory_upsert("m1", "Text", actor="user", pinned=True, created_at="2026-01-01")
    assert ok is True
    assert seen["json"]["items"] == [{"id": "m1", "text": "Text", "actor": "user",
                                       "pinned": True, "created_at": "2026-01-01"}]


def test_memory_delete_with_empty_ids_is_a_noop_success(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: called.append(1))
    assert kc.memory_delete([]) is True
    assert called == []


def test_memory_delete_sends_ids_and_purge_flag(monkeypatch):
    _configure()
    seen = {}
    monkeypatch.setattr(requests, "request",
                         lambda method, url, headers=None, timeout=None, **k: (seen.update(k), FakeResponse(200, {}))[1])
    kc.memory_delete(["a", "b"], purge=True)
    assert seen["json"] == {"ids": ["a", "b"], "purge": True}


# ── Prozedurales Gedächtnis ──────────────────────────────────────────────────
def test_procedure_add_rejects_missing_fields_without_a_network_call(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: called.append(1))
    assert kc.procedure_add("", "solution") is False
    assert kc.procedure_add("problem", "") is False
    assert called == []


def test_procedure_find_returns_list_from_response(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request",
                         lambda *a, **k: FakeResponse(200, {"procedures": [{"id": 1}]}))
    assert kc.procedure_find("q") == [{"id": 1}]


def test_procedure_find_returns_empty_list_when_disabled():
    assert kc.procedure_find("q") == []


# ── Sitzungsarchiv ───────────────────────────────────────────────────────────
def test_archive_session_requires_id_and_messages():
    assert kc.archive_session("", "t", "a", [{"role": "user"}]) is False
    assert kc.archive_session("id", "t", "a", []) is False


def test_archive_session_puts_to_expected_path(monkeypatch):
    _configure()
    seen = {}
    monkeypatch.setattr(requests, "request",
                         lambda method, url, headers=None, timeout=None, **k: (seen.update(method=method, url=url), FakeResponse(200, {}))[1])
    kc.archive_session("c-1", "Titel", "user", [{"role": "user", "content": "hi"}])
    assert seen["method"] == "PUT"
    assert seen["url"].endswith(":8004/sessions/c-1")


# ── Wissensdatenbank ─────────────────────────────────────────────────────────
def test_knowledge_list_and_get(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request",
                         lambda method, url, **k: FakeResponse(200, {"documents": ["a.json"]}) if "GET" == method
                         else FakeResponse(200, {}))
    assert kc.knowledge_list() == ["a.json"]


def test_knowledge_list_empty_when_disabled():
    assert kc.knowledge_list() == []


# ── Embeddings ───────────────────────────────────────────────────────────────
def test_embed_texts_returns_none_for_empty_input():
    assert kc.embed_texts([]) is None


def test_embed_texts_returns_vectors(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, {"embeddings": [[0.1, 0.2]]}))
    assert kc.embed_texts(["hallo"]) == [[0.1, 0.2]]


def test_embed_texts_returns_none_when_response_has_no_embeddings_list(monkeypatch):
    _configure()
    monkeypatch.setattr(requests, "request", lambda *a, **k: FakeResponse(200, {"embeddings": "kaputt"}))
    assert kc.embed_texts(["hallo"]) is None


# ── Qdrant (assoziatives Gedächtnis) ─────────────────────────────────────────
def test_point_id_is_stable_for_the_same_item_id():
    assert kc._point_id("notes:idee") == kc._point_id("notes:idee")
    assert kc._point_id("notes:idee") != kc._point_id("notes:andere-idee")


def test_qdrant_headers_include_api_key_only_when_configured():
    assert "api-key" not in kc._qdrant_headers()
    _configure(qdrant_api_key="qkey")
    assert kc._qdrant_headers()["api-key"] == "qkey"


def test_semantic_memory_upsert_returns_false_without_embeddings(monkeypatch):
    _configure()
    monkeypatch.setattr(kc, "embed_texts", lambda texts: None)
    assert kc.semantic_memory_upsert("id1", "text") is False


def test_semantic_memory_upsert_creates_collection_then_upserts_point(monkeypatch):
    _configure()
    monkeypatch.setattr(kc, "embed_texts", lambda texts: [[0.1, 0.2, 0.3]])
    calls = []

    def fake_qdrant(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return None  # collection does not exist yet
        return {}

    monkeypatch.setattr(kc, "_qdrant_request", fake_qdrant)
    ok = kc.semantic_memory_upsert("notes:idee", "Text hier", {"category": "notes"})
    assert ok is True
    methods_paths = [(m, p) for m, p, _ in calls]
    assert ("GET", "/collections/mia_memory") in methods_paths
    assert ("PUT", "/collections/mia_memory") in methods_paths
    assert ("PUT", "/collections/mia_memory/points") in methods_paths


def test_semantic_memory_search_returns_empty_list_without_embeddings(monkeypatch):
    monkeypatch.setattr(kc, "embed_texts", lambda texts: None)
    assert kc.semantic_memory_search("frage") == []


def test_semantic_memory_search_maps_results_with_score_and_payload(monkeypatch):
    _configure()
    monkeypatch.setattr(kc, "embed_texts", lambda texts: [[0.1]])
    monkeypatch.setattr(kc, "_qdrant_request",
                         lambda method, path, **k: {"result": [{"score": 0.9, "payload": {"text": "gefunden"}}]})
    hits = kc.semantic_memory_search("frage")
    assert hits == [{"score": 0.9, "text": "gefunden"}]
