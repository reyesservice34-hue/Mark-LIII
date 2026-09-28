"""Tests für dashboard/server.py — das lokale HTTP/WebSocket-Dashboard (FastAPI)."""
import base64
import os

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import padding as sym_pad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from dashboard import server as srv

TestClient = fastapi_testclient.TestClient


def _encrypt_cbc(aes_key: bytes, plaintext: str) -> str:
    """Gegenstück zu srv._decrypt_cbc, für Tests des verschlüsselten Kommando-Pfads."""
    iv = os.urandom(16)
    padder = sym_pad.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(iv + ct).decode("ascii")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(srv.DashboardServer, "_cert_paths",
                         staticmethod(lambda: (tmp_path / "none.key", tmp_path / "none.crt")))
    s = srv.DashboardServer()
    s._uploads_dir = tmp_path / "uploads"
    s._uploads_dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def client(server):
    return TestClient(server.app)


def _login(server, client) -> tuple[str, str]:
    """Erzeugt einen Ein-mal-Schlüssel, loggt ein und gibt (token, session_key) zurück."""
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    assert resp.status_code == 200
    return resp.json()["token"], key


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── reine Hilfsfunktionen ────────────────────────────────────────────────────
def test_derive_key_is_deterministic_for_the_same_session_key():
    assert srv._derive_key("ABC123") == srv._derive_key("ABC123")
    assert srv._derive_key("ABC123") != srv._derive_key("XYZ999")


def test_decrypt_cbc_round_trips_with_matching_encryptor():
    key = srv._derive_key("SESSIONKEY")
    enc = _encrypt_cbc(key, "Licht im Wohnzimmer an")
    assert srv._decrypt_cbc(key, enc) == "Licht im Wohnzimmer an"


def test_local_ip_returns_a_non_empty_string():
    ip = srv._local_ip()
    assert isinstance(ip, str) and ip.count(".") == 3


# ── new_key() ────────────────────────────────────────────────────────────────
def test_new_key_uses_only_unambiguous_characters(server):
    key = server.new_key()
    assert len(key) == 6
    assert set(key) <= set(srv._KEY_CHARS)
    assert not set(key) & set("OIL01")


def test_new_key_prunes_expired_keys(server, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(srv.time, "time", lambda: clock["t"])
    old_key = server.new_key(expiry_secs=1)
    clock["t"] += 5  # old_key is now expired
    server.new_key(expiry_secs=600)
    assert old_key not in server._pending_keys


def test_get_url_and_manual_url_are_http_without_certificates(server):
    assert server.get_url().startswith("http://")
    assert ":" in server.get_manual_url()


# ── Auth-Gate ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path", [
    ("get", "/api/system-status"), ("post", "/api/revoke-devices"),
    ("post", "/api/command"), ("post", "/api/wake"), ("get", "/api/files"),
])
def test_protected_endpoints_require_bearer_token(client, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401


# ── /login ───────────────────────────────────────────────────────────────────
def test_login_with_valid_pending_key_returns_token(server, client):
    key = server.new_key()
    resp = client.post("/login", json={"pin": key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["token"]
    assert body["token"] in server._tokens


def test_login_with_unknown_key_is_rejected(client):
    resp = client.post("/login", json={"pin": "ZZZZZZ"})
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


def test_login_key_is_one_time_use(server, client):
    key = server.new_key()
    client.post("/login", json={"pin": key})
    second = client.post("/login", json={"pin": key})
    assert second.status_code == 401


def test_login_pin_is_case_insensitive_and_trimmed(server, client):
    key = server.new_key()
    resp = client.post("/login", json={"pin": f"  {key.lower()}  "})
    assert resp.status_code == 200


# ── /auto-login + device pairing ─────────────────────────────────────────────
def test_auto_login_with_missing_key_shows_expired_page(client):
    resp = client.get("/auto-login")
    assert resp.status_code == 200
    assert "Link Expired" in resp.text


def test_auto_login_with_valid_key_sets_storage_and_registers_device(server, client):
    key = server.new_key()
    resp = client.get(f"/auto-login?key={key}")
    assert resp.status_code == 200
    assert "mia_device_token" in resp.text
    assert key not in server._pending_keys
    assert len(server._device_sessions) == 1


def test_device_login_with_known_token_returns_fresh_auth_token(server, client):
    key = server.new_key()
    client.get(f"/auto-login?key={key}")
    dev_tok = next(iter(server._device_sessions))
    resp = client.post("/api/device-login", json={"device_token": dev_tok})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["token"] in server._tokens


def test_device_login_with_unknown_token_is_rejected(client):
    resp = client.post("/api/device-login", json={"device_token": "gibts-nicht"})
    assert resp.status_code == 401


def test_revoke_devices_clears_all_sessions(server, client):
    key = server.new_key()
    client.get(f"/auto-login?key={key}")
    token, _ = _login(server, client)
    resp = client.post("/api/revoke-devices", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["revoked"] == 1
    assert server._device_sessions == {}


# ── system-status ────────────────────────────────────────────────────────────
def test_system_status_reports_live_counts(server, client):
    token, _ = _login(server, client)
    resp = client.get("/api/system-status", headers=auth(token))
    body = resp.json()
    assert body["ok"] is True
    assert body["active_tokens"] == 1
    assert body["encrypted_sessions"] == 1


# ── /api/command + /api/wake ─────────────────────────────────────────────────
def test_command_with_plain_text_enqueues_it_and_wakes(server, client):
    token, _ = _login(server, client)
    woken = []
    server.set_wake_callback(lambda: woken.append(1))
    resp = client.post("/api/command", headers=auth(token), json={"text": "Licht an"})
    assert resp.status_code == 200
    assert server._command_queue.get_nowait() == "Licht an"
    assert woken == [1]


def test_command_with_blank_text_does_not_enqueue_or_wake(server, client):
    token, _ = _login(server, client)
    woken = []
    server.set_wake_callback(lambda: woken.append(1))
    client.post("/api/command", headers=auth(token), json={"text": "   "})
    assert server._command_queue.empty()
    assert woken == []


def test_command_with_valid_encrypted_payload_decrypts_and_enqueues(server, client):
    token, key = _login(server, client)
    enc = _encrypt_cbc(server._aes_key(key), "Musik pausieren")
    resp = client.post("/api/command", headers=auth(token), json={"enc": enc})
    assert resp.status_code == 200
    assert server._command_queue.get_nowait() == "Musik pausieren"


def test_command_with_garbage_encrypted_payload_returns_400(server, client):
    token, _ = _login(server, client)
    resp = client.post("/api/command", headers=auth(token), json={"enc": "bXVsbCBrYXB1dHQ="})
    assert resp.status_code == 400


def test_wake_endpoint_invokes_callback(server, client):
    token, _ = _login(server, client)
    woken = []
    server.set_wake_callback(lambda: woken.append(1))
    resp = client.post("/api/wake", headers=auth(token))
    assert resp.status_code == 200
    assert woken == [1]


# ── Datei-Upload/-Download ────────────────────────────────────────────────────
def test_upload_requires_auth(client):
    resp = client.post("/api/upload", files={"file": ("a.txt", b"hallo")})
    assert resp.status_code == 401


def test_upload_stores_file_and_sanitizes_a_malicious_filename(server, client):
    token, _ = _login(server, client)
    resp = client.post("/api/upload", headers=auth(token),
                       files={"file": ("../../evil<>:name.txt", b"Inhalt")})
    assert resp.status_code == 200
    body = resp.json()
    assert "/" not in body["name"] and "<" not in body["name"]
    assert (server._uploads_dir / body["name"]).read_bytes() == b"Inhalt"


def test_upload_avoids_overwriting_by_appending_a_counter(server, client):
    token, _ = _login(server, client)
    client.post("/api/upload", headers=auth(token), files={"file": ("doc.txt", b"1")})
    resp = client.post("/api/upload", headers=auth(token), files={"file": ("doc.txt", b"2")})
    assert resp.json()["name"] == "doc_1.txt"


def test_upload_rejects_file_larger_than_configured_max(server, client, monkeypatch):
    token, _ = _login(server, client)
    monkeypatch.setattr(srv, "MAX_UPLOAD_MB", 0)  # jede Nutzlast überschreitet 0 MB
    resp = client.post("/api/upload", headers=auth(token), files={"file": ("big.bin", b"x" * 100)})
    assert resp.status_code == 413


def test_list_files_requires_auth(client):
    assert client.get("/api/files").status_code == 401


def test_list_files_lists_uploaded_files(server, client):
    token, _ = _login(server, client)
    client.post("/api/upload", headers=auth(token), files={"file": ("a.txt", b"x")})
    resp = client.get("/api/files", headers=auth(token))
    names = [f["name"] for f in resp.json()["files"]]
    assert "a.txt" in names


def test_download_requires_token_query_param(server):
    (server._uploads_dir / "a.txt").write_bytes(b"hallo")
    client = TestClient(server.app)
    assert client.get("/uploads/a.txt").status_code == 401


def test_download_unknown_file_is_404(server, client):
    token, _ = _login(server, client)
    resp = client.get(f"/uploads/does-not-exist.txt?token={token}")
    assert resp.status_code == 404


def test_download_returns_file_contents(server, client):
    token, _ = _login(server, client)
    (server._uploads_dir / "a.txt").write_bytes(b"Dateiinhalt")
    resp = client.get(f"/uploads/a.txt?token={token}")
    assert resp.status_code == 200
    assert resp.content == b"Dateiinhalt"


# ── WebSockets ────────────────────────────────────────────────────────────────
def test_ws_rejects_missing_or_unknown_token(server, client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=nicht-vergeben"):
            pass


def test_ws_accepts_valid_token_and_streams_history(server, client):
    token, _ = _login(server, client)  # already broadcasts one "sys" entry into history
    import asyncio
    asyncio.run(server.broadcast({"type": "sys", "text": "vorher"}))
    with client.websocket_connect(f"/ws?token={token}") as ws:
        backlog = [ws.receive_json() for _ in range(len(server._history))]
        assert {"type": "sys", "text": "vorher"} in backlog


def test_ws_command_message_enqueues_and_wakes(server, client):
    token, _ = _login(server, client)
    woken = []
    server.set_wake_callback(lambda: woken.append(1))
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "command", "text": "Wecker stellen"})
        ws.close()
    assert server._command_queue.get_nowait() == "Wecker stellen"
    assert woken == [1]


def test_phone_audio_ws_rejects_unknown_token(server, client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/phone-audio?token=x"):
            pass


def test_phone_audio_ws_queues_received_bytes(server, client):
    token, _ = _login(server, client)
    with client.websocket_connect(f"/ws/phone-audio?token={token}") as ws:
        ws.send_bytes(b"\x01\x02\x03")
        ws.close()
    frame = server._phone_audio_queue.get_nowait()
    assert frame["data"] == b"\x01\x02\x03"
    assert frame["mime_type"] == "audio/pcm"


# ── statische Seiten ──────────────────────────────────────────────────────────
def test_index_page_substitutes_ip_and_port(server, client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "__IP__" not in resp.text and "__PORT__" not in resp.text
    assert server._ip in resp.text


def test_login_page_is_served(client):
    resp = client.get("/login")
    assert resp.status_code == 200
