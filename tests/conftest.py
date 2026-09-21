"""Shared fixtures for the dashboard tests.

HOME is redirected *before* dashboard.server is imported, because the module
creates its uploads folder under ~/Downloads at import time. Device sessions
are redirected to a temp file so tests never touch real pairings.
"""
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="mia-test-home-")
os.environ["HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    import dashboard.server as srv
    monkeypatch.setattr(srv, "DEVICE_SESSIONS_PATH", tmp_path / "device_sessions.json")
    monkeypatch.setattr(srv, "UPLOADS_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    s = srv.DashboardServer()
    s._uploads_dir = tmp_path / "uploads"
    return s


@pytest.fixture()
def client(server):
    from starlette.testclient import TestClient
    with TestClient(server.app) as c:
        yield c


def pair(server, client):
    """Log in with a fresh one-time key; returns (token, key, device_token)."""
    key = server.new_key()
    r = client.post("/login", json={"pin": key})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], key, body["device_token"]


def encrypt(session_key: str, text: str) -> str:
    """Mirror of shared.js _encrypt(): AES-256-CBC, key=SHA256(key+salt)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    aes = hashlib.sha256(session_key.encode() + b"JARVIS-DASHBOARD-v1").digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(aes), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + enc.update(data) + enc.finalize()).decode()
