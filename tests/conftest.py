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


# ── Fixtures aus main (Modul-Aufräumen für Action-/Plugin-Loader-Tests) ──
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# Real, on-disk package directories — modules loaded from here are permanent
# imports (actions.web_search, plugins.n8n_analyzer, ...) and must survive between tests.
_REAL_ACTIONS_DIR = os.path.join(_REPO_ROOT, "actions") + os.sep
_REAL_PLUGINS_DIR = os.path.join(_REPO_ROOT, "plugins") + os.sep


@pytest.fixture(autouse=True)
def _clean_dynamically_loaded_modules():
    """action_loader/plugin_loader import discovered files as actions.<stem> /
    plugins.<stem> into sys.modules. Without this, a stem reused by two tests
    (in different tmp_path dirs) would silently hit the other test's cached
    module instead of being freshly imported. Real, permanently-installed
    modules under the repo's actions/ and plugins/ directories are left alone
    so other tests that import them (e.g. actions.web_search) keep working."""

    def _clean():
        for key in list(sys.modules):
            if not (key.startswith("actions.") or key.startswith("plugins.")):
                continue
            mod_file = getattr(sys.modules[key], "__file__", "") or ""
            if mod_file.startswith(_REAL_ACTIONS_DIR) or mod_file.startswith(_REAL_PLUGINS_DIR):
                continue
            del sys.modules[key]

    _clean()
    yield
    _clean()


@pytest.fixture(autouse=True)
def _isolate_brain_paths(tmp_path, monkeypatch):
    """The brain (episodic/semantic archive, learning inbox, FTS index) lives
    under the repo. Redirect it per test so tests never read or pollute the
    real memory of a running MIA."""
    try:
        import memory.memory_manager as mm
    except Exception:
        yield
        return
    for attr, rel in (
        ("EPISODIC_PATH", "brain/memory/episodic/sessions.jsonl"),
        ("SEMANTIC_PATH", "brain/memory/semantic/archive.jsonl"),
        ("LEARNING_INBOX_PATH", "brain/ingestion/inbox/candidates.jsonl"),
        ("LEARNING_VALIDATED_PATH", "brain/ingestion/validated/processed.jsonl"),
        ("LEARNING_REJECTED_PATH", "brain/ingestion/rejected/rejected.jsonl"),
        ("RETRIEVAL_DB_PATH", "brain/indexes/memory_fts.sqlite3"),
    ):
        if hasattr(mm, attr):
            monkeypatch.setattr(mm, attr, tmp_path / "mia-brain" / rel)
    yield
