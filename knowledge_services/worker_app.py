#!/usr/bin/env python3
"""Celery-Worker (Gerüst). Start: celery -A worker_app worker --loglevel=info"""
import os

from celery import Celery

app = Celery(
    "mia_worker",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://mia-redis:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://mia-redis:6379/1"),
)


@app.task(name="mia.ping")
def ping():
    return {"status": "ok", "worker": os.environ.get("WORKER_ID", "unknown")}
