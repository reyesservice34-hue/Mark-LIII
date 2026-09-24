"""Gemeinsame Helfer für die MIA-KNOWLEDGE-01-Dienste (Gerüste)."""
import os
import sqlite3
from contextlib import closing

from flask import Flask, jsonify


def create_app(service_name: str) -> Flask:
    """Flask-App mit /health, das ohne externe Abhängigkeiten antwortet."""
    app = Flask(service_name)

    @app.get("/health")
    def health():
        return jsonify(status="ok", service=service_name)

    return app


def data_dir(env_var: str, default: str = "/data") -> str:
    path = os.environ.get(env_var, default)
    os.makedirs(path, exist_ok=True)
    return path


def open_db(path: str, schema: str) -> sqlite3.Connection:
    """SQLite-Verbindung; legt das Schema beim ersten Zugriff an."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with closing(conn.cursor()) as cur:
        cur.executescript(schema)
    conn.commit()
    return conn


def run(app: Flask) -> None:
    app.run(host="0.0.0.0", port=5000)
