"""Gemeinsame Helfer für die MIA-KNOWLEDGE-01-Dienste."""
import hmac
import os
import sqlite3
from contextlib import closing

from flask import Flask, jsonify, request

MAX_BODY_BYTES = 16 * 1024 * 1024


def create_app(service_name: str) -> Flask:
    """Flask-App mit /health (ohne Anmeldung) und Bearer-Token-Prüfung für alles andere.

    Das Token kommt aus MIA_SERVICE_TOKEN und wird bei jeder Anfrage gelesen. Fehlt es, startet der Dienst
    nicht (fail-closed) und antwortet zur Laufzeit mit 503. Nur mit MIA_ALLOW_NO_AUTH=1 (lokale Entwicklung)
    läuft er ohne Anmeldung.
    """
    allow_open = os.environ.get("MIA_ALLOW_NO_AUTH") == "1"
    if not os.environ.get("MIA_SERVICE_TOKEN") and not allow_open:
        raise RuntimeError("MIA_SERVICE_TOKEN fehlt - Dienst startet nicht ohne Anmeldung "
                           "(nur für lokale Entwicklung: MIA_ALLOW_NO_AUTH=1)")
    app = Flask(service_name)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

    @app.before_request
    def _require_token():
        if request.path == "/health":
            return None
        token = os.environ.get("MIA_SERVICE_TOKEN", "")
        if not token:
            if os.environ.get("MIA_ALLOW_NO_AUTH") == "1":
                return None
            return jsonify(error="service not configured"), 503
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
            return jsonify(error="unauthorized"), 401
        return None

    @app.errorhandler(413)
    def _too_large(_exc):
        return jsonify(error=f"Anfrage größer als {MAX_BODY_BYTES // (1024 * 1024)} MB"), 413

    @app.get("/health")
    def health():
        return jsonify(status="ok", service=service_name)

    return app


def data_dir(env_var: str, default: str = "/data") -> str:
    path = os.environ.get(env_var, default)
    os.makedirs(path, exist_ok=True)
    return path


def open_db(path: str, schema: str) -> sqlite3.Connection:
    """SQLite-Verbindung (WAL, Wartezeit bei Sperre); legt das Schema beim ersten Zugriff an.

    Immer mit `with closing(open_db(...)) as conn:` benutzen, damit die Verbindung auch bei Fehlern zugeht.
    """
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    with closing(conn.cursor()) as cur:
        cur.executescript(schema)
    conn.commit()
    return conn


def json_body() -> dict:
    """JSON-Objekt aus dem Body; alles andere zählt als leeres Objekt."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def run(app: Flask) -> None:
    app.run(host="0.0.0.0", port=5000)
