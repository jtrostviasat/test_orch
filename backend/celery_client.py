"""
Backend-side Celery handle used ONLY to enqueue tasks by name.

The backend must not import the ``worker`` package: the worker's Docker/psutil
dependencies and task bodies don't belong in the web tier, and they are not even
shipped in the backend image. Enqueuing a task by its registered name over the
shared broker requires no task definition on this side, so this thin client fully
decouples the backend from worker internals while still letting routes dispatch
work to a specific host queue.
"""
from __future__ import annotations

from celery import Celery

from backend.config import get_settings

settings = get_settings()

# Name differs from the worker app ("test_orch") on purpose — this handle never
# registers or executes tasks, it only publishes them by name to a target queue.
celery_client = Celery(
    "test_orch_client",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
