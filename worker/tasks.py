"""
Celery tasks executed on a worker host.

Tasks:
  * run_test_bundle    - the core executor (container run, log stream, artifact
                         upload, status persistence, reservation cleanup).
  * report_status      - answer an admin host-status poll for THIS host by
                         publishing a HostStats reply to the poll's fanout.
  * publish_heartbeat  - periodic load report that upserts this host's
                         WorkerHost row (also how a host first registers).
  * reap_stale         - periodic maintenance for stale reservations/runs.

The dispatcher reserves a container slot optimistically (active_containers += 1).
run_test_bundle releases that reservation in its finally block via
_decrement_active_containers, AFTER the container is removed, so a heartbeat
racing in between still observes an accurate live count.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Any, Dict, Optional

import aio_pika  # noqa: F401  (kept for symmetry; sync publish below uses pika)
import pika

from backend.config import get_settings
from backend.database import SessionLocal, init_db
from backend.models import TestExecution, WorkerHost
from backend.services.quali_client import QualiClient
from shared.host_stats import collect_host_stats
from shared.schemas import TestBundleRequest, TestStatus
from shared.validation import require_non_empty_str
from worker.artifact_uploader import upload_artifact
from worker.celery_app import celery_app
from worker.docker_engine import DockerEngine
from worker.translation_layer import get_runner

logger = logging.getLogger(__name__)
settings = get_settings()


# --------------------------------------------------------------------------- #
# Internal AMQP publish helpers (synchronous; tasks are sync Celery workers)
# --------------------------------------------------------------------------- #
def _publish_fanout(exchange_name: str, body: str) -> None:
    """
    Publish a UTF-8 message to a (declared) fanout exchange, then disconnect.

    Args:
        exchange_name: Fanout exchange to publish to (declared if absent).
        body: The message body to publish.

    Returns:
        None.
    """
    params = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        credentials=pika.PlainCredentials(settings.rabbitmq_user, settings.rabbitmq_password),
    )
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange=exchange_name, exchange_type="fanout", durable=False)
        channel.basic_publish(exchange=exchange_name, routing_key="", body=body.encode("utf-8"))
    finally:
        connection.close()


def _publish_log_line(test_id: str, line: str) -> None:
    """
    Publish one live log line to the test's fanout exchange.

    Args:
        test_id: Owning test identifier.
        line: The log line text.

    Returns:
        None.
    """
    _publish_fanout(f"test_logs.{test_id}", line)


def _publish_stats_reply(correlation_id: str, body: str) -> None:
    """
    Publish this host's stats reply to a poll's correlation fanout exchange.

    Args:
        correlation_id: The poll correlation id from the backend.
        body: JSON-encoded HostStats payload.

    Returns:
        None.
    """
    _publish_fanout(f"host_status.{correlation_id}", body)


# --------------------------------------------------------------------------- #
# Status persistence + capacity bookkeeping
# --------------------------------------------------------------------------- #
def _set_status(
    test_id: str,
    status: TestStatus,
    *,
    target_host_id: Optional[str] = None,
    artifact_url: Optional[str] = None,
) -> None:
    """
    Update a TestExecution's status (and optional fields) in Postgres.

    Args:
        test_id: The test identifier (non-empty).
        status: New :class:`TestStatus` to persist.
        target_host_id: If given, records the host that ran the test.
        artifact_url: If given, records the uploaded artifact URL.

    Returns:
        None. No-op if the execution row does not exist.

    Raises:
        TypeError/ValueError: If ``test_id`` is invalid.
    """
    require_non_empty_str(test_id, "test_id")
    db = SessionLocal()
    try:
        row = db.query(TestExecution).filter_by(test_id=test_id).one_or_none()
        if row is None:
            return
        row.status = status.value
        if target_host_id is not None:
            row.target_host_id = target_host_id
        if artifact_url is not None:
            row.artifact_url = artifact_url
        db.commit()
    finally:
        db.close()


def _decrement_active_containers(host_id: str) -> None:
    """
    Best-effort decrement of a host's optimistic active-container reservation.

    Called after a test container is removed so the scheduler sees freed capacity
    before the next heartbeat reconciles ``active_containers`` to its absolute
    live value. Clamped at zero to avoid going negative if the heartbeat already
    reset the count.

    Args:
        host_id: The worker host whose counter to decrement.

    Returns:
        None. No-op if the host row is missing or already at zero.
    """
    db = SessionLocal()
    try:
        host = db.query(WorkerHost).filter_by(host_id=host_id).one_or_none()
        if host is not None and host.active_containers > 0:
            host.active_containers -= 1
            db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
@celery_app.task(bind=True, name="worker.run_test_bundle")
def run_test_bundle(self, bundle: Dict[str, Any]) -> str:
    """
    Execute one test bundle end-to-end on this worker host.

    Translates UI variables to a framework config, spawns the test container via
    the rootless Docker socket, streams logs to InfluxDB and the live WebSocket
    fanout exchange, uploads artifacts to Artifactory, and records final status.
    On exit it always removes the container and releases the optimistic
    container-count reservation made by the dispatcher.

    Args:
        self: Bound Celery task instance.
        bundle: Serialized :class:`TestBundleRequest` received off the broker
            (validated via ``from_dict`` before any side effects).

    Returns:
        str: The final status value (``"PASSED"`` or ``"FAILED"``).

    Raises:
        TypeError/ValueError: If ``bundle`` is malformed.
        Exception: Re-raised after marking the run FAILED and best-effort
            releasing the Quali reservation (Celery records the failure).
    """
    req = TestBundleRequest.from_dict(bundle)
    test_id = req.test_id
    init_db()
    engine = DockerEngine()
    from backend.services.influx_client import InfluxLogClient

    influx = InfluxLogClient()
    _set_status(test_id, TestStatus.RUNNING, target_host_id=settings.worker_host_id)
    try:
        runner = get_runner(req.runner_type, test_id, req.runner_variables)
        config_dir = runner.write_config()
        # Writable directory the test container writes result files into
        # (mounted rw at /artifacts); scanned for upload after the run.
        output_dir = os.path.join(settings.test_runs_dir, test_id, "artifacts")
        os.makedirs(output_dir, exist_ok=True)
        container = engine.start_test_container(
            image=req.framework_image,
            config_dir=config_dir,
            test_id=test_id,
            output_dir=output_dir,
        )

        db = SessionLocal()
        try:
            from backend.models import LogLine

            for line_no, line in enumerate(DockerEngine.stream_logs(container)):
                influx.write_line(test_id, line_no, line)
                db.add(LogLine(test_id=test_id, line_no=line_no, content=line))
                if line_no % 25 == 0:
                    db.commit()
                _publish_log_line(test_id, line)
            db.commit()
        finally:
            db.close()

        exit_code = container.wait().get("StatusCode", 1)

        artifact_url = None
        artifact_urls = []
        for fname in os.listdir(output_dir):
            if fname.endswith((".log", ".dmp", ".core", ".tar.gz")):
                try:
                    url = upload_artifact(test_id, os.path.join(output_dir, fname))
                    artifact_urls.append(url)
                except Exception:
                    logger.exception("Failed to upload artifact %s for test %s", fname, test_id)

        if artifact_urls:
            artifact_url = artifact_urls[0]

        final = TestStatus.PASSED if exit_code == 0 else TestStatus.FAILED
        _set_status(test_id, final, artifact_url=artifact_url)
        return final.value
    except Exception as exc:  # noqa: BLE001
        logger.exception("Test %s crashed: %s", test_id, exc)
        _set_status(test_id, TestStatus.FAILED)
        if req.quali_reservation_id:
            try:
                QualiClient().release_reservation(req.quali_reservation_id)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to release Quali reservation for %s", test_id)
        raise
    finally:
        # Order matters: remove the container first, THEN release the reservation
        # so a heartbeat racing in between still sees an accurate live count.
        engine.cleanup(test_id)
        _decrement_active_containers(settings.worker_host_id)
        influx.close()


@celery_app.task(name="worker.report_status")
def report_status(correlation_id: str) -> None:
    """
    Answer an admin host-status poll for THIS host.

    Collects a live HostStats snapshot and publishes it to the poll's
    correlation-scoped fanout exchange so only the requesting admin socket
    receives it.

    Args:
        correlation_id: The unique poll correlation id from the backend.

    Returns:
        None.

    Raises:
        TypeError/ValueError: If ``correlation_id`` is missing/blank.
    """
    require_non_empty_str(correlation_id, "correlation_id")
    stats = collect_host_stats(settings.worker_host_id, disk_path=settings.test_runs_dir)
    _publish_stats_reply(correlation_id, json.dumps(stats.to_dict()))


@celery_app.task(name="worker.publish_heartbeat")
def publish_heartbeat() -> None:
    """
    Publish this host's periodic heartbeat, upserting its WorkerHost row.

    Sets ``active_containers`` to the ABSOLUTE live count from Docker (the
    reconciling source of truth that the optimistic dispatch increment and the
    post-run decrement converge toward), refreshes CPU load, marks the host
    online, and stamps ``last_heartbeat``. Creates the row on first run, which is
    how a new host registers itself with the system.

    Args:
        None.

    Returns:
        None.
    """
    init_db()
    stats = collect_host_stats(settings.worker_host_id, disk_path=settings.test_runs_dir)

    live_count = 0
    try:
        engine = DockerEngine()
        live_count = engine.active_test_container_count()
    except Exception as e:
        logger.warning("Failed to query Docker for active container count: %s. Using last known count.", e)
        db_tmp = SessionLocal()
        try:
            cached_host = db_tmp.query(WorkerHost).filter_by(host_id=settings.worker_host_id).one_or_none()
            live_count = cached_host.active_containers if cached_host else 0
        finally:
            db_tmp.close()

    db = SessionLocal()
    try:
        host = db.query(WorkerHost).filter_by(host_id=settings.worker_host_id).one_or_none()
        if host is None:
            host = WorkerHost(host_id=settings.worker_host_id, max_containers=10)
            db.add(host)
        host.is_online = True
        host.active_containers = live_count  # absolute truth; reconciles drift
        host.cpu_utilization = stats.cpu_percent
        host.last_heartbeat = dt.datetime.now(dt.timezone.utc)
        db.commit()
    finally:
        db.close()


@celery_app.task(name="worker.reap_stale")
def reap_stale() -> None:
    """
    Periodic maintenance: fail runs stuck RUNNING far longer than allowed.

    Scans this host's executions still marked RUNNING past a generous deadline,
    marks them FAILED, and best-effort releases any associated Quali reservation
    so hardware is not held indefinitely by a dead run.

    Args:
        None.

    Returns:
        None.
    """
    init_db()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    db = SessionLocal()
    try:
        stuck = (
            db.query(TestExecution)
            .filter(
                TestExecution.target_host_id == settings.worker_host_id,
                TestExecution.status == TestStatus.RUNNING.value,
                TestExecution.updated_at < cutoff,
            )
            .all()
        )
        for row in stuck:
            row.status = TestStatus.FAILED.value
            if row.quali_reservation_id:
                try:
                    QualiClient().release_reservation(row.quali_reservation_id)
                except Exception:  # noqa: BLE001
                    logger.exception("reap: failed releasing reservation %s", row.test_id)
        db.commit()
    finally:
        db.close()