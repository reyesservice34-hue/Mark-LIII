"""Regression tests for behaviour that must survive the Mia control-center work:
login / QR pairing / device reconnect / auth / AES commands / WebSocket /
upload / list / download."""
import json

import pytest

from conftest import encrypt, pair


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_pages_are_served(client):
    for path in ("/", "/desktop", "/login"):
        r = client.get(path)
        assert r.status_code == 200 and "<html" in r.text.lower(), path
    assert client.get("/static/shared.js").status_code == 200
    assert client.get("/static/crypto.js").status_code == 200


def test_login_is_one_time_and_bad_key_rejected(server, client):
    key = server.new_key()
    assert client.post("/login", json={"pin": "ZZZZZZ"}).status_code == 401
    assert client.post("/login", json={"pin": key}).status_code == 200
    assert client.post("/login", json={"pin": key}).status_code == 401  # burned


def test_qr_auto_login_creates_session(server, client):
    key = server.new_key()
    r = client.get("/auto-login", params={"key": key})
    assert r.status_code == 200 and "jarvis_token" in r.text
    assert "Link Expired" in client.get("/auto-login", params={"key": key}).text


def test_auto_login_next_is_whitelisted(server, client):
    key = server.new_key()
    r = client.get("/auto-login", params={"key": key, "next": "//evil.example/x"})
    assert "evil.example" not in r.text


def test_device_reconnect_and_persistence(server, client):
    import dashboard.server as srv
    _, key, dev = pair(server, client)
    r = client.post("/api/device-login", json={"device_token": dev})
    assert r.status_code == 200 and r.json()["key"] == key and r.json()["token"]
    assert client.post("/api/device-login", json={"device_token": "nope"}).status_code == 401
    # persisted to disk, survives a "restart"
    on_disk = json.loads(srv.DEVICE_SESSIONS_PATH.read_text())
    assert dev in on_disk
    assert dev in srv._load_device_sessions()


def test_revoke_devices(server, client):
    tok, _, dev = pair(server, client)
    assert client.post("/api/revoke-devices").status_code == 401
    assert client.post("/api/revoke-devices", headers=auth(tok)).json()["revoked"] >= 1
    assert client.post("/api/device-login", json={"device_token": dev}).status_code == 401


def test_endpoints_require_auth(client):
    assert client.post("/api/command", json={"text": "hi"}).status_code == 401
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/wake").status_code == 401
    assert client.get("/uploads/x.txt?token=bad").status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=bad"):
            pass


def test_command_plain_and_aes(server, client):
    tok, key, _ = pair(server, client)
    assert client.post("/api/command", json={"text": "hallo"}, headers=auth(tok)).json()["ok"]
    assert client.post("/api/command", json={"enc": encrypt(key, "verschlüsselt äöü")},
                       headers=auth(tok)).json()["ok"]
    assert client.post("/api/command", json={"enc": "AAAA"}, headers=auth(tok)).status_code == 400
    got = [server._command_queue.get_nowait() for _ in range(2)]
    assert got == ["hallo", "verschlüsselt äöü"]


def test_websocket_history_and_command(server, client):
    tok, key, _ = pair(server, client)
    with client.websocket_connect(f"/ws?token={tok}") as ws:
        ws.send_json({"type": "command", "enc": encrypt(key, "per ws")})
    assert server._command_queue.get_nowait() == "per ws"


def test_upload_list_download_roundtrip(server, client):
    tok, _, _ = pair(server, client)
    r = client.post("/api/upload", files={"file": ("../../evil name.txt", b"inhalt")},
                    headers=auth(tok))
    assert r.status_code == 200
    name = r.json()["name"]
    assert "/" not in name and ".." not in name
    files = client.get("/api/files", headers=auth(tok)).json()["files"]
    assert any(f["name"] == name and f["size"] == 6 for f in files)
    dl = client.get(f"/uploads/{name}", params={"token": tok})
    assert dl.status_code == 200 and dl.content == b"inhalt"


def test_download_blocks_traversal(server, client, tmp_path):
    tok, _, _ = pair(server, client)
    (tmp_path / "secret.txt").write_text("geheim")
    for evil in ("..%2Fsecret.txt", "%2e%2e%2fsecret.txt", "....//secret.txt", "%2e%2e"):
        r = client.get(f"/uploads/{evil}", params={"token": tok})
        assert r.status_code in (404, 400, 422), (evil, r.status_code)
        assert b"geheim" not in r.content
