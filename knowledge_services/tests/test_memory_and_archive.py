"""Tests für memory_service und session_archive (Flask-Testclient, ohne Docker)."""
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="mia-svc-test-")
os.environ["MIA_SERVICE_TOKEN"] = "test-token"     # ohne Token startet ein Dienst nicht (fail-closed)
os.environ["MEMORY_DB_PATH"] = os.path.join(_TMP, "memory")
os.environ["ARCHIVE_PATH"] = os.path.join(_TMP, "sessions")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import memory_service  # noqa: E402
import session_archive  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("MIA_SERVICE_TOKEN", TOKEN)


@pytest.fixture
def mem():
    return memory_service.app.test_client()


@pytest.fixture
def arch():
    return session_archive.app.test_client()


def item(i, text="Christoph hat keinen Führerschein", **extra):
    return {"id": i, "text": text, "actor": "user", "pinned": 0, "created_at": "2026-09-24T10:00:00Z", **extra}


# ── Anmeldung ────────────────────────────────────────────────────────────
def test_health_needs_no_token(mem):
    assert mem.get("/health").get_json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer falsch"}, {"Authorization": TOKEN}])
def test_requests_without_valid_token_are_rejected(mem, headers):
    assert mem.get("/memory/count", headers=headers).status_code == 401
    assert mem.post("/memory/bulk", json={"items": [item("x")]}, headers=headers).status_code == 401


def test_service_refuses_to_start_without_token(monkeypatch):
    from _common import create_app
    monkeypatch.delenv("MIA_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MIA_ALLOW_NO_AUTH", raising=False)
    with pytest.raises(RuntimeError, match="MIA_SERVICE_TOKEN"):
        create_app("x")
    monkeypatch.setenv("MIA_SERVICE_TOKEN", "")
    with pytest.raises(RuntimeError):
        create_app("x")


def test_token_lost_at_runtime_fails_closed(mem, monkeypatch):
    monkeypatch.setenv("MIA_SERVICE_TOKEN", "")
    monkeypatch.delenv("MIA_ALLOW_NO_AUTH", raising=False)
    assert mem.get("/memory/count").status_code == 503
    assert mem.get("/health").status_code == 200


def test_explicit_opt_out_allows_open_service(mem, monkeypatch):
    from _common import create_app
    monkeypatch.setenv("MIA_SERVICE_TOKEN", "")
    monkeypatch.setenv("MIA_ALLOW_NO_AUTH", "1")
    create_app("dev")            # wirft nicht
    assert mem.get("/memory/count").status_code == 200


@pytest.mark.parametrize("path", ["/Health", "/health/", "//health", "/health/../memory/count"])
def test_health_lookalikes_need_token(mem, path):
    assert mem.get(path).status_code in (401, 404)


# ── Erinnerungen ─────────────────────────────────────────────────────────
def test_bulk_upsert_stores_and_updates(mem):
    assert mem.post("/memory/bulk", json={"items": [item("m-up1"), item("m-up2")]}, headers=AUTH).get_json() == {"stored": 2, "truncated": 0}
    mem.post("/memory/bulk", json={"items": [item("m-up1", text="neu", pinned=1)]}, headers=AUTH)
    row = mem.get("/memory/m-up1", headers=AUTH).get_json()
    assert (row["text"], row["pinned"], row["deleted"]) == ("neu", 1, 0)


def test_soft_delete_keeps_marker_and_bulk_restores(mem):
    mem.post("/memory/bulk", json={"items": [item("m-del1")]}, headers=AUTH)
    assert mem.post("/memory/delete", json={"ids": ["m-del1"]}, headers=AUTH).get_json() == {"deleted": 1}
    assert mem.get("/memory/m-del1", headers=AUTH).get_json()["deleted"] == 1
    live_ids = [m["id"] for m in mem.get("/memory?limit=1000", headers=AUTH).get_json()["memories"]]
    assert "m-del1" not in live_ids
    all_ids = [m["id"] for m in mem.get("/memory?include_deleted=1&limit=1000", headers=AUTH).get_json()["memories"]]
    assert "m-del1" in all_ids
    mem.post("/memory/bulk", json={"items": [item("m-del1")]}, headers=AUTH)
    assert mem.get("/memory/m-del1", headers=AUTH).get_json()["deleted"] == 0


def test_purge_removes_row(mem):
    mem.post("/memory/bulk", json={"items": [item("m-purge")]}, headers=AUTH)
    mem.post("/memory/delete", json={"ids": ["m-purge"], "purge": True}, headers=AUTH)
    assert mem.get("/memory/m-purge", headers=AUTH).status_code == 404


def test_count_reports_live_and_deleted(mem):
    before = mem.get("/memory/count", headers=AUTH).get_json()
    mem.post("/memory/bulk", json={"items": [item("m-c1"), item("m-c2")]}, headers=AUTH)
    mem.post("/memory/delete", json={"ids": ["m-c2"]}, headers=AUTH)
    after = mem.get("/memory/count", headers=AUTH).get_json()
    assert after["live"] == before["live"] + 1 and after["deleted"] == before["deleted"] + 1


@pytest.mark.parametrize("payload", [
    {}, {"items": []}, {"items": "x"}, {"items": [{"text": "ohne id"}]}, {"items": [{"id": "a"}]},
    {"items": [{"id": "", "text": "leer"}]}, {"items": [item(str(n)) for n in range(501)]},
])
def test_bulk_rejects_bad_input(mem, payload):
    assert mem.post("/memory/bulk", json=payload, headers=AUTH).status_code == 400


def test_bulk_rejects_non_json_body(mem):
    assert mem.post("/memory/bulk", data="kein json", headers=AUTH).status_code == 400


@pytest.mark.parametrize("payload", [{}, {"ids": []}, {"ids": [1, 2]}, {"ids": "a"}])
def test_delete_rejects_bad_input(mem, payload):
    assert mem.post("/memory/delete", json=payload, headers=AUTH).status_code == 400


def test_list_rejects_non_numeric_paging(mem):
    assert mem.get("/memory?limit=abc", headers=AUTH).status_code == 400


def test_sql_metacharacters_in_ids_are_harmless(mem):
    evil = "x'); DROP TABLE memories;--"
    mem.post("/memory/bulk", json={"items": [item(evil)]}, headers=AUTH)
    mem.post("/memory/delete", json={"ids": [evil]}, headers=AUTH)
    assert mem.get("/memory/count", headers=AUTH).status_code == 200


# ── Gesprächsarchiv ──────────────────────────────────────────────────────
def conv(messages=None, title="Fahrdienst"):
    return {"title": title, "actor": "user", "created_at": "2026-09-24T09:00:00Z",
            "updated_at": "2026-09-24T09:05:00Z",
            "messages": messages if messages is not None else [
                {"role": "user", "content": "Wer holt Christoph ab?", "created_at": "2026-09-24T09:00:00Z"},
                {"role": "assistant", "content": "Er hat keinen Führerschein, ich rufe ein Taxi.",
                 "created_at": "2026-09-24T09:00:05Z"}]}


def test_session_put_is_idempotent_and_get_returns_messages(arch):
    assert arch.put("/sessions/c-1", json=conv(), headers=AUTH).get_json() == {"id": "c-1", "messages": 2, "truncated": 0}
    arch.put("/sessions/c-1", json=conv(title="Umbenannt"), headers=AUTH)
    got = arch.get("/sessions/c-1", headers=AUTH).get_json()
    assert got["title"] == "Umbenannt" and got["message_count"] == 2 and got["messages"][1]["role"] == "assistant"
    ids = [s["id"] for s in arch.get("/sessions", headers=AUTH).get_json()["sessions"]]
    assert ids.count("c-1") == 1


def test_session_search_finds_by_content_with_snippet(arch):
    arch.put("/sessions/c-s1", json=conv(), headers=AUTH)
    hits = arch.get("/sessions/search?q=Führerschein", headers=AUTH).get_json()["results"]
    assert any(h["id"] == "c-s1" and "Führerschein" in h["snippet"] for h in hits)


def test_session_search_treats_wildcards_literally(arch):
    arch.put("/sessions/c-w1", json=conv([{"role": "user", "content": "Rabatt 50% heute"}], title="Preis"), headers=AUTH)
    arch.put("/sessions/c-w2", json=conv([{"role": "user", "content": "nichts Besonderes"}], title="Anderes"), headers=AUTH)
    ids = [h["id"] for h in arch.get("/sessions/search?q=%25", headers=AUTH).get_json()["results"]]
    assert "c-w1" in ids and "c-w2" not in ids


def test_session_search_needs_query(arch):
    assert arch.get("/sessions/search", headers=AUTH).status_code == 400


def test_session_unknown_id_is_404(arch):
    assert arch.get("/sessions/gibt-es-nicht", headers=AUTH).status_code == 404


@pytest.mark.parametrize("payload", [{}, {"messages": "x"}, {"messages": ["kein objekt"]}])
def test_session_rejects_bad_input(arch, payload):
    assert arch.put("/sessions/c-bad", json=payload, headers=AUTH).status_code == 400


def test_session_requires_token(arch):
    assert arch.get("/sessions").status_code == 401


# ── Befunde aus dem Review ───────────────────────────────────────────────
def test_archive_refuses_to_shrink_a_stored_conversation(arch):
    arch.put("/sessions/c-shrink", json=conv(), headers=AUTH)                       # 2 Nachrichten
    small = conv([{"role": "user", "content": "nur noch eine"}])
    resp = arch.put("/sessions/c-shrink", json=small, headers=AUTH)
    assert resp.status_code == 409 and resp.get_json()["stored"] == 2 and resp.get_json()["received"] == 1
    assert arch.get("/sessions/c-shrink", headers=AUTH).get_json()["message_count"] == 2
    assert arch.put("/sessions/c-shrink?force=1", json=small, headers=AUTH).status_code == 200
    assert arch.get("/sessions/c-shrink", headers=AUTH).get_json()["message_count"] == 1


def test_archive_accepts_equal_or_larger_stand(arch):
    arch.put("/sessions/c-grow", json=conv(), headers=AUTH)
    bigger = conv(conv()["messages"] + [{"role": "user", "content": "Danke"}])
    assert arch.put("/sessions/c-grow", json=conv(), headers=AUTH).status_code == 200
    assert arch.put("/sessions/c-grow", json=bigger, headers=AUTH).status_code == 200


def test_truncation_is_reported_not_silent(arch, mem):
    long_text = "x" * (session_archive.MAX_CONTENT + 50)
    resp = arch.put("/sessions/c-long", json=conv([{"role": "user", "content": long_text}]), headers=AUTH)
    assert resp.get_json()["truncated"] == 1
    got = arch.get("/sessions/c-long", headers=AUTH).get_json()
    assert len(got["messages"][0]["content"]) == session_archive.MAX_CONTENT
    resp = mem.post("/memory/bulk", json={"items": [item("m-long", text="y" * (memory_service.MAX_TEXT + 1))]}, headers=AUTH)
    assert resp.get_json() == {"stored": 1, "truncated": 1}


def test_oversized_body_is_rejected_with_413(arch):
    from _common import MAX_BODY_BYTES
    big = "z" * (MAX_BODY_BYTES + 1024)
    resp = arch.put("/sessions/c-huge", data=big, headers={**AUTH, "Content-Type": "application/json"})
    assert resp.status_code == 413 and "error" in resp.get_json()


def test_search_query_is_length_limited_not_an_error(arch):
    arch.put("/sessions/c-q", json=conv(), headers=AUTH)
    assert arch.get("/sessions/search?q=" + "a" * 60000, headers=AUTH).status_code == 200


def test_connections_are_released(mem):
    import gc
    import sqlite3
    for _ in range(300):
        mem.get("/memory/count", headers=AUTH)
    gc.collect()
    conn = sqlite3.connect(os.path.join(os.environ["MEMORY_DB_PATH"], "memory.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
