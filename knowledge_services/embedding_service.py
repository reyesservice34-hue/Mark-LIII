#!/usr/bin/env python3
"""Embedding Service (Gerüst): lädt das Modell erst beim ersten /embed-Aufruf."""
import os

from flask import jsonify, request

from _common import create_app, run

app = create_app("mia-embedding-service")
MODEL_NAME = os.environ.get("MODEL_NAME", "all-MiniLM-L6-v2")
_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


@app.post("/embed")
def embed():
    texts = (request.get_json(silent=True) or {}).get("texts")
    if not isinstance(texts, list) or not texts:
        return jsonify(error="texts (Liste) fehlt"), 400
    vectors = get_model().encode(texts).tolist()
    return jsonify(model=MODEL_NAME, embeddings=vectors)


if __name__ == "__main__":
    run(app)
