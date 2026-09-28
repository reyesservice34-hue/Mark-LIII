"""Tests for the Mia UI-action channel, dashboard awareness, safe file resolution,
assistant name injection and the new status endpoints."""
import asyncio
import json
import logging

import pytest

from conftest import pair


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def next_action(ws):
    """Skip the replayed chat history (sys/log lines) and return the next ui_action."""
    for _ in range(20):
        msg = ws.receive_json()
        if msg.get("type") == "ui_action":
            return msg
    raise AssertionError("no ui_action received")


# ── safe file resolution ─────────────────────────────────────────────────────
def test_resolve_upload_rejects_unsafe_names(server, tmp_path):
    (server._uploads_dir / "ok.txt").write_text("x")
    (tmp_path / "outside.txt").write_text("secret")
    (server._uploads_dir / "link.txt").symlink_to(tmp_path / "outside.txt")
    assert server._resolve_upload("ok.txt").name == "ok.txt"
    for bad in ("../outside.txt", "..", ".", "", "a/b", "a\\b", "ok.txt\x00", "link.txt", "nope.txt", "x" * 300):
        assert server._resolve_upload(bad) is None, bad


def test_find_upload_unique_fuzzy_only(server):
    (server._uploads_dir / "Angebot Schulz.pdf").write_text("x")
    (server._uploads_dir / "Angebot Meier.pdf").write_text("x")
    assert server._find_upload("angebot schulz.pdf").name == "Angebot Schulz.pdf"
    assert server._find_upload("schulz").name == "Angebot Schulz.pdf"
    assert server._find_upload("angebot") is None          # ambiguous → refuse, never guess


def test_download_uses_safe_resolver(server, client, tmp_path):
    tok, _, _ = pair(server, client)
    (tmp_path / "outside.txt").write_text("secret")
    (server._uploads_dir / "link.txt").symlink_to(tmp_path / "outside.txt")
    r = client.get("/uploads/link.txt", params={"token": tok})
    assert r.status_code == 404 and b"secret" not in r.content


def test_files_listing_has_mtime(server, client):
    tok, _, _ = pair(server, client)
    (server._uploads_dir / "a.txt").write_text("hi")
    f = client.get("/api/files", headers=auth(tok)).json()["files"][0]
    assert f["name"] == "a.txt" and f["size"] == 2 and isinstance(f["mtime"], int)


# ── whitelist validation (no client connected → validation errors come first) ─
@pytest.mark.parametrize("action,target,reason", [
    ("eval", "alert(1)", "unknown_action"),
    ("open_project", "x", "unsupported_action"),
    ("open_device", "x", "unsupported_action"),
    ("open_view", "javascript:alert(1)", "unknown_view"),
    ("open_view", "../../etc/passwd", "unknown_view"),
    ("open_file", "../../etc/passwd", "file_not_found"),
    ("open_file", "", "file_not_found"),
    ("open_task", "", "missing_target"),
    ("show_notification", "", "missing_text"),
    ("open_view", "calendar", "no_dashboard_connected"),
])
def test_ui_action_validation(server, action, target, reason):
    res = asyncio.run(server.send_ui_action(action, target))
    assert res["ok"] is False and res["reason"] == reason


def test_ui_action_failure_is_logged_without_secrets(server, caplog):
    logger = logging.getLogger("mia.dashboard")
    logger.propagate = True                     # let caplog see it
    try:
        with caplog.at_level(logging.INFO, logger="mia.dashboard"):
            asyncio.run(server.send_ui_action("open_view", "nope"))
    finally:
        logger.propagate = False
    line = next(r.getMessage() for r in caplog.records if "ui_action_failed" in r.getMessage())
    data = json.loads(line.split("[Dashboard] ", 1)[1])
    assert data == {"event": "ui_action_failed", "action": "open_view", "target": "nope", "reason": "unknown_view"}


# ── end-to-end over a real WebSocket: action → client → ack → result ────────
def test_open_view_roundtrip_with_ack(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        fut = client.portal.start_task_soon(server.send_ui_action, "open_view", "calendar")
        msg = next_action(ws)
        assert msg["type"] == "ui_action" and msg["action"] == "open_view" and msg["target"] == "calendar"
        ws.send_json({"type": "ui_ack", "id": msg["id"], "ok": True})
        assert fut.result(5) == {"ok": True, "action": "open_view", "target": "calendar"}


def test_client_failure_ack_is_reported_not_swallowed(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        fut = client.portal.start_task_soon(server.send_ui_action, "open_task", "Fliesen")
        msg = next_action(ws)
        ws.send_json({"type": "ui_ack", "id": msg["id"], "ok": False, "reason": "not_found"})
        res = fut.result(5)
        assert res["ok"] is False and res["reason"] == "not_found"
        text = client.portal.start_task_soon(server.ui_action_for_tool, {"action": "open_task", "target": "x"})
        m2 = next_action(ws)
        ws.send_json({"type": "ui_ack", "id": m2["id"], "ok": False, "reason": "not_found"})
        assert "NOT opened" in text.result(5)


def test_no_ack_times_out_visibly(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        fut = client.portal.start_task_soon(server.send_ui_action, "focus_chat", "", "", "info", 0.3)
        next_action(ws)
        assert fut.result(5)["reason"] == "no_confirmation"


def test_open_file_sends_resolved_metadata_only(server, client):
    tok, _, _ = pair(server, client)
    (server._uploads_dir / "Notizen.txt").write_text("hallo")
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        fut = client.portal.start_task_soon(server.send_ui_action, "open_file", "notizen")
        msg = next_action(ws)
        assert msg["target"] == "Notizen.txt" and msg["meta"]["size"] == 5
        assert "path" not in json.dumps(msg).lower() and str(server._uploads_dir) not in json.dumps(msg)
        ws.send_json({"type": "ui_ack", "id": msg["id"], "ok": True})
        assert fut.result(5)["ok"]


def test_actions_are_not_replayed_to_late_clients(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        fut = client.portal.start_task_soon(server.send_ui_action, "focus_chat", "", "", "info", 0.2)
        next_action(ws); fut.result(5)
    assert all(m.get("type") != "ui_action" for m in server._history)


# ── awareness ────────────────────────────────────────────────────────────────
def test_ui_state_is_sanitised_and_exposed(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_json({"type": "ui_state", "view": "files", "views": ["files", "chat"],
                      "selected": {"kind": "file", "name": "a.txt", "evil": "x"},
                      "visible": {"files": 3, "bad": "<script>", "ok": True}, "hidden": False})
        ws.send_json({"type": "ui_state", "view": "javascript:1", "views": ["files", "hack"]})   # invalid → sanitised
        ws.send_json({"type": "command", "text": "sync"})           # ensures both above were processed
        for _ in range(50):
            if not server._command_queue.empty():
                break
    ctx = server.ui_context()
    assert ctx["view"] is None and "hack" not in json.dumps(ctx)
    assert ctx["available_views"][0] == "dashboard" and ctx["connected_dashboards"] in (0, 1)


def test_ui_state_valid_snapshot(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_json({"type": "ui_state", "view": "files", "selected": {"kind": "file", "name": "a.txt", "evil": "x"},
                      "visible": {"files": 3, "bad": "<script>"}, "hidden": False})
        ws.send_json({"type": "command", "text": "sync"})
        server._command_queue  # noqa
    for _ in range(100):
        if server._ui_state:
            break
    ctx = server.ui_context()
    assert ctx["view"] == "files" and ctx["selected"] == {"kind": "file", "name": "a.txt"}
    assert ctx["visible"] == {"files": 3} and "screenshot" not in json.dumps(ctx).lower()


def test_oversized_and_malformed_ws_messages_do_not_kill_the_connection(server, client):
    tok, _, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_text("this is not json")
        ws.send_json({"type": "ui_state", "view": "files", "pad": "x" * 6000})
        ws.send_json({"type": "command", "text": "still alive"})
    assert server._command_queue.get_nowait() == "still alive"
    assert not server._ui_state.get("view")


# ── name, static assets, status ──────────────────────────────────────────────
def test_assistant_name_is_injected_and_sanitised(server, client, monkeypatch, tmp_path):
    import dashboard.server as srv
    assert "Mia" in client.get("/desktop").text and "__ASSISTANT__" not in client.get("/desktop").text
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "api_keys.json").write_text(json.dumps({"assistant_name": 'Ana"><script>x</script>'}))
    monkeypatch.setattr(srv, "BASE_DIR", tmp_path)
    assert srv._assistant_name() == "Anascriptxscript"
    assert "<script>x" not in srv._assistant_name()
    m = client.get("/manifest.json").json()
    assert m["name"] == "Anascriptxscript"


def test_static_whitelist(client):
    for ok in ("mia.css", "mia-desktop.css", "mia-core.js", "mia-ui.js", "mia-desktop.js"):
        assert client.get(f"/static/{ok}").status_code == 200, ok
    for bad in ("server.py", "../server.py", "mia-x.py", "mia..js", "desktop.html", "mia-.js/../x"):
        assert client.get(f"/static/{bad}").status_code == 404, bad


def test_status_endpoint_is_authed_and_secret_free(server, client):
    assert client.get("/api/status").status_code == 401
    tok, key, dev = pair(server, client)
    body = client.get("/api/status", headers=auth(tok)).json()
    assert body["paired_devices"] >= 1 and body["encryption"] == "AES-256-CBC"
    dump = json.dumps(body)
    assert tok not in dump and key not in dump and dev not in dump


def test_device_sessions_file_is_private(server, client):
    import os, stat
    import dashboard.server as srv
    pair(server, client)
    mode = stat.S_IMODE(os.stat(srv.DEVICE_SESSIONS_PATH).st_mode)
    assert mode == 0o600


def test_auth_failures_are_logged(server, client, caplog):
    logger = logging.getLogger("mia.dashboard"); logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="mia.dashboard"):
            client.post("/login", json={"pin": "ZZZZZZ"})
            client.get("/uploads/x.txt?token=nope")
    finally:
        logger.propagate = False
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "auth_failed" in text and "ZZZZZZ" not in text and "nope" not in text
