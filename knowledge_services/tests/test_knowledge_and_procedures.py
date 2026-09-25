"""Tests für knowledge_service und procedural_brain (Flask-Testclient, ohne Docker)."""
import json
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mia-svc-test-")
os.environ["MIA_SERVICE_TOKEN"] = "test-token"
os.environ["KNOWLEDGE_DB_PATH"] = os.path.join(_TMP, "knowledge")
os.environ["PROCEDURE_DB_PATH"] = os.path.join(_TMP, "procedures")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import knowledge_service  # noqa: E402
import procedural_brain  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("MIA_SERVICE_TOKEN", TOKEN)


@pytest.fixture
def kb():
    return knowledge_service.app.test_client()


@pytest.fixture
def proc():
    return procedural_brain.app.test_client()


# ── Anmeldung ────────────────────────────────────────────────────────────
def test_knowledge_health_needs_no_token(kb):
    assert kb.get("/health").get_json()["status"] == "ok"


def test_knowledge_requires_token(kb):
    assert kb.get("/knowledge").status_code == 401


def test_procedures_requires_token(proc):
    assert proc.get("/procedures").status_code == 401
    assert proc.post("/procedures", json={"problem": "p", "solution": "s"}).status_code == 401


# ── Wissensdatenbank ─────────────────────────────────────────────────────
def _write_doc(name, payload):
    path = os.path.join(os.environ["KNOWLEDGE_DB_PATH"], name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_list_documents_returns_sorted_json_filenames(kb):
    _write_doc("z-doc.json", {"a": 1})
    _write_doc("a-doc.json", {"b": 2})
    _write_doc("ignore-me.txt", "kein json")
    names = kb.get("/knowledge", headers=AUTH).get_json()["documents"]
    assert names == sorted(n for n in names)
    assert "a-doc.json" in names and "z-doc.json" in names and "ignore-me.txt" not in names


def test_get_document_returns_its_content(kb):
    _write_doc("wissen.json", {"titel": "Führerschein-Regel", "wert": 42})
    got = kb.get("/knowledge/wissen.json", headers=AUTH).get_json()
    assert got == {"titel": "Führerschein-Regel", "wert": 42}


def test_get_document_unknown_is_404(kb):
    assert kb.get("/knowledge/gibt-es-nicht.json", headers=AUTH).status_code == 404


@pytest.mark.parametrize("name", ["../secret.json", "a/b.json", "ohne-endung", ""])
def test_get_document_rejects_bad_names(kb, name):
    assert kb.get(f"/knowledge/{name}", headers=AUTH).status_code in (400, 404)


def test_get_document_blocks_path_traversal_outside_dir(kb):
    outside = os.path.join(tempfile.gettempdir(), "mia-outside-secret.json")
    with open(outside, "w", encoding="utf-8") as f:
        json.dump({"geheim": True}, f)
    try:
        resp = kb.get("/knowledge/..%2f..%2f..%2f..%2ftmp%2fmia-outside-secret.json", headers=AUTH)
        assert resp.status_code in (400, 404)
    finally:
        os.remove(outside)


# ── Prozedurales Gedächtnis ──────────────────────────────────────────────
def test_add_procedure_stores_and_returns_id(proc):
    resp = proc.post("/procedures", json={"problem": "Reifenpanne", "solution": "Ersatzrad montieren"}, headers=AUTH)
    assert resp.status_code == 201
    assert isinstance(resp.get_json()["id"], int)


@pytest.mark.parametrize("payload", [{}, {"problem": "nur problem"}, {"solution": "nur loesung"},
                                      {"problem": "", "solution": "x"}, {"problem": "x", "solution": ""}])
def test_add_procedure_rejects_missing_fields(proc, payload):
    assert proc.post("/procedures", json=payload, headers=AUTH).status_code == 400


def test_find_procedures_filters_by_problem_substring(proc):
    proc.post("/procedures", json={"problem": "Reifenpanne unterwegs", "solution": "Ersatzrad"}, headers=AUTH)
    proc.post("/procedures", json={"problem": "Motor überhitzt", "solution": "Kühlwasser nachfüllen"}, headers=AUTH)
    hits = proc.get("/procedures?q=Reifen", headers=AUTH).get_json()["procedures"]
    assert any(h["problem"] == "Reifenpanne unterwegs" for h in hits)
    assert all("Motor" not in h["problem"] for h in hits)


def test_find_procedures_without_query_lists_recent_first(proc):
    proc.post("/procedures", json={"problem": "p1", "solution": "s1"}, headers=AUTH)
    proc.post("/procedures", json={"problem": "p2", "solution": "s2"}, headers=AUTH)
    hits = proc.get("/procedures", headers=AUTH).get_json()["procedures"]
    assert hits[0]["problem"] == "p2"


def test_find_procedures_sql_metacharacters_are_harmless(proc):
    proc.post("/procedures", json={"problem": "x", "solution": "y"}, headers=AUTH)
    resp = proc.get("/procedures?q=" + "%' OR '1'='1", headers=AUTH)
    assert resp.status_code == 200
