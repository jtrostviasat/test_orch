"""
Celery application and beat schedule for a single worker host.

Each worker process consumes ONLY its own dedicated queue (``queue_<host_id>``)
so the backend scheduler can target a specific machine. Beat runs two periodic
maintenance tasks: a heartbeat (publishes live load -> WorkerHost row) and a
reaper (releases stale Quali reservations / marks dead runs FAILED).
"""
from __future__ import annotations

from celery import Celery

from backend.config import get_settings
from backend.services.scheduler import queue_name_for

settings = get_settings()

celery_app = Celery(
    "test_orch",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Route this host's task queue. The worker is started with
# --queues=queue_<host_id> so it only pulls work intended for it.
celery_app.conf.update(
    task_default_queue=queue_name_for(settings.worker_host_id),
    task_acks_late=True,                 # redeliver if the worker dies mid-task
    worker_prefetch_multiplier=1,        # don't hoard tasks; fair dispatch
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
)

# Periodic maintenance. Heartbeat cadence (30s) also bounds how quickly the
# admin inventory and scheduler see load changes.
celery_app.conf.beat_schedule = {
    "publish-heartbeat": {
        "task": "worker.publish_heartbeat",
        "schedule": 30.0,
    },
    "reap-stale-reservations": {
        "task": "worker.reap_stale",
        "schedule": 120.0,
    },
}

# Store beat schedule in /tmp to avoid permission issues in rootless containers
celery_app.conf.beat_schedule_filename = "/tmp/celerybeat-schedule"

# Importing the tasks module registers the @celery_app.task functions.
import worker.tasks  # noqa: E402,F401