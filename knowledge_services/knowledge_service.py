#!/usr/bin/env python3
"""Knowledge Base Service (Gerüst): liefert die JSON-Dateien aus KNOWLEDGE_DB_PATH."""
import json
import os

from flask import jsonify

from _common import create_app, data_dir, run

app = create_app("mia-knowledge-service")
KNOWLEDGE_DIR = data_dir("KNOWLEDGE_DB_PATH")


@app.get("/knowledge")
def list_documents():
    names = sorted(f for f in os.listdir(KNOWLEDGE_DIR) if f.endswith(".json"))
    return jsonify(documents=names)


@app.get("/knowledge/<name>")
def get_document(name):
    # Nur einfache Dateinamen zulassen, kein Pfad-Traversal.
    if os.path.basename(name) != name or not name.endswith(".json"):
        return jsonify(error="ungültiger Name"), 400
    path = os.path.join(KNOWLEDGE_DIR, name)
    if not os.path.isfile(path):
        return jsonify(error="nicht gefunden"), 404
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))


if __name__ == "__main__":
    run(app)
