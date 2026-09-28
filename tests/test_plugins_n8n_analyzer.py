"""Tests für plugins/n8n_analyzer.py — n8n-Analyse-Plugin (requests gemockt)."""
import json

import pytest
import requests

from plugins import n8n_analyzer as n8n


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, responses):
        """responses: dict path -> FakeResponse | Exception"""
        self.headers = {}
        self._responses = responses
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for path, resp in self._responses.items():
            if url.endswith(path):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeResponse(404)


def _analyzer(responses: dict) -> n8n.N8nAnalyzer:
    a = n8n.N8nAnalyzer("http://localhost:3000")
    a.session = FakeSession(responses)
    return a


# ── _load_api_key ────────────────────────────────────────────────────────────
def test_load_api_key_prefers_environment_variable(monkeypatch):
    monkeypatch.setenv("N8N_API_KEY", "env-key")
    assert n8n._load_api_key() == "env-key"


def test_load_api_key_falls_back_to_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('N8N_API_KEY="file-key"\n', encoding="utf-8")
    assert n8n._load_api_key() == "file-key"


def test_load_api_key_returns_empty_when_nowhere_found(monkeypatch, tmp_path):
    monkeypatch.delenv("N8N_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert n8n._load_api_key() == ""


# ── N8nAnalyzer: einzelne Methoden ────────────────────────────────────────────
def test_test_connection_true_via_healthz():
    a = _analyzer({"/healthz": FakeResponse(200)})
    assert a.test_connection() is True


def test_test_connection_false_when_both_endpoints_fail():
    a = _analyzer({"/healthz": FakeResponse(500), "/rest/settings": FakeResponse(500)})
    assert a.test_connection() is False


def test_test_connection_records_last_error_on_exception():
    a = _analyzer({"/healthz": requests.RequestException("kein netz")})
    a.test_connection()
    assert "kein netz" in a.last_error


def test_get_workflows_returns_data_list():
    a = _analyzer({"/api/v1/workflows": FakeResponse(200, {"data": [{"id": "wf1"}]})})
    assert a.get_workflows() == [{"id": "wf1"}]


def test_get_workflows_empty_on_non_200():
    a = _analyzer({"/api/v1/workflows": FakeResponse(500)})
    assert a.get_workflows() == []
    assert "HTTP 500" in a.last_error


def test_get_workflows_empty_on_exception():
    a = _analyzer({"/api/v1/workflows": requests.RequestException("boom")})
    assert a.get_workflows() == []


def test_get_nodes_returns_list_on_success():
    a = _analyzer({"/types/nodes.json": FakeResponse(200, [{"name": "n1"}])})
    assert a.get_nodes() == [{"name": "n1"}]


def test_get_nodes_empty_on_failure():
    a = _analyzer({"/types/nodes.json": FakeResponse(500)})
    assert a.get_nodes() == []


def test_get_credentials_returns_data_list():
    a = _analyzer({"/api/v1/credentials": FakeResponse(200, {"data": [{"id": "c1"}]})})
    assert a.get_credentials() == [{"id": "c1"}]


def test_get_credentials_empty_on_failure():
    a = _analyzer({"/api/v1/credentials": requests.RequestException("boom")})
    assert a.get_credentials() == []


# ── analyze_all ──────────────────────────────────────────────────────────────
def test_analyze_all_builds_report_from_live_data():
    a = _analyzer({
        "/healthz": FakeResponse(200),
        "/api/v1/workflows": FakeResponse(200, {"data": [
            {"id": "wf1", "name": "Aktiv", "active": True, "nodes": [1, 2]},
            {"id": "wf2", "name": "Qdrant Sync", "active": False, "nodes": [1], "extra": "qdrant"},
        ]}),
        "/types/nodes.json": FakeResponse(200, [1] * 150),
        "/api/v1/credentials": FakeResponse(200, {"data": [{"id": "c1"}]}),
    })
    result = a.analyze_all()
    assert result["connected"] is True
    assert result["mode"] == "live"
    assert result["inactive_count"] == 1
    assert [w["id"] for w in result["qdrant_workflows"]] == ["wf2"]
    assert result["nodes_total"] == 150
    assert any("inactive workflows" in r for r in result["recommendations"])


def test_analyze_all_falls_back_to_mock_when_unreachable():
    a = _analyzer({"/healthz": FakeResponse(500), "/rest/settings": FakeResponse(500)})
    result = a.analyze_all()
    assert result["mode"] == "mock"
    assert result["connected"] is False
    assert len(result["workflows"]) == 5


def test_generate_recommendations_covers_each_branch():
    a = n8n.N8nAnalyzer()
    analysis = {
        "inactive_count": 2,
        "qdrant_workflows": [{"name": "x"}],
        "nodes_total": 200,
        "credentials_total": 1,
        "workflows": [{"name": "Normal Flow"}],
    }
    recs = a._generate_recommendations(analysis)
    assert any("inactive workflows" in r for r in recs)
    assert any("Qdrant integration identified" in r for r in recs)
    assert any("Rich node library" in r for r in recs)
    assert any("Few credentials" in r for r in recs)
    assert any("No Qdrant-specific workflows" in r for r in recs)


# ── run() ──────────────────────────────────────────────────────────────────
def test_run_analyze_writes_report_and_returns_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(n8n.N8nAnalyzer, "test_connection", lambda self: True)
    monkeypatch.setattr(n8n.N8nAnalyzer, "get_workflows", lambda self: [
        {"id": "wf1", "name": "Sync", "active": False, "nodes": [1]}])
    monkeypatch.setattr(n8n.N8nAnalyzer, "get_nodes", lambda self: [])
    monkeypatch.setattr(n8n.N8nAnalyzer, "get_credentials", lambda self: [])

    class FakePlayer:
        def __init__(self):
            self.logs = []
        def write_log(self, msg):
            self.logs.append(msg)

    player = FakePlayer()
    output = n8n.run({"action": "analyze"}, player=player)
    assert "n8n INFRASTRUCTURE ANALYSIS" in output
    assert (tmp_path / "n8n_analysis_report.json").exists()
    assert player.logs


def test_run_status_reports_connected(monkeypatch):
    monkeypatch.setattr(n8n.N8nAnalyzer, "test_connection", lambda self: True)
    monkeypatch.setattr(n8n.N8nAnalyzer, "get_workflows", lambda self: [1, 2])
    monkeypatch.setattr(n8n.N8nAnalyzer, "get_nodes", lambda self: [1])
    result = n8n.run({"action": "status"})
    assert "n8n is running" in result
    assert "2 workflows" in result


def test_run_status_reports_unreachable(monkeypatch):
    monkeypatch.setattr(n8n.N8nAnalyzer, "test_connection", lambda self: False)
    result = n8n.run({"action": "status", "n8n_url": "http://x:3000"})
    assert "Cannot reach n8n" in result


def test_run_unknown_action_is_reported():
    result = n8n.run({"action": "does-not-exist"})
    assert "Unknown action" in result


def test_run_catches_unexpected_exceptions(monkeypatch):
    def boom(self):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(n8n.N8nAnalyzer, "test_connection", boom)
    result = n8n.run({"action": "status"})
    assert "n8n_analyzer failed" in result and "kaputt" in result


def test_plugin_declaration_shape():
    assert n8n.PLUGIN["name"] == "n8n_analyzer"
    assert n8n.PLUGIN["parameters"]["type"] == "OBJECT"
