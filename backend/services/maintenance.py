"""
Central, worker-independent resiliency sweep (spec §D).

Spec §D requires catching the missing-heartbeat state from somewhere that does
NOT depend on the (possibly dead) worker. The per-worker ``reap_stale`` task can
only ever clean up a *live* host's transient hangs — if a machine crashes, its
beat stops and it can no longer reap itself. This module runs in the always-on
backend instead:

  1. ``sweep_stale_hosts`` — mark hosts whose heartbeat has gone stale (or never
     arrived) as offline so the scheduler immediately stops routing to them.
  2. ``fail_orphaned_runs`` — fail any execution still DISPATCHED/RUNNING on a
     now-offline host and best-effort release its Quali reservation so hardware
     isn't held by a dead job.

``run_maintenance_sweep`` ties the two together against a fresh session and is
what the backend's lifespan loop calls on a timer.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import TestExecution, WorkerHost
from backend.services.quali_client import QualiClient
from shared.schemas import TestStatus

logger = logging.getLogger(__name__)
settings = get_settings()

# Execution states that represent in-flight work which a dead host can orphan.
_ACTIVE_STATES = (TestStatus.DISPATCHED.value, TestStatus.RUNNING.value)


def sweep_stale_hosts(db: Session, now: dt.datetime, max_age_seconds: float) -> list[str]:
    """
    Mark online hosts whose heartbeat has gone stale (or never arrived) offline.

    Args:
        db: Active SQLAlchemy session.
        now: Current timezone-aware UTC time (passed in for testability).
        max_age_seconds: Maximum heartbeat age before a host is considered dead.

    Returns:
        list[str]: The ``host_id``s newly marked offline (empty if none changed).
    """
    cutoff = now - dt.timedelta(seconds=max_age_seconds)
    online_hosts = db.query(WorkerHost).filter(WorkerHost.is_online.is_(True)).all()

    affected: list[str] = []
    for host in online_hosts:
        hb = host.last_heartbeat
        if hb is None:
            is_stale = True  # online flag set but no heartbeat ever recorded
        else:
            if hb.tzinfo is None:  # tolerate naive timestamps from older rows
                hb = hb.replace(tzinfo=dt.timezone.utc)
            is_stale = hb < cutoff
        if is_stale:
            host.is_online = False
            affected.append(host.host_id)

    if affected:
        db.commit()
    return affected


def fail_orphaned_runs(db: Session, host_ids: list[str]) -> int:
    """
    Fail in-flight runs stranded on dead hosts and release their reservations.

    Args:
        db: Active SQLAlchemy session.
        host_ids: Hosts just marked offline whose active runs must be reaped.

    Returns:
        int: Number of executions transitioned to FAILED.
    """
    if not host_ids:
        return 0

    orphaned = (
        db.query(TestExecution)
        .filter(
            TestExecution.target_host_id.in_(host_ids),
            TestExecution.status.in_(_ACTIVE_STATES),
        )
        .all()
    )
    for run in orphaned:
        run.status = TestStatus.FAILED.value
        if run.quali_reservation_id:
            try:
                QualiClient().release_reservation(run.quali_reservation_id)
            except Exception:  # noqa: BLE001 - release is best-effort
                logger.exception(
                    "sweep: failed releasing Quali reservation for %s", run.test_id
                )

    if orphaned:
        db.commit()
    return len(orphaned)


def run_maintenance_sweep() -> None:
    """
    Run one full sweep: offline-mark dead hosts, then fail their orphaned runs.

    Args:
        None.

    Returns:
        None. Opens and closes its own session; safe to call on a timer. This is
        synchronous/blocking by design — the backend runs it via
        ``asyncio.to_thread`` so it never blocks the event loop.
    """
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        offline = sweep_stale_hosts(db, now, settings.heartbeat_timeout_seconds)
        if offline:
            failed = fail_orphaned_runs(db, offline)
            logger.warning(
                "maintenance: marked %d host(s) offline %s; failed %d orphaned run(s)",
                len(offline),
                offline,
                failed,
            )
    finally:
        db.close()
